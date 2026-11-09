"""XBRL grounding benchmark: held-out 10-K pages, image-only OCR, issuer XBRL tags as the key.

Runs on GitHub Actions only (.github/workflows/xbrl-grounding.yml): SEC requests,
Chromium rendering and OCR never run on a developer machine. Subcommands:

  run           select issuers (committed rule or explicit CIKs), fetch each 10-K's
                Inline XBRL HTML from EDGAR, render statement pages, OCR the images,
                score against the iXBRL tags, write the report and publishable assets
  verify-saved  re-score a saved report from its saved key and OCR spans (no OCR,
                no network) and check the published browser assets against it

The extraction sees only page images. The iXBRL HTML is used to choose which
pages to render and to build the answer key, never as extraction input.
"""
from __future__ import annotations

import argparse
import base64
import gzip
from collections import Counter
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.xbrl_grounding import (  # noqa: E402
    PageReading, SCORE_RULE_VERSION, ABSTAIN_REASONS, OUTCOME_LABELS, candidate_order, canonical_digest,
    key_entry, score_fact, slice_summaries, statement_pages, summarize,
)

CONFIG = ROOT / "configs/xbrl-grounding.json"
SITE_DIR = ROOT / "creditlens/grounding"
SCOPE = ("Clean Chromium renders of born-digital Inline XBRL 10-K pages, not scans or the issuers' PDFs. "
         "Answer key = the issuer's own XBRL tags (concept, context, unit, decimals, scale, sign) plus the printed "
         "parentheses next to each tagged number. Extraction input = page image only (RapidOCR, pinned profile); "
         "facts are matched to OCR spans only after recognition.")


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Sources: Internet Archive captures of SEC files (SEC itself refuses Actions runners)
# ---------------------------------------------------------------------------

def sha1_base32(raw: bytes) -> str:
    return base64.b32encode(hashlib.sha1(raw).digest()).decode()


