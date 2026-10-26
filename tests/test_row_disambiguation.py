"""Explicit row choice, immutable input and retained line-layout guards."""
from copy import deepcopy
import unittest

from creditlens.diligence import digest
from creditlens.row_disambiguation import (
    ROW_DISAMBIGUATION_VERSION, RowDisambiguationError,
    enumerate_row_candidates, lookup_selected_row,
)
from creditlens.table_lookup import lookup_layout_column
from creditlens.visual import OcrPage
from tests.test_table_lookup import fixture, span


class RowDisambiguationTests(unittest.TestCase):
    def setUp(self):
        self.raw = fixture()
        self.raw["spans"][7]["text"] = "Operating cash flow"

    def page(self):
        return OcrPage.model_validate(self.raw)

    def discover(self, label="Operating cash flow", year=2025):
        return enumerate_row_candidates(self.page(), row_label=label, year=year)

    def select(self, index=4, selection=None, label="Operating cash flow", year=2025):
        if selection is None:
            selection = {"input_sha256": self.discover(label, year)["input_binding"]["input_sha256"], "row_index": index}
        return lookup_selected_row(self.page(), row_label=label, year=year, selection=selection)

    def error(self, code, callback):
        with self.assertRaises(RowDisambiguationError) as captured:
            callback()
        self.assertEqual(captured.exception.code, code)
        self.assertTrue(captured.exception.path)

    def test_all_duplicates_enumerated_but_none_selected(self):
        before = deepcopy(self.raw)
        discovery = self.discover()
        self.assertEqual(discovery["status"], "selection_required")
        self.assertEqual(discovery["candidate_count"], 2)
        self.assertEqual([c["row_index"] for c in discovery["candidates"]], [4, 7])
        self.assertEqual(discovery["legacy_line_result"]["reason"], "duplicate_row_label")
        self.assertEqual([c["extraction"]["normalized_value"] for c in discovery["candidates"]], ["1200", "400"])
        for candidate in discovery["candidates"]:
            self.assertEqual(candidate["row"]["span"], self.raw["spans"][candidate["row_index"]])
            self.assertFalse(candidate["extraction"]["automatic_approval"])
        pending = lookup_selected_row(self.page(), row_label="Operating cash flow", year=2025)
        self.assertEqual(pending["status"], "selection_required")
        self.assertIsNone(pending["selection"])
        self.assertIsNone(pending["extraction"])
        self.assertEqual(self.raw, before)

    def test_explicit_rows_select_distinct_original_cells_without_removing_other_row(self):
        before = deepcopy(self.raw)
        first, second = self.select(4), self.select(7)
        self.assertEqual(first["extraction"]["normalized_value"], "1200")
        self.assertEqual(second["extraction"]["normalized_value"], "400")
        self.assertEqual(first["extraction"]["selected_span_index"], 5)
        self.assertEqual(second["extraction"]["selected_span_index"], 8)
        self.assertEqual(second["selection"]["row_index"], 7)
        self.assertTrue(second["selection"]["explicit"])
        self.assertEqual(second["candidate_count"], 2)
        self.assertEqual(second["legacy_line_result"]["reason"], "duplicate_row_label")
        self.assertFalse(second["automatic_approval"])
        self.assertEqual(self.raw, before)

    def test_single_row_still_requires_explicit_choice_and_matches_frozen_layout(self):
        self.raw = fixture()
        discovery = self.discover()
        self.assertEqual(discovery["candidate_count"], 1)
        self.assertEqual(discovery["status"], "selection_required")
        actual = self.select()["extraction"]
        expected = lookup_layout_column(self.page(), row_label="Operating cash flow", year=2025)
        expected["method"] = "explicit_row_layout"
        self.assertEqual(actual, expected)

    def test_no_match_has_no_selection_and_cannot_select_an_unrelated_row(self):
        absent = self.discover("Unregistered line")
        self.assertEqual(absent["status"], "not_found")
        self.assertEqual(absent["candidate_count"], 0)
        self.assertEqual(absent["candidates"], [])
        selection = {"input_sha256": absent["input_binding"]["input_sha256"], "row_index": 4}
        self.error("selection_not_candidate", lambda: self.select(selection=selection, label="Unregistered line"))

    def test_exact_normalized_label_not_substring_and_original_query_is_preserved(self):
        discovery = self.discover(" OPERATING  CASH FLOW: ")
        self.assertEqual(discovery["candidate_count"], 2)
        self.assertEqual(discovery["query"]["row_label"], " OPERATING  CASH FLOW: ")
        self.assertEqual(discovery["query"]["normalized_row_label"], "operating cash flow")
        self.raw["spans"][4]["text"] = "Adjustments to reconcile Operating cash flow"
        self.assertEqual([c["row_index"] for c in self.discover()["candidates"]], [7])

    def test_input_binding_is_complete_page_query_and_version(self):
        discovery = self.discover()
        page_sha = digest(self.page().model_dump(mode="json"))
        self.assertEqual(discovery["input_binding"]["page_sha256"], page_sha)
        self.assertEqual(discovery["input_binding"]["input_sha256"], digest({
            "rule_version": ROW_DISAMBIGUATION_VERSION, "page_sha256": page_sha, "query": discovery["query"]}))
        selection = {"input_sha256": discovery["input_binding"]["input_sha256"], "row_index": 4}
        self.raw["spans"].append(span("Unrelated footer", 20, 660, 150, 675))
        self.error("stale_selection", lambda: self.select(selection=selection))

    def test_changed_value_dimensions_or_original_order_invalidates_selection(self):
        for change in ("value", "dimensions", "order"):
            self.setUp()
            selection = {"input_sha256": self.discover()["input_binding"]["input_sha256"], "row_index": 4}
            if change == "value":
                self.raw["spans"][5]["text"] = "1,201"
            elif change == "dimensions":
                self.raw["width"] = 501
            else:
                self.raw["spans"][8], self.raw["spans"][9] = self.raw["spans"][9], self.raw["spans"][8]
            with self.subTest(change=change):
                self.error("stale_selection", lambda: self.select(selection=selection))

    def test_other_year_other_label_and_normalization_equivalent_raw_query_are_stale(self):
        selection = {"input_sha256": self.discover()["input_binding"]["input_sha256"], "row_index": 4}
        for label, year in (("Operating cash flow", 2024), ("Other assets", 2025), ("OPERATING CASH FLOW", 2025)):
            with self.subTest(label=label, year=year):
                self.error("stale_selection", lambda: self.select(selection=selection, label=label, year=year))

    def test_selection_strict_schema_rejects_extra_reference_or_invalid_digest(self):
        good = {"input_sha256": self.discover()["input_binding"]["input_sha256"], "row_index": 4}
        for bad in ({}, [], "4", {**good, "expected_amount": "1200"}, {"row_index": 4}):
            self.error("invalid_selection", lambda: self.select(selection=bad))
        for value in (True, 1, "bad", "F"*64, "a"*63):
            self.error("invalid_selection_digest", lambda: self.select(selection={**good, "input_sha256": value}))

    def test_bool_float_string_and_out_of_range_indices_are_not_coerced(self):
        good = {"input_sha256": self.discover()["input_binding"]["input_sha256"], "row_index": 4}
        for value in (True, 4.0, "4", None, float("nan")):
            self.error("invalid_row_index", lambda: self.select(selection={**good, "row_index": value}))
        for value in (-1, len(self.raw["spans"]), 5001):
            self.error("row_index_out_of_range", lambda: self.select(selection={**good, "row_index": value}))
        self.error("selection_not_candidate", lambda: self.select(5))

    def test_context_is_original_bounded_geometric_text_not_inferred_section(self):
        self.raw["spans"] += [span("Current assets", 20, 65, 150, 70), span("Non-current assets", 20, 98, 150, 102)]
        discovery = self.discover()
        for candidate in discovery["candidates"]:
            context = candidate["context"]
            self.assertIn("not_semantic_section", context["basis"])
            self.assertNotIn("section", candidate)
            self.assertLessEqual(len(context["above"]), 4)
            self.assertLessEqual(len(context["below"]), 4)
            for item in context["above"] + context["below"]:
                self.assertEqual(item["span"], self.raw["spans"][item["span_index"]])
        self.assertEqual(discovery["candidates"][0]["context"]["above"][0]["span"]["text"], "Current assets")
        self.assertEqual(discovery["candidates"][1]["context"]["above"][0]["span"]["text"], "Non-current assets")

    def test_index_is_original_sequence_position_not_row_vertical_rank(self):
        self.raw["spans"][4:7], self.raw["spans"][7:10] = self.raw["spans"][7:10], self.raw["spans"][4:7]
        discovery = self.discover()
        self.assertEqual([c["row_index"] for c in discovery["candidates"]], [4, 7])
        self.assertEqual(self.select(4)["extraction"]["normalized_value"], "400")
        self.assertEqual(self.select(7)["extraction"]["normalized_value"], "1200")

    def test_each_candidate_keeps_its_own_insufficient_result(self):
        self.raw["spans"][8]["text"] = "400 $"
        discovery = self.discover()
        self.assertEqual([c["extraction"]["status"] for c in discovery["candidates"]], ["selected", "insufficient"])
        chosen = self.select(7)
        self.assertEqual(chosen["status"], "insufficient")
        self.assertEqual(chosen["extraction"]["reason"], "invalid_or_merged_amount")
        self.assertIsNone(chosen["extraction"]["normalized_value"])
        self.assertEqual(chosen["selection"]["row_index"], 7)

    def test_selected_row_cannot_ignore_other_same_name_row_that_interferes(self):
        # Place the other duplicate across the selected row's numeric columns.
        self.raw["spans"][7]["bbox"].update(x0=270, x1=400, top=80, bottom=90)
        result = self.select(4)["extraction"]
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual(result["reason"], "invalid_or_merged_amount")
        self.assertIn(7, [e["span_index"] for e in result["evidence"]["guards"]])

    def test_other_same_name_row_in_header_context_is_not_deleted(self):
        self.raw["spans"][7]["bbox"].update(top=30, bottom=40)
        result = self.select(4)["extraction"]
        self.assertEqual(result["reason"], "unanchored_header")
        self.assertIn(7, [e["span_index"] for e in result["evidence"]["year_headers"]])

    def test_parent_confidence_remains_required_for_all_used_roles_and_columns(self):
        for index in (0, 1, 2, 3, 4, 5, 6):
            self.setUp()
            self.raw["spans"][index]["confidence"] = .89
            self.assertEqual(self.select(4)["extraction"]["reason"], "low_confidence")

    def test_missing_header_unit_and_future_query_year_remain_insufficient(self):
        self.raw["spans"][0]["text"] = "Scale unknown"
        self.assertEqual(self.select()["extraction"]["reason"], "unit_missing")
        self.setUp(); self.raw["spans"][3]["text"] = "Year unavailable"
        self.assertEqual(self.select()["extraction"]["reason"], "missing_year_group")
        self.setUp()
        self.assertEqual(self.select(year=2026)["extraction"]["reason"], "year_not_found")

    def test_missing_amount_extra_sign_crossing_column_and_merged_amount_still_refuse(self):
        for change in ("missing", "sign", "crossing", "merged"):
            self.setUp()
            if change == "missing":
                self.raw["spans"].pop(6)
            elif change == "sign":
                self.raw["spans"].append(span("-", 270, 80, 276, 90))
            elif change == "crossing":
                self.raw["spans"][5]["bbox"]["x1"] = 360
            else:
                self.raw["spans"][5]["text"] = "1,200 800"
            with self.subTest(change=change):
                result = self.select()["extraction"]
                self.assertEqual(result["status"], "insufficient")
                self.assertIsNone(result["normalized_value"])

    def test_complete_zero_negative_and_currency_unknown_survive_without_adoption(self):
        for raw, value in (("0", "0"), ("(0)", "-0"), ("-1,200", "-1200"), ("$1,200", "1200")):
            self.raw["spans"][5]["text"] = raw
            result = self.select()["extraction"]
            self.assertEqual(result["raw_value"], raw)
            self.assertEqual(result["normalized_value"], value)
            self.assertIsNone(result["unit"]["currency"])
            self.assertFalse(result["automatic_approval"])

    def test_invalid_page_and_query_are_controlled_without_accepting_gold_arguments(self):
        self.error("invalid_page", lambda: enumerate_row_candidates(self.page().model_copy(update={"width": -1}), row_label="Operating cash flow", year=2025))
        self.error("invalid_year", lambda: enumerate_row_candidates(self.page(), row_label="Operating cash flow", year=True))
        self.error("invalid_row_label", lambda: enumerate_row_candidates(self.page(), row_label="", year=2025))
        with self.assertRaises(TypeError):
            enumerate_row_candidates(self.page(), row_label="Operating cash flow", year=2025, expected_amount="1200")
        with self.assertRaises(TypeError):
            lookup_selected_row(self.page(), row_label="Operating cash flow", year=2025, section="Current assets")


if __name__ == "__main__":
    unittest.main()
