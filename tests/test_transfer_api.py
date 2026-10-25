from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creditlens.api import create_app
from creditlens.transfer_store import MANIFEST, RELEASE, REPORT, transfer_image, transfer_snapshot
from creditlens.visual_store import VisualArtifactError
from scripts.prepare_transfer_preview import prepare_preview

ROOT = Path(__file__).resolve().parents[1]


class TransferApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        manifest = json.loads((ROOT/MANIFEST).read_text(encoding="utf-8"))
        files = {MANIFEST, RELEASE, REPORT, *manifest["files"]}
        files.update(manifest.get("reference_files", {}))
        files.update(d["pack_path"] for d in manifest["documents"])
        files.update({"data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json"})
        for name in files:
            target = self.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)

    def test_reviewed_snapshot_keeps_publication_and_reference_limits(self):
        view = transfer_snapshot(self.root)
        self.assertTrue(view["read_only"])
        self.assertEqual(view["report"]["experiment_id"], view["manifest"]["experiment_id"])
        for document in view["report"]["documents"]:
            self.assertIsNone(document["source"]["wrapper_publication_date"])
            self.assertFalse(document["source"]["point_in_time_eligible"])
            self.assertEqual(sum(document["summary"]["line"].values()), len(document["facts"]))
            self.assertEqual(sum(document["summary"]["word"].values()), len(document["facts"]))

    def test_semantically_identical_unreviewed_report_is_not_published(self):
        path = self.root/REPORT
        path.write_bytes(path.read_bytes()+b" ")
        with self.assertRaisesRegex(VisualArtifactError, "reviewed release"):
            transfer_snapshot(self.root)

    def test_manifest_or_registered_reference_drift_is_rejected(self):
        path = self.root/MANIFEST
        raw = path.read_bytes()
        path.write_bytes(raw+b" ")
        with self.assertRaises(ValueError):
            transfer_snapshot(self.root)
        path.write_bytes(raw)
        manifest = json.loads(raw)
        pack = self.root/manifest["documents"][0]["pack_path"]
        pack.write_bytes(pack.read_bytes()+b" ")
        with self.assertRaises(ValueError):
            transfer_snapshot(self.root)

    def test_authentication_read_only_no_store_and_missing_release(self):
        principal = dict(key_sha256=sha256(b"transfer-test").hexdigest(), user_id="viewer",
                         tenant_id="one", role="viewer")
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(
                self.root/"missing.json", self.root/"audit.sqlite", auth_mode="api_key",
                principals=[principal])) as client:
            headers = {"Authorization": "Bearer transfer-test"}
            self.assertEqual(client.get("/api/document-transfer").status_code, 401)
            self.assertEqual(client.get("/api/document-transfer", headers={"X-Demo-Role": "reviewer"}).status_code, 401)
            response = client.get("/api/document-transfer", headers=headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), transfer_snapshot(self.root))
            self.assertEqual(client.post("/api/document-transfer", headers=headers).status_code, 405)
            report = response.json()["report"]["documents"][0]
            endpoint = f"/api/document-transfer/{report['id']}/pages/{report['pages'][0]['page']}.png?expected_sha256={report['pages'][0]['image_sha256']}"
            self.assertEqual(client.get(endpoint).status_code, 401)
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 404)
            changed = endpoint.rsplit('=', 1)[0]+'='+'0'*64
            self.assertEqual(client.get(changed, headers=headers).status_code, 409)
            self.assertEqual(client.get(endpoint.split('?')[0], headers=headers).status_code, 422)
            image = self.root/".run/document-transfer/raw"/report["id"]/f"page-{report['pages'][0]['page']}.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"synthetic damaged image")
            self.assertEqual(client.get(endpoint, headers=headers).status_code, 409)
            (self.root/RELEASE).unlink()
            self.assertEqual(client.get("/api/document-transfer", headers=headers).status_code, 503)

    def test_only_registered_image_bytes_and_ids_can_be_read(self):
        raw = b"synthetic bytes for image delivery checks only"
        view = {"report": {"documents": [{"id": "issuer", "pages": [
            {"page": 5, "image_sha256": sha256(raw).hexdigest()}]}]}}
        path = self.root/".run/document-transfer/raw/issuer/page-5.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        with patch("creditlens.transfer_store.transfer_snapshot", return_value=view):
            self.assertEqual(transfer_image(self.root, "issuer", 5), raw)
            self.assertEqual(transfer_image(self.root, "issuer", 5, expected_sha256=sha256(raw).hexdigest()), raw)
            with self.assertRaisesRegex(VisualArtifactError, "registration changed"):
                transfer_image(self.root, "issuer", 5, expected_sha256="0"*64)
            with self.assertRaises(FileNotFoundError):
                transfer_image(self.root, "issuer", 6)
            with self.assertRaises(FileNotFoundError):
                transfer_image(self.root, "../outside", 5)
            path.write_bytes(b"different")
            with self.assertRaises(VisualArtifactError):
                transfer_image(self.root, "issuer", 5)

    def test_prepare_preview_checks_all_images_and_preserves_existing_files(self):
        raw = b"synthetic preview test bytes"
        view = {"report": {"documents": [{"id": "issuer", "pages": [
            {"page": 5, "image_sha256": sha256(raw).hexdigest()},
            {"page": 6, "image_sha256": sha256(raw).hexdigest()}]}]}}
        source = self.root/".run/candidate"
        (source/"issuer").mkdir(parents=True)
        (source/"issuer/page-5.png").write_bytes(raw)
        (source/"issuer/page-6.png").write_bytes(b"wrong")
        target = self.root/".run/document-transfer/raw/issuer/page-5.png"
        with patch("scripts.prepare_transfer_preview.transfer_snapshot", return_value=view):
            with self.assertRaises(ValueError):
                prepare_preview(self.root, source)
            self.assertFalse(target.exists())
            (source/"issuer/page-6.png").write_bytes(raw)
            self.assertEqual(prepare_preview(self.root, source), {"created": 2, "preserved": 0})
            timestamp = target.stat().st_mtime_ns
            self.assertEqual(prepare_preview(self.root, source), {"created": 0, "preserved": 2})
            self.assertEqual(target.stat().st_mtime_ns, timestamp)
            target.write_bytes(b"existing differing file")
            with self.assertRaises(ValueError):
                prepare_preview(self.root, source)
            self.assertEqual(target.read_bytes(), b"existing differing file")


if __name__ == "__main__":
    unittest.main()
