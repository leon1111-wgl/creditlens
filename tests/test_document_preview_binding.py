"""Caller report bindings for local previews; synthetic bytes, no new OCR."""
import hashlib
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from creditlens.api import create_app
from creditlens.visual_store import VisualArtifactError, VisualStore
from tests import test_visual


class DocumentPreviewBindingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_visual.VisualStorageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.patch_root = patch("creditlens.api.ROOT", self.fixture.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        principals = [{"key_sha256": hashlib.sha256(b"preview-viewer-key").hexdigest(),
                       "user_id": "preview-viewer", "tenant_id": "preview-workspace",
                       "role": "viewer"}]
        self.client = TestClient(create_app(
            self.fixture.root/"missing.json", self.fixture.root/"preview-api.sqlite",
            auth_mode="api_key", principals=principals))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"Authorization": "Bearer preview-viewer-key"}
        self.route = "/api/documents/msft/pages/50.png"
        self.expected = hashlib.sha256(self.fixture.raw).hexdigest()

    def get(self, expected=None, route=None):
        return self.client.get(route or self.route, headers=self.headers,
                               params={"expected_sha256": expected or self.expected})

    def replace_registered_preview(self, raw):
        self.fixture.report["documents"][0]["pages"][0]["image_sha256"] = hashlib.sha256(raw).hexdigest()
        self.fixture.save()
        self.fixture.image_path.write_bytes(raw)

    def test_matching_report_returns_same_bytes_without_cache(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.fixture.raw)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_report_replacement_rejects_old_binding_and_accepts_new_binding(self):
        report_a = self.client.get("/api/documents", headers=self.headers).json()
        expected_a = report_a["documents"][0]["pages"][0]["image_sha256"]
        raw_b = self.fixture.raw+b"-replacement-render-B"
        self.replace_registered_preview(raw_b)
        stale = self.get(expected_a)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.headers["cache-control"], "no-store")
        self.assertNotEqual(stale.headers["content-type"], "image/png")
        self.assertNotIn(raw_b, stale.content)
        self.assertNotIn(expected_a, stale.text)
        current = self.get(hashlib.sha256(raw_b).hexdigest())
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.content, raw_b)

    def test_missing_or_invalid_binding_is_rejected_before_preview_read(self):
        with patch("creditlens.api.VisualStore.image") as image:
            self.assertEqual(self.client.get(self.route, headers=self.headers).status_code, 422)
            for value in ("", "0"*63, "0"*65, "a"*63+"G", self.expected.upper(),
                          " "+self.expected, self.expected+"\n", "null"):
                with self.subTest(value=value):
                    response = self.client.get(self.route, headers=self.headers,
                                               params={"expected_sha256": value})
                    self.assertEqual(response.status_code, 422)
            image.assert_not_called()

    def test_existing_image_integrity_is_required_even_if_caller_hashes_tampering(self):
        changed = self.fixture.raw+b"-unregistered-change"
        self.fixture.image_path.write_bytes(changed)
        response = self.get(hashlib.sha256(changed).hexdigest())
        self.assertEqual(response.status_code, 409)

    def test_unknown_or_missing_preview_stays_not_found(self):
        for route in ("/api/documents/unregistered/pages/50.png",
                      "/api/documents/msft/pages/51.png",
                      "/api/documents/msft/pages/-1.png"):
            with self.subTest(route=route):
                self.assertEqual(self.get(route=route).status_code, 404)
        self.fixture.image_path.unlink()
        self.assertEqual(self.get().status_code, 404)

    def test_authorization_cannot_be_bypassed_by_a_binding(self):
        with patch("creditlens.api.VisualStore.image") as image:
            for headers in ({}, {"X-Demo-Role": "viewer"}, {"Authorization": "Bearer wrong-key"}):
                for params in ({}, {"expected_sha256": self.expected}):
                    with self.subTest(headers=headers, has_binding=bool(params)):
                        self.assertEqual(self.client.get(self.route, headers=headers, params=params).status_code, 401)
            image.assert_not_called()

    def test_response_uses_validated_bytes_not_a_later_file_read(self):
        original_image = VisualStore.image
        raw_b = self.fixture.raw+b"-updated-after-validated-read"

        def change_after_read(store, document_id, page):
            raw = original_image(store, document_id, page)
            self.replace_registered_preview(raw_b)
            return raw

        with patch("creditlens.api.VisualStore.image", autospec=True, side_effect=change_after_read) as image:
            response = self.get()
        self.assertEqual(image.call_count, 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, self.fixture.raw)
        self.assertEqual(hashlib.sha256(response.content).hexdigest(), self.expected)
        self.assertEqual(self.get().status_code, 409)

    def test_read_failures_are_controlled_without_exposing_local_error_details(self):
        private_detail = "SYNTHETIC_PRIVATE_PATH_C:/private/preview.png"
        for error, status in ((FileNotFoundError(private_detail), 404),
                              (PermissionError(private_detail), 409),
                              (OSError(private_detail), 409),
                              (VisualArtifactError(private_detail), 409)):
            with self.subTest(error=type(error).__name__), patch("creditlens.api.VisualStore.image", side_effect=error):
                response = self.get()
                self.assertEqual(response.status_code, status)
                self.assertNotIn(private_detail, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
