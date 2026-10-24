"""Build a small, inspectable fact pack from Microsoft's official FY25 tables.

This is a source-specific adapter, not a general PDF extraction benchmark.
Raw HTML stays local. Public output contains financial facts and provenance only.
"""
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
import hashlib
import json
import re
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.microsoft.com/en-us/investor/earnings/fy-2025-q4/"
PUBLISHED = "2025-07-30"
METRICS = {
    "balance-sheets": {
        "CashCashEquivalentsAndShortTermInvestments": ("liquid_assets", "现金、现金等价物与短期投资"),
        "CommercialPaper": ("short_debt", "短期债务"),
        "LongTermDebtCurrent": ("current_debt", "一年内到期长期债务"),
        "LongTermDebtNoncurrent": ("long_debt", "长期债务"),
        "AssetsCurrent": ("current_assets", "流动资产"),
        "LiabilitiesCurrent": ("current_liabilities", "流动负债"),
    },
    "cash-flows": {
        "NetCashProvidedByUsedInOperatingActivities": ("cfo", "经营活动现金流"),
        "PaymentsToAcquirePropertyPlantAndEquipment": ("capex", "固定资产购建现金流出"),
        "NetIncomeLoss": ("net_income", "净利润"),
    },
    "income-statements": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": ("revenue", "营业收入"),
    },
}


def cells(row):
    return [re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", c))).strip()
            for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S | re.I)]


def build():
    sources, facts = [], []
    for page, metrics in METRICS.items():
        path = ROOT / "data/raw" / f"msft-fy25-{page}.html"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(urllib.request.urlopen(BASE + page, timeout=40).read())
        raw = path.read_bytes()
        html = raw.decode("utf-8")
        if "2025" not in html or "2024" not in html or "In millions" not in html:
            raise ValueError("Unexpected period or units; inspect the original page")
        source_id = f"msft-fy25-{page}"
        sources.append({"id": source_id, "title": "Microsoft FY25 Q4 / " + page,
                        "url": BASE + page, "published_at": PUBLISHED,
                        "sha256": hashlib.sha256(raw).hexdigest()})
        found = set()
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, re.S | re.I):
            values = cells(row)
            if len(values) < 4 or not values[0].startswith("us-gaap:"):
                continue
            tag = values[0].removeprefix("us-gaap:")
            if tag not in metrics:
                continue
            if tag in found:
                raise ValueError("Ambiguous duplicate source concept: " + tag)
            found.add(tag)
            metric, label = metrics[tag]
            for i, year in enumerate((2025, 2024)):
                number = values[i + 2].replace("$", "").replace(",", "").strip()
                if number.startswith("(") and number.endswith(")"):
                    number = "-" + number[1:-1]
                # Capex is explicitly normalized from signed outflow to positive spend.
                if metric == "capex":
                    number = number.lstrip("-")
                if not re.fullmatch(r"-?\d+(\.\d+)?", number):
                    raise ValueError("Unexpected numeric cell: " + number)
                facts.append({"id": f"{metric}-{year}", "metric": metric, "label": label,
                              "value": number, "unit": "USD_million",
                              "period_start": None if page == "balance-sheets" else f"{year-1}-07-01",
                              "period_end": f"{year}-06-30", "source_id": source_id,
                              "locator": f"us-gaap:{tag} / FY{year}",
                              "raw_value": values[i + 2],
                              "transformation": "absolute_cash_outflow" if metric == "capex" else "displayed_magnitude"})
        if found != set(metrics):
            raise ValueError("Missing source concepts: " + str(set(metrics) - found))
    payload = {"schema_version": 1, "entity_id": "MSFT", "company": "Microsoft Corporation",
               "title": "FY2025 现金流与债务复核", "sources": sources, "facts": facts}
    destination = ROOT / "data/examples/msft-fy2025.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"retrieved_at": datetime.now(timezone.utc).isoformat(),
                "publication_date_source": BASE + "press-release-webcast",
                "pack_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "facts": len(facts), "sources": sources,
                "limitations": ["Historical FY2025 case, not the latest financial position.",
                                "Current website capture is not a frozen historical web archive.",
                                "FY2024 comparison is as disclosed in the FY2025 release.",
                                "Financial facts only; full reports remain with their publisher."]}
    (ROOT / "data/examples/msft-provenance.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(facts)} source-linked financial facts from {len(sources)} official tables.")


if __name__ == "__main__":
    build()