class Fetcher:
    """One declared User-Agent, a minimum interval between requests, bounded sizes."""

    def __init__(self, user_agent: str, interval: float):
        self.user_agent, self.interval, self.last, self.log = user_agent, interval, 0.0, []

    def get(self, url: str, *, max_bytes: int, accept_status=(200,)) -> bytes:
        for attempt in range(4):
            wait = self.interval - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            request = Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"})
            try:
                with urlopen(request, timeout=120) as response:
                    raw = response.read(max_bytes + 1)
                    status = response.status
            except HTTPError as error:
                status, raw = error.code, error.read(2000)
            except URLError as error:
                status, raw = None, str(error).encode()
            self.log.append({"url": url, "status": status, "bytes": len(raw),
                             "at": datetime.now(timezone.utc).isoformat()})
            if status in (429, 500, 502, 503, 504) or status is None:
                time.sleep(3 * (attempt + 1))
                continue
            if status not in accept_status:
                body = re.sub(r"<[^>]+>|\s+", " ", raw.decode("utf-8", "replace"))[:300]
                raise RuntimeError(f"HTTP {status} for {url}: {body}")
            if len(raw) > max_bytes:
                raise RuntimeError(f"Response larger than {max_bytes} bytes: {url}")
            return raw
        raise RuntimeError(f"Gave up after retries: {url}")

    def archived(self, original: str, *, max_bytes: int, before: str | None = None) -> tuple[bytes, dict]:
        """Latest 200 capture of an SEC URL in the Internet Archive; bytes checked against the CDX digest."""
        params = {"url": original, "output": "json", "filter": "statuscode:200", "limit": "-1"}
        if before:
            params["to"] = before
        rows = json.loads(self.get("https://web.archive.org/cdx/search/cdx?" + urlencode(params), max_bytes=2_000_000) or b"[]")
        if len(rows) < 2:
            raise LookupError("no_archive_capture")
        capture = dict(zip(rows[0], rows[-1]))
        archive_url = f"https://web.archive.org/web/{capture['timestamp']}id_/{capture['original']}"
        raw = self.get(archive_url, max_bytes=max_bytes)
        if sha1_base32(raw) != capture["digest"]:
            raise RuntimeError("Archived bytes do not match the capture digest: " + original)
        encoded = raw[:2] == b"\x1f\x8b"
        if encoded:  # stored as served (Content-Encoding: gzip); the digest covers the stored bytes
            raw = gzip.decompress(raw)
        return raw, {"stored_gzip": encoded, "original_url": capture["original"], "capture_timestamp": capture["timestamp"],
                     "archive_url": archive_url, "capture_sha1_base32": capture["digest"],
                     "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


# ---------------------------------------------------------------------------
# Issuer selection
# ---------------------------------------------------------------------------

def finqa_tickers(fetcher: Fetcher) -> tuple[set[str], dict]:
    manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
    raw = fetcher.get(manifest["source_url"], max_bytes=60_000_000)
    digest = hashlib.sha256(raw).hexdigest()
    if digest != manifest["sha256"]:
        raise RuntimeError("Pinned FinQA dev split hash mismatch")
    tickers = set()
    for record in json.loads(raw):
        name = record.get("filename") or record.get("id", "")
        tickers.add(name.split("/")[0].upper())
    return tickers, {"source_url": manifest["source_url"], "sha256": digest, "tickers": len(tickers)}


def _ticker_key(ticker: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", ticker.upper())


def primary_document(instance: str) -> str | None:
    """FSDS names the iXBRL instance '<doc>_htm.xml'; the primary document is '<doc>.htm'."""
    match = re.fullmatch(r"([A-Za-z0-9._-]+)_htm\.xml", instance or "")
    return match.group(1) + ".htm" if match else None


def filing_urls(cik: int, accession: str, document: str) -> dict:
    folder = accession.replace("-", "")
    return {"document_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{document}",
            "index_url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{accession}-index.htm"}


def heldout_candidates(fetcher: Fetcher, config: dict) -> tuple[list[dict], dict]:
    rule = config["heldout_rule"]
    population = rule["population"]
    zip_raw, zip_meta = fetcher.archived(population["dataset_url"], max_bytes=300_000_000)
    with zipfile.ZipFile(BytesIO(zip_raw)) as archive:
        lines = archive.read("sub.txt").decode("utf-8", "replace").splitlines()
    header = lines[0].split("\t")
    rows = [dict(zip(header, line.split("\t"))) for line in lines[1:] if line.strip()]
    want = population["filter"]
    eligible = {}
    for row in rows:
        if all(row.get(k) == v for k, v in want.items()) and primary_document(row.get("instance", "")):
            cik = int(row["cik"])
            if cik not in eligible or row["filed"] < eligible[cik]["filed"]:
                eligible[cik] = row
    tickers_raw, tickers_meta = fetcher.archived(rule["exclude"]["ticker_map_url"], max_bytes=20_000_000)
    by_ticker = {_ticker_key(v["ticker"]): int(v["cik_str"]) for v in json.loads(tickers_raw).values()}
    finqa, finqa_meta = finqa_tickers(fetcher)
    finqa_ciks = {by_ticker[_ticker_key(t)] for t in finqa if _ticker_key(t) in by_ticker}
    excluded = {int(c) for c in rule["exclude"]["ciks"]} | finqa_ciks
    order = candidate_order(set(eligible) - excluded, rule["order"]["salt"])
    audit = {
        "rule_id": rule["rule_id"], "population_dataset": zip_meta, "dataset_filings": len(rows),
        "eligible": len(eligible), "ticker_map": tickers_meta,
        "excluded_project_ciks": sorted(int(c) for c in rule["exclude"]["ciks"]),
        "finqa": dict(finqa_meta, matched_ciks=len(finqa_ciks),
                      unmatched_tickers=sorted(t for t in finqa if _ticker_key(t) not in by_ticker)),
        "excluded_in_population": sorted(excluded & set(eligible)), "population_after_exclusion": len(order),
    }
    candidates = []
    for cik in order:
        row = eligible[cik]
        candidates.append({"cik": cik, "name": row["name"], "sic": row.get("sic"), "accession": row["adsh"],
                           "document": primary_document(row["instance"]), "filed": row["filed"], "period": row["period"],
                           "form": row["form"], "afs": row.get("afs"), "fye": row.get("fye")})
    return candidates, audit


def explicit_candidates(spec: str) -> list[dict]:
    """'cik:accession:document' entries, comma-separated (documents named on the EDGAR filing index)."""
    out = []
    for item in [x.strip() for x in spec.split(",") if x.strip()]:
        match = re.fullmatch(r"([0-9]{1,10}):([0-9]{10}-[0-9]{2}-[0-9]{6}):([A-Za-z0-9._-]+\.html?)", item)
        if not match:
            raise SystemExit("--filings entries must look like 320193:0000320193-25-000079:aapl-20250927.htm")
        out.append({"cik": int(match.group(1)), "name": None, "accession": match.group(2), "document": match.group(3),
                    "filed": None, "period": None, "form": "10-K"})
    if not out:
        raise SystemExit("--filings is required for dev/added runs")
    return out


# ---------------------------------------------------------------------------
# Rendering (Playwright Chromium; network blocked except the local file)
# ---------------------------------------------------------------------------

EXTRACT_JS = r"""
(cfg) => {
  const local = el => (el.localName || '').toLowerCase();
  const tail = el => local(el).split(':').pop();
  const prefixed = el => local(el).includes(':');
  const clean = s => (s || '').replace(/[\s ]+/g, ' ').trim();
  const all = Array.from(document.getElementsByTagName('*'));
  const contexts = {}, units = {}, hiddenById = {};
  for (const el of all) {
    if (!prefixed(el)) continue;
    const name = tail(el);
    if (name === 'context') {
      const period = {}, dims = [];
      for (const c of el.getElementsByTagName('*')) {
        const t = tail(c);
        if (t === 'instant') period.instant = clean(c.textContent);
        else if (t === 'startdate') period.start = clean(c.textContent);
        else if (t === 'enddate') period.end = clean(c.textContent);
        else if (t === 'explicitmember' || t === 'typedmember') dims.push([c.getAttribute('dimension'), clean(c.textContent)]);
      }
      contexts[el.getAttribute('id')] = {period, dims};
    } else if (name === 'unit') {
      const measures = Array.from(el.getElementsByTagName('*')).filter(c => tail(c) === 'measure').map(c => clean(c.textContent));
      const divide = Array.from(el.getElementsByTagName('*')).some(c => tail(c) === 'divide');
      units[el.getAttribute('id')] = divide && measures.length === 2 ? measures[0] + '/' + measures[1] : measures.join('*');
    }
  }
  const ancestors = el => { const out = new Set(); for (let p = el.parentElement; p; p = p.parentElement) out.add(local(p)); return out; };
  const sy = window.scrollY, sx = window.scrollX;
  function box(el) {
    const rects = Array.from(el.getClientRects()).filter(r => r.width > 0 && r.height > 0);
    if (!rects.length) return null;
    return {x0: Math.min(...rects.map(r => r.left)) + sx, top: Math.min(...rects.map(r => r.top)) + sy,
            x1: Math.max(...rects.map(r => r.right)) + sx, bottom: Math.max(...rects.map(r => r.bottom)) + sy, lines: rects.length};
  }
  function visible(el) {
    for (let p = el; p && p.nodeType === 1; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    }
    return true;
  }
  function parens(el) {
    const cell = el.closest('td,th') || el.parentElement;
    const before = document.createRange(); before.setStart(cell, 0); before.setEndBefore(el);
    const after = document.createRange(); after.setStartAfter(el); after.setEnd(cell, cell.childNodes.length);
    const b = before.toString().replace(/[\s $]/g, ''), a = after.toString().replace(/[\s ]/g, '');
    let open = b.endsWith('('), close = a.startsWith(')');
    const nonEmpty = (node, step) => { let n = node && node[step]; while (n && !clean(n.textContent)) n = n[step]; return n; };
    if (!open && !b && cell.tagName.match(/^T[DH]$/)) { const p = nonEmpty(cell, 'previousElementSibling'); if (p && p.textContent.replace(/[\s $]/g, '').endsWith('(')) open = true; }
    if (!close && !a && cell.tagName.match(/^T[DH]$/)) { const n = nonEmpty(cell, 'nextElementSibling'); if (n && n.textContent.replace(/[\s ]/g, '').startsWith(')')) close = true; }
    const letters = /[A-Za-z]/.test((before.toString() + ' ' + after.toString()).replace(/\b(?:US|USD)\b/g, ''));
    return {open, close, letters};
  }
  const facts = [];
  const fractions = all.filter(el => local(el) === 'ix:nonfraction');
  fractions.forEach((el, index) => {
    const anc = ancestors(el);
    const inHeader = anc.has('ix:header');
    const b = inHeader ? null : box(el);
    const f = {
      seq: index, element_id: el.getAttribute('id'), concept: el.getAttribute('name') || '',
      context_ref: el.getAttribute('contextref'), unit_ref: el.getAttribute('unitref'),
      decimals: el.getAttribute('decimals'), scale: el.getAttribute('scale'), sign: el.getAttribute('sign'),
      format: el.getAttribute('format'), text: clean(el.textContent),
      in_header: inHeader, in_hidden: anc.has('ix:hidden'), in_exclude: anc.has('ix:exclude'),
      in_text_block: anc.has('ix:nonnumeric'), nested: anc.has('ix:nonfraction'),
      via_hidden: false, displayed: !!b && !inHeader && visible(el), box: b,
    };
    if (f.displayed) { const p = parens(el); f.printed_open = p.open; f.printed_close = p.close; f.in_text_cell = p.letters; }
    if (f.in_hidden && f.element_id) hiddenById[f.element_id] = f;
    facts.push(f);
  });
  // Values shown in the body for facts kept in ix:hidden (SEC "-sec-ix-hidden" style).
  for (const el of all) {
    const style = el.getAttribute && el.getAttribute('style');
    const m = style && style.match(/-sec-ix-hidden\s*:\s*([^;\s]+)/i);
    if (!m || !hiddenById[m[1]]) continue;
    const h = hiddenById[m[1]], b = box(el);
    if (!b || !visible(el)) continue;
    const p = parens(el);
    facts.push(Object.assign({}, h, {seq: facts.length, via_hidden: true, in_header: false, displayed: true, box: b,
      text: clean(el.textContent), printed_open: p.open, printed_close: p.close, in_text_cell: p.letters}));
  }
  const breaks = [];
  const pattern = /(?:page-)?break-(before|after)\s*:\s*(always|page)/i;
  for (const el of all) {
    const style = el.getAttribute && el.getAttribute('style');
    const m = style && style.match(pattern);
    if (!m) continue;
    const r = el.getBoundingClientRect();
    breaks.push(m[1].toLowerCase() === 'before' ? r.top + sy : r.bottom + sy);
  }
  if (breaks.length < cfg.minBreaks) {
    for (const el of all) {
      if (!/^(div|p|hr|br|table|span|section)$/i.test(el.tagName)) continue;
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      if (s.breakBefore === 'page' || s.pageBreakBefore === 'always') breaks.push(r.top + sy);
      if (s.breakAfter === 'page' || s.pageBreakAfter === 'always') breaks.push(r.bottom + sy);
    }
  }
  const dei = {};
  for (const el of all) {
    if (local(el) !== 'ix:nonnumeric') continue;
    const name = (el.getAttribute('name') || '');
    if (name.startsWith('dei:') && !(name.slice(4) in dei)) dei[name.slice(4)] = clean(el.textContent);
  }
  const title = clean(document.title);
  return {facts, contexts, units, breaks, width: Math.max(document.documentElement.scrollWidth, cfg.width),
          height: document.documentElement.scrollHeight, title, dei};
}
"""


def paginate(breaks: list[float], height: float, min_height: float) -> list[tuple[float, float]]:
    cuts = sorted({0.0, float(height), *[max(0.0, min(float(height), b)) for b in breaks]})
    merged = []
    for cut in cuts:
        if merged and cut - merged[-1] < 2:
            continue
        merged.append(cut)
    return [(a, b) for a, b in zip(merged, merged[1:]) if b - a >= min_height]


def render_issuer(browser, html_path: Path, out_dir: Path, issuer_id: str, config: dict) -> dict:
    render = config["render"]
    context = browser.new_context(viewport={"width": render["viewport_css_px"], "height": 1056},
                                  device_scale_factor=render["device_scale_factor"], java_script_enabled=True)
    page = context.new_page()
    blocked = Counter()

    def route(r):
        if r.request.url.startswith("file://") and r.request.url.endswith(html_path.name):
            return r.continue_()
        blocked[r.request.resource_type] += 1
        return r.abort()
    page.route("**/*", route)
    page.goto(html_path.as_uri(), wait_until="load", timeout=180_000)
    data = page.evaluate(EXTRACT_JS, {"minBreaks": config["heldout_rule"]["accept"]["min_page_breaks"],
                                      "width": render["viewport_css_px"]})
    pages = paginate(data["breaks"], data["height"], render["min_page_css_px"])
    if len(pages) < config["heldout_rule"]["accept"]["min_page_breaks"]:
        context.close()
        return {"rejected": "too_few_page_breaks", "page_breaks": len(pages), "raw_breaks": len(data["breaks"]),
                "height": data["height"], "facts": len(data["facts"]), "title": data["title"][:120],
                "head": html_path.read_bytes()[:160].decode("utf-8", "replace")}
    ppx = render["points_per_css_px"]
    for fact in data["facts"]:
        fact["page"] = None
        if not fact["displayed"]:
            continue
        cy = (fact["box"]["top"] + fact["box"]["bottom"]) / 2
        for n, (top, bottom) in enumerate(pages, start=1):
            if top <= cy < bottom:
                fact["page"] = n
                b = fact["box"]
                fact["box"] = {"x0": round(b["x0"] * ppx, 2), "top": round((b["top"] - top) * ppx, 2),
                               "x1": round(b["x1"] * ppx, 2), "bottom": round((b["bottom"] - top) * ppx, 2)}
                fact["box_lines"] = b["lines"]
                break
        if fact["page"] is None:
            fact["displayed"] = False
    choice = statement_pages(data["facts"], data["contexts"], config["statement_pages"]["anchors"],
                             min_facts=config["statement_pages"]["min_facts_on_page"],
                             max_pages=config["max_pages_per_issuer"])
    missing = [s for s in config["heldout_rule"]["accept"]["statements_required"] if s not in choice["by_statement"]]
    if missing:
        context.close()
        return {"rejected": "statement_pages_not_found", "missing": missing, "pages": len(pages)}
    images = []
    for n in choice["pages"]:
        top, bottom = pages[n - 1]
        png = page.screenshot(clip={"x": 0, "y": top, "width": data["width"], "height": bottom - top},
                              full_page=True, type="png")
        path = out_dir / f"{issuer_id}-p{n}.png"
        path.write_bytes(png)
        from PIL import Image
        with Image.open(BytesIO(png)) as image:
            size = image.size
        images.append({"page": n, "png": path.name, "png_sha256": hashlib.sha256(png).hexdigest(),
                       "image_width": size[0], "image_height": size[1],
                       "width": round(data["width"] * ppx, 2), "height": round((bottom - top) * ppx, 2),
                       "statements": sorted(s for s, p in choice["by_statement"].items() if p == n)})
    context.close()
    facts = data["facts"]
    return {"title": data["title"], "dei": data["dei"], "pages_total": len(pages), "facts_total": len(facts),
            "facts_in_header": sum(f["in_header"] for f in facts), "facts_in_hidden": sum(f["in_hidden"] and not f["via_hidden"] for f in facts),
            "facts_displayed": sum(bool(f["displayed"]) for f in facts),
            "blocked_requests": dict(blocked), "statement_pages": choice, "images": images,
            "contexts": data["contexts"], "units": data["units"],
            "facts": [f for f in facts if f["displayed"] and f["page"] in choice["pages"]],
            "doc_facts_by_key": Counter((f["concept"], f["context_ref"], f["unit_ref"]) for f in facts if not f["via_hidden"])}


# ---------------------------------------------------------------------------
# OCR: the repository's pinned RapidOCR profile, image only
# ---------------------------------------------------------------------------

def ocr_engine(cache_dir: Path):
    import rapidocr
    from rapidocr import RapidOCR, EngineType, ModelType, OCRVersion
    profile = json.loads((ROOT / "configs/document-ai.json").read_text(encoding="utf-8"))
    params = {"EngineConfig.onnxruntime.intra_op_num_threads": profile["intra_op_num_threads"],
              "EngineConfig.onnxruntime.inter_op_num_threads": profile["inter_op_num_threads"],
              "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
              "Global.text_score": 0.0, "Global.use_cls": False, "Det.limit_side_len": profile["det_limit_side_len"]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    weights = {}
    for model in profile["models"]:
        path = Path(rapidocr.__file__).parent / "models" / model["filename"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            path = cache_dir / model["filename"]
            if not path.is_file():
                with urlopen(Request(model["source_url"], headers={"User-Agent": "CreditLens-ocr-weights/1"}), timeout=120) as r:
                    path.write_bytes(r.read(200_000_000))
        if hashlib.sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            raise RuntimeError("OCR weight does not match the pinned profile: " + model["filename"])
        weights[model["stage"]] = {"filename": model["filename"], "sha256": model["sha256"]}
        for key, val in {"model_path": str(path), "engine_type": EngineType.ONNXRUNTIME,
                         "ocr_version": OCRVersion.PPOCRV6, "model_type": ModelType.SMALL}.items():
            params[model["stage"] + "." + key] = val
    return RapidOCR(params=params), profile, weights


def ocr_page(engine, png_path: Path, width: float, height: float) -> tuple[list[dict], float]:
    from PIL import Image
    with Image.open(png_path) as image:
        iw, ih = image.size
    started = time.perf_counter()
    result = engine(str(png_path))  # the full, unannotated page image only
    elapsed = time.perf_counter() - started
    spans = []
    if result.boxes is not None:
        for text, score, quad in zip(result.txts, result.scores, result.boxes):
            spans.append({"text": text, "confidence": round(float(score), 6), "bbox": {
                "x0": round(max(0.0, float(quad[:, 0].min()) * width / iw), 2),
                "top": round(max(0.0, float(quad[:, 1].min()) * height / ih), 2),
                "x1": round(min(width, float(quad[:, 0].max()) * width / iw), 2),
                "bottom": round(min(height, float(quad[:, 1].max()) * height / ih), 2)}})
    return spans, elapsed


# ---------------------------------------------------------------------------
# Scoring and report
# ---------------------------------------------------------------------------

def score_issuer(issuer: dict) -> list[dict]:
    records = []
    occurrences = Counter((f["concept"], f["context_ref"], f["unit_ref"]) for f in issuer["facts"])
    for page in issuer["images"]:
        reading = PageReading(page["ocr"])
        for fact in [f for f in issuer["facts"] if f["page"] == page["page"]]:
            key = key_entry(fact, issuer["contexts"], issuer["units"])
            result = score_fact(fact, key, reading)
            ident = (fact["concept"], fact["context_ref"], fact["unit_ref"])
            records.append({
                "issuer": issuer["id"], "page": page["page"], "fact": fact["seq"], "statement": "+".join(page["statements"]),
                "role": issuer["role"], "key": key, **result,
                "printed_negative": key["printed_negative"], "sign_differs": key["printed_negative"] != key["xbrl_negative"],
                "dash_zero": key["magnitude"] == "0" and not re.search(r"[0-9]", fact.get("text", "")),
                "in_text_cell": bool(fact.get("in_text_cell")), "in_text_block": bool(fact.get("in_text_block")),
                "in_exclude": bool(fact.get("in_exclude")), "nested": bool(fact.get("nested")), "via_hidden": bool(fact.get("via_hidden")),
                "dimensional": bool(key["dimensions"]), "repeated_on_pages": occurrences[ident] > 1,
            })
    return records


def build_summary(records: list[dict]) -> dict:
    return {"all": summarize(records), "by_issuer": slice_summaries(records, "issuer"),
            "by_statement": slice_summaries(records, "statement"), "by_role": slice_summaries(records, "role"),
            "slices": {name: slice_summaries(records, name) for name in
                       ("printed_negative", "sign_differs", "dash_zero", "in_text_cell", "dimensional", "repeated_on_pages", "via_hidden")}}


def run(args) -> int:
    config = load_config()
    out = Path(args.out).resolve()
    (out / "pages").mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(config["user_agent"], config["min_request_interval_seconds"])
    rule = config["heldout_rule"]
    if args.mode == "heldout":
        candidates, audit = heldout_candidates(fetcher, config)
        take = rule["take"]
    else:
        candidates, audit = explicit_candidates(args.filings), {"rule_id": None, "explicit_filings": args.filings}
        take = len(candidates)
    from playwright.sync_api import sync_playwright
    issuers, rejected = [], []

    class Rejections(list):
        def append(self, item):
            print(json.dumps({"rejected": item}, ensure_ascii=False, default=str)[:1500], flush=True)
            super().append(item)
    rejected = Rejections()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for examined, candidate in enumerate(candidates, start=1):
            if len(issuers) >= take or examined > rule["max_candidates"]:
                break
            cik = candidate["cik"]
            urls = filing_urls(cik, candidate["accession"], candidate["document"])
            entry = {"cik": cik, "name": candidate.get("name"), "accession": candidate["accession"], "examined": examined}
            try:
                raw, capture = fetcher.archived(urls["document_url"], max_bytes=rule["accept"]["max_document_bytes"])
            except LookupError as error:
                rejected.append(dict(entry, reasons=[str(error)]))
                continue
            except RuntimeError as error:
                rejected.append(dict(entry, reasons=["archive_fetch_failed: " + str(error)[:200]]))
                continue
            issuer_id = re.sub(r"[^a-z0-9]+", "-", candidate["document"].split("-")[0].lower()).strip("-")[:20] or str(cik)
            if any(i["id"] == issuer_id for i in issuers):
                issuer_id = f"{issuer_id}-{cik}"
            html_path = out / "html" / f"{issuer_id}.htm"
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_bytes(raw)
            rendered = render_issuer(browser, html_path, out / "pages", issuer_id, config)
            if "rejected" in rendered:
                rejected.append(dict(entry, reasons=[rendered["rejected"]], detail={k: v for k, v in rendered.items() if k != "rejected"}))
                continue
            dei = rendered.pop("dei")
            if dei.get("EntityCentralIndexKey", "").lstrip("0") != str(cik) or not dei.get("DocumentType", "").startswith("10-K"):
                rejected.append(dict(entry, reasons=["archived_copy_not_this_filing"], detail=dei))
                continue
            issuers.append({
                "id": issuer_id, "cik": cik, "name": dei.get("EntityRegistrantName") or candidate.get("name"),
                "sic": candidate.get("sic"), "role": args.mode, "examined_order": examined,
                "dei": dei, "filer_status": candidate.get("afs"), "fiscal_year_end": candidate.get("fye"),
                "filing": {"form": dei.get("DocumentType") or candidate["form"], "accession": candidate["accession"],
                           "filed": candidate.get("filed"), "report_date": dei.get("DocumentPeriodEndDate") or candidate.get("period"),
                           **urls, "document_sha256": capture["sha256"], "document_bytes": capture["bytes"],
                           "retrieved_at": datetime.now(timezone.utc).isoformat(), "via": "Internet Archive capture",
                           "capture_timestamp": capture["capture_timestamp"], "archive_url": capture["archive_url"],
                           "capture_sha1_base32": capture["capture_sha1_base32"]},
                **{k: v for k, v in rendered.items() if k != "doc_facts_by_key"},
                "repeated_in_document": sum(1 for f in rendered["facts"]
                                            if rendered["doc_facts_by_key"][(f["concept"], f["context_ref"], f["unit_ref"])] > 1),
            })
            print(json.dumps({"accepted": issuer_id, "name": issuers[-1]["name"], "pages": [i["page"] for i in rendered["images"]],
                              "facts": len(rendered["facts"])}), flush=True)
        browser.close()
    if not issuers:
        raise SystemExit("No issuer accepted; see rejected list")
    engine, profile, weights = ocr_engine(Path(args.model_cache))
    for issuer in issuers:
        for image in issuer["images"]:
            spans, elapsed = ocr_page(engine, out / "pages" / image["png"], image["width"], image["height"])
            image.update(ocr=spans, ocr_seconds=round(elapsed, 3), ocr_lines=len(spans))
            print(json.dumps({"ocr": image["png"], "lines": len(spans), "seconds": round(elapsed, 2)}), flush=True)
    records = [r for issuer in issuers for r in score_issuer(issuer)]
    from importlib.metadata import version
    report = {
        "schema_version": 1, "benchmark_version": config["benchmark_version"], "score_rule_version": SCORE_RULE_VERSION,
        "mode": args.mode, "label": args.label, "scope": SCOPE,
        "run": {"commit": os.getenv("GITHUB_SHA"), "run_id": os.getenv("GITHUB_RUN_ID"),
                "run_url": (f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}/actions/runs/{os.getenv('GITHUB_RUN_ID')}"
                            if os.getenv("GITHUB_RUN_ID") else None),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "environment": {"python": platform.python_version(), "os": platform.platform(),
                                "packages": {n: version(n) for n in ("rapidocr", "onnxruntime", "playwright", "Pillow", "numpy")}},
                "config_sha256": canonical_digest(config), "ocr_profile": profile["profile_version"], "ocr_weights": weights},
        "selection": {"audit": audit, "rejected": rejected, "examined": len(rejected) + len(issuers),
                      "requests": fetcher.log},
        "outcome_labels": OUTCOME_LABELS, "abstain_reasons": ABSTAIN_REASONS,
        "issuers": issuers, "records": records, "summary": build_summary(records),
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    publish(report, out / "pages", out / "site", config)
    (out / "summary.md").write_text(markdown_summary(report), encoding="utf-8")
    print(json.dumps({"summary": report["summary"]["all"]}, ensure_ascii=False), flush=True)
    return 0


# ---------------------------------------------------------------------------
# Publishable browser assets: compressed page images with a source strip
# ---------------------------------------------------------------------------

def _strip_lines(issuer: dict, image: dict) -> list[str]:
    filing = issuer["filing"]
    return [f"{issuer['name']} · Form {filing['form']} filed {filing['filed']} · accession {filing['accession']} · rendered page {image['page']}",
            f"Source: {filing['document_url']}",
            "Chromium render of the Inline XBRL HTML for the CreditLens XBRL grounding benchmark; not the issuer's PDF, not a scan."]


def publish(report: dict, pages_dir: Path, site_dir: Path, config: dict) -> dict:
    from PIL import Image, ImageDraw, ImageFont
    site_dir.mkdir(parents=True, exist_ok=True)
    settings = config["publish"]
    index = {"schema_version": 1, "benchmark_version": report["benchmark_version"], "score_rule_version": report["score_rule_version"],
             "mode": report["mode"], "label": report["label"], "scope": report["scope"], "run": report["run"],
             "summary": report["summary"]["all"], "by_issuer": report["summary"]["by_issuer"],
             "abstain_reasons": report["abstain_reasons"], "issuers": []}
    total = 0
    for issuer in report["issuers"]:
        facts_out = []
        pages = []
        for image in issuer["images"]:
            with Image.open(pages_dir / image["png"]) as source:
                page = source.convert("L")
                strip_h = settings["strip_px"]
                canvas = Image.new("L", (page.width, page.height + strip_h), 255)
                canvas.paste(page, (0, 0))
                draw = ImageDraw.Draw(canvas)
                draw.line([(0, page.height), (page.width, page.height)], fill=120, width=2)
                font = ImageFont.load_default(size=settings["strip_font_px"])
                for i, line in enumerate(_strip_lines(issuer, image)):
                    draw.text((16, page.height + 10 + i * (settings["strip_font_px"] + 6)), line, fill=40, font=font)
                buffer = BytesIO()
                canvas.save(buffer, format="WEBP", quality=settings["quality"], method=6)
            name = f"{issuer['id']}-p{image['page']}.webp"
            (site_dir / name).write_bytes(buffer.getvalue())
            total += len(buffer.getvalue())
            pages.append({"page": image["page"], "statements": image["statements"], "image": name,
                          "sha256": hashlib.sha256(buffer.getvalue()).hexdigest(), "bytes": len(buffer.getvalue()),
                          "width": image["width"], "height": image["height"], "pixel_width": canvas.width,
                          "pixel_height": canvas.height, "content_pixel_height": page.height, "ocr_lines": image["ocr_lines"]})
        by_fact = {(r["page"], r["fact"]): r for r in report["records"] if r["issuer"] == issuer["id"]}
        for fact in issuer["facts"]:
            r = by_fact[(fact["page"], fact["seq"])]
            image = next(i for i in issuer["images"] if i["page"] == fact["page"])
            span = image["ocr"][r["read"]["span"]] if r.get("read") else None
            first_hit = image["ocr"][r["hits"][0]] if r["hits"] else None
            facts_out.append({
                "page": fact["page"], "id": fact["seq"], "concept": fact["concept"], "context": fact["context_ref"],
                "period": r["key"]["period"], "dims": r["key"]["dimensions"], "unit": r["key"]["unit"],
                "decimals": fact.get("decimals"), "scale": fact.get("scale"), "sign": fact.get("sign"), "format": fact.get("format"),
                "text": fact.get("text"), "printed_negative": r["key"]["printed_negative"], "box": fact["box"],
                "year": r["key"]["year"], "printed_value": r["key"]["printed_value"], "xbrl_value": r["key"]["xbrl_value"],
                "outcome": r["outcome"], "reason": r["reason"], "checks": r["checks"],
                "ocr": None if not (span or first_hit) else {"text": (span or first_hit)["text"], "confidence": (span or first_hit)["confidence"], "box": (span or first_hit)["bbox"], "hits": len(r["hits"])},
                "read": None if not r.get("read") else {k: r["read"][k] for k in ("magnitude", "negative", "scale", "year")} | {
                    "unit_text": image["ocr"][r["read"]["unit_span"]]["text"] if r["read"]["unit_span"] is not None else None,
                    "column_text": image["ocr"][r["read"]["column_span"]]["text"] if r["read"].get("column_span") is not None else None},
                "flags": [k for k in ("in_text_cell", "in_text_block", "nested", "via_hidden", "repeated_on_pages", "sign_differs", "dash_zero") if r.get(k)],
            })
        issuer_file = f"{issuer['id']}.json"
        payload = {"id": issuer["id"], "facts": facts_out}
        (site_dir / issuer_file).write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        index["issuers"].append({"id": issuer["id"], "name": issuer["name"], "cik": issuer["cik"], "role": issuer["role"],
                                 "sic_description": issuer.get("sic_description"), "filing": {k: issuer["filing"][k] for k in
                                 ("form", "accession", "filed", "report_date", "document_url", "index_url", "document_sha256", "retrieved_at")},
                                 "pages": pages, "facts_file": issuer_file,
                                 "facts_file_sha256": hashlib.sha256((site_dir / issuer_file).read_bytes()).hexdigest()})
    if total > settings["max_total_bytes"]:
        raise RuntimeError(f"Published images total {total} bytes, above the configured cap")
    index["image_bytes"] = total
    (site_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return index


def markdown_summary(report: dict) -> str:
    s = report["summary"]["all"]
    lines = [f"## XBRL grounding benchmark · {report['mode']} · {report['label']}", "",
             f"Commit `{report['run']['commit']}` · {report['scope']}", "",
             "| issuer | name | pages | facts | match | conflict | abstain | box recall | exact value (of all) | period (of all) |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for issuer in report["issuers"]:
        t = report["summary"]["by_issuer"][issuer["id"]]
        lines.append(f"| {issuer['id']} | {issuer['name']} | {','.join(str(i['page']) for i in issuer['images'])} | {t['facts_scored']} | "
                     f"{t['outcomes']['match']} | {t['outcomes']['conflict']} | {t['outcomes']['abstain']} | {t['box_recall']['rate']} | "
                     f"{t['exact_value']['rate_of_all']} | {t['period_column']['rate_of_all']} |")
    lines += ["", f"All: {json.dumps(s, ensure_ascii=False)}", "",
              f"Rejected candidates: {len(report['selection']['rejected'])}; examined {report['selection']['examined']}"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Verification of a saved report (CI): no OCR, no network
# ---------------------------------------------------------------------------

def verify_saved(report_path: Path, site_dir: Path | None) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    rescored = [r for issuer in report["issuers"] for r in score_issuer(issuer)]
    if rescored != report["records"]:
        mismatch = next(i for i, (a, b) in enumerate(zip(rescored, report["records"])) if a != b) if len(rescored) == len(report["records"]) else "length"
        raise SystemExit(f"Saved records differ from a re-score of the saved key and OCR (first difference: {mismatch})")
    if build_summary(rescored) != report["summary"]:
        raise SystemExit("Saved summary differs from the re-scored records")
    checked_images = 0
    if site_dir is not None:
        index = json.loads((site_dir / "index.json").read_text(encoding="utf-8"))
        if index["summary"] != report["summary"]["all"] or index["run"] != report["run"]:
            raise SystemExit("Published index does not match the saved report")
        saved = {i["id"]: i for i in report["issuers"]}
        for issuer in index["issuers"]:
            source = saved[issuer["id"]]
            if issuer["filing"]["accession"] != source["filing"]["accession"] or not issuer["filing"]["document_url"].startswith("https://www.sec.gov/Archives/edgar/data/"):
                raise SystemExit("Published filing provenance differs for " + issuer["id"])
            facts_raw = (site_dir / issuer["facts_file"]).read_bytes()
            if hashlib.sha256(facts_raw).hexdigest() != issuer["facts_file_sha256"]:
                raise SystemExit("Published facts file hash mismatch for " + issuer["id"])
            facts = json.loads(facts_raw)["facts"]
            records = {(r["page"], r["fact"]): r for r in report["records"] if r["issuer"] == issuer["id"]}
            if len(facts) != len(records) or any(records[(f["page"], f["id"])]["outcome"] != f["outcome"]
                                                 or records[(f["page"], f["id"])]["reason"] != f["reason"] for f in facts):
                raise SystemExit("Published fact outcomes differ from the saved report for " + issuer["id"])
            if len(issuer["pages"]) > load_config()["max_pages_per_issuer"]:
                raise SystemExit("Too many published pages for " + issuer["id"])
            for page in issuer["pages"]:
                raw = (site_dir / page["image"]).read_bytes()
                if hashlib.sha256(raw).hexdigest() != page["sha256"] or not raw.startswith(b"RIFF"):
                    raise SystemExit("Published page image hash or format mismatch: " + page["image"])
                checked_images += 1
    result = {"status": "ok", "records": len(rescored), "images": checked_images, "summary": report["summary"]["all"]}
    print(json.dumps(result, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="Actions only: select, fetch, render, OCR, score, publish")
    r.add_argument("--mode", choices=["heldout", "dev", "added"], required=True)
    r.add_argument("--filings", default="", help="dev/added: cik:accession:document, comma-separated")
    r.add_argument("--label", default="manual")
    r.add_argument("--out", default=str(ROOT / ".run/xbrl-grounding"))
    r.add_argument("--model-cache", default=str(Path.home() / ".cache/creditlens-ocr"))
    v = sub.add_parser("verify-saved", help="Re-score a saved report; check published assets")
    v.add_argument("report", type=Path)
    v.add_argument("--site", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "run":
        if not os.getenv("GITHUB_ACTIONS"):
            raise SystemExit("The benchmark run fetches SEC filings, renders pages and runs OCR; it runs on GitHub Actions only.")
        return run(args)
    verify_saved(args.report, args.site)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
