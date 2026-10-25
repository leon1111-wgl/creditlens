"""Release validation, authenticated delivery and immutable old source checks."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.currency_diagnostic import IMPLEMENTATION_FILES
from creditlens.currency_store import REPORT, RELEASE, currency_snapshot
from creditlens.issuer_store import MANIFEST, REPORT as SOURCE_REPORT, RELEASE as SOURCE_RELEASE

ROOT = Path(__file__).resolve().parents[1]


class CurrencyApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        files = {MANIFEST, SOURCE_REPORT, SOURCE_RELEASE, REPORT, RELEASE,
                 *manifest["files"], *manifest["reference_files"], *IMPLEMENTATION_FILES}
        files.update(d["pack_path"] for d in manifest["documents"])
        files.update({"data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json"})
        for name in files:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def save_reviewed(self, report):
        raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (self.root / REPORT).write_bytes(raw)
        (self.root / RELEASE).write_text(json.dumps({"schema_version": 1,
            "files": {REPORT: sha256(raw).hexdigest()}}), encoding="utf-8")

    def test_complete_old_results_and_non_amount_checks_are_preserved(self):
        view = currency_snapshot(self.root)
        self.assertTrue(view["read_only"])
        self.assertFalse(view["report"]["automatic_approval"])
        self.assertFalse(view["report"]["scope"]["blind_evaluation"])
        sources = {d["id"]: d for d in view["source"]["report"]["documents"]}
        for doc in view["report"]["documents"]:
            old = {f["fact_id"]: f for f in sources[doc["id"]]["facts"]}
            self.assertEqual({f["fact_id"] for f in doc["facts"]}, set(old))
            for fact in doc["facts"]:
                self.assertEqual(fact["legacy_date"], old[fact["fact_id"]]["date"])
                self.assertTrue(fact["non_amount_unchanged"])
                self.assertFalse(fact["currency"]["automatic_approval"])

    def test_rehashing_a_fabricated_summary_does_not_authorize_it(self):
        report = json.loads((self.root / REPORT).read_text(encoding="utf-8"))
        report["summary"]["facts"] += 1
        self.save_reviewed(report)
        with self.assertRaises(ValueError):
            currency_snapshot(self.root)

    def test_rehashing_modified_control_expectation_is_rejected(self):
        report = json.loads((self.root / REPORT).read_text(encoding="utf-8"))
        report["controls"]["cases"][0]["expected"]["overall_status"] = "unknown"
        self.save_reviewed(report)
        with self.assertRaises(ValueError):
            currency_snapshot(self.root)

    def test_byte_drift_source_drift_and_rule_drift_fail_closed(self):
        for name in [REPORT, SOURCE_REPORT, "creditlens/currency_evidence.py"]:
            with self.subTest(name=name):
                path = self.root / name
                raw = path.read_bytes()
                path.write_bytes(raw + b" \n")
                with self.assertRaises(ValueError):
                    currency_snapshot(self.root)
                path.write_bytes(raw)

    def test_strict_release_json_and_schema(self):
        for raw in ['{"schema_version":1,"schema_version":1,"files":{}}',
                    '{"schema_version":true,"files":{}}',
                    '{"schema_version":1,"files":[],"extra":0}',
                    '["schema_version","files"]']:
            with self.subTest(raw=raw):
                (self.root / RELEASE).write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    currency_snapshot(self.root)

    def test_api_auth_read_only_error_states_and_exact_payload(self):
        principal = dict(key_sha256=sha256(b"currency-test").hexdigest(), user_id="viewer",
                         tenant_id="public", role="viewer")
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(
                self.root / "missing.json", self.root / "audit.sqlite", auth_mode="api_key",
                principals=[principal])) as client:
            endpoint = "/api/currency-diagnostic"
            headers = {"Authorization": "Bearer currency-test"}
            self.assertEqual(client.get(endpoint).status_code, 401)
            self.assertEqual(client.get(endpoint, headers={"X-Demo-Role": "reviewer"}).status_code, 401)
            response = client.get(endpoint, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), currency_snapshot(self.root))
            self.assertEqual(client.post(endpoint, headers=headers).status_code, 405)
            (self.root / REPORT).write_text("{}", encoding="utf-8")
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 409)
            (self.root / RELEASE).unlink()
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 503)


if __name__ == "__main__":
    unittest.main()
