"""Finite statement checks, fixed provenance and separate development controls."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import margin_claim_experiment as module
from creditlens import margin_experiment as bridge
from scripts import run_margin_claims as runner

ROOT = Path(__file__).resolve().parents[1]


class MarginClaimExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = module.build_margin_claim_report(ROOT)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        names = (module.CATALOG, bridge.SOURCE, bridge.REPORT, *bridge.SUPPORT_FILES, *module.IMPLEMENTATION_FILES)
        for relative in dict.fromkeys(names):
            target = self.root/relative; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, target)
        self.report = deepcopy(self.expected)
        self.write(module.REPORT, self.report)

    def write(self, relative, value):
        (self.root/relative).write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)+"\n", encoding="utf-8", newline="\n")

    def case(self, identifier):
        return next(c for c in self.report["cases"] if c["id"] == identifier)

    def control(self, identifier):
        return next(c for c in self.report["controls"]["cases"] if c["id"] == identifier)

    def test_registered_eight_cases_have_separate_known_scope_outcomes(self):
        snapshot = module.load_margin_claim_snapshot(self.root)
        self.assertEqual(snapshot["report"], self.report)
        self.assertEqual(self.report["summary"], {"registered_claims": 8, "companies": 2,
            "status_counts": {"matched_numeric": 4, "matched_direction": 3, "scope_unavailable": 1, "contradiction": 0, "invalid": 0},
            "oracle_checks_passed": 8, "independent_human_gold": 0})
        self.assertEqual(snapshot["margin_report_sha256"], module.MARGIN_REPORT_SHA256)
        self.assertFalse(snapshot["validation_scope"]["live_html_fetched"])
        self.assertFalse(snapshot["validation_scope"]["semantic_entailment_checked"])

    def test_microsoft_amount_growth_and_rate_are_independently_calculated(self):
        amount = self.case("msft-margin-commentary-1")
        self.assertEqual(amount["independent_numeric_oracle"]["amount_delta_million"], "22885")
        checks = amount["result"]["numeric_checks"]
        self.assertEqual([(c["kind"], c["rounded"]) for c in checks], [("delta_amount", "22.9"), ("growth_percent", "13")])
        self.assertEqual(Fraction(checks[0]["exact"]), Fraction("22.885"))
        self.assertEqual(self.case("msft-margin-commentary-2")["result"]["direction_check"]["actual"], "decrease")

    def test_cloud_rounded_nearby_total_does_not_make_a_scope_match(self):
        actual = self.case("msft-margin-commentary-3")["result"]
        self.assertEqual(actual["status"], "scope_unavailable")
        self.assertIsNone(actual["actual"])
        self.assertEqual(actual["numeric_checks"], [])
        self.assertEqual(self.control("cloud-not-company")["result"]["status"], "scope_unavailable")
        self.assertEqual(self.control("company-rounded-69")["result"]["status"], "matched_numeric")

    def test_apple_amount_and_rate_directions_are_distinct(self):
        amount = self.case("aapl-margin-commentary-1")["result"]
        rate = self.case("aapl-margin-commentary-2")["result"]
        self.assertEqual(amount["direction_check"]["actual"], "increase")
        self.assertEqual(rate["direction_check"]["actual"], "decrease")
        self.assertEqual(Fraction(amount["actual"]["delta_amount"]), 3254)
        self.assertEqual({c["kind"]: c["rounded"] for c in rate["numeric_checks"]}, {"base_percent": "37.2", "current_percent": "36.8"})

    def test_separately_rounded_rate_levels_are_not_exact_pp_change(self):
        for identifier, expected_delta in (("aapl-margin-commentary-2", "-0.4"), ("aapl-margin-commentary-4", "1.5"),
                                           ("aapl-margin-commentary-5", "0.7")):
            case = self.case(identifier)
            with self.subTest(identifier=identifier):
                self.assertEqual(Fraction(case["result"]["rate_display_comparison"]["stated_delta_pp"]), Fraction(expected_delta))
                self.assertFalse(case["independent_numeric_oracle"]["displayed_rate_delta_equals_exact"])
                self.assertNotEqual(Fraction(case["result"]["rate_display_comparison"]["exact_delta_pp"]), Fraction(expected_delta))

    def test_source_capture_limits_and_numeric_table_evidence_remain_visible(self):
        for case in self.report["cases"]:
            record = case["source_record"]
            self.assertEqual(record["capture_sha256"], case["claim"]["source"]["sha256"])
            self.assertEqual(case["independent_human_confirmation"], "pending")
            self.assertFalse(record["historical_snapshot_verified"])
            if case["issuer_id"] == "AAPL":
                self.assertIsNone(record["raw_response_sha256"])
                if case["claim"]["metric"] == "gross_margin_rate" and case["claim"]["claim_type"] == "company_explanation":
                    self.assertIn("numeric_evidence", record)
            else:
                self.assertIsNotNone(record["raw_response_sha256"])
        total = self.case("aapl-margin-commentary-5")["claim"]
        self.assertEqual(total["claim_type"], "numeric_only")
        self.assertEqual(total["drivers"], [])

    def test_every_synthetic_control_kept_in_denominator(self):
        self.assertEqual(self.report["controls"]["summary"], {"cases": 19, "passed": 19, "failed": 0})
        self.assertEqual(len({c["id"] for c in self.report["controls"]["cases"]}), 19)
        self.assertTrue(all(c["provenance"] == "synthetic_control" for c in self.report["controls"]["cases"]))
        self.assertTrue(all(c["passed"] for c in self.report["controls"]["cases"]))

    def test_fraction_thresholds_and_invalid_units_or_base(self):
        self.assertEqual(self.control("half-up-threshold")["result"]["numeric_checks"][0]["rounded"], "69.5")
        self.assertEqual(self.control("below-threshold")["result"]["numeric_checks"][0]["rounded"], "69.4")
        self.assertEqual(self.control("below-wrong-round")["result"]["status"], "contradiction")
        for identifier in ("wrong-issuer", "wrong-year", "wrong-report", "wrong-period", "growth-percent-not-pp", "rate-pp-not-percent", "zero-base-growth"):
            self.assertEqual(self.control(identifier)["result"]["status"], "invalid")

    def test_prose_is_preserved_but_does_not_upgrade_arithmetic_to_causality(self):
        result = self.control("prose-is-not-causal-proof")["result"]
        self.assertEqual(result["status"], "matched_numeric")
        self.assertIn("全部", result["claim"]["paraphrase_zh"])
        for flag in ("independently_verified", "causal_explanation", "linked_to_accounting_effects", "natural_language_understood", "automatic_approval"):
            self.assertFalse(result[flag])

    def test_original_qualifiers_and_catalog_wording_cannot_silently_change(self):
        original = (self.root/module.CATALOG).read_bytes()
        data = json.loads(original)
        for field in ("qualifiers", "paraphrase_zh", "source_records"):
            changed = deepcopy(data)
            if field == "qualifiers":
                changed["claims"][0][field]["primary"] = not changed["claims"][0][field]["primary"]
            elif field == "paraphrase_zh":
                changed["claims"][0][field] += "全部原因已证实"
            else:
                changed[field][0]["capture_sha256"] = "f"*64
            self.write(module.CATALOG, changed)
            with self.assertRaisesRegex(module.MarginClaimExperimentError, "claim_catalog_changed"):
                module.build_margin_claim_report(self.root)
        (self.root/module.CATALOG).write_bytes(original)

    def test_report_same_count_mutations_are_rejected_by_complete_replay(self):
        mutations = [lambda r: r["cases"][0]["result"]["numeric_checks"][0].update(rounded="22.8"),
                     lambda r: r["cases"][0]["source_record"].update(locator="wrong section"),
                     lambda r: r["cases"][0]["independent_numeric_oracle"].update(amount_delta_million="1"),
                     lambda r: r["controls"]["cases"][0].update(expected_status="invalid"),
                     lambda r: r["scope"].update(automatic_nli=True),
                     lambda r: r["cases"][0]["result"].update(causal_explanation=True)]
        for mutation in mutations:
            report = deepcopy(self.report); mutation(report)
            with self.assertRaisesRegex(module.MarginClaimExperimentError, "report_replay_mismatch"):
                module.validate_margin_claim_report(self.root, report)

    def test_implementation_version_is_bound(self):
        path = self.root/"creditlens/margin_claims.py"
        path.write_text(path.read_text(encoding="utf-8")+"\n# changed implementation\n", encoding="utf-8")
        with self.assertRaisesRegex(module.MarginClaimExperimentError, "report_replay_mismatch"):
            module.load_margin_claim_snapshot(self.root)

    def test_old_margin_report_byte_version_is_bound(self):
        path = self.root/bridge.REPORT
        path.write_bytes(path.read_bytes()+b"\n")
        with self.assertRaisesRegex(module.MarginClaimExperimentError, "margin_snapshot_changed"):
            module.build_margin_claim_report(self.root)

    def test_catalog_double_read_cannot_bind_different_bytes(self):
        real = module._read; count = 0
        def changing(path, limit=3_000_000):
            nonlocal count
            result = real(path, limit)
            if Path(path).name == Path(module.CATALOG).name:
                count += 1
                if count == 2:
                    return result[0]+b"\n", result[1]
            return result
        with patch.object(module, "_read", side_effect=changing):
            with self.assertRaisesRegex(module.MarginClaimExperimentError, "catalog_changed_during_read"):
                module.build_margin_claim_report(self.root)

    def test_strict_json_rejects_duplicate_keys_nonfinite_and_wrong_shape(self):
        path = self.root/module.CATALOG
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'[]'):
            path.write_bytes(raw)
            with self.assertRaises(module.MarginClaimExperimentError):
                module.build_margin_claim_report(self.root)

    def test_global_decimal_context_cannot_change_complete_report(self):
        with localcontext() as context:
            context.prec = 2; context.rounding = ROUND_UP; context.Emax = 3; context.traps[Inexact] = True
            actual = module.build_margin_claim_report(self.root)
        self.assertEqual(actual, self.report)

    def test_runner_is_new_file_only_and_preserves_sources(self):
        before = {p: hashlib.sha256((self.root/p).read_bytes()).hexdigest() for p in (module.CATALOG, bridge.REPORT, module.REPORT)}
        output = runner.run(self.root, "fresh.json")
        self.assertEqual(output["controls"], {"cases": 19, "passed": 19, "failed": 0})
        path = self.root/".run/reproductions/fresh.json"
        self.assertEqual(module.validate_margin_claim_report(self.root, json.loads(path.read_text(encoding="utf-8"))), self.report)
        with self.assertRaises(ValueError):
            runner.run(self.root, "fresh.json")
        self.assertEqual(before, {p: hashlib.sha256((self.root/p).read_bytes()).hexdigest() for p in before})

    def test_output_rejects_public_nested_and_preview_paths(self):
        for path in (self.root/module.REPORT, self.root/module.CATALOG, self.root/".run/document-ai/out.json", "nested/report.json", "../escape.json", "report.txt"):
            with self.assertRaises(ValueError):
                runner.output_path(self.root, path)

    def test_snapshot_exports_no_embedded_old_report_or_model_claim(self):
        snapshot = module.load_margin_claim_snapshot(self.root)
        self.assertNotIn("margin_report", snapshot["report"])
        self.assertEqual(snapshot["report"]["scope"]["model_calls"], 0)
        self.assertEqual(snapshot["report"]["scope"]["new_ocr_calls"], 0)
        self.assertFalse(snapshot["report"]["automatic_approval"])

    def test_missing_required_arithmetic_or_direction_cannot_pass_oracle(self):
        case = self.case("msft-margin-commentary-1")
        result = deepcopy(case["result"])
        result.update(actual=None, status="invalid", numeric_checks=[], direction_check=None)
        comparison = module._oracle_comparison(case["independent_numeric_oracle"], result, require_actual=True)
        self.assertFalse(comparison["passed"])
        self.assertFalse(comparison["arithmetic_compared"])
        result = deepcopy(case["result"]); result["direction_check"] = None
        self.assertFalse(module._oracle_comparison(case["independent_numeric_oracle"], result, require_actual=True)["passed"])
        case = self.case("aapl-margin-commentary-2")
        result = deepcopy(case["result"]); result["rate_display_comparison"] = None
        self.assertFalse(module._oracle_comparison(case["independent_numeric_oracle"], result, require_actual=True)["passed"])

    def test_consistently_reproduced_failure_cannot_be_marked_verified(self):
        report = deepcopy(self.report)
        report["controls"]["cases"][0]["passed"] = False
        report["controls"]["summary"].update(passed=18, failed=1)
        with patch.object(module, "build_margin_claim_report", return_value=report):
            with self.assertRaisesRegex(module.MarginClaimExperimentError, "acceptance_checks_failed"):
                module.validate_margin_claim_report(self.root, report)
        self.assertFalse(self.control("wrong-issuer")["oracle_comparison"]["arithmetic_compared"])
        self.assertTrue(self.control("wrong-issuer")["oracle_comparison"]["structural_refusal_only"])


if __name__ == "__main__":
    unittest.main()
