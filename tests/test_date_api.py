"""Release and authenticated delivery of the observed date diagnostic."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creditlens.api import create_app
from creditlens.date_store import RELEASE, REPORT, date_snapshot
from creditlens.date_diagnostic import IMPLEMENTATION_FILES
from creditlens.transfer_store import MANIFEST, RELEASE as SOURCE_RELEASE, REPORT as SOURCE_REPORT
from creditlens.visual_store import VisualArtifactError

ROOT = Path(__file__).resolve().parents[1]


class DateApiTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        manifest = json.loads((ROOT / MANIFEST).read_text(encoding="utf-8"))
        files = {MANIFEST, SOURCE_RELEASE, SOURCE_REPORT, REPORT, RELEASE,
                 *IMPLEMENTATION_FILES, *manifest["files"], *manifest["reference_files"]}
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

    def test_same_source_snapshot_and_legacy_failures_remain_available(self):
        view = date_snapshot(self.root)
        self.assertTrue(view["read_only"])
        self.assertFalse(view["report"]["automatic_approval"])
        self.assertFalse(view["report"]["scope"]["blind_evaluation"])
        source = {d["id"]: d for d in view["source"]["report"]["documents"]}
        for doc in view["report"]["documents"]:
            old = {f["fact_id"]: f for f in source[doc["id"]]["facts"]}
            for fact in doc["facts"]:
                self.assertEqual(fact["legacy_word"], old[fact["fact_id"]]["word"])
                self.assertTrue(fact["non_year_unchanged"])

    def test_release_cannot_silently_change_or_authorize_fabricated_results(self):
        path = self.root / REPORT
        raw = path.read_bytes()
        path.write_bytes(raw + b" ")
        with self.assertRaisesRegex(VisualArtifactError, "reviewed release"):
            date_snapshot(self.root)
        report = json.loads(raw)
        report["summary"]["facts"] += 1
        self.save_reviewed(report)
        with self.assertRaisesRegex(ValueError, "does not reproduce"):
            date_snapshot(self.root)

    def test_source_and_new_rule_drift_are_rejected(self):
        source = self.root / SOURCE_REPORT
        original = source.read_bytes()
        source.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            date_snapshot(self.root)
        source.write_bytes(original)
        implementation = self.root / "creditlens/date_evidence.py"
        implementation.write_bytes(implementation.read_bytes() + b"\n# drift\n")
        with self.assertRaisesRegex(ValueError, "does not reproduce"):
            date_snapshot(self.root)

    def test_api_auth_read_only_errors_and_export_payload(self):
        principal = dict(key_sha256=sha256(b"date-test").hexdigest(), user_id="viewer",
                         tenant_id="public", role="viewer")
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(
                self.root/"missing.json", self.root/"audit.sqlite", auth_mode="api_key",
                principals=[principal])) as client:
            headers = {"Authorization": "Bearer date-test"}
            self.assertEqual(client.get("/api/date-diagnostic").status_code, 401)
            self.assertEqual(client.get("/api/date-diagnostic", headers={"X-Demo-Role": "reviewer"}).status_code, 401)
            response = client.get("/api/date-diagnostic", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), date_snapshot(self.root))
            self.assertEqual(client.post("/api/date-diagnostic", headers=headers).status_code, 405)
            (self.root / RELEASE).write_text('["schema_version", "files"]', encoding="utf-8")
            self.assertEqual(client.get("/api/date-diagnostic", headers=headers).status_code, 409)
            (self.root / RELEASE).unlink()
            self.assertEqual(client.get("/api/date-diagnostic", headers=headers).status_code, 503)


if __name__ == "__main__":
    unittest.main()
