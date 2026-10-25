"""Synthetic complete-input audit; no AMD OCR, model or original document used."""
import builtins
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from creditlens.diligence import digest
from creditlens.visual import OcrPage
from creditlens.word_evidence import WordPage
from scripts import verify_issuer_raw as verifier
from tests import test_issuer_evaluation as fixtures


class IssuerRawTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.IssuerEvaluationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.report = self.fixture.report()
        self.report_path = self.root/"artifacts/issuer-evaluation-report.json"
        self.save_report()
        registration = self.fixture.manifest["documents"][0]
        self.raw_dir = self.root/".run/reproductions/issuer-evaluation"
        self.document_dir = self.raw_dir/registration["id"]
        self.document_dir.mkdir(parents=True)
        self.pdf = self.document_dir/registration["pdf"]["filename"]
        self.pdf.write_bytes(self.fixture.fixture.raw_pdf)
        number = self.fixture.page.page
        self.image = self.document_dir/f"page-{number}.png"
        self.image.write_bytes(self.fixture.fixture.image)
        self.words = self.document_dir/f"ocr-words-{number}.json"
        self.lines = self.document_dir/f"ocr-lines-{number}.json"
        self.words.write_text(self.fixture.page.model_dump_json(), encoding="utf-8")
        self.lines.write_text(self.fixture.page.line_page().model_dump_json(), encoding="utf-8")

    def save_report(self):
        self.fixture.fixture.write(self.report_path, self.report)

    def test_all_raw_inputs_membership_and_three_rules_match_without_mutation(self):
        paths = [self.pdf, self.image, self.words, self.lines, self.report_path, self.fixture.manifest_path]
        before = {path: path.read_bytes() for path in paths}
        result = verifier.verify(self.root)
        self.assertFalse(result["model_inference_performed"])
        self.assertFalse(result["original_model_execution_verified"])
        self.assertFalse(result["source_authenticity_verified"])
        self.assertTrue(result["published_parent_membership_verified"])
        self.assertTrue(result["full_ocr_canonical_hashes_verified"])
        self.assertEqual(result["distinct_pages"], 1)
        self.assertEqual(result["facts"][0]["statuses"]["date"], "consistent")
        self.assertTrue(result["facts"][0]["exact_after_parent_index_mapping"])
        self.assertEqual(result["source_report_sha256"], sha256(before[self.report_path]).hexdigest())
        self.assertEqual(len(result["raw_file_sha256"]), 4)
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_pdf_image_and_complete_ocr_hash_drift_rejected(self):
        for path in (self.pdf, self.image, self.words, self.lines):
            raw = path.read_bytes()
            if path.suffix == ".json":
                value = json.loads(raw)
                if "lines" in value:
                    value["lines"][0]["line"]["text"] += "7"
                else:
                    value["spans"][0]["text"] += "7"
                path.write_text(json.dumps(value), encoding="utf-8")
            else:
                path.write_bytes(raw+b"modified")
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                verifier.verify(self.root)
            path.write_bytes(raw)

    def test_line_projection_must_agree_even_if_reported_hashes_are_updated(self):
        value = json.loads(self.lines.read_bytes())
        value["spans"][0]["text"] = "different irrelevant footer"
        self.lines.write_text(json.dumps(value), encoding="utf-8")
        self.report["documents"][0]["pages"][0]["full_line_ocr_sha256"] = digest(value)
        self.save_report()
        with self.assertRaisesRegex(ValueError, "project"):
            verifier.verify(self.root)

    def test_public_parent_source_index_cannot_point_to_a_different_complete_line(self):
        page = self.report["documents"][0]["pages"][0]
        # Remains sorted/in range and replays publicly, but now points at footer.
        page["evidence_lines"][0]["source_line_index"] = 0
        self.save_report()
        with self.assertRaisesRegex(ValueError, "source index"):
            verifier.verify(self.root)

    def test_omitted_nearby_empty_word_parent_changes_full_date_result(self):
        words = json.loads(self.words.read_bytes())
        words["lines"].append({"line": {"text": "9", "confidence": .99,
                                        "bbox": {"x0": 151, "top": 50, "x1": 157, "bottom": 60}}, "words": []})
        self.words.write_text(json.dumps(words), encoding="utf-8")
        lines = json.loads(self.lines.read_bytes())
        lines["spans"].append(deepcopy(words["lines"][-1]["line"]))
        self.lines.write_text(json.dumps(lines), encoding="utf-8")
        page = self.report["documents"][0]["pages"][0]
        page.update(full_word_ocr_sha256=digest(WordPage.model_validate(words).model_dump(mode="json")),
                    full_line_ocr_sha256=digest(OcrPage.model_validate(lines).model_dump(mode="json")),
                    ocr_line_count=len(words["lines"]))
        self.save_report()
        # Public results still reproduce from public fragments; full replay must
        # catch the extra numeric parent that was not included in those fragments.
        with self.assertRaisesRegex(ValueError, "three-rule results differ"):
            verifier.verify(self.root)

    def test_line_count_geometry_missing_input_and_bad_mapping_rejected(self):
        word_raw, line_raw = self.words.read_bytes(), self.lines.read_bytes()
        original = deepcopy(self.report)
        words, lines = json.loads(word_raw), json.loads(line_raw)
        words["width"] += 1; lines["width"] += 1
        self.words.write_text(json.dumps(words), encoding="utf-8")
        self.lines.write_text(json.dumps(lines), encoding="utf-8")
        self.report["documents"][0]["pages"][0].update(
            full_word_ocr_sha256=digest(words), full_line_ocr_sha256=digest(lines))
        self.save_report()
        with self.assertRaisesRegex(ValueError, "geometry"):
            verifier.verify(self.root)
        self.words.write_bytes(word_raw); self.lines.write_bytes(line_raw)
        self.report = original
        self.report["documents"][0]["pages"][0]["ocr_line_count"] += 1
        self.save_report()
        with self.assertRaisesRegex(ValueError, "line count"):
            verifier.verify(self.root)
        self.report["documents"][0]["pages"][0]["ocr_line_count"] -= 1
        self.save_report()
        self.words.unlink()
        with self.assertRaises((ValueError, OSError)):
            verifier.verify(self.root)
        with self.assertRaises(ValueError):
            verifier._remap({"candidate_parent_indices": [2]}, [1])

    def test_explicit_manifest_report_and_raw_paths_are_supported_read_only(self):
        alternate_manifest = self.root/"configs/issuer-alt.json"
        alternate_manifest.write_bytes(self.fixture.manifest_path.read_bytes())
        alternate_report = self.root/".run/report-copy.json"
        alternate_report.write_bytes(self.report_path.read_bytes())
        result = verifier.verify(self.root, self.raw_dir, report_path=alternate_report, manifest_path=alternate_manifest)
        self.assertEqual(len(result["facts"]), 1)

    def test_raw_directory_alias_cannot_escape_selected_root(self):
        external = self.root/".run/external"
        external.mkdir()
        alias = self.root/".run/selected/testco"
        alias.parent.mkdir()
        try:
            alias.symlink_to(external, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Directory alias unavailable")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(external)], capture_output=True, text=True)
            if result.returncode:
                self.skipTest("Directory junction unavailable")
        with self.assertRaisesRegex(ValueError, "escaped"):
            verifier.verify(self.root, alias.parent)

    def test_no_model_render_network_or_file_write_and_cli_prints_only_json(self):
        original_import, original_open = builtins.__import__, Path.open
        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"rapidocr", "onnxruntime", "numpy", "pypdfium2", "PIL"}:
                raise AssertionError("Read-only audit imported model/rendering")
            return original_import(name, *args, **kwargs)
        def guarded_open(path, mode="r", *args, **kwargs):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                raise AssertionError("Read-only audit attempted a write")
            return original_open(path, mode, *args, **kwargs)
        output = StringIO()
        with patch("builtins.__import__", side_effect=guarded_import), patch.object(Path, "open", guarded_open), \
             patch("urllib.request.urlopen", side_effect=AssertionError("Network used")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("Network used")), \
             patch.object(verifier, "ROOT", self.root), redirect_stdout(output):
            verifier.main([])
        result = json.loads(output.getvalue())
        self.assertEqual(result["operation"], "verify_complete_issuer_inputs_no_inference")


if __name__ == "__main__":
    unittest.main()
