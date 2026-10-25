"""Read-only release delivery and local preview safety, using synthetic evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.issuer_store import MANIFEST, RELEASE, REPORT, issuer_snapshot, issuer_image
from creditlens.experiment_paths import validate_experiment_paths
from scripts.prepare_issuer_preview import prepare
from tests import test_issuer_evaluation as fixtures


class IssuerApiTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IssuerEvaluationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.report = self.fixture.report()
        self.doc = self.report["documents"][0]
        self.page = self.doc["pages"][0]
        self.write_release(self.report)

    def write_release(self, report):
        self.fixture.fixture.write(self.root / REPORT, report)
        self.fixture.fixture.write(self.root / RELEASE, {"schema_version": 1, "files": {
            p: sha256((self.root/p).read_bytes()).hexdigest() for p in (REPORT, MANIFEST)}})

    def source_images(self):
        source = self.root / ".run/reproductions/issuer-evaluation"
        path = source/self.doc["id"]/f"page-{self.page['page']}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.fixture.fixture.image)
        return source, path

    def test_release_binds_manifest_and_recomputes_public_results(self):
        view = issuer_snapshot(self.root)
        self.assertTrue(view["read_only"])
        self.assertFalse(view["report"]["automatic_approval"])
        self.assertEqual(view["report"], self.report)
        path = self.root/MANIFEST
        raw = path.read_bytes()
        path.write_bytes(raw+b" ")
        with self.assertRaisesRegex(ValueError, "reviewed release"):
            issuer_snapshot(self.root)
        path.write_bytes(raw)
        bad = deepcopy(self.report); bad["summary"]["facts"] += 1
        self.write_release(bad)
        with self.assertRaisesRegex(ValueError, "summary changed"):
            issuer_snapshot(self.root)

    def test_invalid_duplicate_json_release_and_rule_drift_rejected(self):
        (self.root/RELEASE).write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            issuer_snapshot(self.root)
        self.write_release(self.report)
        p = self.root/"creditlens/date_evidence.py"
        p.write_bytes(p.read_bytes()+b"\n# changed\n")
        with self.assertRaisesRegex(ValueError, "dependency changed"):
            issuer_snapshot(self.root)

    def test_preview_is_hash_bound_and_old_runners_cannot_overwrite_it(self):
        source, _ = self.source_images()
        self.assertEqual(prepare(self.root, source), {"created": 1, "preserved": 0})
        self.assertEqual(prepare(self.root, source), {"created": 0, "preserved": 1})
        raw = issuer_image(self.root, self.doc["id"], self.page["page"], expected_sha256=self.page["image_sha256"])
        self.assertEqual(raw, self.fixture.fixture.image)
        target = self.root/".run/document-transfer/issuer-evaluation/raw"/self.doc["id"]/f"page-{self.page['page']}.png"
        with self.assertRaises(ValueError):
            validate_experiment_paths(self.root, target, self.root/".run/fresh-output")
        with self.assertRaisesRegex(ValueError, "reload"):
            issuer_image(self.root, self.doc["id"], self.page["page"], expected_sha256="0"*64)
        with self.assertRaises(FileNotFoundError):
            issuer_image(self.root, self.doc["id"], 999, expected_sha256=self.page["image_sha256"])
        target.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "registered bytes"):
            issuer_image(self.root, self.doc["id"], self.page["page"], expected_sha256=self.page["image_sha256"])
        with self.assertRaisesRegex(ValueError, "refusing to replace"):
            prepare(self.root, source)
        self.assertEqual(target.read_bytes(), b"changed")

    def test_invalid_preview_input_does_not_create_destination(self):
        source, path = self.source_images()
        path.write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "reviewed image"):
            prepare(self.root, source)
        self.assertFalse((self.root/".run/document-transfer/issuer-evaluation/raw").exists())

    def test_api_auth_export_hash_errors_and_read_only(self):
        source, _ = self.source_images(); prepare(self.root, source)
        principal = dict(key_sha256=sha256(b"issuer-test").hexdigest(), user_id="viewer",
                         tenant_id="public", role="viewer")
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(
                self.root/"missing.json", self.root/"audit.sqlite", auth_mode="api_key",
                principals=[principal])) as client:
            headers = {"Authorization": "Bearer issuer-test"}
            endpoint = "/api/issuer-evaluation"
            self.assertEqual(client.get(endpoint).status_code, 401)
            self.assertEqual(client.get(endpoint, headers={"X-Demo-Role": "reviewer"}).status_code, 401)
            response = client.get(endpoint, headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), issuer_snapshot(self.root))
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(client.post(endpoint, headers=headers).status_code, 405)
            image = f"{endpoint}/{self.doc['id']}/pages/{self.page['page']}.png"
            params = {"expected_sha256": self.page["image_sha256"]}
            self.assertEqual(client.get(image, params=params).status_code, 401)
            self.assertEqual(client.get(image, headers=headers).status_code, 422)
            self.assertEqual(client.get(image, headers=headers, params=params).content, self.fixture.fixture.image)
            self.assertEqual(client.get(image, headers=headers, params={"expected_sha256": "0"*64}).status_code, 409)
            (self.root/REPORT).write_bytes(b'{}')
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 409)
            (self.root/RELEASE).unlink()
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 503)


if __name__ == "__main__":
    unittest.main()
