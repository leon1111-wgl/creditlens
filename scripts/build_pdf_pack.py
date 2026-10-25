"""Reproduce a pinned official PDF example, with page/cell evidence and cross-checks.

Install requirements-pdf.lock first. No GPU, OCR, hosted model or paid API is used.
The raw PDF and rendered pages stay local; only minimal facts/locators are public.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import CasePack, digest
from creditlens.document import PARSER_VERSION, EvidenceError, pdfium_tokens, extract_table

URL = "https://microsoft.gcs-web.com/static-files/9640e86d-a99f-4502-934d-1d3c883fa786"
SHA256 = "1d13a76168650bb5efb42f29c6da81aa673e44217d082357ec0815afc70abf2e"
FILING = "https://microsoft.gcs-web.com/node/33951"
TABLES = [
    {"page": 50, "title": "INCOME STATEMENTS", "unit_header": "(In millions, except per share amounts)",
     "period_header": "Year Ended June 30,", "years": [2025, 2024, 2023],
     "metrics": {"revenue": "Total revenue"}},
    {"page": 52, "title": "BALANCE SHEETS", "unit_header": "(In millions)",
     "period_header": "June 30,", "years": [2025, 2024],
     "metrics": {"liquid_assets": "Total cash, cash equivalents, and short-term investments",
                 "short_debt": "Short-term debt", "current_debt": "Current portion of long-term debt",
                 "long_debt": "Long-term debt", "current_assets": "Total current assets",
                 "current_liabilities": "Total current liabilities"}},
    {"page": 53, "title": "CASH FLOWS STATEMENTS", "unit_header": "(In millions)",
     "period_header": "Year Ended June 30,", "years": [2025, 2024, 2023],
     "metrics": {"cfo": "Net cash from operations", "capex": "Additions to property and equipment", "net_income": "Net income"}},
]
LABELS = dict(revenue="营业收入", liquid_assets="现金、现金等价物与短期投资", short_debt="短期债务",
              current_debt="一年内到期长期债务", long_debt="长期债务", current_assets="流动资产",
              current_liabilities="流动负债", cfo="经营活动现金流", capex="固定资产购建现金流出", net_income="净利润")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def verified_pdf(path, download=False):
    if not path.exists():
        if not download:
            raise EvidenceError("PDF missing; rerun with --download to retrieve the registered official source")
        with urllib.request.urlopen(URL, timeout=40) as response:
            raw = response.read(30_000_001)
            final_url = response.url
        if len(raw) > 30_000_000 or not raw.startswith(b"%PDF-") or hashlib.sha256(raw).hexdigest() != SHA256:
            raise EvidenceError("Downloaded source does not match the registered PDF; inspect before updating the profile")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        write_json(path.with_suffix(".download.json"), {"retrieved_at": datetime.now(timezone.utc).isoformat(),
                   "requested_url": URL, "resolved_url": final_url, "sha256": SHA256})
    if path.stat().st_size > 30_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != SHA256:
        raise EvidenceError("Local PDF changed; refusing stale page/label assumptions")
    metadata = path.with_suffix(".download.json")
    return json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {"retrieved_at": None}


def build(path, render_dir=None):
    import pypdfium2 as pdfium
    records = []
    with pdfium.PdfDocument(path) as pdf:
        page_count = len(pdf)
        if page_count != 158:
            raise EvidenceError("Unexpected document page count")
        for spec in TABLES:
            page = pdf[spec["page"]-1]
            try:
                if page.get_rotation() != 0:
                    raise EvidenceError("Rotated pages require another adapter")
                extracted = extract_table(pdfium_tokens(page), width=page.get_width(), height=page.get_height(), **spec)
                records.extend(r for r in extracted if r["year"] in (2025, 2024))
                if render_dir is not None:
                    from PIL import ImageDraw
                    render_dir.mkdir(parents=True, exist_ok=True)
                    bitmap = page.render(scale=1.8)
                    try:
                        preview = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                    draw = ImageDraw.Draw(preview)
                    for item in extracted:
                        if item["year"] not in (2025, 2024):
                            continue
                        loc = item["pdf_location"]
                        for key, color in [("amount_bbox", "#167c52"), ("year_bbox", "#b45b00"),
                                           ("row_label_bbox", "#275eb4"), ("unit_bbox", "#884ba0")]:
                            b = loc[key]
                            draw.rectangle(tuple(b[k]*1.8 for k in ("x0", "top", "x1", "bottom")), outline=color, width=2)
                    preview.save(render_dir / f"msft-page-{spec['page']}-evidence.png")
            finally:
                page.close()
    facts = []
    for r in records:
        metric, year = r["metric"], r["year"]
        facts.append({"id": f"{metric}-{year}", "metric": metric, "label": LABELS[metric],
                      "value": r["value"], "raw_value": r["raw_value"], "unit": "USD_million",
                      "period_start": f"{year-1}-07-01" if metric in {"cfo", "capex", "net_income", "revenue"} else None,
                      "period_end": f"{year}-06-30", "source_id": "msft-fy25-10k-pdf",
                      "locator": f"PDF p.{r['pdf_location']['page']} / {r['pdf_location']['row_label']} / FY{year}",
                      "transformation": "absolute_cash_outflow" if metric == "capex" else "displayed_magnitude",
                      "pdf_location": r["pdf_location"]})
    pack = CasePack.model_validate({"schema_version": 1, "entity_id": "MSFT", "company": "Microsoft Corporation",
             "title": "FY2025 PDF 原页证据复核", "sources": [{"id": "msft-fy25-10k-pdf",
             "title": "Microsoft FY2025 Form 10-K / official PDF", "url": URL,
             "published_at": "2025-07-30", "sha256": SHA256}], "facts": facts})
    return pack, page_count, str(pdfium.PYPDFIUM_INFO), str(pdfium.PDFIUM_INFO)


def compare(pack, reference):
    expected = {f.id: f for f in reference.facts}
    checks = []
    for f in pack.facts:
        ref = expected.get(f.id)
        matched = ref is not None and all(getattr(f, key) == getattr(ref, key)
                    for key in ("metric", "value", "unit", "period_start", "period_end", "transformation"))
        checks.append({"fact_id": f.id, "pdf_value": str(f.value), "html_value": str(ref.value) if ref else None,
                       "page": f.pdf_location.page, "matched": matched})
    return {"compared": len(checks), "matched": sum(c["matched"] for c in checks),
            "missing_pdf_fact_ids": sorted(set(expected)-{f.id for f in pack.facts}), "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=ROOT/"data/raw/msft-fy2025-10k.pdf")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--render-dir", type=Path)
    args = parser.parse_args()
    retrieved = verified_pdf(args.pdf, args.download)
    pack, pages, wrapper_version, engine_version = build(args.pdf, args.render_dir)
    # Reference values are loaded only AFTER PDF extraction, never used to locate cells.
    reference = CasePack.model_validate_json((ROOT/"data/examples/msft-fy2025.json").read_text(encoding="utf-8"))
    report = compare(pack, reference)
    report.update({"generated_at": datetime.now(timezone.utc).isoformat(), "parser_version": PARSER_VERSION,
                   "pdf_sha256": SHA256, "html_pack_sha256": digest(reference.model_dump(mode="json")),
                   "scope": "Same issuer and reporting period across two official formats; not independent truth or general PDF accuracy."})
    write_json(ROOT/"artifacts/pdf-crosscheck.json", report)
    if report["matched"] != 20 or report["compared"] != 20 or report["missing_pdf_fact_ids"]:
        raise EvidenceError("PDF/HTML mismatch: inspect artifacts/pdf-crosscheck.json; example pack was not replaced")
    payload = pack.model_dump(mode="json")
    write_json(ROOT/"data/examples/msft-fy2025-pdf.json", payload)
    write_json(ROOT/"data/examples/msft-pdf-provenance.json", {
        "source_url": URL, "source_sha256": SHA256, "filing_metadata_url": FILING,
        "filing_date": "2025-07-30", "retrieved_at": retrieved["retrieved_at"],
        "document_pages": pages, "selected_pages": [50, 52, 53], "facts": len(pack.facts),
        "pack_sha256": digest(payload), "profile_sha256": digest(TABLES), "profile": TABLES,
        "parser_version": PARSER_VERSION, "pypdfium2_version": wrapper_version, "pdfium_version": engine_version,
        "currency_basis": "USD from the issuer-specific financial statement convention; (In millions) supplies the magnitude only.",
        "license": "Issuer document; public access does not grant a general redistribution license. Raw PDF and page renders excluded.",
        "limitations": ["Pinned digital PDF with three preselected pages and ten declared row labels; no OCR, chart interpretation or generic parser claim.",
                        "Filing date identifies disclosure, not proof of this generated PDF's historical availability.",
                        "FY2024 comparisons are disclosed in the FY2025 filing, not a FY2024 information snapshot.",
                        "Coordinates describe the text layer; visual review is needed to confirm visible content.",
                        "Matching official HTML and PDF does not authenticate a document or certify the financial statements."]})
    print(json.dumps({k: report[k] for k in ("compared", "matched", "missing_pdf_fact_ids", "scope")}))


if __name__ == "__main__":
    main()
