"""Export a source-linked factor snapshot; this is not a labeled prediction dataset."""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import CasePack, analyze_pack, digest


def snapshot(pack, as_of):
    analysis = analyze_pack(pack, as_of, 20)
    factors = [m for m in analysis["metrics"] if m["id"] != "stress_fcf"]
    return {
        "schema_version": 1,
        "entity_id": pack.entity_id,
        "as_of": str(as_of),
        "financial_period_end": analysis["period_end"],
        "source_pack_sha256": digest(pack.model_dump(mode="json")),
        "factors": factors,
        "source_documents": [s.model_dump(mode="json") for s in pack.sources if s.published_at <= as_of],
        "issues": analysis["issues"],
        "outcome_label": None,
        "usable_for_prediction_training": False,
        "limitations": [
            "Single-company historical example; no outcome labels or predictive validation.",
            "Cutoff filters reported publication dates; present-day website capture is not an archived point-in-time snapshot.",
            "Factors are simplified financial ratios; debt scope and cash availability require review.",
            "The stress scenario is intentionally excluded from observed factors.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, default=ROOT / "data/examples/msft-fy2025.json")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2025, 8, 1))
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/factor-snapshot.json")
    args = parser.parse_args()
    pack = CasePack.model_validate_json(args.pack.read_text(encoding="utf-8"))
    data = snapshot(pack, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"factors": len(data["factors"]), "prediction_labels": 0, "as_of": str(args.as_of)}))
