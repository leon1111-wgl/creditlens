from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from creditlens.api import create_app
from creditlens.research import research_view
from creditlens.temporal import TemporalPack, quarterly_snapshot


ROOT = Path(__file__).resolve().parents[1]
PACK = TemporalPack.model_validate_json((ROOT / "data/examples/msft-fy2025-temporal.json").read_text(encoding="utf-8"))


class ResearchViewTests(unittest.TestCase):
    def test_publication_day_has_no_facts_or_freshness_from_future(self):
        result = research_view(PACK, date(2024, 10, 30))
        self.assertEqual(result["quality"], dict(reported=0, derived_requires_review=0, missing=12, blocked=0))
        self.assertIsNone(result["freshness"]["latest_visible_publication"])
        self.assertEqual(result["freshness"]["visible_source_captures"], [])
        self.assertEqual(result["snapshot"]["evidence"], dict(facts=[], sources=[]))

    def test_next_day_reveals_only_first_quarter(self):
        result = research_view(PACK, date(2024, 10, 31))
        self.assertEqual(result["quality"]["reported"], 3)
        self.assertEqual(result["quality"]["missing"], 9)
        self.assertEqual(result["freshness"]["latest_visible_publication"], "2024-10-30")
        self.assertEqual(len(result["snapshot"]["evidence"]["sources"]), 1)

    def test_web_view_preserves_cli_snapshot_and_no_training_approval(self):
        result = research_view(PACK, date(2025, 7, 31))
        self.assertEqual(result["snapshot"], quarterly_snapshot(PACK, date(2025, 7, 31)))
        self.assertFalse(result["is_live"])
        self.assertFalse(result["freshness"]["historical_vintage_verified"])
        self.assertEqual(result["quality"]["derived_requires_review"], 3)
        self.assertFalse(result["snapshot"]["usable_for_prediction_training"])
        for row in result["snapshot"]["quarters"]:
            if row["quarter"] == 4:
                self.assertFalse(row["eligible_as_observed"])
                self.assertEqual(row["status"], "derived_requires_review")

    def test_input_fingerprint_binds_cutoff_even_when_evidence_is_unchanged(self):
        one = quarterly_snapshot(PACK, date(2025, 7, 31))
        two = quarterly_snapshot(PACK, date(2025, 8, 1))
        self.assertEqual(one["evidence"], two["evidence"])
        self.assertNotEqual(one["snapshot_sha256"], two["snapshot_sha256"])

    def test_input_fingerprint_binds_company(self):
        other = PACK.model_copy(update={"entity_id": "OTHER"})
        self.assertNotEqual(quarterly_snapshot(PACK, date(2025, 7, 31))["snapshot_sha256"],
                            quarterly_snapshot(other, date(2025, 7, 31))["snapshot_sha256"])


class ResearchApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        principals = [dict(key_sha256=hashlib.sha256(key.encode()).hexdigest(), user_id=user,
                           tenant_id=tenant, role="viewer") for key, user, tenant in
                      [("a-key", "alice", "a"), ("b-key", "bob", "b")]]
        self.client = TestClient(create_app(root / "missing.json", root / "audit.sqlite",
                                           auth_mode="api_key", principals=principals))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def get(self, query="", key="a-key"):
        return self.client.get("/api/research/quarters" + query, headers={"Authorization": "Bearer " + key})

    def test_no_key_and_forged_demo_role_are_rejected(self):
        self.assertEqual(self.client.get("/api/research/quarters").status_code, 401)
        self.assertEqual(self.client.get("/api/research/quarters", headers={"X-Demo-Role": "viewer"}).status_code, 401)
        self.assertEqual(self.get(key="wrong-key").status_code, 401)

    def test_public_example_is_read_only_and_identical_across_authorized_tenants(self):
        one, two = self.get(), self.get(key="b-key")
        self.assertEqual(one.status_code, 200)
        self.assertEqual(one.json(), two.json())
        self.assertEqual(one.json()["dataset_scope"], "registered_public_historical_example")
        self.assertEqual(one.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.post("/api/research/quarters", headers={"Authorization": "Bearer a-key"}).status_code, 405)
        for key in ("a-key", "b-key"):
            self.assertEqual(self.client.get("/api/reviews", headers={"Authorization": "Bearer " + key}).json(), [])

    def test_unknown_dataset_file_path_and_invalid_date_rejected(self):
        for query in ("?dataset=aapl", "?dataset=../../data/workflow.sqlite", "?as_of=2025-02-30"):
            with self.subTest(query=query):
                self.assertEqual(self.get(query).status_code, 422)

    def test_api_uses_applied_date_and_exports_no_future_source(self):
        result = self.get("?as_of=2025-01-30").json()
        self.assertEqual(result["snapshot"]["as_of"], "2025-01-30")
        self.assertEqual(result["quality"]["reported"], 6)
        self.assertEqual(result["quality"]["missing"], 6)
        self.assertNotIn("msft-fy25-q4", json.dumps(result))
        self.assertEqual(result, research_view(PACK, date(2025, 1, 30)))

    def test_research_shell_is_available_without_exposing_credentials(self):
        response = self.client.get("/research")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertNotIn("a-key", response.text)
        self.assertNotIn("b-key", response.text)


if __name__ == "__main__":
    unittest.main()
