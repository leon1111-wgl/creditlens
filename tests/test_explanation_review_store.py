"""Real registered evidence in temporary SQLite; all actor roles are simulated."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from creditlens.research_task_store import ResearchTaskStore, _json, _sha
from creditlens.explanation_review_store import (
    ExplanationReviewStore, ExplanationReviewCreate, ExplanationReviewUpdate, ExplanationReviewAction,
)
from creditlens.workflow import Actor


ROOT = Path(__file__).resolve().parents[1]
AUTHOR = Actor("parent-author", "tenant-a", "analyst")
EDITOR = Actor("parent-editor", "tenant-a", "analyst")
SUBMITTER = Actor("parent-submitter", "tenant-a", "analyst")
PARENT_REVIEWER = Actor("parent-reviewer", "tenant-a", "reviewer")
CHILD_AUTHOR = Actor("supplement-author", "tenant-a", "analyst")
CHILD_EDITOR = Actor("supplement-editor", "tenant-a", "analyst")
CHILD_SUBMITTER = Actor("supplement-submitter", "tenant-a", "analyst")
REVIEWER = Actor("supplement-reviewer", "tenant-a", "reviewer")
VIEWER = Actor("observer", "tenant-a", "viewer")
NOTE = "仅确认公司陈述的收录与范围，未确认独立因果。"
COVERAGE = "保留主要原因与部分抵消项；公司陈述尚未独立核实。"


class ExplanationReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.parent = ResearchTaskStore(self.directory/"research.sqlite3", ROOT)
        self.store = ExplanationReviewStore(self.parent)
        self.ready = self.parent_ready("main-parent")
        self.parent_export = self.parent.export(self.ready["id"], VIEWER)

    def parent_ready(self, key, *, reject_cfo=False):
        item = self.parent.create(AUTHOR, {"question": "研究经营现金流变化的公司陈述及局限。",
                                 "selection_reason": "使用已登记两期财务材料，研究过程是模拟。"}, key)
        decisions = [{"observation_id": o["id"], "decision": "rejected" if reject_cfo and o["issuer_id"] == "MSFT" and o["fact_id"] == "cfo-2025" else "adopted",
                      "note": "模拟采用或排除决定，不代表真人研究或验证。"} for o in item["bundle"]["observations"]]
        item = self.parent.update(item["id"], EDITOR, item["revision"], decisions, [], COVERAGE)
        item = self.parent.act(item["id"], SUBMITTER, item["revision"], "submit", NOTE)
        return self.parent.act(item["id"], PARENT_REVIEWER, item["revision"], "approve", NOTE, item["snapshot_sha256"])

    def create(self, key="child", parent=None, actor=CHILD_AUTHOR):
        parent = parent or self.ready
        return self.store.create(parent["id"], actor, {"parent_revision": parent["revision"],
                                 "parent_snapshot_sha256": parent["snapshot_sha256"]}, key)

    def edit(self, item, decision="adopt_as_company_statement", actor=CHILD_EDITOR):
        records = [{"entry_id": e["id"], "decision": decision, "note": NOTE} for e in item["evidence"]["attribution"]["entries"]]
        return self.store.update(item["id"], actor, item["revision"], records, COVERAGE)

    def submit(self, item):
        return self.store.act(item["id"], CHILD_SUBMITTER, item["revision"], "submit", NOTE)

    def approve(self, item, actor=REVIEWER):
        return self.store.act(item["id"], actor, item["revision"], "approve", NOTE, item["snapshot_sha256"])

    def assert_http(self, status, function, *args, **kwargs):
        with self.assertRaises(HTTPException) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.status_code, status, error.exception.detail)
        return error.exception

    def test_full_handoff_freezes_parent_and_new_evidence_without_inheriting_approval(self):
        created = self.create()
        self.assertEqual(created["status"], "draft")
        self.assertIsNone(created["review"])
        self.assertIsNone(created["snapshot"])
        self.assertNotIn("parent_export", created)
        self.assertEqual(created["evidence"]["parent_export"], self.parent_export)
        self.assertEqual(created["parent_export_sha256"], _sha(self.parent_export))
        edited = self.edit(created)
        self.assertEqual(edited["decisions"][0]["support_scope"], "company_statement_only")
        submitted = self.submit(edited)
        self.assertEqual(submitted["status"], "in_review")
        approved = self.approve(submitted)
        exported = self.store.export(approved["id"], VIEWER)
        self.assertEqual(approved["status"], "reviewed")
        self.assertEqual(exported["review"]["decision"], "approve")
        self.assertFalse(exported["independently_verified"])
        self.assertFalse(exported["automatic_approval"])
        content = exported["snapshot"]["content"]
        self.assertEqual(content["evidence"]["parent_export"], self.parent_export)
        self.assertEqual(content["parent_snapshot_sha256"], self.ready["snapshot_sha256"])
        self.assertEqual(content["coverage_note"], COVERAGE)
        self.assertEqual(_sha(content), exported["snapshot"]["sha256"])
        self.assertEqual(self.parent.export(self.ready["id"], VIEWER), self.parent_export)
        self.assertEqual(self.parent.get(self.ready["id"], VIEWER), self.ready)

    def test_parent_must_be_reviewed_and_expected_revision_and_hash_match(self):
        draft = self.parent.create(AUTHOR, {"question": "尚未提交的研究任务", "selection_reason": "供开发控制的尚未复核任务"}, "draft")
        self.assert_http(409, self.store.create, draft["id"], CHILD_AUTHOR,
                         {"parent_revision": draft["revision"], "parent_snapshot_sha256": "a" * 64}, "new")
        for payload in ({"parent_revision": self.ready["revision"] - 1, "parent_snapshot_sha256": self.ready["snapshot_sha256"]},
                        {"parent_revision": self.ready["revision"], "parent_snapshot_sha256": "a" * 64}):
            self.assert_http(409, self.store.create, self.ready["id"], CHILD_AUTHOR, payload, "new")
        self.assertEqual(self.store.list(self.ready["id"], VIEWER), [])

    def test_idempotency_binds_actor_parent_and_request_without_live_rebuild_on_retry(self):
        first = self.create()
        with patch.object(self.store, "_build_evidence", side_effect=AssertionError("Do not rebuild an existing receipt")):
            self.assertEqual(self.create()["id"], first["id"])
        self.assert_http(409, self.create, actor=CHILD_EDITOR)
        payload = {"parent_revision": self.ready["revision"] + 1, "parent_snapshot_sha256": self.ready["snapshot_sha256"]}
        self.assert_http(409, self.store.create, self.ready["id"], CHILD_AUTHOR, payload, "child")
        other = self.parent_ready("another-parent")
        self.assert_http(409, self.create, parent=other)
        with ThreadPoolExecutor(max_workers=2) as pool:
            returned = list(pool.map(lambda _: self.create(key="concurrent"), range(2)))
        self.assertEqual(returned[0]["id"], returned[1]["id"])
        self.assertEqual(len(self.store.list(self.ready["id"], VIEWER)), 2)

    def test_roles_and_tenants_are_checked_before_read_or_write(self):
        item = self.create()
        for actor in (VIEWER, REVIEWER):
            self.assert_http(403, self.create, key="forbidden", actor=actor)
            self.assert_http(403, self.edit, item, actor=actor)
        self.assertEqual(self.store.get(item["id"], VIEWER)["id"], item["id"])
        foreign = Actor(CHILD_AUTHOR.user_id, "tenant-b", "analyst")
        self.assert_http(404, self.create, key="foreign", actor=foreign)
        for function, args in ((self.store.get, (item["id"], foreign)),
                               (self.store.list, (self.ready["id"], foreign)),
                               (self.store.export, (item["id"], foreign)),
                               (self.store.update, (item["id"], foreign, item["revision"], [], ""))):
            self.assert_http(404, function, *args)
        self.assert_http(403, self.store.get, item["id"], Actor("unknown", "tenant-a", "admin"))

    def test_all_parent_and_child_contributors_cannot_review_by_switching_role(self):
        submitted = self.submit(self.edit(self.create()))
        people = (AUTHOR, EDITOR, SUBMITTER, CHILD_AUTHOR, CHILD_EDITOR, CHILD_SUBMITTER)
        for person in people:
            actor = Actor(person.user_id, person.tenant_id, "reviewer")
            self.assert_http(403, self.approve, submitted, actor=actor)
        self.assertEqual(self.store.get(submitted["id"], VIEWER)["revision"], submitted["revision"])
        # The original parent's independent reviewer is not a parent author.
        reviewed = self.approve(submitted, actor=PARENT_REVIEWER)
        self.assertEqual(reviewed["status"], "reviewed")

    def test_revision_snapshot_and_status_guards_block_stale_or_illegal_actions(self):
        created = self.create()
        self.assert_http(409, self.store.export, created["id"], VIEWER)
        updated = self.edit(created)
        self.assert_http(409, self.edit, created)
        submitted = self.submit(updated)
        self.assert_http(409, self.edit, submitted)
        for action in ("approve", "return"):
            self.assert_http(422, self.store.act, submitted["id"], REVIEWER, submitted["revision"], action, "短意见", submitted["snapshot_sha256"])
        self.assertEqual(self.store.get(submitted["id"], VIEWER)["revision"], submitted["revision"])
        self.assert_http(409, self.store.act, submitted["id"], REVIEWER, submitted["revision"], "approve", NOTE, "0" * 64)
        self.assert_http(409, self.store.act, submitted["id"], REVIEWER, submitted["revision"] - 1, "approve", NOTE, submitted["snapshot_sha256"])
        self.assert_http(403, self.store.act, submitted["id"], CHILD_AUTHOR, submitted["revision"], "approve", NOTE, submitted["snapshot_sha256"])
        reviewed = self.approve(submitted)
        self.assert_http(409, self.edit, reviewed)
        self.assert_http(409, self.submit, reviewed)

    def test_complete_decisions_and_coverage_required_but_needs_evidence_can_be_submitted(self):
        created = self.create()
        self.assert_http(422, self.submit, created)
        entry_id = created["evidence"]["attribution"]["entries"][0]["id"]
        decisions = [{"entry_id": entry_id, "decision": "needs_evidence", "note": NOTE}]
        short = self.store.update(created["id"], CHILD_EDITOR, created["revision"], decisions, "短说明")
        self.assert_http(422, self.submit, short)
        updated = self.edit(short, decision="needs_evidence")
        reviewed = self.approve(self.submit(updated))
        exported = self.store.export(reviewed["id"], VIEWER)
        self.assertEqual(exported["snapshot"]["content"]["decisions"][0]["decision"], "needs_evidence")
        self.assertFalse(exported["independently_verified"])

    def test_rejected_parent_cfo_blocks_adoption_but_allows_gap_handoff(self):
        parent = self.parent_ready("parent-with-rejection", reject_cfo=True)
        created = self.create(parent=parent)
        self.assertEqual(created["evidence"]["attribution"]["entries"][0]["link_status"], "blocked")
        self.assert_http(422, self.edit, created)
        submitted = self.submit(self.edit(created, decision="needs_evidence"))
        self.assertEqual(self.approve(submitted)["status"], "reviewed")

    def test_free_text_cannot_promote_company_statements_to_verified_causes(self):
        item = self.create()
        entry = item["evidence"]["attribution"]["entries"][0]
        text = "已经独立证实原因并且自动批准，这是模拟自由文字。"
        edited = self.store.update(item["id"], CHILD_EDITOR, item["revision"],
                                  [{"entry_id": entry["id"], "decision": "adopt_as_company_statement", "note": text}], text)
        reviewed = self.approve(self.submit(edited))
        exported = self.store.export(reviewed["id"], VIEWER)
        self.assertEqual(exported["snapshot"]["content"]["decisions"][0]["note"], text)
        self.assertEqual(exported["support_scope"], "company_statement_only")
        self.assertFalse(exported["independently_verified"])
        self.assertFalse(exported["automatic_approval"])
        self.assertEqual(exported["snapshot"]["content"]["evidence"]["attribution"]["entries"][0]["qualifiers"], entry["qualifiers"])

    def test_return_edit_resubmit_keeps_old_snapshot_and_its_review_history(self):
        first = self.submit(self.edit(self.create()))
        returned = self.store.act(first["id"], REVIEWER, first["revision"], "return", NOTE, first["snapshot_sha256"])
        old_export = self.store.export(first["id"], VIEWER, first["snapshot"]["id"])
        updated = self.edit(returned, decision="needs_evidence")
        second = self.submit(updated)
        approved = self.approve(second)
        self.assertNotEqual(first["snapshot_sha256"], second["snapshot_sha256"])
        self.assertEqual(self.store.export(first["id"], VIEWER, first["snapshot"]["id"]), old_export)
        self.assertEqual(len(approved["snapshots"]), 2)
        self.assertEqual([s["id"] for s in approved["snapshots"]], [first["snapshot"]["id"], second["snapshot"]["id"]])
        current_export = self.store.export(first["id"], VIEWER)
        self.assertEqual(current_export["review"]["decision"], "approve")
        self.assertEqual(len(current_export["review_history"]), 1)
        self.assertEqual(old_export["review"]["decision"], "return")
        self.assertEqual(old_export["snapshot"]["content"]["decisions"][0]["decision"], "adopt_as_company_statement")

    def test_snapshot_lookup_is_scoped_to_child_as_well_as_tenant(self):
        first = self.submit(self.edit(self.create("first")))
        second = self.submit(self.edit(self.create("second")))
        self.assert_http(404, self.store.export, first["id"], VIEWER, second["snapshot"]["id"])
        self.assert_http(404, self.store.export, second["id"], VIEWER, first["snapshot"]["id"])

    def test_reviewer_who_later_edits_cannot_review_new_revision(self):
        first = self.submit(self.edit(self.create()))
        returned = self.store.act(first["id"], REVIEWER, first["revision"], "return", NOTE, first["snapshot_sha256"])
        old = self.store.export(first["id"], VIEWER)
        editor = Actor(REVIEWER.user_id, REVIEWER.tenant_id, "analyst")
        second = self.submit(self.edit(returned, decision="needs_evidence", actor=editor))
        self.assert_http(403, self.approve, second, actor=REVIEWER)
        self.assertEqual(self.store.export(first["id"], VIEWER, first["snapshot"]["id"]), old)

    def test_registry_deleted_or_changed_blocks_new_freeze_but_saved_exports_remain_readable(self):
        draft = self.edit(self.create("draft"))
        frozen = self.approve(self.submit(self.edit(self.create("frozen"))))
        exported = self.store.export(frozen["id"], VIEWER)
        live_root = self.directory/"alternate-registration"
        live_root.mkdir()
        self.store.root = live_root
        self.assert_http(409, self.submit, draft)
        self.assert_http(409, self.create, key="missing-registry")
        self.assertEqual(self.store.export(frozen["id"], VIEWER), exported)
        self.assertEqual(self.store.get(draft["id"], VIEWER)["status"], "draft")
        altered = json.loads((ROOT/"data/sources/company-attributions.json").read_text(encoding="utf-8"))
        altered["entries"][0]["reported_numbers"]["current_cfo"] = "136.3"
        path = live_root/"data/sources/company-attributions.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(altered), encoding="utf-8")
        self.assert_http(409, self.submit, draft)
        self.assertEqual(self.store.export(frozen["id"], VIEWER), exported)

    def test_parent_binding_is_rechecked_in_submission_transaction(self):
        draft = self.edit(self.create())
        with self.parent.db(write=True) as db:
            parent = self.parent._load(db, self.ready["id"], AUTHOR)
            previous = parent["revision"]
            parent["revision"] += 1
            self.parent._save(db, parent, previous)
        self.assert_http(409, self.submit, draft)
        self.assertEqual(self.store.get(draft["id"], VIEWER)["revision"], draft["revision"])
        with self.parent.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM explanation_review_snapshots").fetchone()[0], 0)

    def test_concurrent_same_revision_only_one_update_wins(self):
        item = self.create()
        def update(_):
            try:
                return self.edit(item)["revision"]
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, range(2)))
        self.assertCountEqual(results, [2, 409])
        self.assertEqual(len(self.store.get(item["id"], VIEWER)["events"]), 2)

    def test_child_operations_do_not_write_parent_task_or_snapshot_tables(self):
        with self.parent.db(write=True) as db:
            for table in ("research_tasks", "research_snapshots"):
                for operation in ("INSERT", "UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER guard_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'parent table must remain unchanged'); END")
        reviewed = self.approve(self.submit(self.edit(self.create())))
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertEqual(self.parent.export(self.ready["id"], VIEWER), self.parent_export)

    def test_malformed_requests_and_untrusted_decision_fields_do_not_write(self):
        item = self.create()
        entry = item["evidence"]["attribution"]["entries"][0]["id"]
        invalid = [
            [{"entry_id": entry, "decision": "adopt_as_company_statement", "note": "short"}],
            [{"entry_id": "unknown", "decision": "needs_evidence", "note": NOTE}],
            [{"entry_id": entry, "decision": "cause_verified", "note": NOTE}],
            [{"entry_id": entry, "decision": "needs_evidence", "note": NOTE, "support_scope": "cause_verified"}],
            [{"entry_id": entry, "decision": "needs_evidence", "note": NOTE}] * 2,
        ]
        for decisions in invalid:
            self.assert_http(422, self.store.update, item["id"], CHILD_EDITOR, item["revision"], decisions, COVERAGE)
        self.assert_http(422, self.store.update, item["id"], CHILD_EDITOR, True, [], COVERAGE)
        self.assert_http(422, self.store.update, item["id"], CHILD_EDITOR, item["revision"], [], float("nan"))
        self.assert_http(422, self.store.act, item["id"], CHILD_SUBMITTER, item["revision"], "submit", "short")
        payload = {"parent_revision": self.ready["revision"], "parent_snapshot_sha256": self.ready["snapshot_sha256"], "evidence": {"forged": True}}
        self.assert_http(422, self.store.create, self.ready["id"], CHILD_AUTHOR, payload, "untrusted")
        self.assertEqual(self.store.get(item["id"], VIEWER)["revision"], 1)

    def test_returned_objects_cannot_modify_database_and_storage_damage_is_rejected(self):
        item = self.create()
        item["evidence"]["attribution"]["entries"][0]["paraphrase_zh"] = "伪造内容"
        self.assertNotEqual(self.store.get(item["id"], VIEWER)["evidence"]["attribution"]["entries"][0]["paraphrase_zh"], "伪造内容")
        with self.parent.db(write=True) as db:
            db.execute("UPDATE explanation_reviews SET payload_sha256=? WHERE id=?", ("0" * 64, item["id"]))
        self.assert_http(409, self.store.get, item["id"], VIEWER)

    def test_snapshot_damage_is_rejected_even_when_draft_is_intact(self):
        item = self.submit(self.edit(self.create()))
        with self.parent.db(write=True) as db:
            db.execute("UPDATE explanation_review_snapshots SET content_sha256=? WHERE id=?", ("0" * 64, item["snapshot"]["id"]))
        self.assert_http(409, self.store.export, item["id"], VIEWER)
        self.assert_http(409, self.store.get, item["id"], VIEWER)

    def test_database_busy_is_retriable_conflict(self):
        item = self.create()
        original = sqlite3.connect
        locked = original(self.parent.path)
        locked.execute("BEGIN IMMEDIATE")
        try:
            with patch("creditlens.research_task_store.sqlite3.connect", side_effect=lambda path, timeout=10: original(path, timeout=0.01)):
                self.assert_http(409, self.edit, item)
        finally:
            locked.rollback()
            locked.close()
        self.assertEqual(self.edit(item)["revision"], 2)


if __name__ == "__main__":
    unittest.main()
