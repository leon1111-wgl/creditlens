"""Credit snapshot (v0.36.0): gallery selection, archive fetches and the pre-registered checks.

Runs on GitHub Actions only (.github/workflows/xbrl-grounding.yml, modes snapshot-*). No request goes
to SEC: SEC answers this project's declared user agent with 403 (probe run 43477784386), so filings
and SEC data sets come from existing Internet Archive captures, each checked against its CDX digest.
The snapshot itself is computed by the page's own code in Chromium (scripts/credit_snapshot_actions.mjs).

  gallery          select the committed gallery rule's filers, fetch and gzip their filings
  heldout-select   select check (b)'s held-out filings and fetch them (no snapshot is computed)
  fetch            fetch explicit filings (cik:accession:document, comma-separated) for a probe
  merge            add the Chromium-computed snapshot summaries to a gallery index
  fsds-check       check (a): program facts against SEC Financial Statement Data Set num.txt rows
  heldout-compare  check (b): program metrics against the independent model's committed answers
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
import gzip
import hashlib
import html
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from creditlens.xbrl_grounding import candidate_order  # noqa: E402
from xbrl_grounding import Fetcher, filing_urls, primary_document  # noqa: E402

CONFIG = ROOT / "configs/credit-snapshot.json"
GALLERY_DIR = ROOT / "creditlens/snapshot"
FORMS = {"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT"}


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def config_sha256() -> str:
    return hashlib.sha256(CONFIG.read_bytes()).hexdigest()


def run_info() -> dict:
    return {"run": os.environ.get("GITHUB_RUN_ID", "local"), "commit": os.environ.get("GITHUB_SHA", "local"),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}


# ---------------------------------------------------------------------------
# Population and filings
# ---------------------------------------------------------------------------

def read_dataset(fetcher: Fetcher, url: str) -> tuple[zipfile.ZipFile, dict]:
    raw, meta = fetcher.archived(url, max_bytes=400_000_000)
    return zipfile.ZipFile(BytesIO(raw)), meta


def population(archive: zipfile.ZipFile, want: dict) -> dict[int, dict]:
    lines = archive.read("sub.txt").decode("utf-8", "replace").splitlines()
    header = lines[0].split("\t")
    eligible: dict[int, dict] = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        row = dict(zip(header, line.split("\t")))
        if all(row.get(k) == v for k, v in want.items()) and primary_document(row.get("instance", "")):
            cik = int(row["cik"])
            if cik not in eligible or row["filed"] < eligible[cik]["filed"]:
                eligible[cik] = row
    return eligible


def stratum_of(sic: str, strata: list[dict]) -> dict | None:
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    return next((s for s in strata if s["sic"][0] <= code <= s["sic"][1]), None)


_DEI = re.compile(r'<ix:nonNumeric\b([^>]*)>(.*?)</ix:nonNumeric>', re.I | re.S)


def dei_values(text: str) -> dict:
    out: dict[str, str] = {}
    for attrs, body in _DEI.findall(text):
        name = re.search(r'\bname="dei:([A-Za-z]+)"', attrs)
        if name and name.group(1) not in out:
            out[name.group(1)] = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", body))).strip()
    return out


def gzip_bytes(raw: bytes) -> bytes:
    buffer = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0, compresslevel=9) as handle:
        handle.write(raw)
    return buffer.getvalue()


def fetch_filing(fetcher: Fetcher, candidate: dict, max_bytes: int, out: Path) -> tuple[dict | None, str | None]:
    """Archive capture of the primary document, checked; written gzipped. Returns (entry, reject_reason)."""
    cik, accession, document = candidate["cik"], candidate["accession"], candidate["document"]
    urls = filing_urls(cik, accession, document)
    try:
        raw, capture = fetcher.archived(urls["document_url"], max_bytes=max_bytes)
    except LookupError as error:
        return None, str(error)
    except RuntimeError as error:
        text = str(error)
        return None, "too_large" if "larger than" in text else "archive_fetch_failed: " + text[:160]
    dei = dei_values(raw.decode("utf-8", "replace"))
    form = re.sub(r"\s+", "", dei.get("DocumentType", "")).upper()
    if dei.get("EntityCentralIndexKey", "").lstrip("0") != str(cik) or form != candidate["form"].upper():
        return None, "archived_copy_not_this_filing"
    entry_id = f"{cik}-{accession}"
    packed = gzip_bytes(raw)
    path = out / "filings" / f"{entry_id}.htm.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(packed)
    return {
        "id": entry_id, "cik": cik, "name": dei.get("EntityRegistrantName") or candidate.get("name"),
        "ticker": dei.get("TradingSymbol") or None, "sic": candidate.get("sic"), "form": form,
        "accession": accession, "document": document, "period": candidate.get("period"), "filed": candidate.get("filed"),
        **urls, "archive_url": capture["archive_url"], "capture_timestamp": capture["capture_timestamp"],
        "capture_sha1_base32": capture["capture_sha1_base32"], "stored_gzip": capture["stored_gzip"],
        "file": f"filings/{entry_id}.htm.gz", "bytes": len(raw), "gz_bytes": len(packed),
        "sha256": hashlib.sha256(raw).hexdigest(), "gz_sha256": hashlib.sha256(packed).hexdigest(), "snapshot": None,
    }, None


def candidate_from_row(cik: int, row: dict) -> dict:
    return {"cik": cik, "name": row["name"], "sic": row.get("sic"), "accession": row["adsh"], "document": primary_document(row["instance"]),
            "filed": row["filed"], "period": row["period"], "form": row["form"]}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_gallery(args) -> int:
    config = load_config()
    rule = config["gallery"]
    fetcher = Fetcher(config["user_agent"], config["min_request_interval_seconds"])
    out = Path(args.out)
    archive, dataset = read_dataset(fetcher, rule["population"]["dataset_url"])
    eligible = population(archive, rule["population"]["filter"])
    excluded = {int(c) for c in rule["exclude_ciks"]}
    by_stratum: dict[str, list[int]] = defaultdict(list)
    for cik, row in eligible.items():
        stratum = stratum_of(row.get("sic"), rule["strata"])
        if stratum and cik not in excluded:
            by_stratum[stratum["id"]].append(cik)
    entries, strata = [], []
    for stratum in rule["strata"]:
        order = candidate_order(by_stratum[stratum["id"]], rule["order"]["salt"])
        taken, rejected = [], []
        for examined, cik in enumerate(order[: rule["max_candidates_per_stratum"]], start=1):
            if len(taken) >= rule["take_per_stratum"]:
                break
            candidate = candidate_from_row(cik, eligible[cik])
            entry, reason = fetch_filing(fetcher, candidate, rule["accept"]["max_document_bytes"], out)
            if entry is None:
                rejected.append({"cik": cik, "name": candidate["name"], "accession": candidate["accession"], "examined": examined, "reason": reason})
                print(json.dumps({"stratum": stratum["id"], "rejected": cik, "reason": reason}), flush=True)
                continue
            entry.update(stratum=stratum["id"], stratum_label=stratum["label"], examined_order=examined)
            taken.append(entry)
            print(json.dumps({"stratum": stratum["id"], "accepted": entry["name"], "bytes": entry["bytes"], "gz": entry["gz_bytes"]}), flush=True)
        entries.extend(taken)
        strata.append({"id": stratum["id"], "label": stratum["label"], "eligible": len(by_stratum[stratum["id"]]),
                       "examined": len(taken) + len(rejected), "taken": len(taken),
                       "rejected": dict(Counter(r["reason"].split(":")[0] for r in rejected)), "rejected_list": rejected})
        index = {"schema_version": 1, "rule_id": rule["rule_id"], "definitions": config["definitions_version"], "config_sha256": config_sha256(),
                 **run_info(), "population": {"dataset": dataset, "filter": rule["population"]["filter"], "eligible": len(eligible)},
                 "source": config["source"], "complete": len(strata) == len(rule["strata"]), "strata": strata, "entries": entries}
        # Written after every stratum, so a run that times out still leaves what it accepted.
        (out / "gallery.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"entries": len(entries), "gz_mb": round(sum(e["gz_bytes"] for e in entries) / 1e6, 1)}))
    return 0


def cmd_heldout_select(args) -> int:
    config = load_config()
    rule = config["heldout_b"]
    fetcher = Fetcher(config["user_agent"], config["min_request_interval_seconds"])
    out = Path(args.out)
    gallery = json.loads(Path(args.gallery).read_text(encoding="utf-8"))
    excluded = {e["cik"] for e in gallery["entries"]} | {int(c) for c in config["dev_filings"]["ciks"]}
    chosen, audit = [], []
    for sample in rule["samples"]:
        archive, dataset = read_dataset(fetcher, sample["dataset_url"])
        eligible = population(archive, sample["filter"])
        order = candidate_order(set(eligible) - excluded - {e["cik"] for e in chosen}, rule["order"]["salt"])
        taken, rejected = [], []
        for examined, cik in enumerate(order[: rule["max_candidates"]], start=1):
            if len(taken) >= sample["take"]:
                break
            candidate = candidate_from_row(cik, eligible[cik])
            entry, reason = fetch_filing(fetcher, candidate, config["gallery"]["accept"]["max_document_bytes"], out)
            if entry is None:
                rejected.append({"cik": cik, "name": candidate["name"], "examined": examined, "reason": reason})
                continue
            entry.update(sample_form=sample["form"], examined_order=examined)
            taken.append(entry)
        chosen.extend(taken)
        audit.append({"form": sample["form"], "dataset": dataset, "eligible": len(eligible), "after_exclusion": len(order),
                      "taken": len(taken), "rejected": rejected})
    listing = {"schema_version": 1, "rule_id": rule["rule_id"], "definitions": config["definitions_version"], "config_sha256": config_sha256(),
               **run_info(), "gallery_ciks_excluded": sorted(e["cik"] for e in gallery["entries"]),
               "audit": audit, "entries": [{k: v for k, v in e.items() if k != "snapshot"} for e in chosen]}
    (out / "heldout.json").write_text(json.dumps(listing, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"heldout": [(e["name"], e["form"]) for e in chosen]}, ensure_ascii=False))
    return 0


def cmd_fetch(args) -> int:
    """Fetch named filings (any 10-K/10-Q form) or re-fetch a committed list and check its hashes."""
    config = load_config()
    fetcher = Fetcher(config["user_agent"], config["min_request_interval_seconds"])
    out = Path(args.out)
    entries, failures = [], []
    if args.listing:
        listing = json.loads(Path(args.listing).read_text(encoding="utf-8"))
        wanted = [{**e, "form": e["form"]} for e in listing["entries"]]
    else:
        wanted = []
        for item in [x.strip() for x in (args.filings or "").split(",") if x.strip()]:
            m = re.fullmatch(r"([0-9]{1,10}):([0-9]{10}-[0-9]{2}-[0-9]{6}):([A-Za-z0-9._-]+\.html?)", item)
            if not m:
                raise SystemExit("filings must look like 320193:0000320193-25-000079:aapl-20250927.htm")
            wanted.append({"cik": int(m.group(1)), "accession": m.group(2), "document": m.group(3), "form": "?"})
        if not wanted or len(wanted) > 12:
            raise SystemExit("give 1 to 12 filings")
    for w in wanted:
        candidate = {k: w.get(k) for k in ("cik", "name", "sic", "accession", "document", "filed", "period", "form")}
        if candidate["form"] == "?":
            urls = filing_urls(w["cik"], w["accession"], w["document"])
            try:
                raw, _ = fetcher.archived(urls["document_url"], max_bytes=config["gallery"]["accept"]["max_document_bytes"])
                candidate["form"] = re.sub(r"\s+", "", dei_values(raw.decode("utf-8", "replace")).get("DocumentType", "?")).upper()
            except (LookupError, RuntimeError) as error:
                failures.append({**w, "reason": str(error)[:200]})
                continue
        entry, reason = fetch_filing(fetcher, candidate, config["gallery"]["accept"]["max_document_bytes"], out)
        if entry is None:
            failures.append({**w, "reason": reason})
            continue
        if args.listing and entry["sha256"] != w["sha256"]:
            failures.append({**w, "reason": "sha256 differs from the committed listing"})
            continue
        entries.append({**entry, **{k: w[k] for k in ("sample_form", "examined_order") if k in w}})
    (out / "fetched.json").write_text(json.dumps({**run_info(), "entries": entries, "failures": failures}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"fetched": len(entries), "failures": failures}, ensure_ascii=False))
    return 0 if entries else 1


def summary_of(snapshot: dict) -> dict:
    m = snapshot.get("metrics", {})
    val = lambda k: (m.get(k) or {}).get("value")  # noqa: E731
    return {"status": snapshot["status"], "template": snapshot["template"]["kind"], "debt_to_ebitda": val("debt_to_ebitda"),
            "interest_coverage": val("interest_coverage"), "current_ratio": val("current_ratio"), "fcf_to_debt": val("fcf_to_debt"),
            "debt": val("debt"), "currency": snapshot["meta"]["currency"], "display_scale": snapshot["meta"]["display_scale"],
            "cues": [c["id"] for c in snapshot.get("cues", [])]}


def cmd_merge(args) -> int:
    index_path = Path(args.index)
    index = json.loads(index_path.read_text(encoding="utf-8"))
    computed = Path(args.computed)
    for entry in index["entries"]:
        result = json.loads((computed / f"{entry['id']}.json").read_text(encoding="utf-8"))
        if result.get("error"):
            raise SystemExit(f"{entry['id']}: {result['error']}")
        entry["snapshot"] = summary_of(result["snapshot"])
        entry["name"] = result["snapshot"]["meta"]["registrant"] or entry["name"]
        entry["ticker"] = result["snapshot"]["meta"]["ticker"] or entry.get("ticker")
    index["computed_by"] = {"engine": "creditlens/snapshot.js in Playwright Chromium 1.49.1", **run_info()}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


# ---------------------------------------------------------------------------
# Check (a): FSDS num.txt
# ---------------------------------------------------------------------------

def nearest_month_end(day: date) -> date:
    this_end = (day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    prev_end = day.replace(day=1) - timedelta(days=1)
    return this_end if (this_end - day) <= (day - prev_end) else prev_end


def measure_key(unit: str) -> str:
    parts = unit.split("/")
    return "/".join(p.split(":")[-1] for p in parts)


def fact_key(local: str, unit: str, context: dict) -> tuple | None:
    if context.get("dims"):
        return None
    if context.get("instant"):
        end, qtrs = date.fromisoformat(context["instant"]), 0
    elif context.get("start") and context.get("end"):
        start, end = date.fromisoformat(context["start"]), date.fromisoformat(context["end"])
        qtrs = round((end - start).days / 91.3125)
    else:
        return None
    return local, measure_key(unit), nearest_month_end(end).strftime("%Y%m%d"), qtrs


def q4(value: str) -> Decimal:
    return Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def compare_fsds(rows: list[dict], computed: dict, rule: str = "registered") -> dict:
    """rule "registered": the key as pre-registered. rule "corrected" (post hoc, after the first run): FSDS writes a
    per-share unit as its numerator ("USD" for USD/shares), and a nil or empty tag in the filing counts as present
    without a value (its own bucket, never as equal)."""
    keys: dict[tuple, set] = defaultdict(set)
    valueless: set = set()
    contexts, units = computed["contexts"], computed["units"]
    for f in computed["facts"]:
        unit = units.get(f["unit"], f["unit"])
        if rule == "corrected" and unit.endswith(":shares") and "/" in unit:
            unit = unit.split("/")[0]
        key = fact_key(f["local"], unit, contexts.get(f["ctx"], {}))
        if not key:
            continue
        if f["value"] is None:
            valueless.add(key)
            continue
        keys[key].add(q4(f["value"]))
    found = agree = without_value = 0
    missing, conflicts = [], []
    for r in rows:
        key = (r["tag"], r["uom"], r["ddate"], int(r["qtrs"]))
        if key not in keys:
            if rule == "corrected" and key in valueless:
                without_value += 1
                continue
            missing.append(key)
            continue
        found += 1
        if Decimal(r["value"]) in keys[key]:
            agree += 1
        else:
            conflicts.append({"key": key, "fsds": r["value"], "program": sorted(str(v) for v in keys[key])})
    return {"rows": len(rows), "found": found, "agree": agree, "without_value": without_value, "missing": missing, "conflicts": conflicts}


def cmd_fsds_check(args) -> int:
    config = load_config()
    check = config["checks"]["a_parser"]
    fetcher = Fetcher(config["user_agent"], config["min_request_interval_seconds"])
    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    computed_dir = Path(args.computed)
    archive, dataset = read_dataset(fetcher, args.dataset or config["gallery"]["population"]["dataset_url"])
    wanted = {e["accession"]: e for e in index["entries"]}
    rows: dict[str, list[dict]] = defaultdict(list)
    with archive.open("num.txt") as handle:
        header = handle.readline().decode().rstrip("\n").split("\t")
        for line in handle:
            if line[:20].decode("ascii", "replace") not in wanted:
                continue
            row = dict(zip(header, line.decode("utf-8", "replace").rstrip("\n").split("\t")))
            if row.get("segments") or row.get("coreg"):
                continue
            rows[row["adsh"]].append(row)
    per_filing, totals, corrected_totals = [], Counter(), Counter()
    for accession, entry in wanted.items():
        computed = json.loads((computed_dir / f"{entry['id']}.json").read_text(encoding="utf-8"))
        if computed.get("error"):  # the page could not read the filing: every FSDS row of it counts as not found
            computed = {"facts": [], "contexts": {}, "units": {}}
        corrected = compare_fsds(rows.get(accession, []), computed, "corrected")
        corrected_totals.update(rows=corrected["rows"], found=corrected["found"], agree=corrected["agree"], without_value=corrected["without_value"],
                                missing=len(corrected["missing"]))
        result = compare_fsds(rows.get(accession, []), computed)
        totals.update(rows=result["rows"], found=result["found"], agree=result["agree"])
        per_filing.append({"id": entry["id"], "name": entry["name"], "accession": accession, "rows": result["rows"], "found": result["found"],
                           "agree": result["agree"], "recall": result["found"] / result["rows"] if result["rows"] else None,
                           "agreement": result["agree"] / result["found"] if result["found"] else None,
                           "missing": [list(k) for k in result["missing"]], "conflicts": result["conflicts"],
                           "corrected_key": {k: corrected[k] for k in ("found", "agree", "without_value")} | {"missing": [list(k) for k in corrected["missing"]], "conflicts": corrected["conflicts"]},
                           "fsds_rows": rows.get(accession, [])})
    bar = check["pass_bar"]
    recall = totals["found"] / totals["rows"] if totals["rows"] else 0
    agreement = totals["agree"] / totals["found"] if totals["found"] else 0
    passed = (recall >= bar["recall_all_filings"] and agreement >= bar["agreement_all_filings"]
              and all(f["recall"] is not None and f["recall"] >= bar["recall_each_filing"] for f in per_filing))
    report = {"schema_version": 1, "check": "a_parser", "definitions": config["definitions_version"], "config_sha256": config_sha256(), **run_info(),
              "reference": {"dataset": dataset, "rows": "num.txt rows of each gallery accession with empty segments and coreg"},
              "totals": {**totals, "recall": recall, "agreement": agreement}, "pass_bar": bar, "passed": passed,
              "corrected_key_post_hoc": {"note": "Not pre-registered: key corrected after the first run (run 43498565370). Per-share units compared as FSDS writes them; nil or empty tags counted as present without a value.",
                                         **corrected_totals, "agreement": corrected_totals["agree"] / corrected_totals["found"] if corrected_totals["found"] else 0},
              "filings": per_filing}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check-a.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    lines = [f"## Check (a) · parser vs SEC FSDS num.txt — {'PASS' if passed else 'FAIL'}", "",
             f"Rows {totals['rows']}, found {totals['found']} ({recall:.4%}), equal {totals['agree']} ({agreement:.4%}). Bar: recall ≥ {bar['recall_all_filings']:.1%}, agreement ≥ {bar['agreement_all_filings']:.1%}, each filing recall ≥ {bar['recall_each_filing']:.0%}.", "",
             f"Post-hoc corrected key (not pre-registered): found {corrected_totals['found']}, equal {corrected_totals['agree']}, present without a value {corrected_totals['without_value']}, missing {corrected_totals['missing']}.", "",
             "| filing | rows | found | equal | missing | conflicts |", "|---|---:|---:|---:|---:|---:|"]
    for f in per_filing:
        lines.append(f"| {f['name']} | {f['rows']} | {f['found']} | {f['agree']} | {len(f['missing'])} | {len(f['conflicts'])} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# Check (b): program vs the independent model's committed answers
# ---------------------------------------------------------------------------

AMOUNTS = ["total_debt", "current_debt", "liquidity", "net_debt", "ebit", "da", "ebitda", "interest_expense", "cfo", "capex", "fcf",
           "operating_lease_liability", "finance_lease_liability", "maturity_next_12m", "maturity_years_2_5", "maturity_after_5"]
RATIOS = ["interest_coverage", "debt_to_ebitda", "net_debt_to_ebitda", "current_ratio", "fcf_to_debt"]
PROGRAM_KEYS = {"total_debt": ("inputs", "debt"), "current_debt": ("inputs", "current_debt"), "liquidity": ("inputs", "liquidity"),
                "net_debt": ("metrics", "net_debt"), "ebit": ("inputs", "ebit"), "da": ("inputs", "da"), "ebitda": ("metrics", "ebitda"),
                "interest_expense": ("inputs", "interest"), "cfo": ("inputs", "cfo"), "capex": ("inputs", "capex"), "fcf": ("metrics", "fcf"),
                "operating_lease_liability": ("inputs", "operating_leases"), "finance_lease_liability": ("inputs", "finance_leases"),
                "maturity_next_12m": ("ladder", "next12"), "maturity_years_2_5": ("ladder", "years2to5"), "maturity_after_5": ("ladder", "after5"),
                "interest_coverage": ("metrics", "interest_coverage"), "debt_to_ebitda": ("metrics", "debt_to_ebitda"),
                "net_debt_to_ebitda": ("metrics", "net_debt_to_ebitda"), "current_ratio": ("metrics", "current_ratio"), "fcf_to_debt": ("metrics", "fcf_to_debt")}


def program_value(snapshot: dict, key: str):
    where, name = PROGRAM_KEYS[key]
    if snapshot["status"] != "ok":
        return None
    if where == "ladder":
        return (snapshot.get("ladder") or {}).get(name, {}).get("value") if snapshot.get("ladder") else None
    return (snapshot.get(where, {}).get(name) or {}).get("value")


def agree(key: str, a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if key in RATIOS:
        return abs(float(a) - float(b)) <= 0.005
    return Decimal(str(a)) == Decimal(str(b))


def cmd_heldout_compare(args) -> int:
    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    computed_dir = Path(args.computed)
    rows, per_metric = [], defaultdict(Counter)
    for answer in key["filings"]:
        computed = json.loads((computed_dir / f"{answer['id']}.json").read_text(encoding="utf-8"))
        snapshot = computed["snapshot"]
        template_ok = (snapshot["template"]["kind"] == answer["template"])
        period_ok = snapshot["meta"]["period_end"] == answer.get("period_end") and snapshot["meta"].get("months") == answer.get("months")
        diffs = []
        for k in AMOUNTS + RATIOS:
            mine, theirs = program_value(snapshot, k), (answer.get("metrics") or {}).get(k)
            ok = agree(k, mine, theirs)
            per_metric[k]["agree" if ok else "differ"] += 1
            if not ok:
                diffs.append({"metric": k, "program": mine, "independent": theirs})
        rows.append({"id": answer["id"], "name": answer.get("name"), "form": answer.get("form"), "template_agrees": template_ok,
                     "period_agrees": period_ok, "differences": diffs})
    report = {"schema_version": 1, "check": "b_metrics", **run_info(), "key_sha256": hashlib.sha256(Path(args.key).read_bytes()).hexdigest(),
              "per_metric": {k: dict(v) for k, v in per_metric.items()}, "filings": rows,
              "totals": {"comparisons": sum(sum(v.values()) for v in per_metric.values()), "agree": sum(v["agree"] for v in per_metric.values())}}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check-b.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    t = report["totals"]
    lines = [f"## Check (b) · program vs independent model — {t['agree']}/{t['comparisons']} values agree", "",
             "| filing | form | template | period | differences |", "|---|---|---|---|---:|"]
    for r in rows:
        lines.append(f"| {r['name']} | {r['form']} | {'=' if r['template_agrees'] else '≠'} | {'=' if r['period_agrees'] else '≠'} | {len(r['differences'])} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("gallery"); p.add_argument("--out", required=True); p.set_defaults(func=cmd_gallery)
    p = sub.add_parser("heldout-select"); p.add_argument("--out", required=True); p.add_argument("--gallery", default=str(GALLERY_DIR / "gallery.json")); p.set_defaults(func=cmd_heldout_select)
    p = sub.add_parser("fetch"); p.add_argument("--out", required=True); p.add_argument("--filings"); p.add_argument("--listing"); p.set_defaults(func=cmd_fetch)
    p = sub.add_parser("merge"); p.add_argument("--index", required=True); p.add_argument("--computed", required=True); p.set_defaults(func=cmd_merge)
    p = sub.add_parser("fsds-check"); p.add_argument("--index", required=True); p.add_argument("--computed", required=True); p.add_argument("--out", required=True); p.add_argument("--dataset"); p.set_defaults(func=cmd_fsds_check)
    p = sub.add_parser("heldout-compare"); p.add_argument("--key", required=True); p.add_argument("--computed", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_heldout_compare)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
