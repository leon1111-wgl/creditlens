"""Replay synthetic reference observations in a temporary, isolated database.

No prior answer is copied: the values and boxes below are deliberately invented
and do not claim to describe the official page selected from the task catalog.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from creditlens.diligence import digest
from creditlens.experiment_paths import validate_write_path
from creditlens.reference_contract import build_reference_catalog
from creditlens.reference_review_store import ReferenceReviewStore
from creditlens.workflow import Actor

IMPLEMENTATION_FILES = ("creditlens/reference_review_store.py", "creditlens/reference_contract.py",
                        "scripts/replay_reference_review.py")
SCOPE = {"observations": "synthetic_invented_values_and_boxes", "principals": "synthetic",
         "source_tasks": "previously_observed_development_catalog", "independent_human_annotations": 0,
         "human_gold": False, "new_ocr_calls": 0, "model_calls": 0, "network_calls": 0,
         "main_database_modified": False, "identity_independence_verified": False,
         "existing_evaluation_references_replaced": False}


def _observation(value="123.45"):
    return {"status": "answerable", "raw_value": value,
            "amount_bbox": {"x0": 10, "top": 10, "x1": 50, "bottom": 20},
            "unit_text": "Synthetic units, not transcribed from the report",
            "currency_text": None, "section_text": "Synthetic section",
            "section_note": None, "reason": "Synthetic workflow fixture, not an independent human observation"}


def build_replay(root: Path) -> dict:
    root = Path(root)
    catalog = build_reference_catalog(root)
    first, second, reviewer, viewer = (Actor("synthetic-"+name, "synthetic-reference-replay", role)
                                      for name, role in (("first", "analyst"), ("second", "analyst"),
                                                         ("reviewer", "reviewer"), ("viewer", "viewer")))
    checks = []
    counter = iter(range(1, 100))

    def check(name, actual, expected):
        passed = actual == expected
        checks.append({"id": name, "actual": actual, "expected": expected, "passed": passed})
        if not passed:
            raise ValueError("Synthetic reference replay check failed: " + name)

    def refusal(name, call, status):
        try:
            call()
        except HTTPException as exc:
            check(name, exc.status_code, status)
        else:
            raise ValueError("Synthetic refusal was unexpectedly allowed: " + name)

    # Fixed IDs/times belong only to this synthetic replay. Application stores
    # keep normal server-generated IDs and timestamps.
    with tempfile.TemporaryDirectory(prefix="creditlens-reference-replay-") as directory, \
            patch("creditlens.reference_review_store.uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(counter))), \
            patch.object(ReferenceReviewStore, "now", return_value="2026-10-26T00:00:00+00:00"):
        path = Path(directory)/"synthetic-reference.sqlite3"
        store = ReferenceReviewStore(path, root)
        request = {"task_id": catalog["tasks"][0]["id"], "expected_catalog_sha256": catalog["catalog_sha256"]}
        created = store.create(first, request, "create-round-1")
        a = store.submit(created["id"], first, 1, _observation(), "first-round-1")
        hidden = store.get(created["id"], reviewer)
        check("first_content_hidden_from_reviewer", hidden["observations"][0]["observation"], None)
        check("first_comparison_hidden", hidden["comparison"], None)
        check("first_hash_hidden", a["observations"][0]["observation"]["observation_sha256"] in json.dumps(hidden), False)
        refusal("viewer_cannot_submit", lambda: store.submit(created["id"], viewer, 2, _observation(), "viewer"), 403)
        refusal("same_principal_cannot_fill_second", lambda: store.submit(created["id"], first, 2, _observation(), "duplicate"), 403)
        b = store.submit(created["id"], second, 2, _observation("456.78"), "second-round-1")
        check("different_values_remain_disagreement", b["status"], "disagreed")
        refusal("cannot_force_disagreement_to_freeze", lambda: store.freeze(b["id"], reviewer, 3, "Synthetic attempt to freeze disagreement"), 409)
        closed = store.close(b["id"], reviewer, 3, "Close the synthetic disagreement without editing either observation")
        new = store.create(first, {**request, "previous_review_id": closed["id"],
                                  "reason": "Start another synthetic round; previous observations stay unchanged"}, "create-round-2")
        check("new_round_has_no_copied_observations", new["observations"], [])
        store.submit(new["id"], first, 1, _observation(), "first-round-2")
        second_observation = _observation(); second_observation["reason"] = "Another synthetic note; the same invented content was supplied"
        matched = store.submit(new["id"], second, 2, second_observation, "second-round-2")
        check("same_content_different_free_note_agrees", matched["status"], "agreed")
        check("free_note_difference_retained", matched["comparison"]["notes_differ"], True)
        first_reviewer = Actor(first.user_id, first.tenant_id, "reviewer")
        refusal("observer_role_change_cannot_review", lambda: store.freeze(new["id"], first_reviewer, 3, "Synthetic role change is not independence"), 403)
        frozen = store.freeze(new["id"], reviewer, 3, "Freeze only the synthetic agreement candidate, never a human gold answer")
        exported = store.export(new["id"], viewer)
        check("freeze_does_not_create_human_gold", exported["human_gold"], False)
        old_digest = digest(exported)
        third_round = store.create(first, {**request, "previous_review_id": new["id"],
                                          "reason": "Synthetic correction round preserves the preceding frozen candidate"}, "create-round-3")
        check("frozen_export_unchanged_after_new_round", digest(store.export(new["id"], viewer)), old_digest)
        check("old_submit_replay_not_augmented", store.submit(created["id"], first, 1, _observation(), "first-round-1"), a)
        # Keep the check concise; the original response equality was tested above.
        checks[-1].update(actual="original_first_response", expected="original_first_response")
        rebuilt = ReferenceReviewStore(path, root)
        with patch.object(rebuilt, "_catalog", side_effect=AssertionError("History must not read live source files")):
            check("rebuilt_store_export_matches", digest(rebuilt.export(new["id"], viewer)), old_digest)
        original_task = store.get(third_round["id"], viewer)
        with patch.object(store, "_catalog", side_effect=HTTPException(409, {"code": "reference_registration_unavailable"})):
            refusal("missing_registry_blocks_new_observation", lambda: store.submit(third_round["id"], first, 1, _observation(), "unavailable"), 409)
            closed_without_registry = store.close(third_round["id"], reviewer, 1, "Close an incomplete synthetic round while the current registry is unavailable")
        check("missing_registry_does_not_block_history_closure", closed_without_registry["status"], "closed")
        check("closure_keeps_source_snapshot", closed_without_registry["task"], original_task["task"])
        checks[-1].update(actual=digest(closed_without_registry["task"]), expected=digest(original_task["task"]))
        artifact = {"schema_version": 1, "replay_version": "reference-review-replay-1", "scope": deepcopy(SCOPE),
                    "catalog_sha256": catalog["catalog_sha256"], "catalog_task_count": len(catalog["tasks"]),
                    "implementation_sha256": {name: hashlib.sha256((root/name).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest() for name in IMPLEMENTATION_FILES},
                    "implementation_hash_basis": "python_utf8_lf", "checks": checks,
                    "summary": {"checks": len(checks), "passed": sum(c["passed"] for c in checks),
                                "failed": sum(not c["passed"] for c in checks), "synthetic_rounds": 3,
                                "synthetic_observations": 4, "frozen_candidates": 1, "human_gold": 0},
                    "disagreement_round": closed, "frozen_candidate": exported,
                    "final_closed_round": closed_without_registry,
                    "limitations": ["All submitted values, coordinates and principals here are synthetic; no real person supplied or independently confirmed an answer.",
                                    "The 40 source queries concern previously observed development material; public prior answers remain accessible elsewhere.",
                                    "A later round starts empty but its participants may have seen earlier rounds. The service does not authenticate their independence.",
                                    "Stored hashes detect inconsistent contents, not a database administrator rewriting records and all hashes."]}
    return artifact


def output_path(root: Path, path: Path) -> Path:
    root, path = Path(root).resolve(), Path(path)
    if not path.is_absolute():
        path = root/".run/reproductions"/path
    result = validate_write_path(root, path)
    if result.parent != (root/".run/reproductions").resolve() or result.suffix != ".json":
        raise ValueError("Output must be a new JSON file directly inside .run/reproductions")
    if result.exists() or result.is_symlink():
        raise ValueError("Output already exists")
    return result


def run(root=ROOT, output=None):
    target = output_path(root, output or Path("reference-review-replay.json"))
    report = build_replay(root)
    target = output_path(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return {"output": str(target), "summary": report["summary"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON filename in .run/reproductions")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(output=args.output), ensure_ascii=False))
    except (ValueError, OSError, HTTPException):
        parser.exit(1, "Synthetic reference replay failed; no source files or application database were changed.\n")


if __name__ == "__main__":
    main()
