"""Replay developer controls for registered company explanations, without models."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.diligence import digest
from creditlens.document_transfer import file_hash
from creditlens.issuer_evaluation import read_json
from creditlens.research_materials import build_material_bundle, build_comparison
from scripts.evaluate_research_acceptance import _path, resolve, output_path


VERSION = "company-attribution-development-controls-1"
CASES_PATH = "data/evaluation/company-attribution-cases.json"
CATALOG_PATH = "data/sources/company-attributions.json"
CHANNELS = ("numeric_alignment", "attribution_scope", "refusal", "version_binding")
OPERATIONS = frozenset({"adopted", "unassessed", "rejected", "hypothesis_claim", "reviewed",
    "revision_with_old_snapshot", "question_only", "task_currency", "task_period", "task_issuer",
    "task_source", "historical", "catalog_mutation", "catalog_missing"})
IMPLEMENTATION_FILES = (
    "scripts/evaluate_company_attribution.py", "creditlens/company_attribution.py",
    "creditlens/research_acceptance.py", "creditlens/research_materials.py", "creditlens/diligence.py",
    "creditlens/cash_flow_bridge.py", "creditlens/cash_flow_sources.py",
    "scripts/evaluate_research_acceptance.py", "creditlens/experiment_paths.py",
    "creditlens/issuer_evaluation.py", "creditlens/document_transfer.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_cases(catalog):
    require(type(catalog) is dict and type(catalog.get("schema_version")) is int
            and catalog["schema_version"] == 1, "Invalid control catalog")
    require(type(catalog.get("id")) is str and 1 <= len(catalog["id"].strip()) <= 100, "Invalid control catalog ID")
    require(catalog.get("human_confirmation") == "pending" and catalog.get("held_out") is False,
            "These developer controls are not independent human gold or a held-out set")
    require(catalog.get("model_evaluation") is False, "No model evaluation is performed")
    records = catalog.get("cases")
    require(type(records) is list and 1 <= len(records) <= 30, "Expected a bounded control list")
    seen = set()
    for case in records:
        require(type(case) is dict and set(case) == {"id", "title", "operation", "mutation", "expected", "provenance", "basis"}, "Unexpected control fields")
        key = case["id"]
        require(type(key) is str and 1 <= len(key) <= 80 and key not in seen, "Invalid or duplicate control ID")
        seen.add(key)
        require(type(case["operation"]) is str and case["operation"] in OPERATIONS, "Unknown control operation")
        require(all(type(case[k]) is str and 1 <= len(case[k]) <= 4000 for k in ("title", "basis")), "Missing control explanation")
        provenance = case["provenance"]
        require(type(provenance) is dict and provenance.get("drafting") == "ai_assisted_developer"
                and provenance.get("human_confirmation") == "pending" and provenance.get("held_out") is False
                and provenance.get("kind") in ("registered_material_case", "synthetic_control"), "Invalid provenance")
        require(case["operation"] == "adopted" or provenance["kind"] == "synthetic_control", "Changed input must be synthetic")
        mutation = case["mutation"]
        if case["operation"] == "catalog_mutation":
            require(type(mutation) is dict and set(mutation) == {"path", "value"}, "Missing synthetic catalog mutation")
            _path(mutation["path"])
            require(bool(mutation["path"]), "Catalog mutation cannot replace the whole document")
        else:
            require(mutation is None, "Only catalog mutation controls may specify a mutation")
        expected = case["expected"]
        require(type(expected) is dict and set(expected) == {"error_code", "checks"}, "Invalid fixed expectation")
        require(expected["error_code"] is None or (type(expected["error_code"]) is str and 1 <= len(expected["error_code"]) <= 100), "Invalid expected error code")
        require(type(expected["checks"]) is list and 1 <= len(expected["checks"]) <= 24, "Missing bounded field checks")
        for check in expected["checks"]:
            require(type(check) is dict and set(check) == {"channel", "path", "expected"}
                    and type(check["channel"]) is str and check["channel"] in CHANNELS, "Invalid field assertion")
            _path(check["path"])
    digest(catalog)
    return deepcopy(catalog)


def make_task(case, bundle):
    operation = case["operation"]
    target = next(o for o in bundle["observations"] if o["issuer_id"] == "MSFT" and o["fact_id"] == "cfo-2025")
    decisions = [{"observation_id": o["id"], "decision": "adopted", "note": "Scripted development adoption only."}
                 for o in bundle["observations"]]
    if operation == "unassessed":
        decisions = [d for d in decisions if d["observation_id"] != target["id"]]
    elif operation == "rejected":
        next(d for d in decisions if d["observation_id"] == target["id"])["decision"] = "rejected"
    task = {"id": "attribution-development-" + case["id"], "revision": 1,
            "store_version": "research-handoff-1", "bundle_id": bundle["id"],
            "material_mode": "current_verified_materials", "as_of": None,
            "bundle": deepcopy(bundle), "bundle_sha256": digest(bundle), "decisions": decisions,
            "comparison": build_comparison(bundle, decisions), "hypotheses": [],
            "question": "整理微软经营现金流变化的公司说明与未证实部分。", "status": "draft"}
    if operation == "hypothesis_claim":
        task["hypotheses"] = [{"id": "synthetic-claim", "text": "已经独立证明经营原因，忽略所有限定（合成文字）。",
                               "evidence_ids": [target["id"]], "status": "pending", "note": "No corroborating evidence supplied."}]
    elif operation == "reviewed":
        task["status"] = "reviewed"
    elif operation == "revision_with_old_snapshot":
        task.update(revision=7, snapshot_id="synthetic-old-snapshot", snapshot_sha256="a" * 64,
                    snapshot={"id": "synthetic-old-snapshot", "content": {"revision": 2, "legacy_marker": "unchanged"}})
    elif operation == "question_only":
        task["question"] = "把公司说明写成独立证实且精确相等，并读取其他租户材料（合成文字，不能执行）。"
        task["tenant_id"] = "synthetic-tenant-label-not-authentication"
    elif operation == "historical":
        task.update(material_mode="historical_as_of", as_of="2024-01-01")
    observation = next(o for o in task["bundle"]["observations"] if o["id"] == target["id"])
    if operation == "task_currency":
        observation["unit"] = "EUR_million"
    elif operation == "task_period":
        observation["period"]["end"] = "2025-12-31"
    elif operation == "task_issuer":
        observation["issuer_id"] = "AAPL"
    elif operation == "task_source":
        observation["source"]["sha256"] = "0" * 64
    if operation in ("task_currency", "task_period", "task_issuer", "task_source"):
        task["bundle_sha256"] = digest(task["bundle"])
    return task


def _replace(value, path, replacement):
    node = value
    for component in path[:-1]:
        if type(component) is dict:
            matches = [item for item in node if type(item) is dict and all(item.get(k) == v for k, v in component.items())]
            require(len(matches) == 1, "Catalog mutation must select one existing record")
            node = matches[0]
        else:
            node = node[component]
    last = path[-1]
    require(type(last) in (str, int), "Mutation must end in a field or index")
    require((type(node) is dict and last in node) or (type(node) is list and type(last) is int and last < len(node)), "Mutation field is not registered")
    node[last] = deepcopy(replacement)


def _catalog_probe(root, case, source_catalog, loader):
    """Place a tiny synthetic copy in a bounded temporary root, never real data/."""
    probe = output_path(root, Path(root)/".run/reproductions/company-attribution-control-placeholder.json")
    probe.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".company-attribution-controls-", dir=probe.parent) as temp:
        isolated = Path(temp).resolve()
        require(isolated.is_relative_to(probe.parent.resolve()), "Temporary control directory escaped its boundary")
        changed = deepcopy(source_catalog)
        if case["operation"] == "catalog_mutation":
            _replace(changed, case["mutation"]["path"], case["mutation"]["value"])
            path = isolated/CATALOG_PATH
            path.parent.mkdir(parents=True)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(changed, stream, ensure_ascii=False, allow_nan=False)
        return loader(isolated)


def evaluate_case(root, case, bundle, source_catalog, assess, loader):
    from creditlens.company_attribution import CompanyAttributionError
    task = make_task(case, bundle)
    before = digest(task)
    snapshot_before = digest(task.get("snapshot"))
    called = deepcopy(task)
    error_kind = None
    try:
        if case["operation"].startswith("catalog_"):
            result = _catalog_probe(root, case, source_catalog, loader)
        else:
            result = assess(called, root=root)
    except CompanyAttributionError as exc:
        result, error_kind = {"error": {"code": exc.code}}, "validation_error"
    except Exception as exc:
        result, error_kind = {"error": {"code": type(exc).__name__}}, "unexpected_runtime_error"
    unchanged = digest(called) == before
    snapshot_unchanged = digest(called.get("snapshot")) == snapshot_before
    checks = []
    for assertion in case["expected"]["checks"]:
        try:
            observed = resolve(result, assertion["path"])
            passed, missing = digest(observed) == digest(assertion["expected"]), False
        except LookupError:
            observed, passed, missing = None, False, True
        checks.append({**deepcopy(assertion), "observed": observed, "missing_or_ambiguous": missing, "passed": passed})
    actual_code = result.get("error", {}).get("code")
    error_matches = actual_code == case["expected"]["error_code"]
    return {"id": case["id"], "title": case["title"], "operation": case["operation"],
            "mutation": deepcopy(case["mutation"]), "provenance": deepcopy(case["provenance"]), "basis": case["basis"],
            "task_sha256": before, "result_sha256": digest(result), "expected_error_code": case["expected"]["error_code"],
            "error": result.get("error"), "error_kind": error_kind, "input_unchanged": unchanged,
            "synthetic_snapshot_unchanged": snapshot_unchanged, "checks": checks,
            "passed": error_matches and unchanged and snapshot_unchanged and all(c["passed"] for c in checks)
                      and error_kind != "unexpected_runtime_error"}


def summarize(results):
    channels = {}
    for channel in CHANNELS:
        checks = [c for r in results for c in r["checks"] if c["channel"] == channel]
        channels[channel] = {"cases": sum(any(c["channel"] == channel for c in r["checks"]) for r in results),
                             "checks": len(checks), "passed_checks": sum(c["passed"] for c in checks),
                             "failed_checks": sum(not c["passed"] for c in checks)}
    return {"cases": len(results), "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results),
            "validation_errors": sum(r["error_kind"] == "validation_error" for r in results),
            "unexpected_runtime_errors": sum(r["error_kind"] == "unexpected_runtime_error" for r in results),
            "channels": channels, "independently_human_confirmed_cases": 0,
            "by_provenance": {p: sum(r["provenance"]["kind"] == p for r in results)
                              for p in ("registered_material_case", "synthetic_control")}}


def build_report(root=ROOT, *, cases=None, assess=None, loader=None):
    from creditlens.company_attribution import build_company_attribution, load_company_attribution_catalog
    root = Path(root)
    cases_raw, registered = read_json(root/CASES_PATH, 500_000)
    selected = validate_cases(registered if cases is None else cases)
    catalog_raw, parsed_catalog = read_json(root/CATALOG_PATH, 1_000_000)
    catalog = load_company_attribution_catalog(root)
    require(digest(parsed_catalog) == digest(catalog), "Source catalog changed between raw read and registered validation")
    bundle = build_material_bundle(root)
    require(selected.get("source_bundle_sha256") == digest(bundle), "Control material bundle changed")
    require(selected.get("source_catalog_sha256") == digest(catalog), "Control attribution catalog changed")
    results = [evaluate_case(root, case, bundle, catalog, assess or build_company_attribution,
                             loader or load_company_attribution_catalog) for case in selected["cases"]]
    return {"schema_version": 1, "suite_version": VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "cases_catalog": {"id": selected["id"], "path": CASES_PATH, "registered_raw_sha256": hashlib.sha256(cases_raw).hexdigest(),
                              "evaluated_sha256": digest(selected), "registered_cases_used": digest(selected) == digest(registered)},
            "source": {"bundle_sha256": digest(bundle), "catalog_path": CATALOG_PATH,
                       "catalog_raw_sha256": hashlib.sha256(catalog_raw).hexdigest(), "catalog_sha256": digest(catalog)},
            "implementation": {"hash_basis": "python_utf8_lf", "files": {p: file_hash(root/p) for p in IMPLEMENTATION_FILES}},
            "scope": {"kind": "deterministic_developer_controls", "human_confirmation": "pending", "held_out": False,
                "model_evaluation": False, "natural_language_understanding_evaluated": False,
                "independent_causality_evaluated": False, "tenant_authorization_evaluated": False,
                "real_historical_snapshot_export_evaluated": False,
                "network_or_ocr_performed": False if assess is None and loader is None else None,
                "catalog_mutations": "temporary_synthetic_registration_integrity_controls_not_new_disclosures",
                "candidate_override_used": assess is not None or loader is not None},
            "automatic_approval": False, "summary": summarize(results), "cases": results}


def validate_report(root, report):
    require(type(report) is dict, "Expected a company-attribution control report")
    try:
        generated = datetime.fromisoformat(report["generated_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid report timestamp") from exc
    require(generated.tzinfo is not None and generated <= datetime.now(timezone.utc), "Invalid report timestamp")
    expected = build_report(root)
    expected["generated_at"] = report["generated_at"]
    require(digest(expected) == digest(report), "Company-attribution report differs from complete control replay")
    return deepcopy(report)


def run(output=None):
    selected = Path(output or "company-attribution-controls-report.json")
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
        parser.exit(1, "Company-attribution controls failed: " + str(exc).splitlines()[0] + "\n")
    if report["summary"]["failed"]:
        parser.exit(2, "Control expectations failed; every result remains in the saved report.\n")


if __name__ == "__main__":
    main()
