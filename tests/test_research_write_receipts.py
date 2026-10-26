"""Real material handoffs in isolated SQLite, with simulated response loss."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from creditlens.research_task_store import ResearchTaskStore
from creditlens.explanation_review_store import ExplanationReviewStore
from creditlens import research_write_receipts as receipts
from creditlens.workflow import Actor

ROOT = Path(__file__).resolve().parents[1]
AUTHOR = Actor("author", "tenant-a", "analyst")
EDITOR = Actor("editor", "tenant-a", "analyst")
REVIEWER = Actor("independent-reviewer", "tenant-a", "reviewer")
CHILD_AUTHOR = Actor("supplement-author", "tenant-a", "analyst")
CHILD_REVIEWER = Actor("supplement-reviewer", "tenant-a", "reviewer")
VIEWER = Actor("viewer", "tenant-a", "viewer")
NOTE = "仅检查登记来源与范围，不代表独立因果验证。"
COVERAGE = "已记录材料范围与未知项，当前不进行跨公司排名。"


class ResearchWriteReceiptTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory/"research.sqlite"
        self.store = ResearchTaskStore(self.path, ROOT)
        self.child = ExplanationReviewStore(self.store)
        self.counter = 0

    def create(self, actor=AUTHOR, bridge=False):
        self.counter += 1
        return self.store.create(actor, {"question": "核对两期财务数据与其解释。", "selection_reason": "以登记材料构建模拟研究交接。",
                                        "include_cash_flow_bridge": bridge}, "create-"+str(self.counter))

    @staticmethod
    def task_request(task):
        return {"revision": task["revision"], "decisions": [{"observation_id": o["id"], "decision": "adopted", "note": NOTE}
                 for o in task["bundle"]["observations"]], "hypotheses": [], "coverage_note": COVERAGE,
                "bridge_review_note": COVERAGE if "cash_flow_bridge" in task else None}

    @staticmethod
    def child_request(item):
        return {"revision": item["revision"], "decisions": [{"entry_id": e["id"], "decision": "adopt_as_company_statement", "note": NOTE}
                 for e in item["evidence"]["attribution"]["entries"]], "coverage_note": COVERAGE}

    def parent_ready(self):
        task = self.create()
        task = self.store.update(task["id"], AUTHOR, **self.task_request(task))
        task = self.store.act(task["id"], AUTHOR, task["revision"], "submit", NOTE)
        return self.store.act(task["id"], REVIEWER, task["revision"], "approve", NOTE, task["snapshot_sha256"])

    def make_child(self):
        parent = self.parent_ready()
        return self.child.create(parent["id"], CHILD_AUTHOR,
            {"parent_revision": parent["revision"], "parent_snapshot_sha256": parent["snapshot_sha256"]}, "child-"+str(self.counter))

    def setup_kind(self, kind):
        if kind == "task":
            item = self.create()
            return self.store, item, AUTHOR, REVIEWER, self.task_request(item)
        item = self.make_child()
        return self.child, item, CHILD_AUTHOR, CHILD_REVIEWER, self.child_request(item)

    def error(self, status, function, *args, code=None, **kwargs):
        with self.assertRaises(HTTPException) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status_code, status, caught.exception.detail)
        if code:
            self.assertEqual(caught.exception.detail["code"], code)
        return caught.exception

    def dump(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            return list(db.iterdump())

    def count(self, table):
        with closing(sqlite3.connect(self.path)) as db, db:
            return db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]

    def test_unkeyed_update_retains_previous_cas_and_response_contract(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            before = self.count("research_write_receipts")
            first = store.update(item["id"], author, **request)
            self.error(409, store.update, item["id"], author, **request)
            self.assertEqual(before, self.count("research_write_receipts"))
            self.assertNotIn("receipt", first)
            self.assertNotIn("replayed", first)

    def test_updates_replay_first_response_after_later_edit_and_service_recreation(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            key = kind+"-update-key"
            first = store.update(item["id"], author, **request, idempotency_key=key)
            later_request = {**request, "revision": first["revision"], "coverage_note": COVERAGE+"新增范围说明。"}
            later = store.update(item["id"], author, **later_request)
            rebuilt_parent = ResearchTaskStore(self.path, ROOT)
            rebuilt = rebuilt_parent if kind == "task" else ExplanationReviewStore(rebuilt_parent)
            before = self.dump()
            response = rebuilt.update(item["id"], author, **request, idempotency_key=key)
            self.assertEqual(response, first)
            self.assertLess(response["revision"], later["revision"])
            self.assertEqual(rebuilt.get(item["id"], VIEWER), later)
            self.assertEqual(before, self.dump())

    def test_submit_approve_and_return_replay_without_extra_events_or_snapshots(self):
        for kind in ("task", "child"):
            for review_action in ("approve", "return"):
                store, item, author, reviewer, request = self.setup_kind(kind)
                edited = store.update(item["id"], author, **request)
                submit_args = (item["id"], author, edited["revision"], "submit", NOTE)
                key = kind+review_action+"-submit"
                submitted = store.act(*submit_args, idempotency_key=key)
                args = (item["id"], reviewer, submitted["revision"], review_action, NOTE, submitted["snapshot_sha256"])
                reviewed = store.act(*args, idempotency_key=kind+review_action+"-review")
                before = self.dump()
                self.assertEqual(store.act(*args, idempotency_key=kind+review_action+"-review"), reviewed)
                self.assertEqual(store.act(*submit_args, idempotency_key=key), submitted)
                self.assertEqual(store.get(item["id"], VIEWER), reviewed)
                self.assertEqual(before, self.dump())

    def test_first_and_replayed_responses_have_identical_json_bytes(self):
        def wire(value):
            return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")

        for kind in ("task", "child"):
            for action in ("approve", "return"):
                store, item, author, reviewer, request = self.setup_kind(kind)
                update_key = kind+action+"-wire-update"
                edited = store.update(item["id"], author, **request, idempotency_key=update_key)
                self.assertEqual(wire(edited), wire(store.update(item["id"], author, **request, idempotency_key=update_key)))
                submit_args = (item["id"], author, edited["revision"], "submit", NOTE)
                submitted = store.act(*submit_args, idempotency_key=kind+action+"-wire-submit")
                self.assertEqual(wire(submitted), wire(store.act(*submit_args, idempotency_key=kind+action+"-wire-submit")))
                review_args = (item["id"], reviewer, submitted["revision"], action, NOTE, submitted["snapshot_sha256"])
                reviewed = store.act(*review_args, idempotency_key=kind+action+"-wire-review")
                self.assertEqual(wire(reviewed), wire(store.act(*review_args, idempotency_key=kind+action+"-wire-review")))

    def test_return_receipt_remains_old_after_edit_resubmit_and_approve(self):
        for kind in ("task", "child"):
            store, item, author, reviewer, request = self.setup_kind(kind)
            edited = store.update(item["id"], author, **request)
            submitted = store.act(item["id"], author, edited["revision"], "submit", NOTE)
            old_export = store.export(item["id"], VIEWER)
            args = (item["id"], reviewer, submitted["revision"], "return", NOTE, submitted["snapshot_sha256"])
            returned = store.act(*args, idempotency_key=kind+"-return-old")
            edited = store.update(item["id"], author, **{**request, "revision": returned["revision"]})
            submitted2 = store.act(item["id"], author, edited["revision"], "submit", NOTE)
            approved = store.act(item["id"], reviewer, submitted2["revision"], "approve", NOTE, submitted2["snapshot_sha256"])
            before = self.dump()
            self.assertEqual(store.act(*args, idempotency_key=kind+"-return-old"), returned)
            self.assertEqual(store.get(item["id"], VIEWER), approved)
            self.assertEqual(len(approved["snapshots"]), 2)
            self.assertEqual(store.export(item["id"], VIEWER, old_export["snapshot"]["id"])["snapshot"], old_export["snapshot"])
            self.assertEqual(before, self.dump())

    def test_replay_uses_saved_response_without_live_source_or_view_rebuild(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            edited = store.update(item["id"], author, **request)
            args = (item["id"], author, edited["revision"], "submit", NOTE)
            first = store.act(*args, idempotency_key=kind+"-source-offline")
            with patch.object(self.store, "_material_bundle", side_effect=AssertionError("No live source")), \
                 patch.object(self.child, "_build_evidence", side_effect=AssertionError("No rebuild")), \
                 patch.object(store, "_view", side_effect=AssertionError("No current view rebuild")):
                self.assertEqual(store.act(*args, idempotency_key=kind+"-source-offline"), first)

    def test_same_key_changed_body_revision_snapshot_actor_or_entity_conflicts(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            key = kind+"-conflict-key"
            first = store.update(item["id"], author, **request, idempotency_key=key)
            before = self.dump()
            for changed in ({**request, "coverage_note": COVERAGE+"变更"}, {**request, "revision": first["revision"]}):
                self.error(409, store.update, item["id"], author, **changed, idempotency_key=key, code="write_receipt_conflict")
            self.error(409, store.update, item["id"], EDITOR, **request, idempotency_key=key, code="write_receipt_conflict")
            self.error(409, store.act, item["id"], author, first["revision"], "submit", NOTE,
                       idempotency_key=key, code="write_receipt_conflict")
            self.assertEqual(before, self.dump())
        task = self.create()
        self.error(409, self.store.update, task["id"], AUTHOR, **self.task_request(task),
                   idempotency_key="task-conflict-key", code="write_receipt_conflict")
        # Same tenant/key is shared by the two entity types.
        item = self.make_child()
        self.error(409, self.child.update, item["id"], AUTHOR, **self.child_request(item),
                   idempotency_key="task-conflict-key", code="write_receipt_conflict")

    def test_review_receipt_binds_original_snapshot_hash_and_action(self):
        for kind in ("task", "child"):
            store, item, author, reviewer, request = self.setup_kind(kind)
            edited = store.update(item["id"], author, **request)
            submitted = store.act(item["id"], author, edited["revision"], "submit", NOTE)
            args = (item["id"], reviewer, submitted["revision"], "approve", NOTE)
            key = kind+"-snapshot-binding"
            store.act(*args, submitted["snapshot_sha256"], idempotency_key=key)
            self.error(409, store.act, *args, "a"*64, idempotency_key=key, code="write_receipt_conflict")
            self.error(409, store.act, item["id"], reviewer, submitted["revision"], "return", NOTE,
                       submitted["snapshot_sha256"], idempotency_key=key, code="write_receipt_conflict")

    def test_normalized_request_is_bound_not_untrimmed_free_text(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            key = kind+"-normalized"
            first = store.update(item["id"], author, **request, idempotency_key=key)
            spaced = deepcopy(request); spaced["coverage_note"] = "  "+spaced["coverage_note"]+"  "
            for decision in spaced["decisions"]: decision["note"] = " "+decision["note"]+" "
            self.assertEqual(store.update(item["id"], author, **spaced, idempotency_key=key), first)

    def test_current_role_tenant_resource_and_contributor_checks_precede_replay(self):
        for kind in ("task", "child"):
            store, item, author, reviewer, request = self.setup_kind(kind)
            key = kind+"-permissions"
            edited = store.update(item["id"], author, **request, idempotency_key=key)
            for actor in (VIEWER, Actor(author.user_id, author.tenant_id, "reviewer")):
                self.error(403, store.update, item["id"], actor, **request, idempotency_key=key)
            self.error(404, store.update, item["id"], Actor(author.user_id, "tenant-b", "analyst"), **request, idempotency_key=key)
            submitted = store.act(item["id"], author, edited["revision"], "submit", NOTE)
            args = (item["id"], reviewer, submitted["revision"], "return", NOTE, submitted["snapshot_sha256"])
            returned = store.act(*args, idempotency_key=kind+"-return-permissions")
            # The original independent reviewer later edits as an analyst.
            former_reviewer = Actor(reviewer.user_id, reviewer.tenant_id, "analyst")
            store.update(item["id"], former_reviewer, **{**request, "revision": returned["revision"]})
            self.error(403, store.act, *args, idempotency_key=kind+"-return-permissions")

    def test_tenant_namespace_allows_same_key_on_each_tenants_own_object(self):
        task_a = self.create()
        other = Actor(AUTHOR.user_id, "tenant-b", "analyst")
        task_b = self.create(actor=other)
        first = self.store.update(task_a["id"], AUTHOR, **self.task_request(task_a), idempotency_key="shared-tenant-key")
        second = self.store.update(task_b["id"], other, **self.task_request(task_b), idempotency_key="shared-tenant-key")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.count("research_write_receipts"), 2)

    def test_invalid_keys_do_not_modify_business_state(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            before = self.dump()
            for key in (True, 123, "", "short", " "*8, "x"*129):
                self.error(422, store.update, item["id"], author, **request, idempotency_key=key,
                           code="invalid_write_receipt_key")
            self.assertEqual(before, self.dump())

    def test_receipt_insert_failure_rolls_back_update_and_submit_snapshots(self):
        for kind in ("task", "child"):
            for operation in ("update", "submit"):
                store, item, author, _, request = self.setup_kind(kind)
                if operation == "submit": item = store.update(item["id"], author, **request)
                with closing(sqlite3.connect(self.path)) as db, db:
                    db.execute("CREATE TRIGGER receipt_failure BEFORE INSERT ON research_write_receipts BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
                before = self.dump()
                if operation == "update":
                    self.error(503, store.update, item["id"], author, **request, idempotency_key="atomic-update-key", code="write_receipt_storage_failed")
                else:
                    self.error(503, store.act, item["id"], author, item["revision"], "submit", NOTE,
                               idempotency_key="atomic-submit-key", code="write_receipt_storage_failed")
                self.assertEqual(before, self.dump())
                with closing(sqlite3.connect(self.path)) as db, db: db.execute("DROP TRIGGER receipt_failure")

    def test_late_response_generation_failure_rolls_back_business_and_receipt(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            before = self.dump()
            with patch.object(store, "_view", side_effect=HTTPException(409, "Simulated response failure")):
                self.error(409, store.update, item["id"], author, **request, idempotency_key=kind+"-view-failure")
            self.assertEqual(before, self.dump())

    def test_failed_business_operation_does_not_reserve_key(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            key = kind+"-retry-after-failure"
            before = self.count("research_write_receipts")
            self.error(422, store.act, item["id"], author, item["revision"], "submit", NOTE, idempotency_key=key)
            self.assertEqual(before, self.count("research_write_receipts"))
            edited = store.update(item["id"], author, **request)
            submitted = store.act(item["id"], author, edited["revision"], "submit", NOTE, idempotency_key=key)
            self.assertEqual(submitted["status"], "in_review")

    def test_corrupt_receipt_refuses_safely_and_restoration_allows_same_retry(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            key = kind+"-corruption-key"
            first = store.update(item["id"], author, **request, idempotency_key=key)
            with closing(sqlite3.connect(self.path)) as db, db:
                original = db.execute("SELECT response FROM research_write_receipts WHERE tenant=? AND request_key=?", (author.tenant_id, key)).fetchone()[0]
                db.execute("UPDATE research_write_receipts SET response=? WHERE tenant=? AND request_key=?", ('{"secret-canary":true}', author.tenant_id, key))
            before = self.dump()
            error = self.error(409, store.update, item["id"], author, **request, idempotency_key=key, code="write_receipt_integrity_failed")
            self.assertNotIn("secret-canary", str(error.detail))
            self.assertEqual(before, self.dump())
            with closing(sqlite3.connect(self.path)) as db, db:
                db.execute("UPDATE research_write_receipts SET response=? WHERE tenant=? AND request_key=?", (original, author.tenant_id, key))
            self.assertEqual(store.update(item["id"], author, **request, idempotency_key=key), first)

    def test_rehashed_receipt_event_rewrite_still_fails_current_history_binding(self):
        task = self.create(); request = self.task_request(task); key = "event-rewrite-key"
        self.store.update(task["id"], AUTHOR, **request, idempotency_key=key)
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute("SELECT binding_sha256,response FROM research_write_receipts WHERE tenant=? AND request_key=?", (AUTHOR.tenant_id, key)).fetchone()
            response = json.loads(row[1]); response["events"][-1]["note"] = "Altered historical event"
            response_sha = receipts._sha(response)
            db.execute("UPDATE research_write_receipts SET response=?,response_sha256=?,receipt_sha256=? WHERE tenant=? AND request_key=?",
                       (receipts._json(response), response_sha, receipts._receipt_sha(AUTHOR.tenant_id, key, row[0], response_sha), AUTHOR.tenant_id, key))
        self.error(409, self.store.update, task["id"], AUTHOR, **request, idempotency_key=key, code="write_receipt_integrity_failed")

    def test_concurrent_same_key_has_one_mutation_and_same_response(self):
        for kind in ("task", "child"):
            store, item, author, _, request = self.setup_kind(kind)
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(lambda _: store.update(item["id"], author, **request, idempotency_key=kind+"-concurrent-key"), range(2)))
            self.assertEqual(responses[0], responses[1])
            self.assertEqual(responses[0]["revision"], 2)
            self.assertEqual(len(store.get(item["id"], VIEWER)["events"]), 2)

    def test_v2_bridge_and_approved_parent_exports_are_not_rewritten_by_receipts(self):
        task = self.create(bridge=True); request = self.task_request(task)
        first = self.store.update(task["id"], AUTHOR, **request, idempotency_key="bridge-update-key")
        submitted = self.store.act(task["id"], AUTHOR, first["revision"], "submit", NOTE, idempotency_key="bridge-submit-key")
        exported = self.store.export(task["id"], VIEWER)
        self.assertEqual(self.store.update(task["id"], AUTHOR, **request, idempotency_key="bridge-update-key"), first)
        self.assertEqual(self.store.export(task["id"], VIEWER), exported)
        self.assertEqual(submitted["store_version"], "research-handoff-2")
        child = self.make_child(); parent_export = self.store.export(child["parent_task_id"], VIEWER)
        first_child = self.child.update(child["id"], CHILD_AUTHOR, **self.child_request(child), idempotency_key="child-parent-binding")
        self.assertEqual(self.child.update(child["id"], CHILD_AUTHOR, **self.child_request(child), idempotency_key="child-parent-binding"), first_child)
        self.assertEqual(self.store.export(child["parent_task_id"], VIEWER), parent_export)


if __name__ == "__main__":
    unittest.main()
