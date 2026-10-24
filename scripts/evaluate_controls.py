"""Run transparent, counterfactual control probes; not an AI accuracy benchmark."""
from copy import deepcopy
from datetime import date, datetime, timezone
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pydantic import ValidationError
from creditlens.diligence import CasePack, analyze_pack, digest, RULE_VERSION


def run_controls():
    original = json.loads((ROOT / "data/examples/msft-fy2025.json").read_text(encoding="utf-8"))
    results = []

    def probe(name, purpose, mutate, *, absent=(), no_comparison=(), reject=False, block=False, cutoff=date(2025, 8, 1)):
        pack = deepcopy(original)
        mutate(pack)
        try:
            analysis = analyze_pack(CasePack.model_validate(pack), cutoff, 20)
            ids = {m["id"] for m in analysis["metrics"]}
            compared = {r["metric"] for r in analysis["comparison"]}
            passed = (not reject and not (set(absent) & ids) and not (set(no_comparison) & compared)
                      and (not block or analysis["blocking_count"] > 0))
            if name == "official_reference":
                values = {m["id"]: m["value"] for m in analysis["metrics"]}
                passed &= values.get("fcf") == "71611" and values.get("stress_fcf") == "44378.6" and analysis["blocking_count"] == 0
            observation = {"outcome": "analyzed", "metric_ids": sorted(ids),
                           "comparison_metrics": sorted(compared), "blocking_count": analysis["blocking_count"],
                           "issue_ids": [i["id"] for i in analysis["issues"]]}
        except ValidationError as e:
            passed = reject
            observation = {"outcome": "schema_rejected", "errors": [x["type"] for x in e.errors()]}
        results.append({"id": name, "purpose": purpose, "input_kind": "official_reference" if name == "official_reference" else "synthetic_counterfactual",
                        "input_sha256": digest(pack), "passed": bool(passed), "expected": {"schema_rejection": reject,
                        "absent_metrics": list(absent), "absent_comparisons": list(no_comparison), "blocking": block},
                        "observed": observation})

    def change(fact_id, **values):
        return lambda p: next(f for f in p["facts"] if f["id"] == fact_id).update(values)

    def duplicate(fact_id):
        def mutate(p):
            f = deepcopy(next(f for f in p["facts"] if f["id"] == fact_id))
            f.update(id="counterfactual-duplicate", value="1", raw_value="1")
            p["facts"].append(f)
        return mutate

    probe("official_reference", "Known source-linked arithmetic remains unchanged.", lambda p: None)
    probe("missing_capex", "Missing data must not silently become zero.",
          lambda p: p.update(facts=[f for f in p["facts"] if f["id"] != "capex-2025"]), absent=("fcf", "stress_fcf"), block=True)
    probe("mixed_currency", "Do not subtract amounts denominated in different currencies.", change("capex-2025", unit="CNY_million"), absent=("fcf", "stress_fcf"), block=True)
    probe("mixed_period", "Do not subtract six-month spending from a full-year cash flow.", change("capex-2025", period_start="2025-01-01"), absent=("fcf", "stress_fcf"), block=True)
    probe("future_disclosure", "A report unavailable at the cutoff contributes no metrics.", lambda p: None,
          cutoff=date(2025, 7, 29), absent=("liquidity", "cash_conversion", "fcf", "capex_intensity", "current_ratio", "stress_fcf"), block=True)
    probe("conflicting_current", "An ambiguous current cash flow must not leak through the comparison table.", duplicate("cfo-2025"),
          absent=("fcf", "stress_fcf"), no_comparison=("cfo",), block=True)
    probe("conflicting_prior", "An ambiguous prior cash flow cannot produce a growth comparison.", duplicate("cfo-2024"), no_comparison=("cfo",))
    probe("negative_cfo_stress", "A cash-flow downside percentage must not improve an already negative cash flow.",
          change("cfo-2025", value="-100", raw_value="(100)"), absent=("capex_intensity", "stress_fcf"), block=True)
    probe("zero_cfo_stress", "A zero baseline does not define a useful percentage downside scenario.",
          change("cfo-2025", value="0", raw_value="0"), absent=("capex_intensity", "stress_fcf"), block=True)
    probe("negative_net_income", "A loss must not be described with the positive-profit cash conversion rule.",
          change("net_income-2025", value="-100", raw_value="(100)"), absent=("cash_conversion",), block=True)
    probe("raw_normalized_mismatch", "A normalized number must agree with its supplied displayed value.",
          change("cfo-2025", value="1"), reject=True)
    probe("malformed_grouping", "Broken OCR digit grouping must not be silently normalized.",
          change("cfo-2025", value="136162", raw_value="13,61,62"), reject=True)
    probe("wrong_sign_transform", "Absolute cash-outflow normalization cannot be applied to profit.",
          change("net_income-2025", raw_value="(101,832)", transformation="absolute_cash_outflow"), reject=True)
    probe("unknown_source", "Every fact must reference a registered source.", change("cfo-2025", source_id="missing"), reject=True)
    probe("impossible_publication", "The disclosed period cannot end after its source publication date.",
          lambda p: p["sources"][0].update(published_at="2025-06-01"), reject=True)
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "suite_version": "financial-controls-1", "implementation_rule_version": RULE_VERSION,
            "official_reference_pack_sha256": digest(original), "probes": len(results),
            "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results),
            "scope": "One official reference plus deliberate synthetic perturbations; no model inference or population-level accuracy estimate.",
            "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/financial-controls.json")
    args = parser.parse_args()
    report = run_controls()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("probes", "passed", "failed", "scope")}))
    raise SystemExit(1 if report["failed"] else 0)
