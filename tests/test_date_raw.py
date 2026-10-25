"""Synthetic full-input verification; this fixture is not financial evaluation."""
from hashlib import sha256
import json
import unittest

from creditlens.date_diagnostic import build_date_diagnostic
from creditlens.date_store import REPORT, RELEASE
from scripts.verify_date_raw import verify
from tests import test_date_diagnostic as fixtures


class DateRawTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DateDiagnosticTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        report = build_date_diagnostic(self.root)
        raw = json.dumps(report).encode()
        (self.root/REPORT).write_bytes(raw)
        (self.root/RELEASE).write_text(json.dumps({"schema_version": 1,
            "files": {REPORT: sha256(raw).hexdigest()}}), encoding="utf-8")
        registration = self.fixture.fixture.manifest["documents"][0]
        self.doc_id = registration["id"]
        self.raw_dir = self.root/".run/preserved"
        doc_dir = self.raw_dir/self.doc_id
        doc_dir.mkdir(parents=True)
        self.pdf = doc_dir/registration["pdf"]["filename"]
        self.pdf.write_bytes(self.fixture.fixture.raw_pdf)
        number = self.fixture.page.page
        self.png = doc_dir/f"page-{number}.png"
        self.png.write_bytes(self.fixture.fixture.image)
        self.words = doc_dir/f"ocr-words-{number}.json"
        self.lines = doc_dir/f"ocr-lines-{number}.json"
        self.words.write_text(self.fixture.page.model_dump_json(), encoding="utf-8")
        self.lines.write_text(self.fixture.page.line_page().model_dump_json(), encoding="utf-8")

    def test_full_inputs_match_without_mutation_or_model_execution(self):
        before = {p: p.read_bytes() for p in (self.pdf, self.png, self.words, self.lines)}
        result = verify(self.root, self.raw_dir)
        self.assertFalse(result["model_inference_performed"])
        self.assertEqual(len(result["facts"]), 1)
        self.assertTrue(result["facts"][0]["exact_after_parent_index_mapping"])
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_pdf_image_and_full_ocr_drift_fail_closed(self):
        for path in (self.pdf, self.png, self.words, self.lines):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                if path.suffix == ".json":
                    content = json.loads(original)
                    if "lines" in content:
                        content["lines"][0]["line"]["text"] += "7"
                    else:
                        content["spans"][0]["text"] += "7"
                    path.write_text(json.dumps(content), encoding="utf-8")
                else:
                    path.write_bytes(original+b"changed")
                try:
                    with self.assertRaises(ValueError):
                        verify(self.root, self.raw_dir)
                finally:
                    path.write_bytes(original)

    def test_missing_raw_input_is_an_error(self):
        self.words.unlink()
        with self.assertRaises((FileNotFoundError, ValueError)):
            verify(self.root, self.raw_dir)


if __name__ == "__main__":
    unittest.main()
