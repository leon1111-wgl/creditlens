"""Synthetic workflow tests, not financial, model or user-effect evaluation."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from creditlens.research_task_store import (
    ResearchTaskAction, ResearchTaskCreate, ResearchTaskStore, ResearchTaskUpdate,
)
from creditlens.workflow import Actor


def synthetic_bundle():
    observations = []
    for issuer, source_hash in (("A", "a" * 64), ("B", "b" * 64)):
        for year in (2024, 2025):
            for metric in ("cfo", "capex"):
                fact_id = f"{metric}-{year}"
                observations.append({"id": f"{issuer}:{source_hash}:{fact_id}", "issuer_id": issuer,
                                     "source_version_id": f"{issuer}:{source_hash}", "fact_id": fact_id,
                                     "year": year, "metric": metric, "normalized_value": "10",
                                     "source": {"sha256": source_hash}})
    return {"schema_version": 1, "id": "msft-aapl-fy2025", "title": "Synthetic workflow fixture",
            "material_mode": "current_verified_materials", "observations": observations,
            "source_versions": [{"id": "A:" + "a" * 64, "historical_version_verified": False},
                                {"id": "B:" + "b" * 64, "historical_version_verified": False}],
            "contract": {"version": "synthetic-1", "ranking_allowed": False}, "limitations": ["Synthetic only"]}


def synthetic_comparison(bundle, decisions):
    ids = {o["id"] for o in bundle["observations"]}
    adopted = [d["observation_id"] for d in decisions if d["decision"] == "adopted"]
    rejected = [d["observation_id"] for d in decisions if d["decision"] == "rejected"]
    return {"rule_version": "synthetic-1", "adopted_observation_ids": adopted,
            "rejected_observation_ids": rejected,
            "unassessed_observation_ids": sorted(ids - set(adopted) - set(rejected)),
            "annual_changes": [], "prediction": False, "automatic_approval": False}


class ResearchTaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = synthetic_bundle()
        self.materials = patch.object(ResearchTaskStore, "_material_bundle", side_effect=lambda _: copy.deepcopy(self.bundle))
        self.comparison = patch.object(ResearchTaskStore, "_comparison", side_effect=synthetic_comparison)
        self.material_mock = self.materials.start()
        self.comparison_mock = self.comparison.start()
        self.addCleanup(self.materials.stop)
        self.addCleanup(self.comparison.stop)
        self.store = ResearchTaskStore(self.root / "research.sqlite", self.root)
        self.analyst = Actor("alice", "team-a", "analyst")
        self.editor = Actor("ed", "team-a", "analyst")
        self.reviewer = Actor("ruth", "team-a", "reviewer")
        self.viewer = Actor("view", "team-a", "viewer")
        self.other = Actor("bob", "team-b", "analyst")
        self.payload = {"bundle_id": "msft-aapl-fy2025", "question": "Compare annual cash flow changes",
                        "selection_reason": "Two selected peers and two annual periods",
                        "material_mode": "current_verified_materials", "as_of": None}

    def assert_http(self, status, fn, *args, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status_code, status, caught.exception.detail)
        return caught.exception

    def create(self, key="request-1", actor=None):
        return self.store.create(actor or self.analyst, self.payload, key)

    def decisions(self, rejected=False):
        return [{"observation_id": o["id"], "decision": "rejected" if rejected else "adopted",
                 "note": "Read the registered material; scoped decision"} for o in self.bundle["observations"]]

    def edit(self, task, *, actor=None, hypotheses=None, rejected=False):
        return self.store.update(task["id"], actor or self.analyst, task["revision"], self.decisions(rejected),
                                 hypotheses or [], "Current materials only; different year ends prevent ranking.")

    def submit(self, task=None):
        edited = self.edit(task or self.create())
        return self.store.act(edited["id"], self.analyst, edited["revision"], "submit", "Ready for independent review")

    def test_complete_handoff_locks_entire_content_and_exports_same_snapshot(self):
        task = self.create()
        hypothesis = {"id": "h1", "text": "Working capital might explain the movement", "status": "pending",
                      "evidence_ids": [self.bundle["observations"][0]["id"]], "note": "Need adjustment table"}
        edited = self.edit(task, hypotheses=[hypothesis])
        submitted = self.store.act(task["id"], self.analyst, edited["revision"], "submit", "Check evidence and gaps")
        snapshot = submitted["snapshot"]
        frozen = snapshot["content"]
        for field in ("question", "selection_reason", "bundle", "decisions", "hypotheses", "coverage_note", "comparison"):
            self.assertEqual(frozen[field], submitted[field])
        self.assertFalse(frozen["automatic_approval"])
        self.assertEqual(frozen["hypotheses"], [hypothesis])
        self.assert_http(409, self.edit, submitted)
        approved = self.store.act(task["id"], self.reviewer, submitted["revision"], "approve", "Checked the scoped research",
                                  snapshot["sha256"])
        self.assertEqual(approved["status"], "reviewed")
        self.assertEqual(approved["snapshot"], snapshot)
        exported = self.store.export(task["id"], self.viewer)
        self.assertEqual(exported["snapshot"], snapshot)
        self.assertEqual(exported["review"]["snapshot_sha256"], snapshot["sha256"])
        self.assertFalse(exported["editable_draft_included"])
        self.assertEqual([e["action"] for e in approved["events"]], ["create", "update", "submit", "approve"])
        self.assert_http(409, self.edit, approved)

    def test_return_edit_and_resubmit_keeps_old_snapshot_and_review_stable(self):
        submitted = self.submit()
        snapshot = submitted["snapshot"]
        returned = self.store.act(submitted["id"], self.reviewer, submitted["revision"], "return", "Missing explanation material",
                                 snapshot["sha256"])
        old_export = self.store.export(submitted["id"], self.viewer, snapshot["id"])
        edited = self.edit(returned, rejected=True)
        self.assertEqual(self.store.export(submitted["id"], self.viewer), old_export)
        resubmitted = self.store.act(edited["id"], self.analyst, edited["revision"], "submit", "All rejected items identified as gaps")
        self.assertNotEqual(resubmitted["snapshot"]["sha256"], snapshot["sha256"])
        self.assertNotEqual(resubmitted["snapshot"]["id"], snapshot["id"])
        approved = self.store.act(resubmitted["id"], self.reviewer, resubmitted["revision"], "approve", "Gaps are clearly handed off",
                                 resubmitted["snapshot"]["sha256"])
        self.assertEqual([item["id"] for item in approved["snapshots"]], [snapshot["id"], resubmitted["snapshot"]["id"]])
        self.assertTrue(all(set(item) == {"id", "sha256", "created_at", "submitted_by", "revision"} for item in approved["snapshots"]))
        self.assertEqual(self.store.export(approved["id"], self.viewer, snapshot["id"]), old_export)
        self.assertEqual(self.store.get_snapshot(approved["id"], snapshot["id"], self.viewer)["snapshot"], snapshot)
        self.assertEqual(len(approved["review_history"]), 2)

    def test_creator_submitter_and_any_editor_cannot_review_with_changed_role(self):
        task = self.create()
        edited = self.edit(task, actor=self.editor)
        submitter = Actor("sam", "team-a", "analyst")
        submitted = self.store.act(task["id"], submitter, edited["revision"], "submit", "Submit colleague's research")
        for principal in ("alice", "ed", "sam"):
            self.assert_http(403, self.store.act, task["id"], Actor(principal, "team-a", "reviewer"), submitted["revision"],
                             "approve", "Try self review", submitted["snapshot"]["sha256"])
        self.assertEqual(self.store.get(task["id"], self.viewer)["status"], "in_review")

    def test_tenant_and_task_snapshot_isolation(self):
        first = self.submit()
        second = self.create("request-2")
        for reader in (self.store.get, self.store.export):
            self.assert_http(404, reader, first["id"], self.other)
        self.assert_http(404, self.store.get_snapshot, first["id"], first["snapshot"]["id"], self.other)
        self.assert_http(404, self.store.get_snapshot, second["id"], first["snapshot"]["id"], self.viewer)
        self.assert_http(404, self.store.export, second["id"], self.viewer, first["snapshot"]["id"])
        self.assertEqual(self.store.list(self.other), [])
        self.assertEqual(len(self.store.list(self.viewer)), 2)
        other_task = self.create(actor=self.other)
        self.assertNotEqual(other_task["id"], first["id"])

    def test_idempotency_retries_return_current_task_without_live_material_read(self):
        first = self.create()
        self.edit(first)
        self.material_mock.side_effect = HTTPException(409, "Source removed")
        retry = self.create()
        self.assertEqual(retry["id"], first["id"])
        self.assertEqual(retry["revision"], 2)
        changed = {**self.payload, "question": "Different research question"}
        self.assert_http(409, self.store.create, self.analyst, changed, "request-1")
        self.assert_http(409, self.create, actor=self.editor)

    def test_concurrent_creation_and_revision_writes_have_one_winner(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.create(), range(2)))
        self.assertEqual(results[0]["id"], results[1]["id"])
        task = results[0]
        def update_once(_):
            try:
                return self.edit(task)["revision"]
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update_once, range(2)))
        self.assertEqual(sorted(results), [2, 409])
        self.assertEqual(len(self.store.get(task["id"], self.viewer)["events"]), 2)

    def test_submission_needs_all_eight_decisions_and_coverage(self):
        task = self.create()
        self.assert_http(422, self.store.act, task["id"], self.analyst, 1, "submit", "Incomplete research")
        partial = self.store.update(task["id"], self.analyst, 1, self.decisions()[:-1], [], "Current materials limitations")
        self.assert_http(422, self.store.act, task["id"], self.analyst, partial["revision"], "submit", "One item missing")
        empty_coverage = self.store.update(task["id"], self.analyst, partial["revision"], self.decisions(), [], "short")
        self.assert_http(422, self.store.act, task["id"], self.analyst, empty_coverage["revision"], "submit", "No sufficient coverage")
        self.assert_http(409, self.store.export, task["id"], self.viewer)

    def test_rejected_observations_and_zero_hypotheses_can_be_handed_off(self):
        edited = self.edit(self.create(), rejected=True)
        submitted = self.store.act(edited["id"], self.analyst, edited["revision"], "submit", "Handoff explicit evidence gaps")
        self.assertEqual(submitted["hypotheses"], [])
        self.assertEqual(submitted["comparison"]["adopted_observation_ids"], [])
        self.assertEqual(len(submitted["comparison"]["rejected_observation_ids"]), 8)

    def test_unknown_duplicate_and_wrong_issuer_references_rejected(self):
        task = self.create()
        invalid = [self.decisions() + [self.decisions()[0]],
                   self.decisions()[:1] * 2,
                   [{**self.decisions()[0], "observation_id": "cfo-2025"}],
                   [{**self.decisions()[0], "observation_id": self.decisions()[0]["observation_id"].replace("A:", "B:")}]]
        for decisions in invalid:
            self.assert_http(422, self.store.update, task["id"], self.analyst, 1, decisions, [], "")
        hypothesis = {"id": "h", "text": "Candidate", "evidence_ids": ["foreign-observation"], "status": "pending", "note": ""}
        self.assert_http(422, self.store.update, task["id"], self.analyst, 1, [], [hypothesis], "")
        self.assertEqual(self.store.get(task["id"], self.viewer)["revision"], 1)

    def test_material_change_or_deletion_blocks_new_submission_but_not_old_read(self):
        submitted = self.submit()
        frozen = submitted["snapshot"]
        self.bundle["observations"][0]["normalized_value"] = "999"
        self.assertEqual(self.store.export(submitted["id"], self.viewer)["snapshot"], frozen)
        returned = self.store.act(submitted["id"], self.reviewer, submitted["revision"], "return", "Need new evidence version", frozen["sha256"])
        self.assert_http(409, self.store.act, returned["id"], self.analyst, returned["revision"], "submit", "Try changed source")
        self.material_mock.side_effect = HTTPException(409, "Source removed")
        self.assert_http(409, self.store.act, returned["id"], self.analyst, returned["revision"], "submit", "Try missing source")
        self.assertEqual(self.store.get_snapshot(returned["id"], frozen["id"], self.viewer)["snapshot"], frozen)
        self.assertEqual(self.store.get(returned["id"], self.viewer)["revision"], returned["revision"])

    def test_changed_comparison_cannot_be_silently_frozen(self):
        edited = self.edit(self.create())
        self.comparison_mock.side_effect = lambda bundle, decisions: {**synthetic_comparison(bundle, decisions), "rule_version": "new"}
        self.assert_http(409, self.store.act, edited["id"], self.analyst, edited["revision"], "submit", "Rules changed")
        updated = self.edit(edited)
        submitted = self.store.act(updated["id"], self.analyst, updated["revision"], "submit", "New comparison inspected")
        self.assertEqual(submitted["snapshot"]["content"]["comparison"]["rule_version"], "new")

    def test_stale_revision_or_wrong_snapshot_never_approves(self):
        submitted = self.submit()
        self.assert_http(409, self.store.act, submitted["id"], self.reviewer, 1, "approve", "Stale page", submitted["snapshot"]["sha256"])
        self.assert_http(409, self.store.act, submitted["id"], self.reviewer, submitted["revision"], "approve", "Wrong snapshot", "0" * 64)
        self.assert_http(422, self.store.act, submitted["id"], self.reviewer, submitted["revision"], "approve", "No snapshot")
        self.assertEqual(self.store.get(submitted["id"], self.viewer)["status"], "in_review")

    def test_role_limits_and_history_never_changes_on_denied_writes(self):
        task = self.create()
        self.assert_http(403, self.create, actor=self.viewer)
        self.assert_http(403, self.edit, task, actor=self.reviewer)
        self.assert_http(403, self.store.act, task["id"], self.viewer, 1, "submit", "Viewer cannot submit")
        self.assert_http(403, self.store.get, task["id"], Actor("x", "team-a", "unknown"))
        self.assertEqual(self.store.get(task["id"], self.viewer)["events"], task["events"])

    def test_request_contract_rejects_history_forged_fields_and_bad_values(self):
        historical = {**self.payload, "material_mode": "historical_as_of", "as_of": "2025-01-01"}
        error = self.assert_http(422, self.store.create, self.analyst, historical, "history")
        self.assertEqual(error.detail["code"], "historical_version_unverified")
        for mutation in ({"as_of": "2025-01-01"}, {"tenant_id": "other"}, {"bundle": self.bundle},
                         {"question": "   "}, {"question": float("nan")}, {"selection_reason": "x" * 2001},
                         {"bundle_id": "../../private"}):
            self.assert_http(422, self.store.create, self.analyst, {**self.payload, **mutation}, "invalid")
        for key in ("", " ", "x" * 201, None):
            self.assert_http(422, self.store.create, self.analyst, self.payload, key)
        for revision in (True, 0, "1", float("inf")):
            self.assert_http(422, self.store.update, "not-created", self.analyst, revision, [], [], "")
        task = self.create()
        for hypothesis in ({"id": "h", "text": "Unproven", "status": "supported"},
                           {"id": "h", "text": "Unproven", "evidence_ids": [self.decisions()[0]["observation_id"]] * 2}):
            self.assert_http(422, self.store.update, task["id"], self.analyst, 1, [], [hypothesis], "")
        self.assert_http(422, self.store.update, task["id"], self.analyst, 1, [{**self.decisions()[0], "note": "  "}], [], "")

    def test_models_are_public_and_reject_unsupported_actions(self):
        self.assertEqual(ResearchTaskCreate.model_validate(self.payload).model_dump(mode="json"),
                         dict(self.payload, include_cash_flow_bridge=False))
        self.assertEqual(ResearchTaskUpdate(revision=1, decisions=[], hypotheses=[], coverage_note="").revision, 1)
        for action in ("delete", "reopen", "auto_approve"):
            with self.assertRaises(ValidationError):
                ResearchTaskAction(revision=1, action=action, note="Unsupported")

    def test_mutating_returned_objects_does_not_change_persistence(self):
        submitted = self.submit()
        original = self.store.get(submitted["id"], self.viewer)
        submitted["snapshot"]["content"]["hypotheses"].append({"fake": "value"})
        submitted["bundle"]["observations"][0]["normalized_value"] = "999"
        self.assertEqual(self.store.get(submitted["id"], self.viewer), original)

    def test_task_and_snapshot_corruption_detected(self):
        first = self.submit()
        with self.store.db(write=True) as db:
            row = db.execute("SELECT content FROM research_snapshots WHERE id=?", (first["snapshot"]["id"],)).fetchone()
            content = json.loads(row[0])
            content["coverage_note"] = "Tampered"
            db.execute("UPDATE research_snapshots SET content=? WHERE id=?", (json.dumps(content), first["snapshot"]["id"]))
        self.assert_http(409, self.store.export, first["id"], self.viewer)
        second = self.create("different-task")
        with self.store.db(write=True) as db:
            db.execute("UPDATE research_tasks SET revision=999 WHERE id=?", (second["id"],))
        self.assert_http(409, self.store.get, second["id"], self.viewer)

    def test_sqlite_busy_is_actionable_conflict(self):
        with patch("creditlens.research_task_store.sqlite3.connect", side_effect=sqlite3.OperationalError("database is locked")):
            self.assert_http(409, self.store.list, self.viewer)


class RegisteredResearchHandoffTests(unittest.TestCase):
    """Real registered fact-pack integration; no raw PDF or OCR execution."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        repository = Path(__file__).resolve().parents[1]
        for name in ("msft-fy2025-pdf.json", "aapl-fy2025-pdf.json"):
            path = self.root / "data/examples" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository / "data/examples" / name, path)
        self.store = ResearchTaskStore(self.root / "research.sqlite", self.root)
        self.analyst = Actor("analyst", "team", "analyst")
        self.reviewer = Actor("reviewer", "team", "reviewer")

    def submitted(self):
        task = self.store.create(self.analyst, {"question": "Examine annual cash flow changes",
                                 "selection_reason": "Use registered examples without treating them as strict peers"}, "real-packs")
        decisions = [{"observation_id": o["id"], "decision": "adopted", "note": "Use this registered row for its own issuer's annual change"}
                     for o in task["bundle"]["observations"]]
        task = self.store.update(task["id"], self.analyst, task["revision"], decisions, [],
                                 "Current FY2025 materials and comparative columns; no historical availability or peer ranking.")
        return self.store.act(task["id"], self.analyst, task["revision"], "submit", "Review calculation scope and gaps")

    def test_registered_eight_observations_flow_through_frozen_handoff(self):
        task = self.submitted()
        self.assertEqual(len(task["bundle"]["observations"]), 8)
        self.assertEqual([r["status"] for r in task["comparison"]["annual_changes"]], ["computed"] * 4)
        self.assertTrue(all(row["ranking"] is None for row in task["comparison"]["cross_company"]))
        task = self.store.act(task["id"], self.reviewer, task["revision"], "approve", "Scope checked without causal approval",
                              task["snapshot"]["sha256"])
        self.assertEqual(self.store.export(task["id"], self.reviewer)["snapshot"], task["snapshot"])

    def test_registered_custom_root_deletion_preserves_old_but_blocks_new_freeze(self):
        task = self.submitted()
        old = task["snapshot"]
        task = self.store.act(task["id"], self.reviewer, task["revision"], "return", "Needs additional explanation", old["sha256"])
        (self.root / "data/examples/msft-fy2025-pdf.json").unlink()
        self.assertEqual(self.store.export(task["id"], self.reviewer)["snapshot"], old)
        with self.assertRaises(HTTPException) as caught:
            self.store.act(task["id"], self.analyst, task["revision"], "submit", "Try to freeze without local registered source")
        self.assertEqual(caught.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
