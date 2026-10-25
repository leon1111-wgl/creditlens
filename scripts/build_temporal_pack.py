"""Strict Microsoft FY2025 adapter: reported quarters plus cumulative operands."""
from datetime import date, datetime, timezone
from html import unescape
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import displayed_amount
from creditlens.temporal import TemporalPack, month_start
from scripts.build_company_pack import cells, METRICS

ADAPTER_VERSION = "msft-fy2025-quarterly-1"
DATES = {1: "2024-10-30", 2: "2025-01-29", 3: "2025-04-30", 4: "2025-07-30"}
DATELINES = {1: "October 30, 2024", 2: "January 29, 2025", 3: "April 30, 2025", 4: "July 30, 2025"}
HEADERS = {
    1: (["(In millions) (Unaudited)", "Three Months Ended September 30,"], ["2024", "2023"]),
    2: (["(In millions) (Unaudited)", "Three Months Ended December 31,", "Six Months Ended December 31,"], ["2024", "2023", "2024", "2023"]),
    3: (["(In millions) (Unaudited)", "Three Months Ended March 31,", "Nine Months Ended March 31,"], ["2025", "2024", "2025", "2024"]),
    4: (["(In millions)", "Year Ended June 30,"], ["2025", "2024", "2023"]),
}


def parse_cash_flow(html: str, quarter: int) -> list[dict]:
    tags = METRICS["cash-flows"]
    tables = [t for t in re.findall(r"<table\b[^>]*>.*?</table>", html, re.S | re.I)
              if "us-gaap:NetCashProvidedByUsedInOperatingActivities" in t]
    if len(tables) != 1:
        raise ValueError("Expected one cash-flow table")
    rows = [cells(r) for r in re.findall(r"<tr\b[^>]*>.*?</tr>", tables[0], re.S | re.I)]
    if rows[:2] != list(HEADERS[quarter]):
        raise ValueError("Changed unit, duration or year headers; inspect source before adapting")
    from datetime import timedelta
    fy_start = date(2024, 7, 1)
    q_start, end = month_start(fy_start, (quarter - 1) * 3), month_start(fy_start, quarter * 3) - timedelta(days=1)
    facts, found = [], set()
    for row in rows:
        tag = row[0].removeprefix("us-gaap:") if row else ""
        if tag not in tags:
            continue
        if tag in found or len(row) != 2 + len(HEADERS[quarter][1]):
            raise ValueError("Duplicate concept or changed numeric columns")
        found.add(tag)
        metric, label = tags[tag]
        columns = [(2, "quarter", q_start)] if quarter < 4 else [(2, "ytd", fy_start)]
        if quarter in (2, 3):
            columns.append((4, "ytd", fy_start))
        for column, kind, start in columns:
            raw = row[column]
            value = displayed_amount(raw)
            facts.append({"id": f"msft-fy25-q{quarter}-{kind}-{metric}", "metric": metric,
                          "label": label, "value": str(abs(value) if metric == "capex" else value),
                          "unit": "USD_million", "period_start": start.isoformat(), "period_end": end.isoformat(),
                          "source_id": f"msft-fy25-q{quarter}-cash-flows", "raw_value": raw,
                          "locator": f"us-gaap:{tag} / {HEADERS[quarter][0][1 if column == 2 else 2]} / {HEADERS[quarter][1][column - 2]}",
                          "transformation": "absolute_cash_outflow" if metric == "capex" else "displayed_magnitude",
                          "observation_kind": "reported", "basis_id": "us_gaap_consolidated"})
    if found != set(tags):
        raise ValueError("Missing cash-flow concept")
    return facts


def cached_source(quarter: int, page: str, download: bool):
    folder = ROOT / "data/raw/quarterly-msft"
    path = folder / f"fy2025-q{quarter}-{page}.html"
    meta_path = path.with_suffix(".download.json")
    url = f"https://www.microsoft.com/en-us/Investor/earnings/FY-2025-Q{quarter}/{page}"
    if not path.exists():
        if not download:
            raise FileNotFoundError(f"{path.name}: use --download to obtain official sources")
        request = urllib.request.Request(url, headers={"User-Agent": "CreditLens public financial research (https://github.com/leon1111-wgl/creditlens)"})
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read(3_000_001)
        if len(raw) > 3_000_000:
            raise ValueError("Unexpected source size")
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        meta_path.write_text(json.dumps({"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                        "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}, indent=2) + "\n", encoding="utf-8")
    raw = path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta["url"] != url or meta["sha256"] != hashlib.sha256(raw).hexdigest() or meta["bytes"] != len(raw):
        raise ValueError("Cached source does not match its download receipt")
    return raw.decode("utf-8-sig"), meta


def build(download=False):
    sources, facts, publication_evidence = [], [], []
    for quarter in range(1, 5):
        html, meta = cached_source(quarter, "cash-flows", download)
        press, press_meta = cached_source(quarter, "press-release-webcast", download)
        plain = re.sub(r"\s+", " ", unescape(re.sub("<[^>]+>", " ", press)))
        if not re.search(r"REDMOND,\s*Wash\..{0,30}" + re.escape(DATELINES[quarter]), plain):
            raise ValueError("Publication dateline changed or absent")
        sources.append({"id": f"msft-fy25-q{quarter}-cash-flows", "title": f"Microsoft FY2025 Q{quarter} cash flows",
                        "url": meta["url"], "published_at": DATES[quarter], "sha256": meta["sha256"],
                        "retrieved_at": meta["retrieved_at"]})
        publication_evidence.append({"quarter": quarter, "published_at": DATES[quarter], "locator": "REDMOND, Wash. dateline", **press_meta})
        facts.extend(parse_cash_flow(html, quarter))
    pack = TemporalPack(entity_id="MSFT", company="Microsoft Corporation", fiscal_year_start="2024-07-01",
                        fiscal_calendar="calendar_month_quarters", sources=sources, facts=facts)
    destination = ROOT / "data/examples/msft-fy2025-temporal.json"
    destination.write_text(pack.model_dump_json(indent=2) + "\n", encoding="utf-8")
    manifest = {"adapter_version": ADAPTER_VERSION, "pack_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "publication_evidence": publication_evidence, "source_count": len(sources), "reported_facts": len(facts),
                "license": "Issuer materials retain publisher rights; no blanket redistribution license asserted. Only selected facts and provenance included.",
                "scope": "Official public FY2025 disclosures; source-specific HTML adapter, not historical-vintage proof or a training dataset.",
                "limitations": ["Current website capture, not an archive from the disclosure date.",
                                "Prior-year comparison columns deliberately excluded.",
                                "Source statements do not automatically prove cross-release accounting comparability."]}
    (destination.parent / "msft-temporal-provenance.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(facts)} reported flow facts from {len(sources)} quarterly disclosures.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    build(parser.parse_args().download)
