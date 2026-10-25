"""Explicit synchronous attempts persist summaries, never OCR bodies."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from creditlens.batch_attempt_store import BatchAttemptStore
from creditlens.batch_input import inspect_document_batch
from creditlens.workflow import Actor
from tests import test_batch_input as fixtures

ANALYST = Actor("analyst-one", "tenant-a", "analyst")
VIEWER = Actor("viewer-one", "tenant-a", "viewer")
REVIEWER = Actor("reviewer-one", "tenant-a", "reviewer")
OUTSIDER = Actor("analyst-one", "tenant-b", "analyst")


class BatchAttemptStoreTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BatchInputTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.path = self.root/".run/batch.sqlite3"
        self.store = BatchAttemptStore(self.path, self.root)
        self.good = deepcopy(self.fixture.value)
        self.bad = deepcopy(self.good); self.bad["pages"].pop()

    def assert_http(self, status, call, code=None):
        with self.assertRaises(HTTPException) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        if code:
            self.assertEqual(caught.exception.detail["code"], code)
        return caught.exception

    def test_create_real_core_and_rebuild_then_fix_same_document(self):
        first = self.store.create(ANALYST, self.bad, "create")
        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["latest_attempt"]["report"], inspect_document_batch(self.root,self.bad))
        old = self.store.export(first["id"], VIEWER)
        rebuilt = BatchAttemptStore(self.path, self.root)
        self.assertEqual(rebuilt.get(first["id"], REVIEWER), first)
        second = rebuilt.retry(ANALYST, first["id"], self.good, 1, "fix")
        self.assertEqual(second["status"], "prepared_not_indexed")
        self.assertEqual(second["revision"], 2)
        self.assertEqual(len(second["attempts"]), 2)
        self.assertFalse(second["queryable"])
        self.assertFalse(second["automatic_approval"])
        self.assertTrue(second["attempt_metadata_persisted"])
        self.assertFalse(second["raw_ocr_persisted"])
        self.assertFalse(second["latest_attempt"]["report"]["database_written"])
        self.assertEqual(rebuilt.export(first["id"], VIEWER, first["latest_attempt"]["id"]), old)

    def test_same_idempotency_request_replays_original_not_current_state(self):
        first = self.store.create(ANALYST,self.bad,"create")
        second = self.store.retry(ANALYST,first["id"],self.good,1,"fix")
        with patch.object(self.store,"_inspect",side_effect=AssertionError("Replay must not re-inspect")):
            self.assertEqual(self.store.create(ANALYST,self.bad,"create"), first)
            self.assertEqual(self.store.retry(ANALYST,first["id"],self.good,1,"fix"), second)
        self.assertEqual(len(self.store.get(first["id"],VIEWER)["attempts"]),2)

    def test_idempotency_conflict_includes_payload_caller_operation(self):
        first = self.store.create(ANALYST,self.bad,"key")
        self.assert_http(409,lambda:self.store.create(ANALYST,self.good,"key"),"idempotency_conflict")
        other = Actor("analyst-two",ANALYST.tenant_id,"analyst")
        self.assert_http(409,lambda:self.store.create(other,self.bad,"key"),"idempotency_conflict")
        self.assert_http(409,lambda:self.store.retry(ANALYST,first["id"],self.good,1,"key"),"idempotency_conflict")
        separate = self.store.create(OUTSIDER,self.bad,"key")
        self.assertNotEqual(separate["id"],first["id"])

    def test_tenant_isolation_get_retry_list_and_export(self):
        job=self.store.create(ANALYST,self.bad,"new")
        for call in (lambda:self.store.get(job["id"],OUTSIDER),lambda:self.store.export(job["id"],OUTSIDER),
                     lambda:self.store.retry(OUTSIDER,job["id"],self.good,1,"fix")):
            self.assert_http(404,call)
        self.assertEqual(self.store.list(OUTSIDER),[])
        self.assertEqual(self.store.list(VIEWER)[0]["id"],job["id"])

    def test_only_analyst_writes_all_known_roles_read(self):
        job=self.store.create(ANALYST,self.bad,"new")
        for actor in (VIEWER,REVIEWER):
            self.assert_http(403,lambda:self.store.create(actor,self.good,"new-role"))
            self.assert_http(403,lambda:self.store.retry(actor,job["id"],self.good,1,"fix"))
            self.assertEqual(self.store.get(job["id"],actor),job)
            self.assertEqual(self.store.export(job["id"],actor)["attempt"]["revision"],1)
        self.assert_http(403,lambda:self.store.list(Actor("x","tenant-a","admin")))

    def test_prepared_terminal_and_stale_revision(self):
        job=self.store.create(ANALYST,self.bad,"new")
        self.assert_http(409,lambda:self.store.retry(ANALYST,job["id"],self.good,2,"stale"),"stale_batch_revision")
        second=self.store.retry(ANALYST,job["id"],self.good,1,"fix")
        self.assert_http(409,lambda:self.store.retry(ANALYST,job["id"],self.good,2,"again"),"batch_is_terminal")
        self.assertEqual(self.store.get(job["id"],VIEWER),second)

    def test_different_document_or_provenance_cannot_replace_failed_job(self):
        job=self.store.create(ANALYST,self.bad,"new")
        for field,value in (("document_id","other"),("provenance","caller_declared_model_output")):
            changed=deepcopy(self.good);changed[field]=value
            self.assert_http(409,lambda:self.store.retry(ANALYST,job["id"],changed,1,"replace"),"batch_identity_changed")
        self.assertEqual(self.store.get(job["id"],VIEWER),job)

    def test_catalog_change_or_missing_blocks_new_attempt_not_old_reads(self):
        job=self.store.create(ANALYST,self.bad,"new")
        old=self.store.export(job["id"],VIEWER)
        manifest=self.fixture.fixture.manifest_path
        original=manifest.read_bytes()
        value=json.loads(original);value["selection_reason"]+=" Updated registered selection."
        manifest.write_text(json.dumps(value),encoding="utf-8")
        self.assert_http(409,lambda:self.store.retry(ANALYST,job["id"],self.good,1,"drift"),"batch_material_changed")
        manifest.unlink()
        self.assertEqual(self.store.export(job["id"],VIEWER),old)
        self.assertEqual(self.store.create(ANALYST,self.bad,"new"),job)
        self.assert_http(409,lambda:self.store.retry(ANALYST,job["id"],self.good,1,"missing"),"material_registration_failed")
        self.assertEqual(self.store.get(job["id"],VIEWER),job)

    def test_core_detected_mid_batch_drift_is_not_recorded(self):
        report=inspect_document_batch(self.root,self.bad)
        report["scope"]["same_inventory_checked"]=False
        with patch.object(self.store,"_inspect",return_value=report):
            self.assert_http(409,lambda:self.store.create(ANALYST,self.bad,"drift"),"batch_material_changed")
        self.assertEqual(self.store.list(ANALYST),[])

    def test_revision_boolean_noncanonical_and_unknown_fields_never_stored(self):
        mutations=[lambda p:p.update(schema_version=True),lambda p:p.update(tenant="stolen"),
                   lambda p:p.update(report={"status":"prepared_not_indexed"}),
                   lambda p:p["pages"][0]["ocr_input"]["result"]["scores"].__setitem__(0,float("nan")),
                   lambda p:p.update(pages=tuple(p["pages"])),
                   lambda p:p.update(pages=[{1:"nonstring-key"}])]
        for i,change in enumerate(mutations):
            value=deepcopy(self.good);change(value)
            self.assert_http(422,lambda:self.store.create(ANALYST,value,f"invalid-{i}"))
        self.assertEqual(self.store.list(ANALYST),[])
        job=self.store.create(ANALYST,self.bad,"new")
        for value in (True,False,"1",1.0,0):
            self.assert_http(422,lambda:self.store.retry(ANALYST,job["id"],self.good,value,"bool"))

    def test_nested_invalid_page_is_persisted_blocked_without_echo(self):
        value=deepcopy(self.good);value["pages"][0]["ocr_input"]["result"]["boxes"][0][0][0]=True
        result=self.store.create(ANALYST,value,"bad-page")
        self.assertEqual(result["status"],"blocked")
        self.assertEqual(result["latest_attempt"]["report"]["page_results"][0]["structure_status"],"failed")
        fixed=self.store.retry(ANALYST,result["id"],self.good,1,"fixed-page")
        self.assertEqual(fixed["status"],"prepared_not_indexed")

    def test_invalid_ocr_object_records_fixed_union_path_without_echo(self):
        for index, raw in enumerate((None, [], False, "raw-secret-invalid-object")):
            value=deepcopy(self.good);value["pages"][0]["ocr_input"]=raw
            result=self.store.create(ANALYST,value,f"invalid-object-{index}")
            self.assertEqual(result["status"],"blocked")
            error=result["latest_attempt"]["report"]["page_results"][0]["errors"][0]
            self.assertEqual(error["path"],"pages[0].document_id/ocr_input")
            self.assertNotIn("raw-secret-invalid-object",json.dumps(result))
            fixed=self.store.retry(ANALYST,result["id"],self.good,1,f"fix-object-{index}")
            self.assertEqual(fixed["status"],"prepared_not_indexed")

    def test_raw_canary_never_in_sqlite_journal_response_or_export(self):
        canary="RAW_OCR_ONLY_CANARY_91df82"
        value=deepcopy(self.good)
        for page in value["pages"]:
            page["ocr_input"]["result"]["txts"][0]=canary
            page["ocr_input"]["result"]["word_results"][0][0][0]=canary
        job=self.store.create(ANALYST,value,"canary")
        self.assertNotIn(canary,json.dumps(job))
        self.assertNotIn(canary,json.dumps(self.store.export(job["id"],VIEWER)))
        for file in self.path.parent.glob(self.path.name+"*"):
            self.assertNotIn(canary.encode(),file.read_bytes())

    def test_core_report_whitelist_rejects_raw_or_overstated_claim(self):
        baseline=inspect_document_batch(self.root,self.good)
        changes=[lambda r:r.update(ocr_input=self.good),lambda r:r.update(queryable=True),
                 lambda r:r["page_results"][0].update(normalized=self.good),
                 lambda r:r["scope"].update(model_execution_verified=True),
                 lambda r:r.update(input_sha256="f"*64)]
        for i,change in enumerate(changes):
            report=deepcopy(baseline);change(report)
            with patch.object(self.store,"_inspect",return_value=report):
                self.assert_http(503,lambda:self.store.create(ANALYST,self.good,f"fake-{i}"),"invalid_batch_core_report")
        self.assertEqual(self.store.list(ANALYST),[])

    def test_concurrent_same_revision_creates_at_most_one_attempt(self):
        job=self.store.create(ANALYST,self.bad,"new")
        def retry(key):
            try:
                return self.store.retry(ANALYST,job["id"],self.bad,1,key)["revision"]
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(retry,("parallel-a","parallel-b")))
        self.assertCountEqual(outcomes,[2,409])
        self.assertEqual(len(self.store.get(job["id"],VIEWER)["attempts"]),2)

    def test_concurrent_same_key_replays_one_committed_attempt(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(lambda _:self.store.create(ANALYST,self.bad,"parallel"),range(2)))
        self.assertEqual(outcomes[0],outcomes[1])
        self.assertEqual(len(self.store.list(ANALYST)),1)

    def test_cross_job_attempt_lookup_is_not_allowed(self):
        first=self.store.create(ANALYST,self.bad,"first")
        second=self.store.create(ANALYST,self.bad,"second")
        self.assert_http(404,lambda:self.store.export(first["id"],VIEWER,second["latest_attempt"]["id"]),"batch_attempt_not_found")

    def test_partial_transaction_failure_does_not_accept_request(self):
        with patch.object(self.store,"_remember",side_effect=HTTPException(503,"simulated commit-path failure")):
            self.assert_http(503,lambda:self.store.create(ANALYST,self.bad,"new"))
        self.assertEqual(self.store.list(ANALYST),[])
        created=self.store.create(ANALYST,self.bad,"new")
        self.assertEqual(created["revision"],1)

    def test_stored_tampering_is_detected_without_reading_live_sources(self):
        job=self.store.create(ANALYST,self.bad,"new")
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE batch_attempts SET payload=? WHERE task_id=?", ('{}',job["id"]))
        self.assert_http(409,lambda:self.store.get(job["id"],VIEWER),"batch_storage_integrity_failed")

    def test_unknown_document_and_invalid_key_have_no_attempt(self):
        value=deepcopy(self.good);value["document_id"]="unregistered"
        self.assert_http(422,lambda:self.store.create(ANALYST,value,"unknown"),"unregistered_document")
        for key in (True,"", "key with spaces", "a"*129):
            self.assert_http(422,lambda:self.store.create(ANALYST,self.good,key),"invalid_idempotency_key")
        self.assertEqual(self.store.list(ANALYST),[])


if __name__ == "__main__":
    unittest.main()
