"""Registered-source accounting and separate fixed financial counterexamples."""
from copy import deepcopy
from decimal import localcontext, Decimal, Inexact, ROUND_UP
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import margin_experiment as module
from creditlens.diligence import digest
from scripts import evaluate_margin_bridge as runner

ROOT = Path(__file__).resolve().parents[1]


class MarginExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for relative in (module.SOURCE, module.REPORT, *module.SUPPORT_FILES, *module.IMPLEMENTATION_FILES):
            target = self.root/relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, target)
        self.report = json.loads((self.root/module.REPORT).read_text(encoding="utf-8"))

    def write_json(self, relative, value):
        (self.root/relative).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")

    def error(self, callback, code=None):
        with self.assertRaises(module.MarginExperimentError) as caught:
            callback()
        if code is not None:
            self.assertEqual(caught.exception.code, code)

    def test_full_saved_report_replays_two_companies_not_twenty_eight_questions(self):
        snapshot = module.load_margin_bridge_snapshot(self.root)
        self.assertEqual(snapshot["report"], self.report)
        self.assertEqual(self.report["summary"], {"registered_company_cases": 2, "computed": 2, "blocked": 0,
                                                  "registered_amount_observations": 28, "independent_human_gold": 0})
        self.assertEqual(len(self.report["cases"]), 2)
        self.assertFalse(snapshot["validation_scope"]["full_ocr_replayed"])
        self.assertFalse(snapshot["validation_scope"]["raw_pdf_bytes_checked"])

    def test_real_expected_values_are_fixed_independently_and_all_amount_identities_close(self):
        for case in self.report["cases"]:
            expected = module.EXPECTED_DISPLAYS[case["document_id"]]
            display = case["result"]["calculation"]["display"]
            self.assertTrue(all(display[k] == v for k, v in expected.items()))
            self.assertEqual(case["independent_oracle"]["exact"]["residual_pp"], "0/1")
            for statement in case["result"]["period_results"]:
                self.assertEqual(statement["reconciliation_residuals"], {"revenue": "0", "cost": "0", "gross_profit": "0"})
                self.assertEqual(Decimal(statement["total_revenue"])-Decimal(statement["total_cost"]), Decimal(statement["gross_profit_reported"]))

    def test_category_contributions_match_independent_rational_oracle(self):
        for case in self.report["cases"]:
            oracle = module.fraction_oracle(case["input"])
            self.assertEqual(case["result"]["calculation"]["display"]["components"], oracle["components"])
            self.assertEqual(oracle["display"]["total_change_pp"], case["expected_display"]["total_change_pp"])

    def test_display_rounding_is_not_forced_to_zero(self):
        msft = next(c for c in self.report["cases"] if c["document_id"] == "msft")
        calculation = msft["result"]["calculation"]
        self.assertEqual(Decimal(calculation["display"]["displayed_residual_pp"]), Decimal("0.000001"))
        self.assertLessEqual(abs(Decimal(calculation["residual_pp"])), Decimal("1e-24"))

    def test_controls_have_three_valid_cases_and_every_financial_refusal(self):
        controls = self.report["controls"]
        self.assertEqual(controls["summary"], {"cases": 19, "passed": 19, "failed": 0})
        self.assertEqual(sum(c["result"]["status"] == "computed" for c in controls["cases"]), 3)
        self.assertTrue(all(c["provenance"] == "synthetic_control" for c in controls["cases"]))
        for case in controls["cases"]:
            if case["expected"]["status"] == "blocked":
                self.assertEqual(case["result"]["status"], "blocked")
                self.assertIsNone(case["result"]["calculation"])
                self.assertTrue(case["result"]["reasons"])

    def test_negative_margin_is_retained_not_rejected_as_negative_cost(self):
        case = next(c for c in self.report["controls"]["cases"] if c["id"] == "negative-margin")
        self.assertEqual(case["result"]["status"], "computed")
        self.assertTrue(all(Decimal(s["gross_margin_percent"]) < 0 for s in case["result"]["period_results"]))
        codes = {warning["code"] for warning in case["result"]["warnings"]}
        self.assertIn("negative_total_margin", codes)

    def test_locator_retains_complete_ocr_parent_and_pending_reference_scope(self):
        for case in self.report["cases"]:
            self.assertEqual(len(case["registered_observations"]), 14)
            for fact in case["registered_observations"]:
                self.assertEqual(fact["amount_bbox"], fact["amount_parent"]["bbox"])
                self.assertEqual(fact["bbox_role"], "approximate_locator")
                self.assertEqual(fact["independent_human_confirmation"], "pending")
                self.assertFalse(fact["human_gold"])
        self.assertTrue(self.report["scope"]["amount_bboxes_are_approximate_locators_not_evaluation_gold"])

    def test_same_count_changes_to_result_input_reference_scope_or_control_are_rejected(self):
        changes = [lambda r: r["cases"][0]["result"]["calculation"]["display"].update(mix_effect_pp="0.000000"),
                   lambda r: r["cases"][0]["registered_observations"][0].update(raw_value="1"),
                   lambda r: r["scope"].update(causal_explanation=True),
                   lambda r: r["controls"]["cases"][0]["expected"].update(status="blocked"),
                   lambda r: r["cases"][0]["result"]["checks"][0].update(message="Unverified replacement"),
                   lambda r: r["cases"][0].update(input_sha256="0"*64)]
        for change in changes:
            bad = deepcopy(self.report); change(bad)
            self.error(lambda: module.validate_margin_bridge_report(self.root, bad), "report_does_not_reproduce")

    def test_new_source_version_is_rejected_even_if_all_arithmetic_was_recomputed(self):
        source = json.loads((self.root/module.SOURCE).read_text(encoding="utf-8"))
        source["documents"][0]["facts"][0]["normalized_value"] = "1"
        self.write_json(module.SOURCE, source)
        self.error(lambda: module.build_margin_bridge_report(self.root), "source_registry_changed")

    def test_old_revenue_reference_change_rejects_adapter(self):
        relative = "data/examples/msft-fy2025-pdf.json"
        pack = json.loads((self.root/relative).read_text(encoding="utf-8"))
        revenue = next(f for f in pack["facts"] if f["id"] == "revenue-2025")
        revenue.update(raw_value="281,725", value="281725")
        self.write_json(relative, pack)
        self.error(lambda: module.build_margin_bridge_report(self.root))

    def test_registered_page_image_or_ocr_identity_change_is_rejected(self):
        relative = "artifacts/document-ai-report.json"
        original = json.loads((self.root/relative).read_text(encoding="utf-8"))
        for field in ("image_sha256", "ocr_sha256"):
            report = deepcopy(original)
            report["documents"][0]["pages"][0][field] = "0"*64
            self.write_json(relative, report)
            self.error(lambda: module.build_margin_bridge_report(self.root), "page_binding_mismatch")

    def test_even_newly_pinned_source_must_match_prior_total_and_period(self):
        source = json.loads((self.root/module.SOURCE).read_text(encoding="utf-8"))
        source["documents"][0]["periods"][0]["end"] = "2025-06-29"
        self.write_json(module.SOURCE, source)
        raw = (self.root/module.SOURCE).read_bytes()
        with patch.object(module, "SOURCE_RAW_SHA256", hashlib.sha256(raw).hexdigest()), patch.object(module, "SOURCE_CANONICAL_SHA256", digest(source)):
            self.error(lambda: module.build_margin_bridge_report(self.root), "prior_revenue_binding_mismatch")

    def test_observable_source_change_during_read_is_rejected(self):
        original = module._read
        seen = 0
        def changing(path, limit=2_000_000):
            nonlocal seen
            raw, value = original(path, limit)
            if Path(path).name == Path(module.SOURCE).name:
                seen += 1
                if seen == 2:
                    return raw+b" ", value
            return raw, value
        with patch.object(module, "_read", side_effect=changing):
            self.error(lambda: module.build_margin_bridge_report(self.root), "source_changed_during_read")

    def test_missing_duplicate_key_and_nonfinite_source_fail(self):
        path = self.root/module.SOURCE
        for raw in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}'):
            path.write_text(raw, encoding="utf-8")
            self.error(lambda: module.build_margin_bridge_report(self.root), "input_unavailable")
        path.unlink()
        self.error(lambda: module.build_margin_bridge_report(self.root), "input_unavailable")

    def test_saved_report_binds_implementation_version(self):
        target = self.root/"creditlens/margin_bridge.py"
        target.write_text(target.read_text(encoding="utf-8")+"\n# changed version\n", encoding="utf-8")
        self.error(lambda: module.load_margin_bridge_snapshot(self.root), "report_does_not_reproduce")

    def test_fraction_oracle_catches_wrong_candidate_calculation(self):
        result = deepcopy(self.report["cases"][0]["result"])
        result["calculation"]["display"]["components"][0]["mix_contribution_pp"] = "0.000000"
        self.error(lambda: module._oracle_check(self.report["cases"][0]["input"], result), "independent_component_disagreement")

    def test_caller_low_decimal_precision_does_not_change_results(self):
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(module.build_margin_bridge_report(self.root), self.report)

    def test_caller_traps_rounding_and_exponent_range_do_not_change_results(self):
        with localcontext() as context:
            context.prec = 2
            context.traps[Inexact] = True
            context.rounding = ROUND_UP
            context.Emax = 3
            context.Emin = -3
            self.assertEqual(module.build_margin_bridge_report(self.root), self.report)

    def test_prior_json_formatting_does_not_invent_a_new_financial_version(self):
        for relative in module.SUPPORT_FILES:
            path = self.root/relative
            value = json.loads(path.read_text(encoding="utf-8"))
            path.write_bytes(json.dumps(value, ensure_ascii=False, indent=4).replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(module.load_margin_bridge_snapshot(self.root)["report"], self.report)

    def test_image_metadata_comes_from_same_verified_snapshot(self):
        snapshot = module.load_margin_bridge_snapshot(self.root)
        for case in snapshot["report"]["cases"]:
            meta = module.margin_image_metadata(snapshot, case["id"])
            self.assertEqual(meta["image_sha256"], case["page"]["image_sha256"])
            self.assertEqual(meta["pdf_sha256"], case["source"]["pdf_sha256"])
            self.assertEqual(meta["page"], case["page"]["page"])
        self.error(lambda: module.margin_image_metadata(snapshot, "unknown"), "unknown_margin_case")

    def test_cli_creates_new_report_and_never_overwrites_registered_or_preview_files(self):
        result = runner.run(self.root, Path("fresh-margin.json"))
        self.assertEqual(result["summary"], self.report["summary"])
        target = self.root/".run/reproductions/fresh-margin.json"
        before = target.read_bytes()
        for path in (target, self.root/module.REPORT, Path("nested/report.json"), Path("report.txt"), self.root/".run/document-ai/report.json"):
            with self.assertRaises(ValueError):
                runner.output_path(self.root, path)
        self.assertEqual(target.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
