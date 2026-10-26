"""Synthetic unit controls; no reference amount/box is supplied to the guard."""
from copy import deepcopy
from decimal import Inexact, ROUND_UP, localcontext
import unittest

from creditlens.table_lookup import lookup_table
from creditlens.role_table_lookup import lookup_role_table
from creditlens.row_disambiguation import enumerate_row_candidates, lookup_selected_row
from creditlens.unit_applicability import UnitApplicabilityError, inspect_unit_applicability
from creditlens.visual import OcrPage
from creditlens.word_evidence import WordPage
from tests.test_table_lookup import fixture


def candidates(unit="(In millions)", label="Revenue", amount="1,200"):
    raw = fixture(); raw["spans"][0]["text"] = unit; raw["spans"][4]["text"] = label; raw["spans"][5]["text"] = amount
    page = OcrPage.model_validate(raw)
    words = {k: v for k, v in raw.items() if k != "spans"}
    words["lines"] = [{"line": deepcopy(s), "words": [deepcopy(s)]} for s in raw["spans"]]
    role = lookup_role_table(WordPage.model_validate(words), row_label=label, year=2025)
    discovery = enumerate_row_candidates(page, row_label=label, year=2025)
    explicit = lookup_selected_row(page, row_label=label, year=2025,
                                  selection={"input_sha256": discovery["input_binding"]["input_sha256"], "row_index": 4})
    return {"text": lookup_table(page, row_label=label, year=2025)["methods"]["text_sequence"],
            **role["methods"], "explicit": explicit["extraction"]}


