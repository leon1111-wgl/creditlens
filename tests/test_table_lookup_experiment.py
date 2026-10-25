from copy import deepcopy
from decimal import localcontext
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import table_lookup_experiment as module
from creditlens.diligence import digest
from creditlens.visual import OcrPage
from scripts import evaluate_table_lookup as runner


ROOT = Path(__file__).resolve().parents[1]


class TableLookupExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        files = [module.CONFIG_PATH, module.REPORT_PATH, module.RELEASE_PATH,
                 "artifacts/document-ai-report.json", "configs/document-ai.json",
                 "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json", *module.IMPLEMENTATION_FILES]
        for relative in files:
            target = self.root/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, target)
        self.report = json.loads((self.root/module.REPORT_PATH).read_text(encoding="utf-8"))

    def test_entire_42_fact_inventory_retained(self):
        snap = module.load_table_lookup_snapshot(self.root)
        report = snap["report"]
        self.assertEqual([(d["id"], len(d["cases"])) for d in report["documents"]], [("msft", 20), ("aapl", 22)])
        self.assertEqual(len({c["id"] for d in report["documents"] for c in d["cases"]}), 42)
        for summary in report["summary"].values():
            self.assertEqual(summary["cases"], 42)
            self.assertEqual(summary["matched"]+summary["wrong"]+summary["insufficient"], 42)
        self.assertEqual(report["scope"]["independent_human_gold"], 0)
        self.assertEqual(report["scope"]["new_ocr_calls"], 0)

    def test_published_snapshot_does_not_read_ocr_or_rerun_extraction(self):
        with patch.object(module, "_load_ocr", side_effect=AssertionError("Must not read OCR")), \
             patch.object(module, "extract", side_effect=AssertionError("Must not extract")):
            snap = module.load_table_lookup_snapshot(self.root)
        self.assertFalse(snap["full_ocr_replayed"])

    def test_expected_amount_and_box_only_change_score_not_extraction(self):
        case = module._synthetic_cases()[0]
        page = OcrPage.model_validate(case["page"])
        before = module.extract(page, case["query"])
        reference = deepcopy(case["reference"])
        reference["signed_amount"] = "999"
        reference["raw_value"] = "999"
        reference["amount_bbox"]["x0"] -= 100
        reference["amount_bbox"]["x1"] -= 100
        after = module.extract(page, case["query"])
        self.assertEqual(before, after)
        self.assertEqual(digest(before), digest(after))
        original = module.score_extraction(before["methods"]["layout_column"], case["reference"])
        changed = module.score_extraction(after["methods"]["layout_column"], reference)
        self.assertEqual(original["outcome"], "matched")
        self.assertEqual(changed["outcome"], "wrong")
        self.assertFalse(changed["amount_match"])
        self.assertFalse(changed["reference_location_match"])

    def test_query_boundary_refuses_reference_amount_and_box(self):
        case = module._synthetic_cases()[0]
        for name, value in (("expected_amount", "200"), ("amount_bbox", case["reference"]["amount_bbox"]), ("section", "current")):
            with self.subTest(field=name):
                with self.assertRaises(module.TableLookupExperimentError):
                    module.extract(OcrPage.model_validate(case["page"]), {**case["query"], name: value})

    def test_same_amount_wrong_location_is_wrong_not_matched(self):
        case = module._synthetic_cases()[0]
        actual = module.extract(OcrPage.model_validate(case["page"]), case["query"])
        reference = deepcopy(case["reference"])
        reference["amount_bbox"]["x0"] -= 100
        reference["amount_bbox"]["x1"] -= 100
        score = module.score_extraction(actual["methods"]["layout_column"], reference)
        self.assertTrue(score["amount_match"])
        self.assertFalse(score["reference_location_match"])
        self.assertEqual(score["outcome"], "wrong")

    def test_six_synthetic_controls_are_separate_from_real_denominator(self):
        controls = self.report["controls"]
        self.assertEqual(controls["summary"]["cases"], 6)
        self.assertEqual(len(controls["cases"]), 6)
        for case in controls["cases"]:
            self.assertEqual(case["provenance"], "synthetic_developer_control")
            actual = module.extract(OcrPage.model_validate(case["page"]), case["query"])
            self.assertEqual(digest(actual), case["extraction_sha256"])
        self.assertEqual(self.report["summary"]["layout"]["cases"], 42)

    def test_negative_cash_outflow_uses_signed_reference_not_business_magnitude(self):
        rows = [c for d in self.report["documents"] for c in d["cases"] if c["fact_id"].startswith("capex-")]
        self.assertEqual(len(rows), 4)
        for case in rows:
            self.assertTrue(case["reference"]["signed_amount"].startswith("-"))
            self.assertFalse(case["reference"]["business_normalized_value"].startswith("-"))

    def test_amount_match_does_not_certify_currency_or_complete_period(self):
        for doc in self.report["documents"]:
            for case in doc["cases"]:
                for method in case["methods"].values():
                    self.assertIsNone(method["extraction"]["unit"]["currency"])
                    self.assertFalse(method["score"]["unit_proven"])
                    self.assertFalse(method["score"]["complete_period_proven"])

    def test_same_summary_does_not_hide_changed_score(self):
        altered = deepcopy(self.report)
        score = altered["documents"][0]["cases"][0]["methods"]["layout"]["score"]
        score["amount_match"] = not score["amount_match"]
        with self.assertRaises(module.TableLookupExperimentError):
            module.validate_table_lookup_report(self.root, altered)

    def test_duplicate_case_cannot_replace_missing_case(self):
        altered = deepcopy(self.report)
        altered["documents"][0]["cases"][1] = deepcopy(altered["documents"][0]["cases"][0])
        with self.assertRaises(module.TableLookupExperimentError) as caught:
            module.validate_table_lookup_report(self.root, altered)
        self.assertEqual(caught.exception.code, "case_inventory_mismatch")

    def test_release_hash_rejects_self_reported_evidence_change(self):
        altered = deepcopy(self.report)
        altered["documents"][0]["cases"][0]["methods"]["layout"]["extraction"]["message"] = "synthetic changed explanation"
        (self.root/module.REPORT_PATH).write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaises(module.TableLookupExperimentError) as caught:
            module.load_table_lookup_snapshot(self.root)
        self.assertEqual(caught.exception.code, "published_report_changed")

    def test_pack_config_and_implementation_drift_rejected(self):
        for relative in ("data/examples/msft-fy2025-pdf.json", module.CONFIG_PATH, "creditlens/table_lookup.py"):
            with self.subTest(path=relative):
                path = self.root/relative
                original = path.read_bytes()
                if relative.endswith(".py"):
                    path.write_bytes(original+b"\n# synthetic implementation change\n")
                else:
                    value = json.loads(original)
                    value["schema_version"] = 99
                    path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(module.TableLookupExperimentError):
                    module.load_table_lookup_snapshot(self.root)
                path.write_bytes(original)

    def test_missing_ocr_is_explicit_and_does_not_generate_or_download(self):
        with self.assertRaises(module.TableLookupExperimentError) as caught:
            module.build_table_lookup_report(self.root, self.root/"missing")
        self.assertEqual(caught.exception.code, "input_unavailable")
        self.assertFalse((self.root/"missing").exists())

    def test_wrong_local_ocr_bytes_fail_before_extraction(self):
        path = self.root/"local/msft/ocr-50.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(module._synthetic_cases()[0]["page"]), encoding="utf-8")
        with patch.object(module, "extract", side_effect=AssertionError("Do not extract wrong source")):
            with self.assertRaises(module.TableLookupExperimentError) as caught:
                module.build_table_lookup_report(self.root, self.root/"local")
        self.assertEqual(caught.exception.code, "ocr_hash_mismatch")

    def test_cli_new_file_only_and_no_public_overwrite(self):
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "build_table_lookup_report", return_value=self.report), \
             patch.object(runner, "validate_table_lookup_report", return_value=self.report):
            runner.run(output=Path("new.json"))
            with self.assertRaises(ValueError): runner.run(output=Path("new.json"))
            with self.assertRaises(ValueError): runner.run(output=self.root/module.REPORT_PATH)
        self.assertEqual(hashlib.sha256((self.root/module.REPORT_PATH).read_bytes()).hexdigest(),
                         json.loads((self.root/module.RELEASE_PATH).read_text())["report_sha256"])

    def test_cli_verify_saved_never_runs_extraction_or_writes_a_report(self):
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "run", side_effect=AssertionError("Must not run")):
            runner.main(["--verify-saved"])
        self.assertFalse((self.root/".run").exists())

    def test_fixed_scope_label_and_reason_claims_cannot_be_rewritten(self):
        for field in ("scope", "normalized_label", "message"):
            with self.subTest(field=field):
                altered = deepcopy(self.report)
                case = altered["documents"][0]["cases"][0]
                if field == "scope": case["extraction_metadata"]["scope"] = ["Approved financial truth"]
                elif field == "normalized_label": case["extraction_metadata"]["query"]["normalized_row_label"] = "wrong label"
                else: case["methods"]["layout"]["extraction"]["message"] = "Verified and approved"
                with self.assertRaises(module.TableLookupExperimentError):
                    module.validate_table_lookup_report(self.root, altered)

    def test_report_validation_is_independent_of_external_decimal_precision(self):
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(module.validate_table_lookup_report(self.root, self.report), self.report)


if __name__ == "__main__":
    unittest.main()
