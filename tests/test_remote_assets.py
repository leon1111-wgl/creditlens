"""Mock-network download controls; no external files are fetched by these tests."""
from hashlib import sha1, sha256
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.request import Request

from scripts.fetch_registered_sources import (
    RegisteredRedirects, RemoteAssetError, destination_path, fetch_asset, load_registry, main,
)


class RemoteAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = b"%PDF-1.7\nsynthetic test bytes\n"
        self.asset = {"id": "example-pdf", "title": "Synthetic fixture", "kind": "pdf",
                      "url": "https://example.org/file.pdf", "sha256": sha256(self.raw).hexdigest(),
                      "filename": "example.pdf", "max_bytes": 1000, "allowed_hosts": ["example.org"],
                      "license_note": "Synthetic test data"}
        self.registry()

    def registry(self):
        path = self.root / "data/remote-assets.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": 1, "assets": [self.asset]}), encoding="utf-8")

    def mock_network(self, raw=None, *, url=None, content_length=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = url or self.asset["url"]
        response.headers = {} if content_length is None else {"Content-Length": content_length}
        response.read.side_effect = BytesIO(self.raw if raw is None else raw).read
        opener = MagicMock()
        opener.open.return_value = response
        patched = patch("scripts.fetch_registered_sources.build_opener", return_value=opener)
        patched.start()
        self.addCleanup(patched.stop)
        return opener

    def target(self):
        return self.root / ".run/downloads" / self.asset["id"] / self.asset["filename"]

    def test_verified_file_is_created_only_once(self):
        opener = self.mock_network(content_length=str(len(self.raw)))
        result = fetch_asset(self.root, "example-pdf")
        self.assertTrue(result["verified"])
        self.assertEqual(self.target().read_bytes(), self.raw)
        with self.assertRaises(RemoteAssetError):
            fetch_asset(self.root, "example-pdf")
        self.assertEqual(opener.open.call_count, 1)

    def test_hash_mismatch_does_not_create_accepted_file_or_cache_directory(self):
        self.mock_network(b"%PDF-1.7\nchanged bytes")
        with self.assertRaises(RemoteAssetError):
            fetch_asset(self.root, "example-pdf")
        self.assertFalse(self.target().exists())
        self.assertFalse((self.root / ".run").exists())

    def test_declared_and_streamed_size_limits_are_enforced(self):
        for declared in ("1001", None):
            with self.subTest(declared=declared):
                self.mock_network(b"x" * 1001, content_length=declared)
                with self.assertRaises(RemoteAssetError):
                    fetch_asset(self.root, "example-pdf")
                self.assertFalse(self.target().exists())

    def test_redirects_reject_unregistered_hosts_and_downgrade(self):
        handler = RegisteredRedirects(["example.org"])
        request = Request(self.asset["url"])
        for target in ("https://evil.example/file", "http://example.org/file", "https://user:pass@example.org/file",
                       "https://example.org:444/file"):
            with self.subTest(target=target), self.assertRaises(RemoteAssetError):
                handler.redirect_request(request, None, 302, "Found", {}, target)
        accepted = handler.redirect_request(request, None, 302, "Found", {}, "/next.pdf")
        self.assertEqual(accepted.full_url, "https://example.org/next.pdf")

    def test_final_response_url_is_checked_even_if_handler_is_bypassed(self):
        self.mock_network(url="https://other.example/file.pdf")
        with self.assertRaises(RemoteAssetError):
            fetch_asset(self.root, "example-pdf")
        self.assertFalse(self.target().exists())

    def test_unknown_asset_and_disallowed_output_do_not_use_network(self):
        opener = self.mock_network()
        with self.assertRaises(RemoteAssetError):
            fetch_asset(self.root, "not-registered")
        for path in (self.root / "data/raw/new.pdf", self.root / ".run/document-ai/new.pdf",
                     self.root.parent / "outside.pdf", self.root / ".run/downloads/../new.pdf"):
            with self.assertRaises(RemoteAssetError):
                fetch_asset(self.root, "example-pdf", output=path)
        opener.open.assert_not_called()

    def test_private_cache_alias_cannot_redirect_into_public_data(self):
        public = self.root / "data"
        run = self.root / ".run"
        run.mkdir()
        try:
            (run / "downloads").symlink_to(public, target_is_directory=True)
        except OSError:
            # Windows may disallow creating symlinks; still exercise the alias guard itself.
            original = Path.is_symlink
            with patch.object(Path, "is_symlink", lambda p: p == run or original(p)):
                with self.assertRaises(RemoteAssetError):
                    destination_path(self.root, self.asset)
        else:
            with self.assertRaises(RemoteAssetError):
                destination_path(self.root, self.asset)
        self.assertFalse((public / "example-pdf").exists())

    def test_registered_json_is_checked_as_json_and_git_blob(self):
        self.raw = json.dumps([{"id": "sample", "table": [], "pre_text": [], "post_text": [], "qa": {}}]).encode()
        self.asset.update(id="finqa-test", kind="finqa_json", filename="dev.json", url="https://example.org/dev.json",
                          records=1, revision="a" * 40, sha256=sha256(self.raw).hexdigest(),
                          git_blob_sha1=sha1(b"blob " + str(len(self.raw)).encode() + b"\0" + self.raw).hexdigest())
        self.registry()
        self.mock_network()
        self.assertTrue(fetch_asset(self.root, "finqa-test")["verified"])
        self.assertEqual(json.loads(self.target().read_bytes())[0]["id"], "sample")

    def test_malformed_format_never_becomes_accepted_file(self):
        self.raw = b"not a PDF"
        self.asset["sha256"] = sha256(self.raw).hexdigest()
        self.registry()
        self.mock_network()
        with self.assertRaises(RemoteAssetError):
            fetch_asset(self.root, "example-pdf")
        self.assertFalse(self.target().exists())

    def test_cli_without_explicit_asset_does_not_download(self):
        with patch("scripts.fetch_registered_sources.fetch_asset") as fetch:
            with self.assertRaises(SystemExit) as caught:
                main([])
            self.assertEqual(caught.exception.code, 2)
            fetch.assert_not_called()

    def test_real_registry_matches_existing_frozen_source_hashes(self):
        root = Path(__file__).resolve().parents[1]
        assets = load_registry(root)
        self.assertEqual(set(assets), {"msft-fy2025", "aapl-fy2025", "nvda-fy2025", "amd-fy2025", "finqa-dev"})
        for issuer in ("msft", "aapl"):
            source = json.loads((root / f"data/examples/{issuer}-fy2025-pdf.json").read_text(encoding="utf-8"))["sources"][0]
            self.assertEqual((assets[issuer + "-fy2025"]["url"], assets[issuer + "-fy2025"]["sha256"]),
                             (source["url"], source["sha256"]))
        for issuer, config in (("nvda", "document-transfer"), ("amd", "issuer-evaluation")):
            pdf = json.loads((root / f"configs/{config}.json").read_text(encoding="utf-8"))["documents"][0]["pdf"]
            self.assertEqual(assets[issuer + "-fy2025"]["url"], pdf["url"])
            self.assertEqual(assets[issuer + "-fy2025"]["sha256"], pdf["sha256"])
        manifest = json.loads((root / "data/manifest.json").read_text(encoding="utf-8"))
        for field in ("sha256", "git_blob_sha1", "revision", "records"):
            self.assertEqual(assets["finqa-dev"][field], manifest[field])
        self.assertEqual(assets["finqa-dev"]["url"], manifest["source_url"])


if __name__ == "__main__":
    unittest.main()
