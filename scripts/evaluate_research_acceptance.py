"""Replay fixed task-contract development cases, not model question answering."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.diligence import digest
from creditlens.experiment_paths import validate_write_path
from creditlens.document_transfer import file_hash
from creditlens.issuer_evaluation import read_json
from creditlens.research_materials import build_material_bundle, build_comparison
from creditlens.cash_flow_bridge import build_cash_flow_bridge


CASES_PATH = "data/evaluation/research-task-cases.json"
VERSION = "research-acceptance-development-eval-1"
CHANNELS = ("correctness", "citation", "refusal")
CONTRACTS = ("metric_lookup", "comparable_change", "operating_explanation")
OUTCOMES = ("supported", "insufficient", "refused", "exception")
VARIANTS = frozenset({"all_adopted", "rejected_target", "unassessed_target", "with_bridge",
    "bridge_unassessed", "hypothesis_claim", "reviewed_bridge", "historical", "question_only",
    "tamper_unit", "tamper_period", "tamper_source", "tamper_page", "tamper_amount",
    "tamper_comparison", "tamper_bridge", "unknown_hypothesis"})
IMPLEMENTATION_FILES = (
    "scripts/evaluate_research_acceptance.py", "creditlens/research_acceptance.py",
    "creditlens/research_materials.py", "creditlens/cash_flow_bridge.py",
    "creditlens/cash_flow_sources.py", "creditlens/diligence.py",
    "creditlens/experiment_paths.py", "creditlens/issuer_evaluation.py", "creditlens/document_transfer.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _path(value):
    require(type(value) is list and len(value) <= 16, "Invalid bounded assertion path")
    for part in value:
        if type(part) is str:
            require(0 < len(part) <= 256, "Invalid path key")
        elif type(part) is int:
            require(0 <= part <= 1000, "Invalid path index")
        else:
            require(type(part) is dict and 1 <= len(part) <= 3, "Invalid unique-match selector")
            require(all(type(k) is str and type(v) in (str, int) for k, v in part.items()), "Invalid match fields")


def validate_catalog(catalog):
    require(type(catalog) is dict and type(catalog.get("schema_version")) is int
            and catalog.get("schema_version") == 1, "Invalid catalog")
    require(type(catalog.get("id")) is str and 1 <= len(catalog["id"].strip()) <= 100, "Invalid catalog ID")
    require(catalog.get("purpose") == "deterministic_task_contract_development_acceptance", "Wrong evaluation purpose")
    require(catalog.get("human_confirmation") == "pending" and catalog.get("held_out") is False,
            "Development cases cannot claim independent human confirmation or held-out status")
    require(catalog.get("natural_language_understanding_evaluated") is False
            and catalog.get("model_accuracy_evaluated") is False, "Not a language-model evaluation")
    cases = catalog.get("cases")
    require(type(cases) is list and 30 <= len(cases) <= 50, "Expected 30–50 development cases")
    ids = []
    for case in cases:
        require(type(case) is dict and set(case) == {"id", "contract_id", "question", "input", "provenance", "expected", "basis", "scope"}, "Unexpected case fields")
        require(type(case["id"]) is str and 1 <= len(case["id"]) <= 64, "Invalid case ID")
        ids.append(case["id"])
        require(type(case["contract_id"]) is str and case["contract_id"] in CONTRACTS, "Unknown contract")
        require(all(type(case[k]) is str and 1 <= len(case[k]) <= 4000 for k in ("question", "basis", "scope")), "Missing case explanation")
        data = case["input"]
        require(type(data) is dict and set(data) == {"variant", "issuer_id", "metric", "year"}, "Unexpected input fields")
        require(type(data["variant"]) is str and data["variant"] in VARIANTS
                and type(data["issuer_id"]) is str and data["issuer_id"] in ("MSFT", "AAPL")
                and data["metric"] in ("cfo", "capex") and type(data["year"]) is int
                and data["year"] in (2024, 2025), "Unknown case input")
        provenance = case["provenance"]
        require(type(provenance) is dict and provenance.get("kind") in ("registered_material_case", "synthetic_control")
                and provenance.get("drafting") == "ai_assisted_developer"
                and provenance.get("human_confirmation") == "pending" and provenance.get("held_out") is False
                and provenance.get("scenario") == "scripted_development_fixture", "Invalid provenance")
        require(data["variant"] in ("all_adopted", "with_bridge") or provenance["kind"] == "synthetic_control", "Mutations must be marked synthetic")
        expected = case["expected"]
        require(type(expected) is dict and set(expected) <= {"outcome", "status_path", "checks", "error_code"}
                and set(expected) >= {"outcome", "status_path", "checks"} and expected["outcome"] in OUTCOMES, "Invalid expected result")
        _path(expected["status_path"])
        require(bool(expected.get("error_code")) == (expected["outcome"] == "exception"), "Expected exception requires its fixed code")
        require(bool(expected["status_path"]) != (expected["outcome"] == "exception"), "Expected status path mismatch")
        require(type(expected["checks"]) is list and 1 <= len(expected["checks"]) <= 20, "Missing bounded assertions")
        for check in expected["checks"]:
            require(type(check) is dict and set(check) == {"channel", "path", "operator", "expected"}
                    and check["channel"] in CHANNELS and check["operator"] == "equals", "Invalid assertion")
            _path(check["path"])
    require(len(ids) == len(set(ids)), "Duplicate case ID")
    digest(catalog)  # Reject non-JSON/nonfinite expectations.
    return deepcopy(catalog)


def resolve(value, path):
    """Select exact structured fields; missing or ambiguous paths never equal null."""
    for part in path:
        if type(part) is dict:
            if type(value) is not list:
                raise LookupError("Selector needs a list")
            found = [item for item in value if type(item) is dict and all(k in item and digest(item[k]) == digest(v) for k, v in part.items())]
            if len(found) != 1:
                raise LookupError("Selector must identify exactly one result")
            value = found[0]
        elif type(part) is int:
            if type(value) is not list or part >= len(value):
                raise LookupError("Missing list item")
            value = value[part]
        else:
            if type(value) is not dict or part not in value:
                raise LookupError("Missing result field")
            value = value[part]
    return deepcopy(value)


def make_task(case, bundle, bridge):
    """The case selects a declared recipe; question wording never dispatches code."""
    selected = case["input"]
    target = next(o["id"] for o in bundle["observations"] if o["issuer_id"] == selected["issuer_id"]
                  and o["metric"] == selected["metric"] and o["year"] == selected["year"])
    decisions = [{"observation_id": o["id"], "decision": "adopted", "note": "Scripted development adoption, not a human review."}
                 for o in bundle["observations"]]
    variant = selected["variant"]
    if variant == "rejected_target":
        next(d for d in decisions if d["observation_id"] == target)["decision"] = "rejected"
    elif variant in ("unassessed_target", "bridge_unassessed"):
        decisions = [d for d in decisions if d["observation_id"] != target]
    task = {"id": "development-" + case["id"], "revision": 1, "store_version": "research-handoff-1",
            "bundle_id": bundle["id"], "material_mode": "current_verified_materials", "as_of": None,
            "question": case["question"], "status": "draft", "bundle": deepcopy(bundle), "bundle_sha256": digest(bundle),
            "decisions": decisions, "comparison": build_comparison(bundle, decisions), "hypotheses": []}
    if variant in ("with_bridge", "bridge_unassessed", "reviewed_bridge", "tamper_bridge"):
        task.update(store_version="research-handoff-2", include_cash_flow_bridge=True,
                    cash_flow_bridge=deepcopy(bridge), cash_flow_bridge_sha256=digest(bridge))
    if variant == "reviewed_bridge":
        task["status"] = "reviewed"
    if variant in ("hypothesis_claim", "unknown_hypothesis"):
        task["hypotheses"] = [{"id": "synthetic-hypothesis", "text": "已证明是收款改善（开发模拟文字，未提供经营证据）",
                               "evidence_ids": [target if variant == "hypothesis_claim" else "UNKNOWN:source:revenue-2025"],
                               "status": "pending", "note": "Synthetic candidate only."}]
    if variant == "historical":
        task.update(material_mode="historical_as_of", as_of="2024-01-01")
    observation = next(o for o in task["bundle"]["observations"] if o["id"] == target)
    if variant == "tamper_unit":
        observation["unit"] = "EUR_million"
    elif variant == "tamper_period":
        observation["period"]["end"] = "2025-12-31"
    elif variant == "tamper_source":
        other = next(o for o in bundle["observations"] if o["issuer_id"] != observation["issuer_id"])
        observation["source"] = deepcopy(other["source"])
    elif variant == "tamper_page":
        observation["source"]["page"] = 999
    elif variant == "tamper_amount":
        observation["normalized_value"] = "999"
    elif variant == "tamper_comparison":
        task["comparison"]["annual_changes"][0]["absolute_change"] = "999"
    elif variant == "tamper_bridge":
        task["cash_flow_bridge"]["changes"][0]["groups"][0]["contribution"] = "999"
        task["cash_flow_bridge_sha256"] = digest(task["cash_flow_bridge"])
    if variant in ("tamper_unit", "tamper_period", "tamper_source", "tamper_page", "tamper_amount"):
        task["bundle_sha256"] = digest(task["bundle"])  # A self-consistent forged hash must not make data trusted.
    return task


def evaluate_case(case, task, assess):
    from creditlens.research_acceptance import ResearchAcceptanceError
    before = digest(task)
    try:
        result = assess(deepcopy(task))
        error_kind = None
    except ResearchAcceptanceError as exc:
        result, error_kind = {"error": {"code": exc.code, "message": exc.message}}, "validation_error"
    except Exception as exc:
        result, error_kind = {"error": {"code": type(exc).__name__, "message": str(exc)[:1000]}}, "unexpected_runtime_error"
    if error_kind:
        outcome = "exception"
    else:
        try:
            status = resolve(result, case["expected"]["status_path"])
        except LookupError:
            status = None
        outcome = {"evidence_available": "supported", "computed": "supported", "available": "supported",
                   "insufficient": "insufficient", "blocked": "refused", "side_by_side_only": "refused"}.get(status, "unrecognized")
    checks = []
    for assertion in case["expected"]["checks"]:
        try:
            observed = resolve(result, assertion["path"])
            passed, missing = digest(observed) == digest(assertion["expected"]), False
        except LookupError:
            observed, passed, missing = None, False, True
        checks.append({**deepcopy(assertion), "observed": observed, "path_missing_or_ambiguous": missing, "passed": passed})
    outcome_matches = outcome == case["expected"]["outcome"]
    if case["expected"].get("error_code"):
        outcome_matches = outcome_matches and result.get("error", {}).get("code") == case["expected"]["error_code"]
    passed = outcome_matches and all(c["passed"] for c in checks) and error_kind != "unexpected_runtime_error"
    return {"id": case["id"], "contract_id": case["contract_id"], "question": case["question"],
            "provenance": deepcopy(case["provenance"]), "basis": case["basis"], "scope": case["scope"],
            "input_recipe": deepcopy(case["input"]), "input_sha256": before, "result_sha256": digest(result),
            "expected_outcome": case["expected"]["outcome"], "observed_outcome": outcome,
            "outcome_matches": outcome_matches, "error_kind": error_kind, "error": result.get("error"),
            "checks": checks, "passed": passed}


def _group_summary(results):
    channels = {}
    for channel in CHANNELS:
        eligible = [r for r in results if any(c["channel"] == channel for c in r["checks"])]
        checks = [c for r in eligible for c in r["checks"] if c["channel"] == channel]
        channels[channel] = {"cases": len(eligible), "checks": len(checks), "passed_checks": sum(c["passed"] for c in checks),
                             "failed_checks": sum(not c["passed"] for c in checks),
                             "passed_cases": sum(all(c["passed"] for c in r["checks"] if c["channel"] == channel) for r in eligible)}
    return {"cases": len(results), "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results),
            "outcomes": {o: sum(r["observed_outcome"] == o for r in results) for o in (*OUTCOMES, "unrecognized")},
            "unexpected_runtime_errors": sum(r["error_kind"] == "unexpected_runtime_error" for r in results),
            "channels": channels}


def summarize(results):
    return {**_group_summary(results),
            "by_contract": {c: _group_summary([r for r in results if r["contract_id"] == c]) for c in CONTRACTS},
            "by_provenance": {p: _group_summary([r for r in results if r["provenance"]["kind"] == p])
                              for p in ("registered_material_case", "synthetic_control")},
            "independently_human_confirmed_cases": 0}


def build_report(root: Path = ROOT, *, catalog=None, assess=None):
    from creditlens.research_acceptance import build_task_acceptance, task_contract_catalog
    root = Path(root)
    raw, registered = read_json(root/CASES_PATH, 1_000_000)
    selected = validate_catalog(registered if catalog is None else catalog)
    bundle, bridge = build_material_bundle(root), build_cash_flow_bridge(root)
    require(selected.get("source_bundle_id") == bundle["id"] and selected.get("source_bundle_sha256") == digest(bundle), "Case materials changed")
    require(selected.get("cash_flow_bridge_sha256") == digest(bridge), "Case accounting source changed")
    registered_sources = {(source["pack_path"], source["sha256"]) for source in bundle["source_versions"]}
    for case in selected["cases"]:
        provenance = case["provenance"]
        paths, hashes = provenance.get("source_paths"), provenance.get("source_sha256")
        require(type(paths) is list and type(hashes) is list and len(paths) == len(hashes)
                and 1 <= len(paths) <= 2 and all(type(p) is str for p in paths)
                and len(paths) == len(set(paths)), "Missing case source registration")
        require(all(type(p) is str and type(h) is str and (p, h) in registered_sources for p, h in zip(paths, hashes)), "Case cites an unregistered source")
    evaluator = assess or build_task_acceptance
    results = [evaluate_case(case, make_task(case, bundle, bridge), evaluator) for case in selected["cases"]]
    return {"schema_version": 1, "suite_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "catalog": {"id": selected["id"], "path": CASES_PATH, "registered_file_sha256": hashlib.sha256(raw).hexdigest(),
                        "evaluated_canonical_sha256": digest(selected), "registered_catalog_used": digest(selected) == digest(registered),
                        "human_confirmation": "pending", "held_out": False},
            "source": {"bundle_sha256": digest(bundle), "cash_flow_bridge_sha256": digest(bridge),
                       "source_versions": deepcopy(bundle["source_versions"])},
            "implementation": {"hash_basis": "python_utf8_lf", "files": {p: file_hash(root/p) for p in IMPLEMENTATION_FILES}},
            "contracts": task_contract_catalog(), "scope": {"kind": "deterministic_development_acceptance",
                "natural_language_understanding_evaluated": False, "model_accuracy_evaluated": False,
                "human_gold_standard": False, "historical_snapshot_evaluated": False, "pdf_bytes_rechecked": False,
                "live_data_fetched": False, "synthetic_expected_results_candidate_generated": False,
                "supported_means": "Selected structured evidence/result is available; it does not endorse the wording of the question.",
                "candidate_override_used": assess is not None},
            "resource_use": {"model_calls": 0 if assess is None else None,
                             "paid_api_calls": 0 if assess is None else None, "gpu_hours": 0 if assess is None else None,
                             "note": "Built-in evaluator uses deterministic Python only; injected test candidates are not audited. Not a model cost or latency benchmark."},
            "automatic_approval": False, "summary": summarize(results), "cases": results}


def validate_report(root: Path, report: dict):
    """Replay the registered catalog and compare every recorded check and hash."""
    require(type(report) is dict, "Expected an acceptance report object")
    try:
        generated = datetime.fromisoformat(report["generated_at"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError("Invalid report generation time") from exc
    require(generated.tzinfo is not None and generated <= datetime.now(timezone.utc), "Invalid report generation time")
    expected = build_report(root)
    expected["generated_at"] = report["generated_at"]
    require(digest(expected) == digest(report), "Report differs from complete registered-case replay")
    return deepcopy(report)


def output_path(root, output):
    root = Path(root).resolve()
    for directory in (root/".run", root/".run/reproductions"):
        require(not directory.is_symlink() and not (hasattr(directory, "is_junction") and directory.is_junction()), "Output directories cannot be aliases")
    target = validate_write_path(root, output)
    require(target.parent == (root/".run/reproductions").resolve() and target.suffix.lower() == ".json"
            and not target.exists(), "Create a new .json directly inside .run/reproductions")
    return target


def run(output=None):
    selected = Path(output or "research-acceptance-report.json")
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = ROOT/".run/reproductions"/selected
    target = output_path(ROOT, selected)
    report = build_report(ROOT)
    target = output_path(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(target), "summary": report["summary"]}, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON filename in .run/reproductions")
    args = parser.parse_args(argv)
    try:
        report = run(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Task acceptance failed: " + str(exc).splitlines()[0] + "\n")
    if report["summary"]["failed"]:
        parser.exit(2, "Development expectations failed; all failures remain in the saved report.\n")


if __name__ == "__main__":
    main()
