"""Synthetic preservation/rescoring contracts; no model, rendering or network."""
import builtins
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens.diligence import digest
from creditlens.document_transfer import file_hash, validate_transfer_report
from scripts import rescore_document_transfer as rescore
from tests import test_document_transfer as fixtures


class TransferRescoreTests(unittest.TestCase):
    def setUp(self):
        # Compose the fixture helpers; do not inherit and re-run their test methods.
        self.fixture = fixtures.TransferTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.word_page, _ = self.fixture.page()
        correct_pack = deepcopy(self.fixture.pack)
        for fact in self.fixture.pack["facts"]:
            loc = fact["pdf_location"]
            for name in ("amount_bbox", "year_bbox", "period_bbox", "unit_bbox", "row_label_bbox"):
                loc[name]["x0"] += 9; loc[name]["x1"] += 9
                loc[name]["top"] -= 9; loc[name]["bottom"] -= 9
        self.fixture.save_pack(); self.fixture.save_manifest()
        self.source_manifest = deepcopy(self.fixture.manifest)
        self.source_manifest_path = self.root/"configs/document-transfer-v1-invalid.json"
        self.fixture.write(self.source_manifest_path, self.source_manifest)
        self.source_report = self.fixture.report(self.word_page)
        self.source_report_path = self.root/"artifacts/document-transfer-v1-invalid-report.json"
        self.fixture.write(self.source_report_path, self.source_report)
        self.local = self.root/".run/reproductions/document-transfer"
        self.doc_dir = self.local/"testco"
        self.doc_dir.mkdir(parents=True)
        registration = self.source_manifest["documents"][0]
        self.number = registration["pages"][0]["page"]
        (self.doc_dir/registration["pdf"]["filename"]).write_bytes(self.fixture.raw_pdf)
        (self.doc_dir/f"page-{self.number}.png").write_bytes(self.fixture.image)
        self.word_path = self.doc_dir/f"ocr-words-{self.number}.json"
        self.line_path = self.doc_dir/f"ocr-lines-{self.number}.json"
        self.fixture.write(self.word_path, self.word_page.model_dump(mode="json"))
        self.fixture.write(self.line_path, self.word_page.line_page().model_dump(mode="json"))
        self.fixture.pack = correct_pack
        self.fixture.manifest["experiment_id"] = "corrected-transfer"
        self.fixture.manifest["documents"][0]["pack_path"] = "data/examples/independent-transfer-v2.json"
        # Different reference code is permitted; inference code/config is unchanged.
        builder = self.root/"scripts/build_test_reference.py"
        builder.write_text("# Corrected synthetic reference geometry\n", encoding="utf-8", newline="\n")
        self.fixture.manifest["reference_files"]["scripts/build_test_reference.py"] = file_hash(builder)
        self.fixture.save_pack(); self.fixture.save_manifest()
        self.output = self.root/".run/reproductions/document-transfer-rescored.json"

    def run_rescore(self, **kwargs):
        args = {"source_report": self.source_report_path, "source_manifest": self.source_manifest_path,
                "local_dir": self.local, "manifest": self.fixture.manifest_path, "output": self.output}
        args.update(kwargs)
        with patch.object(rescore, "ROOT", self.root), redirect_stdout(StringIO()):
            return rescore.run(**args)

    def save_source(self):
        self.fixture.write(self.source_manifest_path, self.source_manifest)
        self.source_report["manifest_sha256"] = sha256(self.source_manifest_path.read_bytes()).hexdigest()
        self.fixture.write(self.source_report_path, self.source_report)

    def assert_rejected(self, **kwargs):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.run_rescore(**kwargs)
        self.assertFalse(self.output.exists())

    def test_corrected_reference_reuses_identical_ocr_and_retains_old_inference_metadata(self):
        files = [self.source_manifest_path, self.source_report_path, *self.doc_dir.iterdir(),
                 self.root/self.source_manifest["documents"][0]["pack_path"]]
        snapshots = {path: path.read_bytes() for path in files}
        report = self.run_rescore()
        self.assertEqual(report["summary"]["line"]["consistent"], 1)
        self.assertEqual(report["summary"]["word"]["consistent"], 1)
        self.assertNotEqual(report["summary"], self.source_report["summary"])
        self.assertEqual(report["experiment_id"], "corrected-transfer")
        for name in ("full_line_ocr_sha256", "full_word_ocr_sha256", "elapsed_seconds", "ocr_line_count", "image_sha256"):
            self.assertEqual(report["documents"][0]["pages"][0][name], self.source_report["documents"][0]["pages"][0][name])
        self.assertEqual(report["environment"]["operation"], "rescoring_preserved_ocr")
        self.assertEqual(report["environment"]["source_report_sha256"], sha256(snapshots[self.source_report_path]).hexdigest())
        self.assertEqual(report["environment"]["source_manifest_sha256"], sha256(snapshots[self.source_manifest_path]).hexdigest())
        self.assertEqual(datetime.fromisoformat(report["environment"]["inference_generated_at"]),
                         datetime.fromisoformat(self.source_report["generated_at"]))
        self.assertEqual(report["environment"]["rescore_implementation_sha256"], file_hash(Path(rescore.__file__)))
        self.assertNotEqual(report["generated_at"], self.source_report["generated_at"])
        validate_transfer_report(self.root, report)
        for path, raw in snapshots.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_no_ocr_imaging_or_network_used(self):
        original_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"rapidocr", "onnxruntime", "pypdfium2", "PIL"}:
                raise AssertionError("Rescoring cannot import inference or rendering")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), \
             patch("urllib.request.urlopen", side_effect=AssertionError("Network used")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("Network used")):
            self.run_rescore()

    def test_source_manifest_binding_and_changed_inference_configuration_rejected(self):
        self.source_report["manifest_sha256"] = "0"*64
        self.fixture.write(self.source_report_path, self.source_report)
        self.assert_rejected()
        self.source_manifest["files"]["configs/document-ai.json"] = "0"*64
        self.save_source()
        self.assert_rejected()

    def test_missing_duplicate_or_reassigned_pages_rejected(self):
        original = deepcopy(self.source_report)
        for mutate in (lambda r: r["documents"][0].update(pages=[]),
                       lambda r: r["documents"][0]["pages"].append(deepcopy(r["documents"][0]["pages"][0])),
                       lambda r: r["documents"][0]["pages"][0].update(page=999),
                       lambda r: r["documents"][0]["pages"][0].update(fact_ids=["other-fact"])):
            self.source_report = deepcopy(original); mutate(self.source_report)
            self.fixture.write(self.source_report_path, self.source_report)
            self.assert_rejected()

    def test_changed_registration_or_financial_answers_rejected(self):
        self.source_manifest["documents"][0]["pages"][0]["image_sha256"] = "0"*64
        self.save_source(); self.assert_rejected()
        self.source_manifest["documents"][0]["pages"][0]["image_sha256"] = sha256(self.fixture.image).hexdigest()
        self.save_source()
        self.fixture.pack["facts"][0].update(value="123", raw_value="(123)")
        self.fixture.save_pack(); self.fixture.save_manifest()
        self.assert_rejected()

    def test_source_pack_and_original_pdf_and_image_hashes_rejected(self):
        targets = [self.root/self.source_manifest["documents"][0]["pack_path"],
                   self.doc_dir/self.source_manifest["documents"][0]["pdf"]["filename"],
                   self.doc_dir/f"page-{self.number}.png"]
        for path in targets:
            raw = path.read_bytes(); path.write_bytes(raw+b"changed")
            with self.subTest(path=path):
                self.assert_rejected()
            path.write_bytes(raw)

    def test_complete_ocr_hash_and_line_parent_equality_rejected(self):
        line = self.word_page.line_page().model_dump(mode="json")
        line["spans"][0]["text"] = "different parent"
        self.fixture.write(self.line_path, line)
        self.assert_rejected()
        word = self.word_page.model_dump(mode="json")
        word["lines"][0]["line"]["text"] = "different parent"
        self.fixture.write(self.word_path, word)
        self.assert_rejected()

    def test_complete_ocr_missing_and_out_of_page_coordinates_rejected(self):
        self.word_path.unlink(); self.assert_rejected()
        word = self.word_page.model_dump(mode="json")
        word["lines"][0]["words"][0]["bbox"]["x1"] = word["width"]+1
        self.fixture.write(self.word_path, word)
        self.assert_rejected()

    def test_source_public_evidence_cannot_disagree_with_preserved_complete_page(self):
        # An omitted footer is not part of the compact public evidence. Changing
        # the declared full hash to another complete page still must bind the raw file.
        self.source_report["documents"][0]["pages"][0]["full_word_ocr_sha256"] = "1"*64
        self.fixture.write(self.source_report_path, self.source_report)
        self.assert_rejected()
        self.source_report["documents"][0]["pages"][0]["full_word_ocr_sha256"] = digest(self.word_page.model_dump(mode="json"))
        self.source_report["summary"]["line"]["consistent"] = 99
        self.fixture.write(self.source_report_path, self.source_report)
        self.assert_rejected()

    def test_raw_directory_alias_escape_rejected(self):
        from tests.test_experiment_paths import ExperimentPathTests
        external = self.root/"outside-raw"
        external.mkdir()
        original = self.doc_dir/self.source_manifest["documents"][0]["pdf"]["filename"]
        target = external/original.name
        target.write_bytes(original.read_bytes())
        # A directory alias is supported without Windows symbolic-link privilege.
        alias = self.local/"redirected"
        ExperimentPathTests.symlink(self, external, alias, directory=True)
        with self.assertRaises(ValueError):
            rescore.read_bytes(alias/target.name, 60_000_000, within=self.local)
        self.assertFalse(self.output.exists())

    def test_original_and_release_paths_existing_output_and_preview_aliases_rejected(self):
        targets = (self.source_report_path, self.source_manifest_path, self.word_path,
                   self.root/"artifacts/rescore.json", self.root/".run/document-transfer/report.json",
                   self.root/".run/document-ai/report.json")
        for output in targets:
            with self.subTest(output=output):
                self.assert_rejected(output=output)
        self.output.write_text("existing local report", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.run_rescore()
        self.assertEqual(self.output.read_text(encoding="utf-8"), "existing local report")

    def test_invalid_json_duplicate_keys_and_nan_fail_before_any_output(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}', '{"value":1e999}'):
            self.word_path.write_text(raw, encoding="utf-8")
            self.assert_rejected()

    def test_cli_uses_explicit_source_and_default_current_output(self):
        with patch.object(rescore, "ROOT", self.root), redirect_stdout(StringIO()) as stream:
            status = rescore.main(["--source-report", str(self.source_report_path)])
        self.assertEqual(status, 0)
        self.assertTrue(self.output.is_file())
        self.assertEqual(json.loads(stream.getvalue())["operation"], "rescoring_preserved_ocr")


if __name__ == "__main__":
    unittest.main()
