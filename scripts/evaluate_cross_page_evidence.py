"""Replay four registered cross-statement tasks and fixed synthetic controls."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.cross_page_materials import build_cross_page_materials
from creditlens.diligence import digest
from scripts.evaluate_currency_diagnostic import output_path


VERSION = "cross-page-development-replay-1"
IMPLEMENTATION_FILES = (
    "creditlens/cross_page_materials.py", "creditlens/cross_page_evidence.py",
    "creditlens/research_materials.py", "creditlens/diligence.py",
    "scripts/evaluate_cross_page_evidence.py", "scripts/evaluate_currency_diagnostic.py",
    "creditlens/experiment_paths.py",
)
FIXED_PERCENTAGES = {"msft-cfo-revenue-2024": "48.362856", "msft-cfo-revenue-2025": "48.331701",
                     "aapl-cfo-revenue-2024": "30.241283", "aapl-cfo-revenue-2025": "26.788190"}
_CONTROLS = (
    ("missing_scope", "不选 Note 1 范围依据", "refused", None),
    ("missing_cfo", "缺经营现金流金额", "refused", None),
    ("missing_revenue", "缺营业收入金额", "refused", None),
    ("wrong_year", "混入同公司另一年度现金流", "refused", None),
    ("cross_company", "混入另一公司的营业收入", "refused", None),
    ("duplicate", "同一证据选择两次", "refused", None),
    ("unknown", "选择未登记的证据 ID", "refused", None),
    ("zero_revenue", "合成零收入分母", "refused", None),
    ("negative_revenue", "合成负收入分母", "refused", None),
    ("negative_cfo", "合成负现金流保留符号", "computed", "-48.362856"),
    ("wrong_unit", "收入与现金流单位不一致", "refused", None),
    ("wrong_period", "收入期间起点不同", "refused", None),
    ("segment_scope", "收入仅某分部，非报表合并总额", "refused", None),
    ("historical", "请求未经验证的历史时点", "refused", None),
    ("future_source_date", "登记披露日在任务查看日之后", "refused", None),
    ("raw_mismatch", "显示金额与归一金额不一致", "refused", None),
    ("unreadable_cfo", "现金流卡登记为无法读取", "refused", None),
    ("missing_amount_location", "已披露金额缺少登记框", "refused", None),
)


def cases() -> list[dict]:
    result = [{"id": "registered-" + task_id, "task_id": task_id, "operation": "complete",
               "title": task_id + " 三角色完整选择", "provenance": "registered_disclosure_development_case",
               "expected": {"status": "computed", "percentage": percentage},
               "basis": "固定原始披露值；Decimal(cfo)/Decimal(revenue)*100，ROUND_HALF_UP 保留六位。"}
              for task_id, percentage in FIXED_PERCENTAGES.items()]
    result.extend({"id": "synthetic-" + operation, "task_id": "msft-cfo-revenue-2024", "operation": operation,
                   "title": title, "provenance": "synthetic_selection_or_card_control",
                   "expected": {"status": status, "percentage": percentage},
                   "basis": "开发者预先规定的选择/材料异常；修改只发生于内存副本，不改变官方事实或冻结源包。"}
                  for operation, title, status, percentage in _CONTROLS)
    return result


def _prepare(materials: dict, case: dict):
    task = deepcopy(next(t for t in materials["tasks"] if t["id"] == case["task_id"]))
    cards = deepcopy(materials["cards"])
    roles = task["required_role_ids"]
    selected = list(roles.values())
    operation = case["operation"]
    by_id = {c["id"]: c for c in cards}
    cfo, revenue, scope = (by_id[roles[role]] for role in ("cfo", "revenue", "consolidation_scope"))
    changed = None
    if operation.startswith("missing_") and operation != "missing_amount_location":
        role = {"missing_scope": "consolidation_scope", "missing_cfo": "cfo", "missing_revenue": "revenue"}[operation]
        selected.remove(roles[role])
    elif operation in ("wrong_year", "cross_company"):
        other_task = next(t for t in materials["tasks"] if t["id"] == (
            "msft-cfo-revenue-2025" if operation == "wrong_year" else "aapl-cfo-revenue-2024"))
        role = "cfo" if operation == "wrong_year" else "revenue"
        selected[selected.index(roles[role])] = other_task["required_role_ids"][role]
    elif operation == "duplicate":
        selected.append(roles["cfo"])
    elif operation == "unknown":
        selected.append("unregistered-evidence-control")
    elif operation in ("zero_revenue", "negative_revenue"):
        revenue.update(raw_value="0" if operation == "zero_revenue" else "(245,122)",
                       normalized_value="0" if operation == "zero_revenue" else "-245122")
        changed = revenue
    elif operation == "negative_cfo":
        cfo.update(raw_value="(118,548)", normalized_value="-118548")
        changed = cfo
    elif operation == "wrong_unit":
        revenue["unit"] = "EUR_million"
        changed = revenue
    elif operation == "wrong_period":
        revenue["period"]["start"] = "2023-07-02"
        changed = revenue
    elif operation == "segment_scope":
        revenue["financial_scope"]["consolidation"] = "segment"
        changed = revenue
    elif operation == "historical":
        task["availability"] = {"mode": "historical_as_of", "as_of": "2024-06-30"}
    elif operation == "future_source_date":
        revenue["availability"]["source_published_at"] = "2027-01-01"
        changed = revenue
    elif operation == "raw_mismatch":
        revenue["normalized_value"] = "245123"
        changed = revenue
    elif operation == "unreadable_cfo":
        cfo.update(value_status="unreadable", raw_value=None, normalized_value=None)
        changed = cfo
    elif operation == "missing_amount_location":
        revenue["location"]["amount_bbox"] = None
        changed = revenue
    elif operation != "complete":
        raise ValueError("Unknown fixed operation")
    if changed is not None:
        changed["provenance"] = {"kind": "synthetic_control", "reference_method": case["title"],
                                  "independent_human_verified": False, "model_output_verified": False}
    return task, cards, selected


def _implementation_hashes(root: Path) -> dict:
    return {relative: hashlib.sha256((root/relative).read_text(encoding="utf-8").replace("\r\n", "\n")
                                    .replace("\r", "\n").encode("utf-8")).hexdigest()
            for relative in IMPLEMENTATION_FILES}


def build_report(root: Path) -> dict:
    from creditlens.cross_page_evidence import evaluate_evidence_selection
    root = Path(root).resolve()
    materials = build_cross_page_materials(root)
    implementation = _implementation_hashes(root)
    results = []
    for case in cases():
        task, cards, selected = _prepare(materials, case)
        actual = evaluate_evidence_selection(task, cards, selected)
        by_id = {c["id"]: c for c in cards}
        expected_references = [{k: v for k, v in by_id[cid].items() if k != "schema_version"}
                               for cid in selected if cid in by_id]
        percentage = actual["calculation"]["result"]["value"] if actual["calculation"] is not None else None
        checks = {"status": actual["status"] == case["expected"]["status"],
                  "percentage": percentage == case["expected"]["percentage"],
                  "refusal_without_calculation": actual["status"] != "refused" or actual["calculation"] is None,
                  "selected_ids_preserved": actual["selected_ids"] == selected,
                  "references_preserved": actual["references"] == expected_references}
        results.append({**case, "input_sha256": digest({"task": task, "cards": cards, "selected_ids": selected}),
                        "checks": checks, "passed": all(checks.values()), "result": actual})
    if materials != build_cross_page_materials(root) or implementation != _implementation_hashes(root):
        raise ValueError("Inputs changed while replaying cross-page controls")
    return {"schema_version": 1, "replay_version": VERSION, "material_sha256": materials["material_sha256"],
            "catalog_sha256": digest(materials), "cases_sha256": digest(cases()), "implementation_sha256": implementation,
            "implementation_hash_basis": "python_utf8_lf", "source_bindings": materials["source_bindings"],
            "scope": {"registered_tasks": 4, "synthetic_controls": len(_CONTROLS), "independent_human_gold": 0,
                      "human_confirmation": "pending", "model_calls": 0, "model_accuracy_measured": False,
                      "natural_language_understanding_measured": False, "new_ocr_run": False,
                      "raw_pdf_bytes_checked": False, "full_ocr_verified": False,
                      "test_protocol": "developer_fixed_selection_and_synthetic_card_replay"},
            "summary": {"cases": len(results), "passed": sum(c["passed"] for c in results),
                        "failed": sum(not c["passed"] for c in results),
                        "registered_disclosure_cases": 4, "synthetic_controls": len(_CONTROLS),
                        "computed": sum(c["result"]["status"] == "computed" for c in results),
                        "refused": sum(c["result"]["status"] == "refused" for c in results)},
            "check_summary": {
                "fixed_result_expectations": {"cases": len(results), "met": sum(c["checks"]["status"] and c["checks"]["percentage"] for c in results)},
                "selected_reference_preservation": {"cases": len(results), "met": sum(c["checks"]["selected_ids_preserved"] and c["checks"]["references_preserved"] for c in results)},
                "expected_refusals": {"cases": sum(c["expected"]["status"] == "refused" for c in results),
                                      "met": sum(c["expected"]["status"] == "refused" and c["checks"]["status"] and c["result"]["calculation"] is None for c in results)},
            },
            "cases": results, "limitations": materials["limitations"] + [
                "开发控制全量进入分母；符合固定预期不是通用财报准确率、独立盲测或人工金标准。",
                "合成控制中负现金流等修改不属于真实披露值；没有把合成选择当作模型检索或回答。",
                "规范摘要只用于内容绑定，不证明外部身份或来源真实性。"],
            "read_only": True, "automatic_approval": False}


def validate_report(root: Path, report: dict) -> dict:
    expected = build_report(root)
    # Compare every result/check/reference, not just counts. Strict canonical JSON
    # rejects nonfinite values and makes ordering of list evidence significant.
    if type(report) is not dict or digest(report) != digest(expected):
        raise ValueError("Cross-page replay report does not match current pinned inputs and implementation")
    return deepcopy(report)


def run(output: Path | None = None) -> dict:
    selected = Path(output) if output is not None else Path("cross-page-evidence-checks.json")
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = ROOT/".run/reproductions"/selected
    target = output_path(ROOT, selected)
    report = build_report(ROOT)
    validate_report(ROOT, report)
    target = output_path(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(target), "summary": report["summary"]}, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New .json directly inside repository .run/reproductions")
    args = parser.parse_args(argv)
    try:
        run(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Cross-page replay failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
