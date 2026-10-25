"""Replay fictional enterprise research workflows using registered official facts.

Uses only ResearchTaskStore's public workflow interfaces. Simulated principals
are not real authentication or human review. No network calls or financial
values are invented; the output database and exports must be new isolated paths.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.experiment_paths import validate_experiment_paths
from creditlens.research_task_store import ResearchTaskStore
from creditlens.workflow import Actor
from fastapi import HTTPException
from scripts.verify_research_export import verify_export


SEED_PATH = "data/demo/research-scenarios.json"
SCENARIO_IDS = ("profit-cash-flow-handoff", "scope-correction-resubmission", "insufficient-capital-scope")


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate scenario JSON key")
        result[key] = value
    return result


def load_scenarios(root: Path) -> dict:
    path = Path(root)/SEED_PATH
    if not path.resolve().is_relative_to(Path(root).resolve()) or path.stat().st_size > 512*1024:
        raise ValueError("Scenario registration is outside the workspace or too large")
    payload = path.read_bytes()
    if len(payload) > 512*1024:
        raise ValueError("Scenario registration is too large")
    seed = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    _json_bytes(seed)
    if (seed.get("schema_version") != 1 or seed.get("provenance", {}).get("business_scenarios") != "synthetic"
            or seed["provenance"].get("real_client_or_university_collaboration") is not False
            or seed["provenance"].get("measured_customer_outcome") is not False
            or [s.get("id") for s in seed.get("scenarios", [])] != list(SCENARIO_IDS)):
        raise ValueError("Expected the three explicitly synthetic registered scenarios")
    actors = seed["actors"]
    if (actors["analyst"]["role"] != "analyst" or actors["reviewer"]["role"] != "reviewer"
            or actors["analyst"]["user_id"] == actors["reviewer"]["user_id"]
            or not actors["tenant_id"].startswith("synthetic-")):
        raise ValueError("Scenario actors must be distinct simulated analyst and reviewer principals")
    for scenario in seed["scenarios"]:
        if (scenario.get("synthetic") is not True or type(scenario.get("include_cash_flow_bridge")) is not bool
                or not scenario["user_question"].startswith("【模拟业务】")
                or not re.fullmatch(r"[a-z0-9-]+", scenario["id"])
                or not scenario["steps"]):
            raise ValueError("Invalid scenario identity or provenance")
        if any(step.get("action") not in {"update", "submit", "approve", "return"} for step in scenario["steps"]):
            raise ValueError("Unsupported replay action")
    return seed


def _reference_map(task: dict) -> dict[str, dict]:
    observations = task["bundle"]["observations"]
    mapping = {f"{o['issuer_id']}.{o['metric']}.{o['year']}": o for o in observations}
    if len(mapping) != 8:
        raise ValueError("Replay requires the eight registered unique financial observations")
    return mapping


def _update_arguments(task: dict, step: dict) -> tuple[list, list]:
    refs = _reference_map(task)
    overrides = {}
    for item in step["decision_overrides"]:
        ref = item["reference"]
        if ref not in refs or ref in overrides:
            raise ValueError("Unknown or repeated scenario evidence reference")
        overrides[ref] = item
    decisions = [{"observation_id": o["id"], "decision": overrides.get(ref, {}).get("decision", "adopted"),
                  "note": overrides.get(ref, {}).get("note", step["adoption_note"])} for ref, o in refs.items()]
    hypotheses = []
    for item in step["hypotheses"]:
        if any(ref not in refs for ref in item["evidence_refs"]):
            raise ValueError("Hypothesis refers outside registered facts")
        hypotheses.append({"id": item["id"], "text": item["text"], "status": item["status"], "note": item["note"],
                           "evidence_ids": [refs[ref]["id"] for ref in item["evidence_refs"]]})
    return decisions, hypotheses


def _verify_outcome(task: dict, scenario: dict) -> dict:
    expected = scenario["expected"]
    if (task["status"] != expected["status"] or len(task["snapshots"]) != expected["snapshot_count"]
            or [r["decision"] for r in task["review_history"]] != expected["review_decisions"]):
        raise ValueError("Replay did not reach its registered workflow outcome")
    comparisons = {f"{r['issuer_id']}.{r['metric']}": r for r in task["comparison"]["annual_changes"]}
    blocked = sorted(key for key, row in comparisons.items() if row["status"] == "blocked")
    if blocked != sorted(expected["blocked_comparisons"]):
        raise ValueError("Rejected or missing evidence did not block the expected calculation")
    if any(row["status"] != "side_by_side_only" or row["ranking"] is not None for row in task["comparison"]["cross_company"]):
        raise ValueError("Scenario replay must never create a cross-company ranking")
    direction = None
    if expected["cash_flow_direction_check"]:
        if expected["cash_flow_direction_check"] != "AAPL_net_income_up_cfo_down":
            raise ValueError("Unregistered direction diagnostic")
        change = next(x for x in task["cash_flow_bridge"]["changes"] if x["issuer_id"] == "AAPL")
        net_income = next(x for x in change["groups"] if x["group"] == "net_income")
        if change["status"] != "reconciled" or Decimal(net_income["contribution"]) <= 0 or Decimal(change["reported_cfo_change"]) >= 0:
            raise ValueError("The official facts do not support the scenario's observed direction")
        direction = {"issuer_id": "AAPL", "net_income_change": net_income["contribution"],
                     "cfo_change": change["reported_cfo_change"], "unit": change["unit"],
                     "source_sha256": change["source_sha256"], "scope": "Observed accounting movement; no causal conclusion"}
    return {"status": task["status"], "blocked_comparisons": blocked, "direction_diagnostic": direction,
            "cross_company_ranking": None, "automatic_approval": False}


def _write_new(path: Path, value) -> str:
    payload = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def replay(root: Path, database: Path, output_dir: Path) -> dict:
    root = Path(root).resolve()
    # A database is also an output. Reuse the common preflight for public paths,
    # aliases, registered previews and separation from the export subtree.
    paths = validate_experiment_paths(root, database, output_dir,
                                      read_only_dirs=(root/".run/issuer-evaluation",))
    if paths.output.exists() or paths.local_dir.exists():
        raise ValueError("Replay database and export directory must both be new; no overwrite is supported")
    if paths.output.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError("Use an explicit isolated SQLite database filename")
    seed = load_scenarios(root)
    actor_seed = seed["actors"]
    analyst = Actor(actor_seed["analyst"]["user_id"], actor_seed["tenant_id"], "analyst")
    reviewer = Actor(actor_seed["reviewer"]["user_id"], actor_seed["tenant_id"], "reviewer")
    paths.output.parent.mkdir(parents=True, exist_ok=True)
    with paths.output.open("xb"):
        pass  # Exclusive reservation; no existing database is opened or migrated.
    paths.local_dir.mkdir(parents=True, exist_ok=False)
    store = ResearchTaskStore(paths.output, root)
    artifacts = []
    for scenario in seed["scenarios"]:
        task = store.create(analyst, {
            "bundle_id": seed["provenance"]["bundle_id"], "question": scenario["user_question"],
            "selection_reason": scenario["selection_reason"], "material_mode": "current_verified_materials",
            "as_of": None, "include_cash_flow_bridge": scenario["include_cash_flow_bridge"],
        }, "synthetic-replay:"+scenario["id"])
        for step in scenario["steps"]:
            action = step["action"]
            if action == "update":
                decisions, hypotheses = _update_arguments(task, step)
                task = store.update(task["id"], analyst, task["revision"], decisions, hypotheses,
                                    step["coverage_note"], step.get("bridge_review_note"))
            else:
                actor = analyst if action == "submit" else reviewer
                sha = None if action == "submit" else task["snapshot_sha256"]
                task = store.act(task["id"], actor, task["revision"], action, step["note"], sha)
        outcome = _verify_outcome(task, scenario)
        snapshots = []
        for index, snapshot in enumerate(task["snapshots"], 1):
            exported = store.export(task["id"], reviewer, snapshot["id"])
            verified = verify_export(exported)
            latest = snapshot["id"] == task["snapshot_id"]
            relative = f"{scenario['id']}.json" if latest else f"history/{scenario['id']}-snapshot-{index}.json"
            file_sha = _write_new(paths.local_dir/relative, exported)
            snapshots.append({**snapshot, "export_path": relative, "file_sha256": file_sha,
                              "export_consistency_checked": verified["ok"], "latest": latest,
                              "review_decision": exported["review"]["decision"] if exported["review"] else None})
        artifacts.append({"scenario_id": scenario["id"], "title": scenario["title"], "synthetic": True,
                          "task_id": task["id"], "tenant_id": task["tenant_id"], "store_version": task["store_version"],
                          "outcome": outcome, "snapshot_id": task["snapshot_id"], "snapshot_sha256": task["snapshot_sha256"],
                          "snapshots": snapshots, "events": deepcopy(task["events"]),
                          "financial_source_versions": [{k: source[k] for k in ("id", "issuer_id", "url", "sha256")}
                                                        for source in task["bundle"]["source_versions"]]})
    manifest = {"schema_version": 1, "id": seed["id"], "scenario_seed_sha256": _sha(seed),
                "provenance": deepcopy(seed["provenance"]), "actors": deepcopy(seed["actors"]),
                "replay_method": "ResearchTaskStore.create/update/act/export; simulated principals; no SQL state mutation",
                "reproducibility": "Scenario actions, references and expected outcomes are deterministic; UUIDs/timestamps and exported bytes are not fixed",
                "artifacts": artifacts, "expected_return_is_successful_replay": True,
                "automatic_approval": False, "database_published": False,
                "scope": "Synthetic business workflow over registered public facts; unsigned exports, no real human review or production claim"}
    _write_new(paths.local_dir/"manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT/".run/reproductions/research-scenarios/replay.sqlite")
    parser.add_argument("--output-dir", type=Path, default=ROOT/".run/reproductions/research-scenarios/exports")
    args = parser.parse_args(argv)
    try:
        manifest = replay(ROOT, args.database, args.output_dir)
        print(json.dumps({"status": "replayed", "synthetic": True,
                          "scenarios": [{"id": a["scenario_id"], "status": a["outcome"]["status"],
                                         "snapshots": len(a["snapshots"])} for a in manifest["artifacts"]],
                          "manifest": str(args.output_dir/"manifest.json")}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, HTTPException) as exc:
        reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
        print(json.dumps({"status": "refused", "reason": reason,
                          "http_status": exc.status_code if isinstance(exc, HTTPException) else None}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
