import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from creditlens.api import create_app
from creditlens.diligence import CasePack, analyze_pack
from creditlens.workflow import Actor, WorkflowService

PACK = json.loads((Path(__file__).resolve().parents[1] / "data/examples/msft-fy2025.json").read_text(encoding="utf-8"))


class FactTests(unittest.TestCase):
    def test_verified_source_values_and_cash_flow(self):
        p = CasePack.model_validate(PACK)
        r = analyze_pack(p, date(2025, 8, 1), 20)
        m = {i["id"]: i for i in r["metrics"]}
        self.assertEqual(m["fcf"]["value"], "71611")
        self.assertEqual(m["stress_fcf"]["value"], "44378.6")
        self.assertEqual(m["fcf"]["fact_ids"], ["cfo-2025", "capex-2025"])
        self.assertEqual(r["blocking_count"], 0)
        self.assertIn("capex_pressure", [i["id"] for i in r["issues"]])

    def test_future_disclosures_are_not_available(self):
        r = analyze_pack(CasePack.model_validate(PACK), date(2025, 7, 29))
        self.assertEqual(r["metrics"], [])
        self.assertEqual(r["excluded_future_facts"], 20)
        self.assertGreater(r["blocking_count"], 0)

    def test_mixed_currency_blocks_formula(self):
        pack = copy.deepcopy(PACK)
        next(f for f in pack["facts"] if f["id"] == "capex-2025")["unit"] = "CNY_million"
        r = analyze_pack(CasePack.model_validate(pack), date(2025, 8, 1))
        self.assertNotIn("fcf", [i["id"] for i in r["metrics"]])
        self.assertIn("basis_fcf", [i["id"] for i in r["issues"]])

    def test_mixed_reporting_period_blocks_formula(self):
        pack = copy.deepcopy(PACK)
        next(f for f in pack["facts"] if f["id"] == "capex-2025")["period_start"] = "2025-01-01"
        r = analyze_pack(CasePack.model_validate(pack), date(2025, 8, 1))
        self.assertNotIn("fcf", [i["id"] for i in r["metrics"]])

    def test_conflicting_fact_cannot_be_silently_selected(self):
        pack = copy.deepcopy(PACK)
        f = copy.deepcopy(next(f for f in pack["facts"] if f["id"] == "cfo-2025"))
        f.update(id="conflicting-cfo", value="1")
        pack["facts"].append(f)
        r = analyze_pack(CasePack.model_validate(pack), date(2025, 8, 1))
        self.assertIn("conflict_cfo", [i["id"] for i in r["issues"]])
        self.assertNotIn("fcf", [i["id"] for i in r["metrics"]])

    def test_zero_and_negative_denominators_are_not_misleading_ratios(self):
        pack = copy.deepcopy(PACK)
        for f in pack["facts"]:
            if f["id"] in {"cfo-2025", "net_income-2025", "current_debt-2025"}:
                f["value"] = "0"
        r = analyze_pack(CasePack.model_validate(pack), date(2025, 8, 1))
        self.assertNotIn("liquidity", [i["id"] for i in r["metrics"]])
        self.assertNotIn("cash_conversion", [i["id"] for i in r["metrics"]])

    def test_broken_source_and_duplicate_ids_rejected(self):
        for mutate in (lambda p: p["facts"][0].update(source_id="missing"),
                       lambda p: p["facts"].append(copy.deepcopy(p["facts"][0]))):
            pack = copy.deepcopy(PACK)
            mutate(pack)
            with self.assertRaises(ValidationError):
                CasePack.model_validate(pack)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        entries = [("a-key", "alice", "bank-a", "analyst"),
                   ("r-key", "ruth", "bank-a", "reviewer"),
                   ("self-key", "alice", "bank-a", "reviewer"),
                   ("v-key", "victor", "bank-a", "viewer"),
                   ("b-key", "beth", "bank-b", "analyst")]
        principals = [dict(key_sha256=hashlib.sha256(k.encode()).hexdigest(), user_id=u, tenant_id=t, role=r)
                      for k, u, t, r in entries]
        self.client = TestClient(create_app(self.root / "missing.json", self.root / "audit.sqlite",
                                           auth_mode="api_key", principals=principals))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def headers(self, key="a-key", idem="test-request-001"):
        return {"Authorization": "Bearer " + key, "Idempotency-Key": idem}

    def create(self, body=None, key="a-key", idem="test-request-001"):
        return self.client.post("/api/reviews/example", headers=self.headers(key, idem), json=body or {})

    def change(self, case, action, key="a-key", **kw):
        return self.client.post("/api/reviews/" + case["id"] + "/actions", headers=self.headers(key),
                                json={"revision": case["revision"], "action": action, **kw})

    def submitted(self):
        case = self.create().json()
        for issue in case["analysis"]["issues"]:
            response = self.change(case, "disposition", issue_id=issue["id"], disposition="accepted_limitation",
                                   note="Reviewed source facts; scope limitation remains explicit.")
            self.assertEqual(response.status_code, 200)
            case = response.json()
        response = self.change(case, "submit")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_missing_auth_and_forged_demo_identity_rejected(self):
        self.assertEqual(self.client.get("/api/reviews").status_code, 401)
        self.assertEqual(self.client.get("/api/reviews", headers={"X-Demo-Role": "reviewer"}).status_code, 401)

    def test_role_enforcement(self):
        self.assertEqual(self.create(key="v-key").status_code, 403)
        self.assertEqual(self.create(key="r-key").status_code, 403)

    def test_cross_tenant_read_list_action_and_export_blocked(self):
        case = self.create().json()
        path = "/api/reviews/" + case["id"]
        for route in (path, path + "/memo"):
            self.assertEqual(self.client.get(route, headers=self.headers("b-key")).status_code, 404)
        self.assertEqual(self.client.get("/api/reviews", headers=self.headers("b-key")).json(), [])
        self.assertEqual(self.change(case, "submit", key="b-key").status_code, 404)

    def test_create_idempotency_and_payload_mismatch(self):
        one, two = self.create(), self.create()
        self.assertEqual(one.json()["id"], two.json()["id"])
        self.assertEqual(self.create({"stress_pct": 30}).status_code, 409)

    def test_unresolved_items_and_blocking_data_cannot_be_submitted(self):
        case = self.create().json()
        self.assertEqual(self.change(case, "submit").status_code, 422)
        blocked = self.create({"as_of": "2025-01-01"}, idem="future-date-001").json()
        self.assertEqual(self.change(blocked, "disposition", issue_id="no_evidence", disposition="verified",
                                     note="Attempt to waive missing data incorrectly").status_code, 422)

    def test_self_review_blocked_even_with_reviewer_role(self):
        case = self.submitted()
        self.assertEqual(self.change(case, "approve", key="self-key", note="I cannot approve my own source review.").status_code, 403)

    def test_review_roundtrip_and_immutable_reviewed_snapshot(self):
        case = self.submitted()
        response = self.change(case, "approve", key="r-key", note="Independent review complete; this is not a lending decision.")
        self.assertEqual(response.status_code, 200)
        reviewed = response.json()
        self.assertEqual(reviewed["status"], "reviewed")
        self.assertTrue(reviewed["event_integrity_valid"])
        self.assertEqual(self.change(reviewed, "disposition", issue_id="scope_review", disposition="verified",
                                     note="Attempt to edit an already reviewed case").status_code, 409)
        memo = self.client.get("/api/reviews/" + case["id"] + "/memo", headers=self.headers("v-key"))
        self.assertEqual(memo.status_code, 200)
        self.assertIn("71611", memo.text)
        self.assertIn("microsoft.com", memo.text)

    def test_return_and_resubmit(self):
        case = self.submitted()
        returned = self.change(case, "return", key="r-key", note="Please expand the explanation of liquidity restrictions.").json()
        self.assertEqual(returned["status"], "returned")
        changed = self.change(returned, "disposition", issue_id="scope_review", disposition="accepted_limitation",
                              note="Restricted cash and leases require additional source verification.").json()
        self.assertEqual(self.change(changed, "submit").json()["status"], "in_review")

    def test_stale_revision_rejected(self):
        case = self.create().json()
        args = dict(issue_id="scope_review", disposition="verified", note="Check source availability and coverage.")
        self.assertEqual(self.change(case, "disposition", **args).status_code, 200)
        self.assertEqual(self.change(case, "disposition", **args).status_code, 409)

    def test_concurrent_edits_have_exactly_one_winner(self):
        case = self.create().json()
        svc = self.client.app.state.workflow
        actor = Actor("alice", "bank-a", "analyst")
        def edit(_):
            try:
                svc.update(case["id"], actor, 1, "disposition", issue_id="scope_review",
                           disposition="verified", note="Concurrency check with explicit snapshot revision")
                return 200
            except HTTPException as e:
                return e.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(edit, range(2))), [200, 409])

    def test_tampered_event_is_detected(self):
        case = self.create().json()
        svc = self.client.app.state.workflow
        with svc.db(write=True) as db:
            row = db.execute("SELECT payload FROM events WHERE case_id=?", (case["id"],)).fetchone()
            event = json.loads(row[0]); event["note"] = "tampered"
            db.execute("UPDATE events SET payload=? WHERE case_id=?", (json.dumps(event), case["id"]))
        result = self.client.get("/api/reviews/"+case["id"], headers=self.headers()).json()
        self.assertFalse(result["event_integrity_valid"])
        self.assertEqual(self.change(case, "disposition", issue_id="scope_review", disposition="verified",
                                     note="Do not approve data after an integrity failure").status_code, 409)

    def test_cross_origin_write_and_benchmark_escape_blocked(self):
        headers = {**self.headers(), "Origin": "https://foreign.example"}
        self.assertEqual(self.client.post("/api/reviews/example", headers=headers, json={}).status_code, 403)
        self.assertEqual(self.client.get("/api/cases", headers=self.headers()).status_code, 403)

    def test_request_schema_and_blank_notes(self):
        self.assertEqual(self.create({"tenant": "bank-b"}).status_code, 422)
        case = self.create().json()
        self.assertEqual(self.change(case, "disposition", issue_id="scope_review", disposition="verified", note=" "*20).status_code, 422)

    def test_auth_configuration_fails_closed(self):
        with self.assertRaises(ValueError):
            with TestClient(create_app(self.root/"missing.json", self.root/"another.sqlite", auth_mode="api_key", principals=[])):
                pass


if __name__ == "__main__":
    unittest.main()