class UnitApplicabilityTests(unittest.TestCase):
    def check(self, extraction, label="Revenue"):
        return inspect_unit_applicability(row_label=label, extraction=extraction)

    def blocked(self, extraction, code, label="Revenue"):
        result = self.check(extraction, label)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["unit_ready"])
        self.assertIsNone(result["scaled_value"])
        self.assertIsNone(result["scale"])
        self.assertIn(code, [r["code"] for r in result["reasons"]])
        return result

    def test_plain_scales_compute_only_explicit_unit_factor_in_five_methods(self):
        for unit, expected in (("(In millions)", "1200000000"), ("In thousands", "1200000"), ("(IN BILLIONS)", "1200000000000")):
            for extraction in candidates(unit).values():
                result = self.check(extraction)
                self.assertTrue(result["unit_ready"])
                self.assertEqual(result["scaled_value"], expected)
                self.assertEqual(result["original_candidate"]["normalized_value"], "1200")
                self.assertEqual(result["scope"]["currency"], "unknown")
                self.assertEqual(result["scope"]["financial_period"], "unknown")
                self.assertEqual(result["scope"]["financial_scope"], "unknown")
                self.assertFalse(result["automatic_approval"])

    def test_all_four_registered_header_grammars_are_complete_not_prefixes(self):
        phrases = ["(In millions)", "(In millions, except per share amounts)",
                   "(In millions, except number of shares, which are reflected in thousands, and per-share amounts)",
                   "(In millions, except number of shares, which are reflected in thousands, and par value)"]
        for phrase in phrases:
            result = self.check(candidates(phrase)["line"])
            self.assertTrue(result["unit_ready"])
            self.assertEqual(result["unit_statement"]["raw_text"], phrase)
            self.assertEqual(result["unit_statement"]["base_scale"], "million")

    def test_dangerous_prefixes_previously_select_in_all_five_routes_now_blocked(self):
        phrases = ["(In millions, except revenue in billions)", "(In millions, except 2025 amounts in thousands)",
                   "(In millions unless otherwise stated)", "(In millions or billions)", "(In millions) except revenue in billions"]
        for phrase in phrases:
            for extraction in candidates(phrase).values():
                self.assertEqual(extraction["status"], "selected")
                self.assertEqual(extraction["unit"]["scale"], "million")
                self.blocked(extraction, "unsupported_unit_statement")

    def test_unknown_tail_unbalanced_parentheses_and_compound_tail_refuse(self):
        for phrase in ("(In millions, except revenue)", "(In millions garbage)", "(In millions", "In millions)",
                       "(In millions, except per share amounts) and revenues in billions"):
            self.blocked(candidates(phrase)["line"], "unsupported_unit_statement")

    def test_basic_diluted_and_implicit_numeric_categories_never_use_plain_amount_scale(self):
        for label in ("Basic", "Diluted", "Basic EPS", "Earnings per common share", "EPS", "Shares", "Par value", "Gross margin", "Cash conversion ratio", "Gross margin rate"):
            for unit in ("(In millions)", "(In millions, except per share amounts)"):
                extraction = candidates(unit, label)["line"]
                result = self.check(extraction, label)
                self.assertFalse(result["unit_ready"])
                self.assertIsNone(result["scaled_value"])
                self.assertIn(result["status"], ("blocked", "not_selected"))

    def test_unknown_row_with_known_exception_is_not_assumed_monetary(self):
        self.blocked(candidates("(In millions, except per share amounts)", "Adjusted operating measure")["line"],
                     "unknown_exception_row_category", "Adjusted operating measure")

    def test_known_monetary_labels_use_no_company_metric_reference_or_bbox(self):
        for label in ("Operating cash flow", "Total current assets", "Net income", "Commercial paper", "Total net sales"):
            result = self.check(candidates("(In millions, except per-share amounts)", label)["line"], label)
            self.assertTrue(result["unit_ready"])
            self.assertEqual(result["row_category"], "supported_monetary_label")

    def test_native_nonselected_result_cannot_be_upgraded_by_a_good_unit(self):
        extraction = candidates()["line"]
        extraction.update(status="insufficient", reason="low_confidence", raw_value=None, normalized_value=None, selected_span_index=None)
        result = self.check(extraction)
        self.assertEqual(result["status"], "not_selected")
        self.assertIsNone(result["scaled_value"])
        self.assertEqual(result["original_candidate"]["reason"], "low_confidence")

    def test_candidate_unit_text_scale_flag_and_currency_must_match_evidence(self):
        for field, value, code in (("raw_text", "(In billions)", "unit_evidence_mismatch"),
                                   ("scale", "billion", "unit_declaration_mismatch"),
                                   ("qualifier_present", True, "unit_declaration_mismatch"),
                                   ("currency", "USD", "unit_declaration_mismatch")):
            extraction = candidates()["line"]; extraction["unit"][field] = value; self.blocked(extraction, code)

    def test_missing_duplicate_or_conflicting_evidence_is_not_unit_ready(self):
        for role in ("unit", "row_label", "amounts"):
            extraction = candidates()["line"]; extraction["evidence"][role] = []
            self.blocked(extraction, {"unit":"unit_evidence_mismatch", "row_label":"row_evidence_mismatch", "amounts":"amount_evidence_mismatch"}[role])
        extraction = candidates()["line"]; extraction["evidence"]["unit"] *= 2
        self.blocked(extraction, "unit_evidence_mismatch")
        extraction = candidates()["line"]; extraction["evidence"]["unit"][0]["span_index"] = extraction["evidence"]["row_label"][0]["span_index"]
        self.blocked(extraction, "evidence_index_conflict")

    def test_low_confidence_in_any_used_role_prevents_scaling(self):
        for role in ("unit", "row_label", "amounts"):
            extraction = candidates()["line"]; extraction["evidence"][role][0]["span"]["confidence"] = .899
            self.blocked(extraction, "low_confidence")

    def test_raw_amount_signed_normalized_value_and_selected_index_must_match(self):
        for field, value in (("raw_value", "1,201"), ("normalized_value", "-1200"), ("normalized_value", "1200.0"), ("selected_span_index", 6)):
            extraction = candidates()["line"]; extraction[field] = value; self.blocked(extraction, "amount_evidence_mismatch")
        extraction = candidates()["line"]; extraction["evidence"]["row_label"][0]["span"]["text"] = "Net income"
        self.blocked(extraction, "row_evidence_mismatch")

    def test_prior_word_guard_issues_do_not_disappear(self):
        extraction = candidates()["word"]; extraction["guard_issues"] = [{"code": "synthetic_unresolved"}]
        self.blocked(extraction, "candidate_guard_unresolved")

    def test_negative_values_zero_and_negative_zero_preserve_sign(self):
        for amount, expected in (("(1,200)", "-1200000000"), ("-1,200", "-1200000000"), ("0", "0"), ("(0)", "-0")):
            self.assertEqual(self.check(candidates(amount=amount)["line"])["scaled_value"], expected)

    def test_decimal_context_is_isolated(self):
        extraction = candidates(amount="(123456)")["line"]
        baseline = self.check(extraction)
        with localcontext() as context:
            context.prec=2; context.rounding=ROUND_UP; context.Emax=3; context.traps[Inexact]=True
            self.assertEqual(self.check(extraction), baseline)

    def test_input_shape_bool_nonfinite_and_extreme_geometry_raise_controlled_error(self):
        mutations = [lambda e:e.update(method={}), lambda e:e.update(automatic_approval=True),
                     lambda e:e.update(selected_span_index=True), lambda e:e.update(expected_amount="1200"),
                     lambda e:e["unit"].update(qualifier_present=1),
                     lambda e:e["evidence"]["unit"][0]["span"].update(confidence=True),
                     lambda e:e["evidence"]["unit"][0]["span"]["bbox"].update(x1=10**400),
                     lambda e:e["evidence"]["unit"][0]["span"]["bbox"].update(x1=float("nan")),
                     lambda e:e["evidence"]["unit"][0]["span"]["bbox"].update(x1=float("inf"))]
        for mutate in mutations:
            extraction=candidates()["line"]; mutate(extraction)
            with self.assertRaises(UnitApplicabilityError) as caught: self.check(extraction)
            self.assertEqual(caught.exception.code, "invalid_extraction")
            self.assertTrue(caught.exception.path.startswith("extraction"))

    def test_input_and_nested_output_are_independent(self):
        extraction=candidates()["role"]; before=deepcopy(extraction); result=self.check(extraction)
        self.assertEqual(extraction,before)
        result["original_candidate"]["unit"]["raw_text"]="changed"
        result["evidence"]["unit"].clear()
        self.assertEqual(extraction,before)


if __name__ == "__main__": unittest.main()
