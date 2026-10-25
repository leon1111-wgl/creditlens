"""Synthetic, reference-free word grouping and refusal controls."""
from copy import deepcopy
from decimal import localcontext
import unittest

from creditlens.table_lookup import TableLookupError, lookup_layout_column
from creditlens.word_evidence import WordPage
from creditlens.word_table_lookup import lookup_word_layout, lookup_word_table
from tests.test_table_lookup import fixture, span


def word_fixture():
    raw = fixture()
    raw["lines"] = [{"line": deepcopy(s), "words": [deepcopy(s)]} for s in raw.pop("spans")]
    return raw


class WordTableLookupTests(unittest.TestCase):
    def setUp(self):
        self.raw = word_fixture()

    def result(self, **kwargs):
        return lookup_word_table(WordPage.model_validate(self.raw), row_label="Operating cash flow", year=kwargs.get("year", 2025))

    def word(self):
        return self.result()["methods"]["word"]

    def refused(self, code=None):
        word = self.word()
        self.assertEqual(word["status"], "insufficient")
        self.assertIsNone(word["raw_value"])
        self.assertIsNone(word["normalized_value"])
        self.assertIsNone(word["selected_span_index"])
        self.assertFalse(word["automatic_approval"])
        self.assertTrue(word["message"])
        if code:
            self.assertIn(code, [i["code"] for i in word["guard_issues"]])
        return word

    def merge(self, text="1,200 800", words=None):
        words = words or [span("1,200", 280, 80, 320, 90), span("800", 390, 80, 415, 90)]
        self.raw["lines"][5:7] = [{"line": span(text, 270, 80, 420, 90), "words": words}]

    def test_normal_projection_unchanged_and_derived_evidence_is_explicit(self):
        before = deepcopy(self.raw)
        result = self.result()
        self.assertEqual(result["methods"]["line"], lookup_layout_column(WordPage.model_validate(self.raw).line_page(), row_label="Operating cash flow", year=2025))
        word = result["methods"]["word"]
        self.assertEqual(word["status"], "selected")
        self.assertEqual(word["normalized_value"], "1200")
        self.assertEqual(word["selected_span_origin"], "derived_word_group")
        self.assertEqual(word["adaptation"]["mapping_scope"], "referenced_spans_only")
        self.assertEqual(len(word["adaptation"]["derived_page_sha256"]), 64)
        self.assertNotIn("derived_page", word)
        self.assertIsNone(word["unit"]["currency"])
        self.assertEqual(before, self.raw)

    def test_same_parent_multiple_cells_preserve_every_word_and_can_split(self):
        self.merge()
        result = self.result()
        self.assertEqual(result["methods"]["line"]["status"], "insufficient")
        word = result["methods"]["word"]
        self.assertEqual(word["status"], "selected")
        self.assertEqual(word["normalized_value"], "1200")
        parent = next(p for p in word["parent_evidence"] if p["line_index"] == 5)
        self.assertEqual(len(parent["words"]), 2)
        refs = [ref for m in word["adaptation"]["source_map"] if m["origin"] == "derived_word_group" for ref in m["word_refs"]]
        self.assertEqual(refs, [{"line_index": 5, "word_index": 0}, {"line_index": 5, "word_index": 1}])
        self.assertEqual(self.result(year=2024)["methods"]["word"]["normalized_value"], "800")

    def test_complete_same_parent_currency_groups_keep_symbols_and_unknown_currency(self):
        self.merge("$ 1,200 $ 800", [span("$", 270, 80, 276, 90), span("1,200", 280, 80, 320, 90),
                                     span("$", 380, 80, 386, 90), span("800", 390, 80, 415, 90)])
        word = self.word()
        self.assertEqual(word["status"], "selected")
        self.assertEqual(word["raw_value"], "$1,200")
        self.assertIsNone(word["unit"]["currency"])
        candidate = next(e["span"] for e in word["evidence"]["amounts"] if e["span_index"] == word["selected_span_index"])
        self.assertEqual(candidate["bbox"]["x0"], 270)
        self.assertEqual(candidate["bbox"]["x1"], 320)

    def test_split_negative_parentheses_and_negative_zero_preserved(self):
        for text, words, expected in [
            ("- 1,200", [span("-", 270, 80, 276, 90), span("1,200", 280, 80, 320, 90)], "-1200"),
            ("( 1,200 )", [span("(", 270, 80, 276, 90), span("1,200", 280, 80, 320, 90), span(")", 324, 80, 330, 90)], "-1200"),
            ("(0)", [span("(0)", 280, 80, 320, 90)], "-0"),
        ]:
            with self.subTest(text=text):
                self.raw = word_fixture()
                self.raw["lines"][5] = {"line": span(text, 270, 80, 330, 90), "words": words}
                with localcontext() as context:
                    context.prec = 2
                    self.assertEqual(self.word()["normalized_value"], expected)

    def test_lost_negative_symbol_or_extra_word_rejected_even_when_baseline_selects(self):
        self.raw["lines"][5]["line"]["text"] = "-1,200"
        self.assertEqual(self.result()["methods"]["line"]["status"], "selected")
        self.refused("parent_word_text_mismatch")
        self.raw = word_fixture()
        self.raw["lines"][5]["words"].append(span("$", 321, 80, 325, 90))
        self.refused("parent_word_text_mismatch")

    def test_shifted_parent_and_half_point_escape_are_not_repaired(self):
        for shift in (60, .5):
            self.raw = word_fixture()
            self.raw["lines"][5]["line"]["bbox"]["x0"] += shift
            self.raw["lines"][5]["line"]["bbox"]["x1"] += shift
            self.refused("word_outside_parent")

    def test_parent_shifted_off_row_cannot_hide_word_in_row(self):
        self.raw["lines"][5]["line"]["bbox"]["top"] += 60
        self.raw["lines"][5]["line"]["bbox"]["bottom"] += 60
        self.refused("word_outside_parent")

    def test_relevant_empty_words_including_external_symbol_are_not_dropped(self):
        self.raw["lines"][5]["words"] = []
        self.refused("missing_word_evidence")
        self.raw = word_fixture()
        self.raw["lines"].append({"line": span("-", 271, 80, 276, 90), "words": []})
        self.refused("missing_word_evidence")

    def test_parent_and_native_word_confidence_each_guarded(self):
        for index in (0, 1, 2, 4, 5, 6):
            for level in ("line", "word"):
                self.raw = word_fixture()
                target = self.raw["lines"][index]["line"] if level == "line" else self.raw["lines"][index]["words"][0]
                target["confidence"] = .89
                self.refused("word_low_confidence")

    def test_unrelated_low_score_and_unrelated_empty_footer_do_not_block(self):
        self.raw["lines"] += [{"line": span("Footer", 20, 660, 150, 675, .10), "words": []}]
        self.raw["lines"][8]["words"][0]["confidence"] = .10
        self.assertEqual(self.word()["status"], "selected")
        self.assertNotIn(10, [p["line_index"] for p in self.word()["parent_evidence"]])

    def test_duplicate_labels_are_not_disambiguated_using_tokens(self):
        self.raw["lines"].append({"line": span("Operating cash flow", 20, 300, 180, 310),
                                  "words": [span("Operating cash flow", 20, 300, 180, 310)]})
        self.assertEqual(self.refused()["reason"], "duplicate_row_label")

    def test_missing_header_or_unit_not_repaired_by_numeric_words(self):
        self.raw["lines"].pop(3)
        self.assertEqual(self.refused()["reason"], "missing_year_group")
        self.raw = word_fixture(); self.raw["lines"].pop(0)
        self.assertEqual(self.refused()["reason"], "unit_missing")

    def test_multiple_numeric_tokens_in_one_column_cannot_be_joined(self):
        self.raw["lines"][5] = {"line": span("12 00", 280, 80, 320, 90),
                                  "words": [span("12", 280, 80, 296, 90), span("00", 300, 80, 320, 90)]}
        self.refused("multiple_numeric_words")

    def test_unknown_conserved_text_and_currency_prefix_are_not_discarded(self):
        for prefix in ("USD", "C", "note"):
            self.raw = word_fixture()
            self.raw["lines"][5] = {"line": span(prefix + "1,200", 250, 80, 320, 90),
                                      "words": [span(prefix, 250, 80, 276, 90), span("1,200", 280, 80, 320, 90)]}
            self.refused("unsupported_word_content")

    def test_trailing_currency_is_not_removed_to_get_the_number(self):
        self.raw["lines"][5] = {"line": span("1,200 $", 280, 80, 335, 90),
                                  "words": [span("1,200", 280, 80, 320, 90), span("$", 324, 80, 335, 90)]}
        self.refused("incomplete_word_amount")

    def test_cross_parent_symbols_do_not_silently_change_sign_or_currency(self):
        for symbol in ("-", "$", "(", "C"):
            self.raw = word_fixture()
            self.raw["lines"].append({"line": span(symbol, 270, 80, 276, 90), "words": [span(symbol, 270, 80, 276, 90)]})
            self.refused("cross_parent_symbol_association")

    def test_same_parent_symbol_gap_and_overlapping_tokens_refuse(self):
        self.raw["lines"][5] = {"line": span("$1,200", 250, 80, 320, 90),
                                  "words": [span("$", 250, 80, 256, 90), span("1,200", 280, 80, 320, 90)]}
        self.refused("word_symbol_gap")
        self.raw["lines"][5]["words"][0]["bbox"]["x1"] = 282
        self.refused("word_order_or_overlap")

    def test_reversed_word_order_is_not_sorted_to_hide_text_conflict(self):
        self.merge("800 1,200", [span("800", 390, 80, 415, 90), span("1,200", 280, 80, 320, 90)])
        self.refused("word_order_or_overlap")

    def test_word_crossing_column_and_missing_column_refuse(self):
        self.raw["lines"][5]["line"]["bbox"]["x1"] = 360
        self.raw["lines"][5]["words"][0]["bbox"]["x1"] = 360
        self.refused("word_crosses_column")
        self.raw = word_fixture(); self.raw["lines"].pop(6)
        self.refused("word_column_coverage")

    def test_duplicate_amount_and_unassigned_wide_parent_refuse(self):
        self.raw["lines"].append(deepcopy(self.raw["lines"][5]))
        self.refused("multiple_numeric_words")
        self.raw = word_fixture()
        self.raw["lines"][5] = {"line": span("note 1,200", 100, 80, 320, 90),
                                  "words": [span("note", 100, 80, 140, 90), span("1,200", 280, 80, 320, 90)]}
        self.refused("word_crosses_column")

    def test_parent_words_from_other_row_cannot_be_discarded(self):
        self.raw["lines"][5] = {"line": span("1,200 400", 280, 80, 320, 120),
                                  "words": [span("1,200", 280, 80, 320, 90), span("400", 290, 110, 320, 120)]}
        self.refused("word_row_mismatch")

    def test_derived_score_is_minimum_and_all_parent_words_remain_visible(self):
        self.merge()
        self.raw["lines"][5]["line"]["confidence"] = .97
        self.raw["lines"][5]["words"][0]["confidence"] = .92
        word = self.word()
        selected = next(e["span"] for e in word["evidence"]["amounts"] if e["span_index"] == word["selected_span_index"])
        self.assertEqual(selected["confidence"], .92)
        self.assertIn("not_calibrated", word["confidence_basis"])
        self.assertEqual(len(next(p for p in word["parent_evidence"] if p["line_index"] == 5)["words"]), 2)

    def test_no_expected_value_box_or_fact_argument_and_revalidate_models(self):
        page = WordPage.model_validate(self.raw)
        with self.assertRaises(TypeError):
            lookup_word_layout(page, row_label="Operating cash flow", year=2025, expected_amount=1200)
        with self.assertRaises(TableLookupError):
            lookup_word_layout(page.model_copy(update={"width": -1}), row_label="Operating cash flow", year=2025)
        for year in (True, "2025", 2200):
            with self.assertRaises(TableLookupError):
                lookup_word_layout(page, row_label="Operating cash flow", year=year)


if __name__ == "__main__":
    unittest.main()
