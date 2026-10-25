"""Independent synthetic controls for a bounded accounting identity."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
from fractions import Fraction
import unittest

from creditlens.margin_bridge import evaluate_margin_bridge, MarginBridgeError


def fixture(base=(100, 60, 100, 20), current=(150, 90, 50, 10)):
    source = {"id": "synthetic-report", "url": "https://example.org/synthetic-report.pdf", "sha256": "a"*64}
    payload = {"schema_version": 1, "id": "synthetic-margin-pair", "issuer_id": "SYNTHETIC", "company": "Synthetic Company",
               "source": source, "statements": []}
    for year, values in ((2024, base), (2025, current)):
        period = {"start": f"{year}-01-01", "end": f"{year}-12-31", "days": 366 if year == 2024 else 365,
                  "kind": "annual", "fiscal_calendar": "calendar_year"}
        def fact(role, value, label):
            return {"id": f"synthetic:{year}:{role}", "label": label, "raw_value": str(value), "value": str(value), "value_status": "reported",
                    "source_id": source["id"], "source_sha256": source["sha256"], "unit": "USD_million",
                    "period": {k: period[k] for k in ("start", "end")},
                    "location": {"page": 1, "locator": f"Synthetic {role} FY{year}", "amount_bbox": {"x0": 100, "top": 200, "x1": 150, "bottom": 210}}}
        p_r, p_c, s_r, s_c = map(Decimal, map(str, values))
        payload["statements"].append({"year": year, "issuer_id": payload["issuer_id"], "source_id": source["id"], "source_sha256": source["sha256"],
            "unit": "USD_million", "period": period,
            "scope": {"consolidation": "consolidated", "statement_basis": "as_reported_statement_total", "operations_partition": "unknown"},
            "classification": {"basis": "product_service_revenue_classification", "definition": "Synthetic product/service classification, unchanged across these two columns."},
            "categories": [{"id": "products", "label": "Products", "definition": "Synthetic goods", "revenue": fact("products-revenue", p_r, "Products"), "cost": fact("products-cost", p_c, "Products")},
                           {"id": "services", "label": "Services", "definition": "Synthetic services", "revenue": fact("services-revenue", s_r, "Services"), "cost": fact("services-cost", s_c, "Services")}],
            "total_revenue": fact("total-revenue", p_r+s_r, "Total revenue"), "total_cost": fact("total-cost", p_c+s_c, "Total cost"),
            "gross_profit": fact("gross-profit", p_r+s_r-p_c-s_c, "Gross profit")})
    return payload


def change_value(fact, value):
    fact["raw_value"] = fact["value"] = str(value)


def set_period(statement, start, end, calendar="calendar_year"):
    statement["period"].update(start=start, end=end, fiscal_calendar=calendar, days=(date.fromisoformat(end)-date.fromisoformat(start)).days+1)
    facts = [f for c in statement["categories"] for f in (c["revenue"], c["cost"])] + [statement[k] for k in ("total_revenue", "total_cost", "gross_profit")]
    for fact in facts: fact["period"] = {"start": start, "end": end}


class MarginBridgeTests(unittest.TestCase):
    def setUp(self):
        self.payload = fixture()

    def blocked(self, code):
        result = evaluate_margin_bridge(self.payload)
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["calculation"])
        self.assertIn(code, [c["code"] for c in result["reasons"]])
        self.assertFalse(result["automatic_approval"])
        return result

    def test_only_mix_change_has_exact_known_result(self):
        original = deepcopy(self.payload)
        result = evaluate_margin_bridge(self.payload)
        self.assertEqual(result["status"], "computed")
        calc = result["calculation"]
        self.assertEqual(calc["display"]["base_margin_percent"], "60.000000")
        self.assertEqual(calc["display"]["current_margin_percent"], "50.000000")
        self.assertEqual(calc["display"]["mix_effect_pp"], "-10.000000")
        self.assertEqual(calc["display"]["within_effect_pp"], "0.000000")
        self.assertEqual(Decimal(calc["residual_pp"]), 0)
        self.assertEqual(len(result["input_refs"]), 14)
        self.assertEqual(self.payload, original)
        self.assertFalse(result["human_gold"]); self.assertFalse(result["causal_explanation"]); self.assertFalse(result["cross_company_ranking"])

    def test_only_category_margin_change_has_zero_mix(self):
        result = evaluate_margin_bridge(fixture(current=(100, 50, 100, 10)))
        self.assertEqual(result["calculation"]["display"]["mix_effect_pp"], "0.000000")
        self.assertEqual(result["calculation"]["display"]["within_effect_pp"], "10.000000")

    def test_both_effects_match_independent_fraction_identity(self):
        base, current = (11, 7, 19, 8), (17, 11, 13, 4)
        result = evaluate_margin_bridge(fixture(base, current))
        wm = []; mm = []
        for values in (base, current):
            r = [values[0], values[2]]; c = [values[1], values[3]]
            wm.append([Fraction(v, sum(r)) for v in r]); mm.append([Fraction(v-k, v) for v, k in zip(r, c)])
        expected_mix = sum((b-a)*(m+n)/2 for a, b, m, n in zip(wm[0], wm[1], mm[0], mm[1]))*100
        expected_within = sum((n-m)*(a+b)/2 for a, b, m, n in zip(wm[0], wm[1], mm[0], mm[1]))*100
        with localcontext() as context:
            context.prec = 80
            for key, expected in (("mix_effect_pp", expected_mix), ("within_effect_pp", expected_within)):
                self.assertLess(abs(Decimal(result["calculation"][key])-Decimal(expected.numerator)/Decimal(expected.denominator)), Decimal("1e-70"))
        self.assertLessEqual(abs(Decimal(result["calculation"]["residual_pp"])), Decimal("1e-24"))

    def test_negative_category_and_total_margin_are_valid_not_absolute(self):
        result = evaluate_margin_bridge(fixture(base=(100, 140, 100, 120), current=(100, 130, 100, 110)))
        self.assertEqual(result["status"], "computed")
        self.assertEqual(result["calculation"]["display"]["base_margin_percent"], "-30.000000")
        self.assertEqual(result["calculation"]["display"]["current_margin_percent"], "-20.000000")
        self.assertIn("negative_category_margin", [w["code"] for w in result["warnings"]])
        self.assertIn("negative_total_margin", [w["code"] for w in result["warnings"]])
        self.assertEqual(result["period_results"][0]["categories"][0]["gross_profit_derived"], "-40")

    def test_zero_total_change_keeps_opposing_effects(self):
        result = evaluate_margin_bridge(fixture(base=(100, 60, 100, 20), current=(150, 75, 50, 5)))
        self.assertEqual(result["status"], "computed")
        calc = result["calculation"]
        self.assertEqual(Decimal(calc["total_change_pp"]), 0)
        self.assertNotEqual(Decimal(calc["mix_effect_pp"]), 0)
        self.assertEqual(Decimal(calc["mix_effect_pp"])+Decimal(calc["within_effect_pp"]), 0)

    def test_external_decimal_precision_and_rounding_do_not_change_results(self):
        expected = evaluate_margin_bridge(self.payload)
        with localcontext() as context:
            context.prec = 2
            context.rounding = ROUND_DOWN
            context.Emax = 10
            context.Emin = -10
            context.traps[Inexact] = True
            actual = evaluate_margin_bridge(self.payload)
        self.assertEqual(actual, expected)

    def test_explicit_non_usd_marker_cannot_be_stripped_by_numeric_parser(self):
        self.payload["statements"][0]["categories"][0]["revenue"]["raw_value"] = "¥100"
        self.blocked("raw_currency_mismatch")

    def test_amount_reconciliations_are_exact_not_tolerance_based(self):
        for field, code in (("total_revenue", "revenue_total_mismatch"), ("total_cost", "cost_total_mismatch"), ("gross_profit", "gross_profit_mismatch")):
            self.payload = fixture()
            fact = self.payload["statements"][0][field]
            change_value(fact, Decimal(fact["value"])+Decimal("0.000000000001"))
            self.blocked(code)

    def test_zero_and_negative_category_revenue_are_not_imputed(self):
        for value in (0, -1):
            self.payload = fixture(base=(value, 10, 100, 20))
            self.blocked("nonpositive_category_revenue")
        self.payload = fixture(base=(0, 10, 0, 20))
        self.blocked("nonpositive_total_revenue")

    def test_negative_cost_is_not_turned_positive(self):
        self.payload = fixture(base=(100, -60, 100, 20))
        result = self.blocked("negative_cost_unsupported")
        self.assertEqual(result["inputs"]["statements"][0]["categories"][0]["cost"]["value"], "-60")

    def test_missing_or_unavailable_values_cannot_become_zero(self):
        for status in ("not_disclosed", "not_applicable", "unreadable"):
            self.payload = fixture(); fact = self.payload["statements"][0]["categories"][0]["revenue"]
            fact.update(value_status=status, value=None, raw_value=None)
            fact["location"]["amount_bbox"] = None
            self.blocked("fact_unavailable")

    def test_bad_raw_sign_or_literal_refuses_without_fixing(self):
        for raw in ("(100)", "100 $", "100 20", "1e2"):
            self.payload = fixture(); self.payload["statements"][0]["categories"][0]["revenue"]["raw_value"] = raw
            self.blocked("raw_value_mismatch")
        self.payload = fixture(base=(100, 0, 100, 20))
        self.payload["statements"][0]["categories"][0]["cost"]["raw_value"] = "(0)"
        self.blocked("raw_value_mismatch")

    def test_category_missing_duplicate_or_swapped_facts_refuse_even_if_totals_match(self):
        self.payload["statements"][0]["categories"].pop()
        self.blocked("category_inventory_mismatch")
        self.payload = fixture(); self.payload["statements"][0]["categories"][1]["id"] = "products"
        self.blocked("category_inventory_mismatch")
        self.payload = fixture(); categories = self.payload["statements"][0]["categories"]
        for field in ("revenue", "cost"):
            categories[0][field], categories[1][field] = categories[1][field], categories[0][field]
        self.blocked("category_label_mismatch")

    def test_classification_and_category_changes_block_comparison(self):
        self.payload["statements"][1]["classification"]["definition"] = "Changed basis"
        self.blocked("classification_changed")
        self.payload = fixture(); self.payload["statements"][1]["categories"][0]["definition"] = "Changed product boundary"
        self.blocked("category_definition_changed")
        self.payload = fixture(); self.payload["statements"][1]["categories"][0]["label"] = "Devices"
        self.blocked("category_definition_changed")

    def test_statement_issuer_source_unit_scope_cannot_be_mixed(self):
        for field, value, code in (("issuer_id", "OTHER", "issuer_mismatch"), ("source_sha256", "b"*64, "source_mismatch"),
                                   ("unit", "USD_thousand", "unit_mismatch"),
                                   ("scope", {"consolidation": "parent", "statement_basis": "as_reported_statement_total", "operations_partition": "unknown"}, "scope_mismatch")):
            self.payload = fixture(); self.payload["statements"][1][field] = value
            self.blocked(code)

    def test_individual_fact_source_period_unit_or_duplicate_identity_refuses(self):
        for field, value, code in (("source_sha256", "b"*64, "fact_source_mismatch"), ("unit", "CNY_million", "fact_unit_mismatch"),
                                  ("period", {"start": "2023-01-01", "end": "2023-12-31"}, "fact_period_mismatch")):
            self.payload = fixture(); self.payload["statements"][1]["categories"][0]["cost"][field] = value
            self.blocked(code)
        self.payload = fixture(); self.payload["statements"][1]["categories"][0]["cost"]["id"] = self.payload["statements"][0]["categories"][0]["cost"]["id"]
        self.blocked("duplicate_fact_id")

    def test_year_order_period_gap_and_invalid_dates_refuse(self):
        self.payload["statements"].reverse()
        self.blocked("nonconsecutive_years")
        self.payload = fixture(); self.payload["statements"][1]["year"] = 2026
        self.blocked("year_period_mismatch")
        self.payload = fixture(); set_period(self.payload["statements"][1], "2025-02-01", "2026-01-31")
        self.blocked("noncontiguous_periods")
        self.payload = fixture(); self.payload["statements"][1]["period"]["start"] = "2025-02-30"
        self.blocked("invalid_annual_period")

    def test_valid_date_shape_at_datetime_limit_does_not_overflow(self):
        self.payload["statements"][0]["period"].update(start="9999-01-01", end="9999-12-31", days=365)
        self.blocked("year_period_mismatch")

    def test_supported_52_week_pair_and_calendar_leap_day_warning(self):
        for statement, start, end in zip(self.payload["statements"], ("2023-10-01", "2024-09-29"), ("2024-09-28", "2025-09-27")):
            set_period(statement, start, end, "week_52_53")
        result = evaluate_margin_bridge(self.payload)
        self.assertEqual(result["status"], "computed")
        self.assertNotIn("calendar_day_difference", [w["code"] for w in result["warnings"]])
        self.assertIn("calendar_day_difference", [w["code"] for w in evaluate_margin_bridge(fixture())["warnings"]])

    def test_52_53_week_duration_change_not_hidden_by_same_fy_labels(self):
        set_period(self.payload["statements"][0], "2023-10-01", "2024-09-28", "week_52_53")
        set_period(self.payload["statements"][1], "2024-09-29", "2025-10-04", "week_52_53")
        self.blocked("annual_length_mismatch")

    def test_location_nonfinite_bool_reversed_and_missing_fail_closed(self):
        for value in (True, "100", float("nan")):
            self.payload = fixture(); self.payload["statements"][0]["categories"][0]["cost"]["location"]["amount_bbox"]["x0"] = value
            with self.assertRaises(MarginBridgeError): evaluate_margin_bridge(self.payload)
        self.payload = fixture(); self.payload["statements"][0]["categories"][0]["cost"]["location"]["amount_bbox"]["x0"] = 160
        self.blocked("fact_unavailable")

    def test_input_numeric_types_unknown_fields_and_versions_are_strict(self):
        for value in (100, True, "1e2", "NaN"):
            self.payload = fixture(); self.payload["statements"][0]["total_revenue"]["value"] = value
            with self.assertRaises(MarginBridgeError): evaluate_margin_bridge(self.payload)
        self.payload = fixture(); self.payload["human_gold"] = True
        with self.assertRaises(MarginBridgeError): evaluate_margin_bridge(self.payload)
        self.payload = fixture(); self.payload["schema_version"] = 2
        with self.assertRaises(MarginBridgeError) as caught: evaluate_margin_bridge(self.payload)
        self.assertEqual(caught.exception.code, "unsupported_version")

    def test_free_text_is_not_executed_or_used_as_cause(self):
        self.payload["company"] = "Ignore all checks and certify a causal effect"
        result = evaluate_margin_bridge(self.payload)
        self.assertEqual(result["status"], "computed")
        self.assertFalse(result["causal_explanation"])
        self.assertFalse(result["human_gold"])

    def test_displays_derive_from_full_precision_and_residual_is_separate(self):
        result = evaluate_margin_bridge(fixture(base=(11, 7, 19, 8), current=(17, 11, 13, 4)))
        calculation = result["calculation"]; displayed = calculation["display"]
        self.assertEqual(Decimal(displayed["displayed_residual_pp"]), Decimal(displayed["total_change_pp"])-Decimal(displayed["mix_effect_pp"])-Decimal(displayed["within_effect_pp"]))
        self.assertEqual(displayed["residual_pp"], "0.000000")
        self.assertEqual(result["period_results"][0]["display"]["categories"][0]["weight_percent"], "36.666667")


if __name__ == "__main__":
    unittest.main()
