"""Export Python answers for the TypeScript parity test (web/tests/fixtures/parity.json, gitignored).

Covers every lookup over registered and unregistered selectors and units, every
same-metric pair and same-issuer cross-metric pair for each pair kind, and
perturbed observation pairs for the two comparability rules.
"""
from __future__ import annotations

from copy import deepcopy
import itertools
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.evidence_answers import (  # noqa: E402
    METRICS, AnswerError, _instant_change, answer_plan, build_answer_catalog,
)
from creditlens.research_materials import _annual_change  # noqa: E402

OUT = ROOT/"web/tests/fixtures/parity.json"
UNITS = [None, {"currency": "USD", "scale": None}, {"currency": "USD", "scale": "million"},
         {"currency": "USD", "scale": "hundred_million"}, {"currency": "USD", "scale": "billion"},
         {"currency": "USD", "scale": "one"}, {"currency": "USD", "scale": "thousand"},
         {"currency": "USD", "scale": "ten_thousand"}, {"currency": "CNY", "scale": "hundred_million"},
         {"currency": "HKD", "scale": None}]


def plans():
    issuers, years = ("MSFT", "AAPL", "NVDA"), (2023, 2024, 2025, 2026)
    metrics = (*METRICS, "operating_income")
    for issuer, metric, year in itertools.product(issuers, metrics, years):
        for unit in UNITS:
            yield {"kind": "lookup", "items": [{"issuer_id": issuer, "metric": metric, "year": year}], "unit": unit}
        yield {"kind": "lookup", "items": [{"issuer_id": issuer, "metric": metric, "year": year}], "unit": None,
               "scope": "parent_only"}
    items = [{"issuer_id": i, "metric": m, "year": y} for i in ("MSFT", "AAPL") for m in METRICS for y in (2023, 2024, 2025)]
    for first, second in itertools.product(items, items):
        same_metric = first["metric"] == second["metric"]
        same_slot = first["issuer_id"] == second["issuer_id"] and first["year"] == second["year"]
        if not (same_metric or same_slot):
            continue
        for kind in ("change", "comparable_check", "cross_company"):
            units = UNITS[:5] if kind == "change" and same_metric and first["issuer_id"] == second["issuer_id"] else [None]
            for unit in units:
                yield {"kind": kind, "items": [first, second], "unit": unit}
    yield {"kind": "change", "items": [items[0]], "unit": None}
    yield {"kind": "lookup", "items": [{"issuer_id": "MSFT", "metric": "revenue", "year": "2025"}], "unit": None}
    yield {"kind": "lookup", "items": [{"issuer_id": "MSFT", "metric": "revenue", "year": 2025}], "unit": None, "extra": 1}


def perturbations(catalog):
    """Pairs that exercise each reason of both comparability rules."""
    by = {o["id"]: o for o in catalog["observations"]}
    pairs = []
    for metric in ("revenue", "cfo", "capex", "net_income", "gross_profit", "long_debt", "short_debt", "current_assets"):
        for issuer in ("MSFT", "AAPL"):
            pairs.append((by[f"{issuer}:{metric}:2024"], by[f"{issuer}:{metric}:2025"]))
    edits = [
        lambda p, c: c.__setitem__("unit", "USD_thousand"),
        lambda p, c: c.__setitem__("transformation", "absolute_cash_outflow"),
        lambda p, c: c.__setitem__("source_version_id", "other"),
        lambda p, c: c["basis"].__setitem__("definition", "Other line"),
        lambda p, c: c["basis"].__setitem__("operations_scope", "unknown"),
        lambda p, c: c["basis"].__setitem__("scope_evidence", []),
        lambda p, c: c["period"].__setitem__("days", 300),
        lambda p, c: c["period"].__setitem__("fiscal_calendar", "week_52_53"),
        lambda p, c: c["period"].__setitem__("fiscal_calendar", "retail_445"),
        lambda p, c: c["period"].__setitem__("end", "2025-07-31"),
        lambda p, c: c["period"].__setitem__("start", "2024-08-01"),
        lambda p, c: c["period"].__setitem__("start", "not-a-date"),
        lambda p, c: c["period"].__setitem__("kind", "quarter"),
        lambda p, c: c.__setitem__("year", 2026),
        lambda p, c: p.__setitem__("normalized_value", "0"),
        lambda p, c: p.__setitem__("normalized_value", "-12.5"),
        lambda p, c: (p.__setitem__("normalized_value", "3"), c.__setitem__("normalized_value", "4")),
        lambda p, c: (p.__setitem__("normalized_value", "7"), c.__setitem__("normalized_value", "8.0000005")),
        lambda p, c: c["period"].__setitem__("end", "2025-09-20"),
        lambda p, c: p["period"].__setitem__("end", "2024-07-31"),
    ]
    out = []
    for previous, current in pairs:
        variants = [(previous, current)]
        for edit in edits:
            p, c = deepcopy(previous), deepcopy(current)
            edit(p, c)
            variants.append((p, c))
        variants.append((current, previous))
        for p, c in variants:
            rule = "instant" if p["kind"] == "instant" else "annual"
            for adopted in ([p["id"], c["id"]], [p["id"]]) if rule == "annual" else ([p["id"], c["id"]],):
                result = _instant_change(p, c) if rule == "instant" else _annual_change(p, c, set(adopted))
                out.append({"rule": rule, "previous": p, "current": c, "adopted": adopted, "result": result})
    return out


def main() -> int:
    catalog = build_answer_catalog(ROOT)
    cases = []
    for plan in plans():
        try:
            cases.append({"plan": plan, "answer": answer_plan(catalog, plan)})
        except AnswerError as exc:
            cases.append({"plan": plan, "error": exc.code})
    rules = perturbations(catalog)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"answers": cases, "rules": rules}, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"status": "exported", "answers": len(cases), "rules": len(rules), "path": str(OUT.relative_to(ROOT))}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
