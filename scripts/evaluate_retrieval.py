"""Within-page retrieval only. No answer generation or gold-program execution."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.core import ingest_finqa, retrieve


def evaluate(path, k):
    content = path.read_bytes()
    records = json.loads(content)
    started = time.perf_counter()
    rows, missing_labels = [], []
    recalls, complete = [], []
    for record in records:
        case = ingest_finqa(record)
        predictions = retrieve(case, case.question, k=k)
        predicted = {x["evidence"]["id"] for x in predictions}
        # Labels enter ONLY after retrieval has completed.
        gold = set(record["qa"].get("gold_inds", {}))
        known_ids = {e.id for e in case.evidence}
        invalid = gold - known_ids
        if invalid:
            missing_labels.append({"id": case.id, "missing_ids": sorted(invalid)})
        if not gold:
            rows.append({"id": case.id, "status": "unscored_no_gold", "predicted": sorted(predicted)})
            continue
        hit = len(predicted & gold)
        recall = hit / len(gold)
        full = gold <= predicted
        recalls.append(recall)
        complete.append(full)
        rows.append({"id": case.id, "predicted": sorted(predicted), "gold": sorted(gold),
                     "recall": recall, "all_evidence_retrieved": full})
    return {
        "experiment": "within_page_bm25_retrieval_v1", "split": "dev",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "dataset_sha256": hashlib.sha256(content).hexdigest(),
        "cases": len(records), "scored_cases": len(recalls), "k": k,
        "macro_evidence_recall_at_k": sum(recalls) / len(recalls) if recalls else None,
        "all_gold_evidence_rate_at_k": sum(complete) / len(complete) if complete else None,
        "elapsed_seconds": time.perf_counter() - started,
        "invalid_gold_locators": missing_labels,
        "scope": "Retrieval inside the provided report page; NOT corpus search or end-to-end financial QA accuracy.",
        "label_isolation": "Only pre_text, post_text and table are indexed. No gold_inds, model_input, program, answer or retrieved fields enter retrieval.",
        "cases_detail": rows,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/raw/finqa-dev.json")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/retrieval-baseline.json")
    args = parser.parse_args()
    result = evaluate(args.dataset, args.k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases_detail"}, indent=2))


if __name__ == "__main__":
    main()
