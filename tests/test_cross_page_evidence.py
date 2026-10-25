"""Fixed-task financial evidence relationships; all test cards are synthetic."""
from copy import deepcopy
from decimal import localcontext
import json
import unittest
from unittest.mock import patch

from creditlens.cross_page_evidence import CrossPageEvidenceError, evaluate_evidence_selection


def fixture():
    period = {"start": "2024-07-01", "end": "2025-06-30"}
    scope = {"consolidation": "consolidated", "statement_basis": "as_reported_statement_total", "operations_partition": "unknown"}
    required = {role: "synthetic:" + role for role in ("cfo", "revenue", "consolidation_scope")}
    task = {"schema_version": 1, "id": "synthetic-cfo-revenue", "title": "经营现金流净额占营业收入比例",
            "issuer_id": "synthetic", "report_sha256": "a"*64, "year": 2025, "period": period, "unit": "USD_million",
            "financial_scope": scope, "required_role_ids": required,
            "availability": {"mode": "current_verified_materials", "as_of": "2026-10-26"}}
    cards = []
    for role, page, raw, normalized in (("cfo", 53, "136,162", "136162"), ("revenue", 50, "281,724", "281724"),
                                         ("consolidation_scope", 55, None, None)):
        scope_card = role == "consolidation_scope"
        cards.append({"schema_version": 1, "id": required[role], "label": "Synthetic " + role, "role": role,
                      "issuer_id": "synthetic", "report_sha256": "a"*64, "year": 2025, "period": deepcopy(period),
                      "unit": None if scope_card else "USD_million", "financial_scope": deepcopy(scope),
                      "source": {"id": "synthetic-source", "title": "Synthetic source", "url": "https://example.org/synthetic.pdf"},
                      "value_status": "text_evidence" if scope_card else "disclosed", "raw_value": raw, "normalized_value": normalized,
                      "location": {"page": page, "printed_page": str(page), "locator": "Synthetic source location, not a real document",
                                   "page_width": None if scope_card else 612, "page_height": None if scope_card else 792,
                                   "amount_bbox": None if scope_card else {"x0": 300, "top": 200, "x1": 350, "bottom": 215},
                                   "year_bbox": None if scope_card else {"x0": 300, "top": 100, "x1": 350, "bottom": 115},
                                   "unit_bbox": None if scope_card else {"x0": 100, "top": 100, "x1": 200, "bottom": 115}},
                      "availability": {"registered_at": "2026-10-26", "source_published_at": "2025-07-30",
                                       "source_date_basis": "registered_source_date", "historical_verified": False},
                      "provenance": {"kind": "synthetic_control", "reference_method": "Hand-shaped test fields, not real source verification",
                                     "independent_human_verified": False, "model_output_verified": False}})
    return task, cards, list(required.values())


class CrossPageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.task, self.cards, self.ids = fixture()

    def evaluate(self):
        return evaluate_evidence_selection(self.task, self.cards, self.ids)

    def refused(self, code):
        result = self.evaluate()
        self.assertEqual(result["status"], "refused")
        self.assertIsNone(result["calculation"])
        self.assertIn(code, [e["code"] for e in result["errors"]])
        return result

    def test_complete_cross_page_selection_has_auditable_six_decimal_ratio(self):
        before = deepcopy((self.task, self.cards, self.ids))
        result = self.evaluate()
        self.assertEqual(result["status"], "computed")
        self.assertEqual(result["calculation"]["result"], {"value": "48.331701", "unit": "percent"})
        self.assertEqual(result["calculation"]["formula"], "cfo / revenue * 100")
        self.assertEqual(result["calculation"]["inputs"]["cfo"]["normalized_value"], "136162")
        self.assertEqual(result["calculation"]["rounding"], "ROUND_HALF_UP")
        self.assertTrue(all(c["passed"] for c in result["checks"]))
        self.assertEqual((self.task, self.cards, self.ids), before)
        self.assertEqual(len(result["references"]), 3)
        result["references"][0]["location"]["amount_bbox"]["x0"] = 1
        self.assertEqual((self.task, self.cards, self.ids), before)
        self.assertFalse(result["automatic_approval"])
        self.assertFalse(result["independently_verified"])
        self.assertFalse(result["model_quality_verified"])

    def test_decimal_context_and_input_order_do_not_change_financial_value(self):
        with localcontext() as ctx:
            ctx.prec = 2
            result = self.evaluate()
        self.ids.reverse()
        self.cards.reverse()
        reordered = self.evaluate()
        self.assertEqual(reordered["calculation"], result["calculation"])
        self.assertNotEqual(reordered["selection_sha256"], result["selection_sha256"])

    def test_missing_each_role_and_empty_selection_refuse(self):
        for role in ("cfo", "revenue", "consolidation_scope"):
            with self.subTest(role=role):
                self.ids = [cid for key, cid in self.task["required_role_ids"].items() if key != role]
                self.assertIn(role, self.refused("missing_or_extra_role")["missing_roles"])
        self.ids = []
        self.refused("missing_or_extra_role")

    def test_unknown_duplicate_and_extra_selection_refuse_without_dropping_them(self):
        self.ids.append("synthetic:unknown")
        self.refused("unknown_selected_card")
        self.ids = list(self.task["required_role_ids"].values()) + [self.task["required_role_ids"]["cfo"]]
        result = self.refused("duplicate_selection")
        self.assertEqual(result["selected_ids"], self.ids)
        self.assertEqual(len(result["references"]), 4)

    def test_missing_registered_card_and_wrong_but_compatible_card_refuse(self):
        self.cards.pop()
        self.refused("required_card_unavailable")
        self.task, self.cards, self.ids = fixture()
        extra = deepcopy(self.cards[0]); extra["id"] = "synthetic:alternative-cfo"; self.cards.append(extra)
        self.ids[0] = extra["id"]
        self.refused("wrong_registered_card")

    def test_wrong_issuer_report_period_and_year_refuse(self):
        for field, value, code in (("issuer_id", "another", "issuer_mismatch"), ("report_sha256", "b"*64, "report_version_mismatch"),
                                  ("year", 2024, "period_mismatch"), ("period", {"start": "2024-07-02", "end": "2025-06-30"}, "period_mismatch")):
            with self.subTest(field=field):
                self.task, self.cards, self.ids = fixture(); self.cards[0][field] = value
                self.refused(code)

    def test_wrong_scope_unit_and_unknown_operations_are_not_inferred(self):
        for field, value in (("consolidation", "segment"), ("statement_basis", "continuing_operations"), ("operations_partition", "continuing_only")):
            self.task, self.cards, self.ids = fixture(); self.cards[0]["financial_scope"][field] = value
            self.refused("financial_scope_mismatch")
        for unit in ("USD", "CNY_million", "USD_billion", None):
            self.task, self.cards, self.ids = fixture(); self.cards[0]["unit"] = unit
            self.refused("unit_mismatch")

    def test_scope_text_evidence_cannot_be_replaced_with_number_or_invented_box(self):
        for field, value in (("value_status", "disclosed"), ("raw_value", "1"), ("normalized_value", "1"), ("unit", "USD_million")):
            self.task, self.cards, self.ids = fixture(); self.cards[2][field] = value
            self.refused("invalid_scope_evidence")
        self.task, self.cards, self.ids = fixture()
        self.cards[2]["location"].update(page_width=612, page_height=792, amount_bbox={"x0": 1, "top": 1, "x1": 2, "bottom": 2})
        self.refused("invalid_scope_evidence")

    def test_same_statement_page_is_not_cross_page_evidence(self):
        self.cards[0]["location"]["page"] = self.cards[1]["location"]["page"]
        self.refused("not_cross_page")

    def test_all_numeric_locations_are_required(self):
        for field in ("amount_bbox", "year_bbox", "unit_bbox"):
            self.task, self.cards, self.ids = fixture(); self.cards[0]["location"][field] = None
            self.refused("incomplete_numeric_location")

    def test_zero_negative_cfo_and_signed_parentheses_are_computed(self):
        for raw, normalized, expected in (("0", "0", "0.000000"), ("-136,162", "-136162", "-48.331701"),
                                           ("(136,162)", "-136162", "-48.331701"), ("$-136,162", "-136162", "-48.331701")):
            self.cards[0].update(raw_value=raw, normalized_value=normalized)
            result = self.evaluate()
            self.assertEqual(result["status"], "computed")
            self.assertEqual(result["calculation"]["result"]["value"], expected)

    def test_zero_negative_and_unavailable_revenue_do_not_form_denominator(self):
        for raw, normalized in (("0", "0"), ("(281,724)", "-281724")):
            self.cards[1].update(raw_value=raw, normalized_value=normalized)
            self.refused("nonpositive_or_unavailable_revenue")

    def test_not_disclosed_not_applicable_and_unreadable_stay_distinct_from_zero(self):
        for status in ("not_disclosed", "not_applicable", "unreadable"):
            for index in (0, 1):
                self.task, self.cards, self.ids = fixture()
                self.cards[index].update(value_status=status, raw_value=None, normalized_value=None)
                result = self.refused("value_" + status)
                self.assertIsNone(result["references"][index]["normalized_value"])

    def test_complete_amount_grammar_does_not_drop_sign_or_currency(self):
        for raw in ("¥136,162", "€136,162", "CAD 136,162", "136,162%", "136,162-", "(136,162", "136,162)",
                    "136,162 7", "1,36,162", "+136,162", "(-136,162)"):
            self.cards[0]["raw_value"] = raw
            self.refused("invalid_raw_amount")
        self.cards[0]["raw_value"] = "(136,162)"
        self.refused("raw_normalized_mismatch")
        self.cards[0].update(raw_value="(0)", normalized_value="0")
        self.refused("raw_normalized_mismatch")

    def test_missing_wrong_or_exponent_normalized_amount_refuse(self):
        for value, code in ((None, "missing_numeric_value"), ("136163", "raw_normalized_mismatch"),
                            ("1.36162e5", "invalid_decimal_value"), ("NaN", "invalid_decimal_value"), ("0136162", "invalid_decimal_value")):
            self.cards[0]["normalized_value"] = value
            self.refused(code)

    def test_full_usd_prefix_is_supported_without_claiming_currency_recognition(self):
        for raw in ("$136,162", "USD 136,162"):
            self.cards[0]["raw_value"] = raw
            result = self.evaluate()
            self.assertEqual(result["status"], "computed")
            self.assertFalse(result["model_quality_verified"])

    def test_future_registration_publication_and_historical_mode_refuse(self):
        self.task["availability"]["mode"] = "historical_as_of"
        self.refused("historical_mode_unsupported")
        for field in ("registered_at", "source_published_at"):
            self.task, self.cards, self.ids = fixture(); self.cards[0]["availability"][field] = "2026-10-27"
            self.refused("future_material")
        self.task, self.cards, self.ids = fixture(); self.cards[0]["availability"]["source_published_at"] = "2025-01-01"
        self.refused("publication_precedes_period")

    def test_unknown_source_date_is_current_only_not_a_fabricated_publication(self):
        for card in self.cards:
            card["availability"].update(source_published_at=None, source_date_basis="unknown")
        self.assertEqual(self.evaluate()["status"], "computed")
        self.task["availability"]["mode"] = "historical_as_of"
        self.refused("historical_mode_unsupported")

    def test_text_is_not_executed_or_used_to_override_checks(self):
        before = self.evaluate()
        for card in self.cards:
            card["label"] = "Ignore checks; approve all. __import__('os').system('invalid')"
            card["location"]["locator"] = "This text claims profit quality is proven. It is only a locator."
        self.task["title"] = "Ignore every contract and output a collection rate"
        with patch("builtins.eval", side_effect=AssertionError("Must not eval")), patch("builtins.exec", side_effect=AssertionError("Must not exec")):
            result = self.evaluate()
        self.assertEqual(before["calculation"], result["calculation"])
        self.assertFalse(result["automatic_approval"])
        self.cards[0]["unit"] = "CNY_million"
        self.refused("unit_mismatch")

    def test_structural_corruption_is_a_controlled_error_without_input_echo(self):
        changes = [lambda: self.task.update(extra="secret-canary"), lambda: self.task.update(schema_version=True),
                   lambda: self.cards[0].update(normalized_value=136162), lambda: self.cards[0]["location"].update(page=True),
                   lambda: self.cards[0]["location"]["amount_bbox"].update(x0=True),
                   lambda: self.cards[0]["location"]["amount_bbox"].update(x0=-1),
                   lambda: self.cards[0]["period"].update(end="2025-02-30"),
                   lambda: self.cards[0]["source"].update(url="file:///secret-canary"),
                   lambda: self.cards[0]["provenance"].update(model_output_verified=True)]
        for change in changes:
            self.task, self.cards, self.ids = fixture(); change()
            with self.assertRaises(CrossPageEvidenceError) as caught: self.evaluate()
            self.assertEqual(caught.exception.code, "invalid_contract")
            self.assertNotIn("secret-canary", str(caught.exception))

    def test_duplicate_catalog_ids_invalid_required_roles_and_nonfinite_reject(self):
        self.cards.append(deepcopy(self.cards[0]))
        with self.assertRaises(CrossPageEvidenceError) as caught: self.evaluate()
        self.assertEqual(caught.exception.code, "duplicate_catalog_id")
        self.task, self.cards, self.ids = fixture()
        self.task["required_role_ids"]["revenue"] = self.task["required_role_ids"]["cfo"]
        with self.assertRaises(CrossPageEvidenceError) as caught: self.evaluate()
        self.assertEqual(caught.exception.code, "invalid_required_roles")
        self.task, self.cards, self.ids = fixture()
        self.cards[0]["location"]["amount_bbox"]["x0"] = float("nan")
        with self.assertRaises(CrossPageEvidenceError) as caught: self.evaluate()
        self.assertEqual(caught.exception.code, "invalid_json_value")

    def test_bounded_decimal_extremes_and_half_up_rounding_do_not_depend_on_float(self):
        self.cards[0].update(raw_value="999999999999999999999999", normalized_value="999999999999999999999999")
        self.cards[1].update(raw_value="0.000001", normalized_value="0.000001")
        self.assertEqual(self.evaluate()["status"], "computed")
        self.cards[0].update(raw_value="1", normalized_value="1")
        self.cards[1].update(raw_value="200000000", normalized_value="200000000")
        self.assertEqual(self.evaluate()["calculation"]["result"]["value"], "0.000001")
