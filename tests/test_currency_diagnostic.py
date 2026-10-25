"""Public diagnostic replay and handmade controls, not OCR accuracy tests."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from creditlens import currency_diagnostic as diagnostic
from creditlens.diligence import digest
from scripts import evaluate_currency_diagnostic as runner
from tests import test_issuer_evaluation as issuer_fixtures


ROOT = Path(__file__).resolve().parents[1]


class CurrencyDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.fixture = issuer_fixtures.IssuerEvaluationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        for relative in diagnostic.IMPLEMENTATION_FILES:
            destination = self.root/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, destination)
        self.source_report = self.fixture.report()
        self.write(self.root/diagnostic.REPORT, self.source_report)
        self.write(self.root/diagnostic.RELEASE, {"schema_version": 1, "files": {
            diagnostic.REPORT: sha256((self.root/diagnostic.REPORT).read_bytes()).hexdigest(),
            diagnostic.MANIFEST: sha256((self.root/diagnostic.MANIFEST).read_bytes()).hexdigest()}})

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    def test_entire_source_inventory_and_separate_controls_replay(self):
        report = diagnostic.build_currency_diagnostic(self.root)
        view = diagnostic.currency_diagnostic_view(self.root, report)
        self.assertEqual(view["report"], report)
        self.assertEqual(view["source"]["report"], self.source_report)
        self.assertTrue(view["read_only"])
        self.assertEqual(report["summary"]["facts"], 1)
        self.assertEqual(report["controls"]["summary"], {"cases": 34, "passed": 34, "failed": 0,
                                                         "positive_cases": 4, "negative_cases": 30})
        self.assertTrue(report["summary"]["non_amount_unchanged"])
        self.assertFalse(report["scope"]["full_ocr_verified"])
        self.assertFalse(report["scope"]["image_pdf_bytes_verified"])
        self.assertFalse(report["automatic_approval"])

    def test_control_expectations_are_fixed_and_do_not_depend_on_candidate(self):
        catalog = diagnostic.control_catalog()
        self.assertEqual(len(catalog["cases"]), 34)
        before = deepcopy(catalog)
        diagnostic.synthetic_control_inputs()
        self.assertEqual(diagnostic.control_catalog(), before)
        catalog["cases"].clear()
        self.assertEqual(diagnostic.control_catalog(), before)
        entries = {case["id"]: case for case in before["cases"]}
        for name in ("negative_matching_parentheses", "negative_matching_minus"):
            self.assertEqual(entries[name]["expected"]["overall_status"], "consistent")
        for name in ("negative_reference_missing_sign", "separate_c_prefix", "separate_cad_prefix_words"):
            self.assertEqual(entries[name]["expected"]["overall_status"], "not_consistent")

    def test_synthetic_input_hashes_and_negative_signs_are_preserved(self):
        report = diagnostic.build_currency_diagnostic(self.root)
        for case in report["controls"]["cases"]:
            self.assertEqual(case["input_sha256"], digest(case["input"]))
            self.assertEqual(case["provenance"], "synthetic")
            self.assertTrue(case["non_amount_unchanged"])
        cases = {c["id"]: c for c in report["controls"]["cases"]}
        for name in ("negative_matching_parentheses", "negative_matching_minus"):
            self.assertEqual(cases[name]["currency"]["currency_association"]["parsed_signed_amount"], "-1234")
        self.assertFalse(cases["split_usd"]["currency"]["currency_association"]["applied"])

    def test_failed_controls_remain_in_report_and_are_not_relabeled(self):
        from creditlens.currency_evidence import CURRENCY_RULE_VERSION, crosscheck_currency_fact
        def faulty(fact, page, **kwargs):
            result = crosscheck_currency_fact(fact, page, **kwargs)
            result["status"] = "consistent"
            return result
        controls = diagnostic._controls(.9, faulty, CURRENCY_RULE_VERSION)
        self.assertEqual(controls["summary"]["cases"], 34)
        self.assertEqual(controls["summary"]["failed"], 30)
        self.assertEqual(controls["cases"][0]["expected"]["overall_status"], "consistent")
        self.assertTrue(any(not item["passed"] for item in controls["cases"]))

    def test_changed_counts_or_equal_count_different_evidence_are_rejected(self):
        original = diagnostic.build_currency_diagnostic(self.root)
        mutations = [
            lambda r: r["summary"].update(facts=99),
            lambda r: r["documents"][0]["facts"][0]["currency"]["checks"][0].update(reason="invented"),
            lambda r: r["documents"][0]["facts"].clear(),
            lambda r: r["controls"]["cases"][4]["expected"].update(overall_status="consistent"),
            lambda r: r["controls"]["cases"][0]["input"]["page"]["lines"][6]["line"].update(text="$999"),
            lambda r: r["controls"]["cases"].pop(),
            lambda r: r["scope"].update(full_ocr_verified=True),
        ]
        for mutate in mutations:
            report = deepcopy(original); mutate(report)
            with self.assertRaises(ValueError):
                diagnostic.validate_currency_diagnostic(self.root, report)

    def test_current_implementation_hash_is_part_of_validation(self):
        report = diagnostic.build_currency_diagnostic(self.root)
        path = self.root/"creditlens/currency_evidence.py"
        path.write_text(path.read_text(encoding="utf-8")+"\n# different recorded implementation\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            diagnostic.validate_currency_diagnostic(self.root, report)

    def test_source_release_and_reference_drift_are_rejected(self):
        report = diagnostic.build_currency_diagnostic(self.root)
        path = self.root/diagnostic.REPORT
        data = json.loads(path.read_text(encoding="utf-8")); data["summary"]["facts"] += 1
        self.write(path, data)
        with self.assertRaises(ValueError):
            diagnostic.validate_currency_diagnostic(self.root, report)

    def test_reject_naive_future_or_pre_source_timestamps(self):
        for timestamp in ("2026-01-01T00:00:00", "2020-01-01T00:00:00+00:00",
                          (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()):
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                diagnostic.build_currency_diagnostic(self.root, generated_at=timestamp)

    def test_report_writes_new_file_and_never_overwrites(self):
        with patch.object(runner, "ROOT", self.root), redirect_stdout(StringIO()):
            report = runner.run()
            target = self.root/".run/reproductions/currency-diagnostic-report.json"
            original = target.read_bytes()
            self.assertEqual(json.loads(original), report)
            with self.assertRaises(ValueError):
                runner.run()
            self.assertEqual(target.read_bytes(), original)

    def test_protected_output_and_directory_aliases_are_rejected(self):
        for relative in ("artifacts/new.json", "configs/new.json", "data/new.json", ".run/document-transfer/new.json",
                         ".run/reproductions/nested/new.json", ".run/reproductions/new.txt"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                runner.output_path(self.root, self.root/relative)
        run = self.root/".run"
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == run or original(path)):
            with self.assertRaises(ValueError):
                runner.output_path(self.root, self.root/".run/reproductions/new.json")


if __name__ == "__main__":
    unittest.main()
