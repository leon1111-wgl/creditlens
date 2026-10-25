"""Independent developer controls; no live HTML, model or human-gold claims."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.diligence import digest
from creditlens.research_materials import build_material_bundle
from creditlens.company_attribution import build_company_attribution, load_company_attribution_catalog
from scripts import evaluate_company_attribution as evaluation


class CompanyAttributionEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.cases = json.loads((cls.root/evaluation.CASES_PATH).read_text(encoding="utf-8"))
        cls.bundle = build_material_bundle(cls.root)
        cls.catalog = load_company_attribution_catalog(cls.root)
        cls.report = evaluation.build_report(cls.root)

    def case(self, key):
        return deepcopy(next(c for c in self.cases["cases"] if c["id"] == key))

    def evaluate(self, key, candidate=build_company_attribution, loader=load_company_attribution_catalog):
        return evaluation.evaluate_case(self.root, self.case(key), self.bundle, self.catalog, candidate, loader)

    def test_registered_rounding_and_synthetic_controls_are_reported_separately(self):
        summary = self.report["summary"]
        self.assertEqual((summary["cases"], summary["passed"], summary["failed"]), (23, 23, 0))
        self.assertEqual(summary["by_provenance"], {"registered_material_case": 5, "synthetic_control": 18})
        self.assertEqual(summary["validation_errors"], 12)
        self.assertEqual(summary["unexpected_runtime_errors"], 0)
        self.assertEqual(summary["independently_human_confirmed_cases"], 0)
        self.assertEqual(set(summary["channels"]), set(evaluation.CHANNELS))
        self.assertTrue(all(c["input_unchanged"] and c["synthetic_snapshot_unchanged"] for c in self.report["cases"]))
        self.assertFalse(self.report["scope"]["model_evaluation"])
        self.assertFalse(self.report["scope"]["tenant_authorization_evaluated"])
        self.assertFalse(self.report["scope"]["real_historical_snapshot_export_evaluated"])

    def test_complete_replay_rejects_same_count_different_value_provenance_or_code_hash(self):
        self.assertEqual(evaluation.validate_report(self.root, self.report), self.report)
        for kind in ("value", "provenance", "code", "future"):
            report = deepcopy(self.report)
            if kind == "value":
                report["cases"][0]["checks"][0]["observed"] = "136163"
            elif kind == "provenance":
                report["cases"][0]["provenance"]["human_confirmation"] = "confirmed"
            elif kind == "code":
                report["implementation"]["files"]["creditlens/company_attribution.py"] = "0" * 64
            else:
                report["generated_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                evaluation.validate_report(self.root, report)

    def test_false_exact_match_or_causality_does_not_pass_numerical_alignment_controls(self):
        def false_exact(task, root=None):
            result = build_company_attribution(task, root=root)
            result["entries"][0]["numeric_checks"][0]["exact_match"] = True
            return result
        result = self.evaluate("CA-01", false_exact)
        self.assertFalse(result["passed"])
        self.assertEqual([c["channel"] for c in result["checks"] if not c["passed"]], ["numeric_alignment"])
        self.assertFalse(next(c for c in result["checks"] if c["path"][-1] == "exact_match")["expected"])
        def false_causality(task, root=None):
            result = build_company_attribution(task, root=root)
            result["independently_verified"] = True
            return result
        result = self.evaluate("CA-04", false_causality)
        self.assertFalse(result["passed"])
        self.assertEqual([c["channel"] for c in result["checks"] if not c["passed"]], ["refusal"])

    def test_disappearing_offset_is_not_hidden_by_correct_numbers(self):
        def omit_offset(task, root=None):
            result = build_company_attribution(task, root=root)
            result["entries"][0]["qualifiers"]["partial_offsets_zh"].pop()
            return result
        result = self.evaluate("CA-04", omit_offset)
        self.assertFalse(result["passed"])
        self.assertEqual([c["channel"] for c in result["checks"] if not c["passed"]], ["attribution_scope"])

    def test_mutating_input_or_old_snapshot_fails_even_when_output_checks_match(self):
        def edit_task(task, root=None):
            result = build_company_attribution(task, root=root)
            task["revision"] += 1
            return result
        result = self.evaluate("CA-01", edit_task)
        self.assertFalse(result["input_unchanged"])
        self.assertTrue(all(c["passed"] for c in result["checks"]))
        self.assertFalse(result["passed"])
        def edit_snapshot(task, root=None):
            result = build_company_attribution(task, root=root)
            task["snapshot"]["content"]["company_attribution"] = {"forged": True}
            return result
        result = self.evaluate("CA-10", edit_snapshot)
        self.assertFalse(result["synthetic_snapshot_unchanged"])
        self.assertFalse(result["passed"])

    def test_runtime_error_is_not_expected_refusal(self):
        def broken(*args, **kwargs):
            raise RuntimeError("material_integrity_mismatch")
        result = self.evaluate("CA-12", broken)
        self.assertEqual(result["error_kind"], "unexpected_runtime_error")
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"]["code"], "RuntimeError")

    def test_catalog_probes_do_not_change_or_delete_registered_sources(self):
        paths = [self.root/evaluation.CATALOG_PATH,
                 self.root/"data/examples/msft-fy2025-pdf.json", self.root/"data/examples/aapl-fy2025-pdf.json"]
        before = {p: p.read_bytes() for p in paths}
        for key in ("CA-17", "CA-18", "CA-19", "CA-20", "CA-21", "CA-22", "CA-23"):
            self.assertTrue(self.evaluate(key)["passed"])
        self.assertEqual(before, {p: p.read_bytes() for p in paths})
        self.assertEqual(list((self.root/".run/reproductions").glob(".company-attribution-controls-*")), [])

    def test_bad_case_fields_are_value_errors_and_cannot_claim_human_review(self):
        changes = []
        for field, value in (("schema_version", True), ("id", ""), ("held_out", True),
                             ("human_confirmation", "confirmed"), ("model_evaluation", True)):
            data = deepcopy(self.cases)
            data[field] = value
            changes.append(data)
        for operation in ([], {}, None, "execute_url"):
            data = deepcopy(self.cases)
            data["cases"][0]["operation"] = operation
            changes.append(data)
        data = deepcopy(self.cases)
        data["cases"][1]["id"] = data["cases"][0]["id"]
        changes.append(data)
        data = deepcopy(self.cases)
        data["cases"][16]["provenance"]["kind"] = "registered_material_case"
        changes.append(data)
        for data in changes:
            with self.assertRaises(ValueError):
                evaluation.validate_cases(data)

    def test_material_catalog_binding_and_missing_results_are_checked(self):
        for key in ("source_catalog_sha256", "source_bundle_sha256"):
            data = deepcopy(self.cases)
            data[key] = "0" * 64
            with self.assertRaises(ValueError):
                evaluation.build_report(self.root, cases=data)
        def missing(task, root=None):
            result = build_company_attribution(task, root=root)
            del result["entries"][0]["source"]["exact_asset_publication_date"]
            return result
        result = self.evaluate("CA-05", missing)
        self.assertFalse(result["passed"])
        self.assertTrue(any(c["missing_or_ambiguous"] for c in result["checks"]))

    def test_raw_catalog_bytes_and_validated_object_cannot_refer_to_different_versions(self):
        changed = deepcopy(self.catalog)
        changed["entries"][0]["reported_numbers"]["current_cfo"] = "136.3"
        with patch("creditlens.company_attribution.load_company_attribution_catalog", return_value=changed):
            with self.assertRaisesRegex(ValueError, "between raw read and registered validation"):
                evaluation.build_report(self.root)

    def test_revision_changes_do_not_modify_first_returned_result(self):
        task = evaluation.make_task(self.case("CA-10"), self.bundle)
        first = build_company_attribution(task, root=self.root)
        first_sha = digest(first)
        task["revision"] = 8
        second = build_company_attribution(task, root=self.root)
        self.assertEqual(digest(first), first_sha)
        self.assertEqual((first["revision"], second["revision"]), (7, 8))
        self.assertFalse(second["included_in_frozen_snapshot"])
        self.assertNotIn("company_attribution", task["snapshot"]["content"])

    def test_new_local_output_and_failure_records_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch.object(evaluation, "ROOT", root), patch.object(evaluation, "build_report", return_value=deepcopy(self.report)):
                evaluation.run("controls.json")
                path = root/".run/reproductions/controls.json"
                original = path.read_bytes()
                with self.assertRaises(ValueError):
                    evaluation.run("controls.json")
                self.assertEqual(path.read_bytes(), original)
                failed = deepcopy(self.report)
                failed["summary"]["failed"] = 1
                with patch.object(evaluation, "build_report", return_value=failed):
                    with self.assertRaises(SystemExit) as raised:
                        evaluation.main(["--output", "failed.json"])
                    self.assertEqual(raised.exception.code, 2)
                    self.assertTrue((root/".run/reproductions/failed.json").is_file())
            for suffix in ("artifacts/out.json", "data/out.json", ".run/document-ai/out.json",
                           ".run/document-transfer/out.json", ".run/reproductions/sub/out.json"):
                with self.assertRaises(ValueError):
                    evaluation.output_path(root, root/suffix)
            with self.assertRaises(ValueError):
                evaluation.output_path(root, root.parent/"outside.json")


if __name__ == "__main__":
    unittest.main()
