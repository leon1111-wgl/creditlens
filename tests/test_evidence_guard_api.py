"""Reviewed diagnostic delivery, authentication and corruption handling."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.evidence_guard_diagnostic import IMPLEMENTATION_FILES
from creditlens.evidence_guard_store import REPORT, RELEASE, evidence_guard_snapshot
from creditlens.currency_store import REPORT as CURRENCY_REPORT, RELEASE as CURRENCY_RELEASE
from creditlens.issuer_store import MANIFEST, REPORT as SOURCE_REPORT, RELEASE as SOURCE_RELEASE

ROOT = Path(__file__).resolve().parents[1]


class EvidenceGuardApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        files = {MANIFEST, SOURCE_REPORT, SOURCE_RELEASE, CURRENCY_REPORT, CURRENCY_RELEASE,
                 REPORT, RELEASE, "data/evaluation/evidence-guard-cases.json",
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

    def test_read_only_diagnostic_preserves_old_results_and_scope(self):
        result = evidence_guard_snapshot(self.root)
        self.assertTrue(result["read_only"])
        for doc in result["report"]["documents"]:
            for fact in doc["facts"]:
                self.assertFalse(fact["guarded"]["automatic_approval"])
                self.assertTrue(fact["non_amount_unchanged"])
                self.assertEqual(fact["guarded"]["legacy_currency_result"], fact["currency"])

    def test_rehashing_fabricated_summary_does_not_make_it_valid(self):
        report = json.loads((self.root / REPORT).read_text(encoding="utf-8"))
        report["summary"]["facts"] += 1
        self.save_reviewed(report)
        with self.assertRaises(ValueError):
            evidence_guard_snapshot(self.root)

    def test_rehashing_changed_control_does_not_change_expectation(self):
        report = json.loads((self.root / REPORT).read_text(encoding="utf-8"))
        report["controls"]["cases"][0]["expected"]["overall_status"] = "unknown"
        self.save_reviewed(report)
        with self.assertRaises(ValueError):
            evidence_guard_snapshot(self.root)

    def test_source_and_rule_drift_fail_closed(self):
        for name in [REPORT, SOURCE_REPORT, "creditlens/evidence_guard.py"]:
            with self.subTest(name=name):
                path = self.root / name
                raw = path.read_bytes()
                path.write_bytes(raw + b" \n")
                with self.assertRaises(ValueError):
                    evidence_guard_snapshot(self.root)
                path.write_bytes(raw)

    def test_ambiguous_or_wrong_release_schema_is_rejected(self):
        for raw in ['{"schema_version":1,"schema_version":1,"files":{}}',
                    '{"schema_version":true,"files":{}}',
                    '{"schema_version":1,"files":[],"extra":0}', '[1]']:
            with self.subTest(raw=raw):
                (self.root / RELEASE).write_text(raw, encoding="utf-8")
                with self.assertRaises(ValueError):
                    evidence_guard_snapshot(self.root)

    def test_authenticated_api_and_explainable_error_states(self):
        key = "evidence-guard-test"
        principal = dict(key_sha256=sha256(key.encode()).hexdigest(), user_id="viewer",
                         tenant_id="public", role="viewer")
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(
                self.root / "missing.json", self.root / "audit.sqlite", auth_mode="api_key",
                principals=[principal])) as client:
            endpoint, headers = "/api/evidence-guard", {"Authorization": "Bearer " + key}
            self.assertEqual(client.get(endpoint).status_code, 401)
            self.assertEqual(client.get(endpoint, headers={"X-Demo-Role": "reviewer"}).status_code, 401)
            response = client.get(endpoint, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), evidence_guard_snapshot(self.root))
            self.assertEqual(client.post(endpoint, headers=headers).status_code, 405)
            (self.root / REPORT).write_text("{}", encoding="utf-8")
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 409)
            (self.root / RELEASE).unlink()
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 503)
