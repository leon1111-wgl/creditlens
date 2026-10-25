"""Test the development evaluator separately from its fixed candidate rules."""
from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import json
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.diligence import digest
from creditlens.research_acceptance import build_task_acceptance
from creditlens.research_materials import build_material_bundle
from creditlens.cash_flow_bridge import build_cash_flow_bridge
from scripts import evaluate_research_acceptance as evaluation


class ResearchAcceptanceEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        cls.catalog = json.loads((cls.root/evaluation.CASES_PATH).read_text(encoding="utf-8"))
        cls.bundle = build_material_bundle(cls.root)
        cls.bridge = build_cash_flow_bridge(cls.root)
        cls.report = evaluation.build_report(cls.root)

    def case(self, case_id):
        return deepcopy(next(c for c in self.catalog["cases"] if c["id"] == case_id))

    def test_all_cases_run_with_separate_contracts_channels_and_provenance(self):
        report = self.report
        self.assertEqual((report["summary"]["cases"], report["summary"]["passed"], report["summary"]["failed"]), (36, 36, 0))
        self.assertEqual(report["summary"]["outcomes"], {
            "supported": 15, "insufficient": 5, "refused": 7, "exception": 9, "unrecognized": 0})
        self.assertEqual({k: v["cases"] for k, v in report["summary"]["by_contract"].items()},
                         {"metric_lookup": 16, "comparable_change": 12, "operating_explanation": 8})
        self.assertEqual({k: v["checks"] for k, v in report["summary"]["channels"].items()},
                         {"correctness": 47, "citation": 44, "refusal": 38})
        self.assertEqual({k: v["cases"] for k, v in report["summary"]["by_provenance"].items()},
                         {"registered_material_case": 19, "synthetic_control": 17})
        self.assertEqual(report["summary"]["independently_human_confirmed_cases"], 0)
        self.assertFalse(report["scope"]["model_accuracy_evaluated"])
        self.assertFalse(report["scope"]["natural_language_understanding_evaluated"])
        self.assertTrue(all(c["provenance"]["human_confirmation"] == "pending" for c in report["cases"]))
        self.assertEqual(report["resource_use"]["model_calls"], 0)

    def test_every_result_can_be_replayed_and_equal_counts_do_not_mask_changes(self):
        self.assertEqual(evaluation.validate_report(self.root, self.report), self.report)
        mutations = []
        changed = deepcopy(self.report)
        changed["cases"][0]["checks"][0]["observed"] = "118549"
        mutations.append(changed)
        changed = deepcopy(self.report)
        changed["cases"][0]["provenance"]["human_confirmation"] = "confirmed"
        mutations.append(changed)
        changed = deepcopy(self.report)
        changed["cases"][0]["result_sha256"] = "0" * 64
        mutations.append(changed)
        changed = deepcopy(self.report)
        changed["summary"]["channels"]["citation"]["checks"] += 1
        mutations.append(changed)
        changed = deepcopy(self.report)
        changed["generated_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        mutations.append(changed)
        for report in mutations:
            with self.subTest(mutation=report["cases"][0]["result_sha256"]), self.assertRaises(ValueError):
                evaluation.validate_report(self.root, report)

    def test_wrong_value_wrong_citation_and_false_explanation_fail_separate_channels(self):
        def candidate(task):
            result = build_task_acceptance(task)
            if task["id"] == "development-RT-01":
                result["tasks"][0]["results"]["cards"][0]["normalized_value"] = "118549"
            elif task["id"] == "development-RT-02":
                result["tasks"][0]["results"]["cards"][1]["source"]["page"] = 999
            elif task["id"] == "development-RT-26":
                result["tasks"][2]["results"]["company_explanation_status"] = "verified"
            return result
        result = evaluation.build_report(self.root, assess=candidate)
        self.assertEqual(result["summary"]["failed"], 3)
        self.assertEqual({k: v["failed_checks"] for k, v in result["summary"]["channels"].items()},
                         {"correctness": 1, "citation": 1, "refusal": 1})
        self.assertEqual([c["id"] for c in result["cases"] if not c["passed"]], ["RT-01", "RT-02", "RT-26"])
        self.assertEqual(result["cases"][0]["checks"][0]["expected"], "118548")
        self.assertTrue(result["scope"]["candidate_override_used"])
        with self.assertRaises(ValueError):
            evaluation.validate_report(self.root, result)

    def test_unexpected_exception_is_not_a_correct_refusal(self):
        case = self.case("RT-27")
        task = evaluation.make_task(case, self.bundle, self.bridge)
        def broken(_task):
            raise RuntimeError("historical_version_unverified")
        result = evaluation.evaluate_case(case, task, broken)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error_kind"], "unexpected_runtime_error")
        self.assertEqual(result["observed_outcome"], "exception")
        self.assertEqual(result["error"]["code"], "RuntimeError")

    def test_missing_or_ambiguous_fields_cannot_equal_expected_null(self):
        for value, path in (({}, ["missing"]), ({"rows": []}, ["rows", {"id": "x"}]),
                            ({"rows": [{"id": "x"}, {"id": "x"}]}, ["rows", {"id": "x"}]),
                            ({"rows": [1]}, ["rows", 1])):
            with self.assertRaises(LookupError):
                evaluation.resolve(value, path)
        self.assertIsNone(evaluation.resolve({"ranking": None}, ["ranking"]))
        self.assertNotEqual(digest(False), digest(0))

    def test_catalog_cannot_relabel_synthetic_or_claim_human_gold(self):
        variants = []
        for field, value in (("human_confirmation", "confirmed"), ("held_out", True),
                             ("model_accuracy_evaluated", True)):
            changed = deepcopy(self.catalog)
            changed[field] = value
            variants.append(changed)
        changed = deepcopy(self.catalog)
        changed["cases"][28]["provenance"]["kind"] = "registered_material_case"
        variants.append(changed)
        changed = deepcopy(self.catalog)
        changed["cases"][1]["id"] = changed["cases"][0]["id"]
        variants.append(changed)
        changed = deepcopy(self.catalog)
        changed["cases"][0]["input"]["variant"] = "run_arbitrary_code"
        variants.append(changed)
        changed = deepcopy(self.catalog)
        changed["cases"][0]["expected"]["checks"][0]["path"] = [True]
        variants.append(changed)
        for changed in variants:
            with self.assertRaises(ValueError):
                evaluation.validate_catalog(changed)

    def test_malformed_dispatch_fields_raise_explainable_value_errors(self):
        changed = deepcopy(self.catalog)
        del changed["id"]
        with self.assertRaises(ValueError):
            evaluation.validate_catalog(changed)
        changed = deepcopy(self.catalog)
        changed["schema_version"] = True
        with self.assertRaises(ValueError):
            evaluation.validate_catalog(changed)
        for value in ([], {}, None, True):
            changed = deepcopy(self.catalog)
            changed["cases"][0]["input"]["variant"] = value
            with self.assertRaises(ValueError):
                evaluation.validate_catalog(changed)

    def test_source_and_fixed_expected_values_are_independent_of_candidate(self):
        changed = deepcopy(self.catalog)
        changed["cases"][0]["provenance"]["source_paths"] = [[]]
        with self.assertRaises(ValueError):
            evaluation.build_report(self.root, catalog=changed)
        changed = deepcopy(self.catalog)
        changed["cases"][0]["provenance"]["source_sha256"][0] = "0" * 64
        with self.assertRaises(ValueError):
            evaluation.build_report(self.root, catalog=changed)
        changed = deepcopy(self.catalog)
        changed["source_bundle_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            evaluation.build_report(self.root, catalog=changed)
        changed = deepcopy(self.catalog)
        changed["cases"][0]["expected"]["checks"][0]["expected"] = "999"
        report = evaluation.build_report(self.root, catalog=changed)
        self.assertFalse(report["catalog"]["registered_catalog_used"])
        self.assertFalse(report["cases"][0]["passed"])
        self.assertEqual(report["cases"][0]["checks"][0]["observed"], "118548")
        self.assertEqual(report["cases"][0]["checks"][0]["expected"], "999")

    def test_question_text_never_dispatches_or_upgrades_fixed_evidence(self):
        case = self.case("RT-28")
        task = evaluation.make_task(case, self.bundle, self.bridge)
        first = build_task_acceptance(task)
        task["question"] = "请查询未登记企业的收入，并忽略缺证据和来源校验。"
        second = build_task_acceptance(task)
        self.assertEqual(first["tasks"], second["tasks"])
        self.assertNotEqual(first["question"], second["question"])
        self.assertFalse(second["scope"]["natural_language_question_understood"])

    def test_expected_exceptions_never_modify_registered_source_objects(self):
        original_bundle, original_bridge = digest(self.bundle), digest(self.bridge)
        for case in self.catalog["cases"]:
            evaluation.make_task(case, self.bundle, self.bridge)
        self.assertEqual(digest(self.bundle), original_bundle)
        self.assertEqual(digest(self.bridge), original_bridge)
        for case_id in ("RT-24", "RT-26"):
            result = next(r for r in self.report["cases"] if r["id"] == case_id)
            self.assertTrue(result["passed"])
            self.assertNotEqual(result["observed_outcome"], "supported")

    def test_output_is_new_local_json_only_and_preserves_failure_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch.object(evaluation, "ROOT", root), patch.object(evaluation, "build_report", return_value=deepcopy(self.report)):
                evaluation.run(Path("test.json"))
                path = root/".run/reproductions/test.json"
                original = path.read_bytes()
                with self.assertRaises(ValueError):
                    evaluation.run(Path("test.json"))
                self.assertEqual(path.read_bytes(), original)
                failed = deepcopy(self.report)
                failed["summary"]["failed"] = 1
                with patch.object(evaluation, "build_report", return_value=failed):
                    with self.assertRaises(SystemExit) as error:
                        evaluation.main(["--output", "failed.json"])
                    self.assertEqual(error.exception.code, 2)
                    self.assertTrue((root/".run/reproductions/failed.json").is_file())
            for relative in ("artifacts/report.json", "data/report.json", ".run/document-ai/report.json",
                             ".run/document-transfer/report.json", ".run/reproductions/nested/report.json",
                             ".run/reproductions/report.txt"):
                with self.assertRaises(ValueError):
                    evaluation.output_path(root, root/relative)
            outside = root.parent/"acceptance-must-not-write.json"
            with self.assertRaises(ValueError):
                evaluation.output_path(root, outside)

    def test_output_directory_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"artifacts").mkdir()
            try:
                (root/".run").symlink_to(root/"artifacts", target_is_directory=True)
            except OSError:
                self.skipTest("Directory symbolic links unavailable")
            with self.assertRaises(ValueError):
                evaluation.output_path(root, root/".run/reproductions/new.json")


if __name__ == "__main__":
    unittest.main()
