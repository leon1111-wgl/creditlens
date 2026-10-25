"""Independent synthetic extraction controls; no gold amount is an input."""
from copy import deepcopy
from decimal import localcontext
import unittest
from unittest.mock import patch

from creditlens.table_lookup import (TableLookupError, lookup_table, lookup_text_sequence,
                                    lookup_layout_column, parse_lookup_amount)
from creditlens.visual import OcrPage


def span(text, x0, top, x1, bottom, confidence=.99):
    return {"text": text, "confidence": confidence, "bbox": {"x0": x0, "top": top, "x1": x1, "bottom": bottom}}


def fixture():
    return {"page": 1, "width": 500, "height": 700, "coordinate_system": "pdf_points_top_left", "spans": [
        span("(In millions)", 20, 10, 150, 20), span("Year ended June 30,", 20, 30, 150, 40),
        span("2025", 285, 30, 315, 40), span("2024", 385, 30, 415, 40),
        span("Operating cash flow", 20, 80, 180, 90), span("1,200", 280, 80, 320, 90), span("800", 390, 80, 415, 90),
        span("Other assets", 20, 110, 180, 120), span("400", 290, 110, 320, 120), span("300", 390, 110, 420, 120),
    ]}


class TableLookupTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture()

    def lookup(self, label="Operating cash flow", year=2025):
        return lookup_table(OcrPage.model_validate(self.raw), row_label=label, year=year)

    def both_insufficient(self, reason=None):
        result = self.lookup()
        for method in result["methods"].values():
            self.assertEqual(method["status"], "insufficient")
            self.assertIsNone(method["normalized_value"])
            self.assertIsNone(method["selected_span_index"])
            self.assertTrue(method["message"])
            if reason: self.assertEqual(method["reason"], reason)
        return result

    def test_normal_two_column_query_selects_same_full_span_with_trace(self):
        original = deepcopy(self.raw)
        result = self.lookup()
        for method in result["methods"].values():
            self.assertEqual(method["status"], "selected")
            self.assertEqual(method["raw_value"], "1,200")
            self.assertEqual(method["normalized_value"], "1200")
            self.assertEqual(method["selected_span_index"], 5)
            self.assertEqual(method["unit"]["raw_text"], "(In millions)")
            self.assertEqual(method["unit"]["scale"], "million")
            self.assertIsNone(method["unit"]["currency"])
            self.assertFalse(method["automatic_approval"])
            selected = next(e for e in method["evidence"]["amounts"] if e["span_index"] == 5)
            self.assertEqual(selected["span"]["text"], self.raw["spans"][5]["text"])
        self.assertEqual(self.raw, original)
        self.assertEqual(self.lookup(year=2024)["methods"]["layout_column"]["raw_value"], "800")

    def test_ocr_sequence_swapped_cells_can_mislead_baseline_but_not_layout(self):
        self.raw["spans"][5], self.raw["spans"][6] = self.raw["spans"][6], self.raw["spans"][5]
        result = self.lookup()
        self.assertEqual(result["methods"]["text_sequence"]["normalized_value"], "800")
        self.assertEqual(result["methods"]["layout_column"]["normalized_value"], "1200")

    def test_text_baseline_never_uses_geometry_to_select(self):
        page = OcrPage.model_validate(self.raw)
        with patch("creditlens.table_lookup._cx", side_effect=AssertionError("No geometry")), \
             patch("creditlens.table_lookup._cy", side_effect=AssertionError("No geometry")), \
             patch("creditlens.table_lookup._same_row", side_effect=AssertionError("No geometry")):
            before = lookup_text_sequence(page, row_label="Operating cash flow", year=2025)
        self.raw["spans"][5]["bbox"] = deepcopy(self.raw["spans"][6]["bbox"])
        after = self.lookup()
        self.assertEqual(after["methods"]["text_sequence"]["normalized_value"], before["normalized_value"])
        self.assertEqual(after["methods"]["layout_column"]["status"], "insufficient")

    def test_label_normalization_is_whole_line_not_substring(self):
        self.raw["spans"][4]["text"] = "  OPERATING  cash\nflow: "
        self.assertEqual(self.lookup()["methods"]["layout_column"]["status"], "selected")
        self.raw["spans"][4]["text"] = "Adjustments to reconcile Operating cash flow:"
        self.both_insufficient("row_not_found")
        self.raw["spans"][4]["text"] = "Operating cash"
        self.raw["spans"].insert(5, span("flow", 20, 90, 100, 100))
        self.both_insufficient("row_not_found")

    def test_duplicate_labels_in_different_sections_both_refuse(self):
        self.raw["spans"].append(span("Operating cash flow", 20, 300, 180, 310))
        result = self.both_insufficient("duplicate_row_label")
        self.assertEqual(len(result["methods"]["layout_column"]["evidence"]["row_label"]), 2)

    def test_missing_year_column_and_footer_year_do_not_complete_header(self):
        self.raw["spans"].pop(3)
        self.raw["spans"].append(span("Apple Inc. | 2024 Form 10-K | 29", 100, 650, 400, 665))
        self.both_insufficient("missing_year_group")
        self.raw["spans"].append(span("2024", 390, 675, 420, 690))
        self.both_insufficient("missing_year_group")

    def test_wrong_query_year_and_repeated_header_year_refuse(self):
        result = self.lookup(year=2023)
        self.assertTrue(all(r["reason"] == "year_not_found" for r in result["methods"].values()))
        self.raw["spans"][3]["text"] = "2025"
        self.both_insufficient("duplicate_header_year")

    def test_second_header_group_is_not_silently_ignored(self):
        self.raw["spans"][4:4] = [span("2025", 285, 50, 315, 60), span("2024", 385, 50, 415, 60)]
        self.both_insufficient()

    def test_a_later_isolated_year_does_not_borrow_the_previous_complete_header(self):
        self.raw["spans"][4:4] = [span("2025", 285, 50, 315, 60)]
        result = self.both_insufficient()
        self.assertEqual(result["methods"]["text_sequence"]["reason"], "duplicate_header_year")
        self.assertEqual(result["methods"]["layout_column"]["reason"], "ambiguous_year_group")

    def test_missing_or_multiple_units_refuse_and_currency_remains_unknown(self):
        self.raw["spans"].pop(0)
        self.both_insufficient("unit_missing")
        self.raw = fixture(); self.raw["spans"].insert(0, span("(In thousands)", 20, 1, 150, 8))
        self.both_insufficient("unit_ambiguous")

    def test_unit_exceptions_cannot_be_applied_to_per_share_or_share_counts(self):
        self.raw["spans"][0]["text"] = "(In millions, except number of shares, which are reflected in thousands, and per-share amounts)"
        for label in ("Earnings per share", "Diluted shares", "Par value"):
            self.raw["spans"][4]["text"] = label
            result = self.lookup(label=label)
            self.assertTrue(all(r["reason"] == "unit_exception_row" for r in result["methods"].values()))
        self.raw["spans"][4]["text"] = "Operating cash flow"
        self.assertTrue(all(r["unit"]["qualifier_present"] for r in self.lookup()["methods"].values()))

    def test_low_confidence_label_unit_header_or_amount_refuses_in_both_methods(self):
        for index in (0, 1, 2, 4, 5, 6):
            self.raw = fixture(); self.raw["spans"][index]["confidence"] = .89
            self.both_insufficient("low_confidence")

    def test_merged_values_trailing_currency_and_unmatched_signs_refuse(self):
        for text in ("1,200 $", "1,200 800", "(1,200", "1,200)", "1,200%", "1,200-", "1.2e3", "1,20", "--1,200"):
            self.raw = fixture(); self.raw["spans"][5]["text"] = text
            self.both_insufficient("invalid_or_merged_amount")

    def test_legal_negative_parentheses_zero_and_explicit_currency_are_preserved(self):
        for text, expected in (("-1,200", "-1200"), ("(1,200)", "-1200"), ("0", "0"), ("(0)", "-0"),
                               ("$1,200", "1200"), ("¥1,200", "1200")):
            self.raw = fixture(); self.raw["spans"][5]["text"] = text
            for method in self.lookup()["methods"].values():
                self.assertEqual(method["status"], "selected")
                self.assertEqual(method["raw_value"], text)
                self.assertEqual(method["normalized_value"], expected)
                self.assertIsNone(method["unit"]["currency"])

    def test_removed_cell_does_not_shift_the_other_year(self):
        self.raw["spans"].pop(5)
        self.both_insufficient("row_amount_count_mismatch")

    def test_standalone_sign_or_currency_is_not_ignored(self):
        for text in ("-", "$", ")", "CAD", "C"):
            self.raw = fixture(); self.raw["spans"].insert(5, span(text, 260, 82, 275, 88))
            self.both_insufficient("invalid_or_merged_amount")

    def test_small_detached_sign_overlapping_amount_row_is_guarded(self):
        self.raw["spans"].append(span("-", 271, 88, 279, 90))
        result = self.lookup()["methods"]["layout_column"]
        self.assertEqual(result["status"], "insufficient")
        self.assertTrue(any(e["span"]["text"] == "-" for e in result["evidence"]["guards"]))

    def test_wide_conflicting_parent_starting_left_of_label_is_not_dropped(self):
        self.raw["spans"].append(span("- 1,200 800", 5, 80, 440, 90))
        self.assertEqual(self.lookup()["methods"]["layout_column"]["reason"], "invalid_or_merged_amount")

    def test_cross_column_and_duplicate_column_candidates_refuse(self):
        self.raw["spans"][5]["bbox"]["x1"] = 370
        self.assertEqual(self.lookup()["methods"]["layout_column"]["reason"], "cell_crosses_column")
        self.raw = fixture(); self.raw["spans"][6]["bbox"] = deepcopy(self.raw["spans"][5]["bbox"])
        self.assertEqual(self.lookup()["methods"]["layout_column"]["reason"], "duplicate_amount_column")

    def test_numeric_data_cannot_become_year_header_after_a_body_label(self):
        self.raw["spans"][1:4] = [span("Assets", 20, 25, 100, 35), span("2025", 285, 45, 315, 55), span("2024", 385, 45, 415, 55)]
        result = self.both_insufficient()
        self.assertEqual(result["methods"]["layout_column"]["reason"], "unanchored_header")

    def test_page_geometry_can_reject_adjacent_row_even_when_sequence_is_plausible(self):
        self.raw["spans"][5]["bbox"].update(top=95, bottom=105)
        result = self.lookup()
        self.assertEqual(result["methods"]["text_sequence"]["status"], "selected")
        self.assertEqual(result["methods"]["layout_column"]["status"], "insufficient")

    def test_strict_public_input_never_accepts_expected_amount_or_bbox(self):
        page = OcrPage.model_validate(self.raw)
        with self.assertRaises(TypeError): lookup_table(page, row_label="Operating cash flow", year=2025, expected="1200")
        with self.assertRaises(TypeError): lookup_layout_column(page, row_label="Operating cash flow", year=2025, bbox={})
        for year in (True, "2025", 2025.0, 1000):
            with self.assertRaises(TableLookupError): lookup_table(page, row_label="Operating cash flow", year=year)
        for label in (None, "", " ", 123):
            with self.assertRaises(TableLookupError): lookup_table(page, row_label=label, year=2025)
        with self.assertRaises(TableLookupError): lookup_table(self.raw, row_label="Operating cash flow", year=2025)

    def test_public_numeric_parser_refuses_substrings_and_unsupported_currency(self):
        self.assertEqual(parse_lookup_amount("(123.40)"), "-123.40")
        for text in ("total 123", "123 $", "USD 123", "123 456", "NaN", "1e6", "-(123)"):
            with self.assertRaises(TableLookupError): parse_lookup_amount(text)

    def test_parenthesized_values_are_not_rounded_by_global_decimal_precision(self):
        with localcontext() as context:
            context.prec = 2
            for raw, expected in (("(123456)", "-123456"), ("(123456.78)", "-123456.78"),
                                  ("(0)", "-0"), ("123456.78", "123456.78"), ("-123456.78", "-123456.78")):
                self.assertEqual(parse_lookup_amount(raw), expected)
            self.raw["spans"][5]["text"] = "(123456)"
            for method in self.lookup()["methods"].values():
                self.assertEqual(method["normalized_value"], "-123456")
