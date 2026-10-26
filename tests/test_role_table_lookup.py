"""Synthetic, reference-free role and accounting-prefix boundary controls."""
from copy import deepcopy
from decimal import localcontext
import unittest

from creditlens.role_table_lookup import lookup_role_table, lookup_role_layout
from creditlens.table_lookup import TableLookupError
from creditlens.word_evidence import WordPage
from creditlens.word_table_lookup import lookup_word_table
from tests.test_table_lookup import span
from tests.test_word_table_lookup import word_fixture


def entry(text, x0, x1, *, top=80, bottom=90, score=.99):
    value = span(text, x0, top, x1, bottom, score)
    return {"line": value, "words": [deepcopy(value)]}


class RoleTableLookupTests(unittest.TestCase):
    def setUp(self):
        self.raw = word_fixture()

    def result(self, year=2025):
        return lookup_role_table(WordPage.model_validate(self.raw), row_label="Operating cash flow", year=year)

    def role(self, year=2025):
        return self.result(year)["methods"]["role"]

    def refused(self, code=None):
        result = self.role()
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["raw_value"])
        self.assertIsNone(result["normalized_value"])
        self.assertIsNone(result["selected_span_index"])
        self.assertTrue(result["message"])
        self.assertFalse(result["automatic_approval"])
        if code:
            self.assertIn(code, [item["code"] for item in result["guard_issues"]])
        return result

    def accounting_row(self, first="1,200", second="800"):
        self.raw["lines"][5:7] = [entry(first, 280, 320), entry(second, 390, 415)]
        # The first dollar lies outside the old column's 250-point left edge.
        # A 35-point gap tests the new complete-row grammar, not a changed 8pt rule.
        self.raw["lines"] += [entry("$", 240, 245), entry("$", 365, 370)]

    def test_old_methods_unchanged_inputs_unmodified_and_unknown_currency(self):
        before = deepcopy(self.raw)
        old = lookup_word_table(WordPage.model_validate(self.raw), row_label="Operating cash flow", year=2025)
        result = self.result()
        self.assertEqual(result["methods"]["line"], old["methods"]["line"])
        self.assertEqual(result["methods"]["word"], old["methods"]["word"])
        role = result["methods"]["role"]
        self.assertEqual(role["normalized_value"], "1200")
        self.assertEqual(role["selected_span_origin"], "derived_word_group")
        self.assertIsNone(role["unit"]["currency"])
        self.assertFalse(result["automatic_approval"])
        self.assertEqual(self.raw, before)

    def test_native_anchor_low_score_is_visible_but_does_not_replace_line(self):
        for index in (0, 1, 2, 3, 4):
            with self.subTest(index=index):
                self.raw = word_fixture()
                self.raw["lines"][index]["words"][0]["confidence"] = .1
                result = self.result()
                self.assertEqual(result["methods"]["word"]["status"], "insufficient")
                role = result["methods"]["role"]
                self.assertEqual(role["status"], "selected")
                self.assertIn("word_low_confidence", [i["code"] for i in role["nonblocking_anchor_issues"]])
                original = next(p for p in role["parent_evidence"] if p["line_index"] == index)
                self.assertEqual(original["line"], self.raw["lines"][index]["line"])
                self.assertEqual(original["words"][0]["span"]["confidence"], .1)

    def test_anchor_geometry_text_and_missing_word_defects_are_not_hidden(self):
        mutations = (
            lambda e: e["words"][0]["bbox"].update(x1=e["line"]["bbox"]["x1"]+.5),
            lambda e: e["words"][0].update(text="Different unused token"),
            lambda e: e.update(words=[]),
        )
        for mutation, code in zip(mutations, ("word_outside_parent", "parent_word_text_mismatch", "missing_word_evidence")):
            self.raw = word_fixture()
            mutation(self.raw["lines"][4])
            role = self.role()
            self.assertEqual(role["status"], "selected")
            self.assertIn(code, [i["code"] for i in role["nonblocking_anchor_issues"]])
            self.assertEqual(role["evidence"]["row_label"][0]["span"]["text"], "Operating cash flow")

    def test_anchor_parent_score_remains_blocking_even_if_words_are_strong(self):
        for index in (0, 1, 2, 3, 4):
            self.raw = word_fixture()
            self.raw["lines"][index]["line"]["confidence"] = .89
            self.refused("anchor_line_low_confidence")

    def test_amount_parent_and_native_scores_are_each_blocking(self):
        for index in (5, 6):
            for source in ("line", "word"):
                self.raw = word_fixture()
                target = self.raw["lines"][index]["line"] if source == "line" else self.raw["lines"][index]["words"][0]
                target["confidence"] = .89
                self.refused("word_low_confidence")

    def test_amount_geometry_is_still_strict_and_not_fixed(self):
        for shift in (.001, .5, 60):
            self.raw = word_fixture()
            self.raw["lines"][5]["line"]["bbox"]["x0"] += shift
            self.raw["lines"][5]["line"]["bbox"]["x1"] += shift
            self.refused("word_outside_parent")
        self.raw = word_fixture()
        self.raw["lines"][5]["line"]["bbox"].update(top=140, bottom=150)
        self.refused("word_outside_parent")

    def test_lost_sign_and_missing_words_never_become_positive_amount(self):
        self.raw["lines"][5]["line"]["text"] = "(1,200)"
        self.refused("parent_word_text_mismatch")
        self.raw = word_fixture()
        self.raw["lines"][5]["words"] = []
        self.refused("missing_word_evidence")
        self.raw = word_fixture()
        self.raw["lines"].append({"line": span("-", 270, 80, 276, 90), "words": []})
        self.refused("missing_word_evidence")

    def test_same_parent_multi_column_amounts_still_split_with_all_words(self):
        self.raw["lines"][5:7] = [{"line": span("1,200 800", 270, 80, 420, 90),
            "words": [span("1,200", 280, 80, 320, 90), span("800", 390, 80, 415, 90)]}]
        role = self.role()
        self.assertEqual(role["normalized_value"], "1200")
        self.assertEqual(self.role(2024)["normalized_value"], "800")
        self.assertEqual(len(next(p for p in role["parent_evidence"] if p["line_index"] == 5)["words"]), 2)
        self.assertEqual(role["prefix_associations"], [])

    def test_complete_accounting_row_keeps_dual_frames_all_words_and_unknown_currency(self):
        self.accounting_row()
        before = deepcopy(self.raw)
        role = self.role()
        self.assertEqual(role["raw_value"], "$1,200")
        self.assertEqual(role["normalized_value"], "1200")
        self.assertEqual(role["selected_span_origin"], "derived_accounting_cell")
        self.assertIsNone(role["unit"]["currency"])
        self.assertEqual(self.role(2024)["raw_value"], "$800")
        first = role["prefix_associations"][0]
        self.assertEqual(first["numeric_bbox"]["x0"], 280)
        self.assertEqual(first["display_bbox"]["x0"], 240)
        self.assertEqual(first["display_bbox"]["x1"], 320)
        self.assertEqual(first["gap"], 35)
        self.assertEqual(first["left_barrier"], 180)
        selected = next(e["span"] for e in role["evidence"]["amounts"] if e["span_index"] == role["selected_span_index"])
        self.assertEqual(selected["bbox"], first["numeric_bbox"])
        refs = [r for m in role["adaptation"]["source_map"] if m["origin"] == "derived_accounting_cell" for r in m["word_refs"]]
        self.assertEqual(len(refs), 4)
        self.assertEqual(len({(r["line_index"], r["word_index"]) for r in refs}), 4)
        self.assertEqual(self.raw, before)

    def test_complete_negative_and_negative_zero_survive_dollar_association(self):
        for raw, normalized in (("-1,200", "-1200"), ("(1,200)", "-1200"), ("(0)", "-0")):
            self.raw = word_fixture()
            self.accounting_row(first=raw)
            with localcontext() as context:
                context.prec = 2
                role = self.role()
            self.assertEqual(role["raw_value"], "$"+raw)
            self.assertEqual(role["normalized_value"], normalized)

    def test_cross_column_parent_suffix_is_used_for_next_cell_not_discarded(self):
        self.raw["lines"][5:7] = [
            {"line": span("1,200 $", 280, 80, 370, 90),
             "words": [span("1,200", 280, 80, 320, 90), span("$", 365, 80, 370, 90)]},
            entry("800", 390, 415),
        ]
        self.raw["lines"].append(entry("$", 240, 245))
        role = self.role()
        self.assertEqual(role["status"], "selected")
        self.assertEqual(role["prefix_associations"][1]["prefix_ref"], {"line_index": 5, "word_index": 1})
        refs = [r for m in role["adaptation"]["source_map"] if m["origin"] == "derived_accounting_cell" for r in m["word_refs"]]
        self.assertEqual(sum(r == {"line_index": 5, "word_index": 1} for r in refs), 1)
        self.assertTrue(next(p for p in role["parent_evidence"] if p["line_index"] == 5)["text_conserved"])

    def test_accounting_parent_and_native_prefix_scores_still_block(self):
        for source in ("line", "word"):
            self.raw = word_fixture(); self.accounting_row()
            item = self.raw["lines"][-1]["line"] if source == "line" else self.raw["lines"][-1]["words"][0]
            item["confidence"] = .89
            self.refused("word_low_confidence")

    def test_trailing_extra_missing_or_other_currency_symbols_cannot_be_discarded(self):
        for change in ("trailing", "double", "missing", "yen", "text"):
            self.raw = word_fixture(); self.accounting_row()
            if change == "trailing":
                self.raw["lines"][-1] = entry("$", 420, 425)
            elif change == "double":
                self.raw["lines"].append(entry("$", 250, 255))
            elif change == "missing":
                self.raw["lines"].pop()
            elif change == "yen":
                self.raw["lines"][-1] = entry("¥", 365, 370)
            else:
                self.raw["lines"].append(entry("USD", 250, 270))
            with self.subTest(change=change):
                self.refused()

    def test_partial_cross_parent_sign_and_parentheses_do_not_get_attached(self):
        for symbol in ("-", "(", ")"):
            self.raw = word_fixture(); self.accounting_row()
            self.raw["lines"].append(entry(symbol, 270, 276))
            self.refused()

    def test_accounting_prefix_cannot_cross_label_previous_number_or_overlap(self):
        changes = ((-2, 170, 185), (-2, 310, 325), (-1, 310, 325))
        for index, left, right in changes:
            self.raw = word_fixture(); self.accounting_row()
            self.raw["lines"][index] = entry("$", left, right)
            self.refused()

    def test_accounting_amount_must_cover_each_year_column_once(self):
        self.accounting_row()
        self.raw["lines"][6] = entry("800", 325, 340)
        self.refused()
        self.raw = word_fixture(); self.accounting_row()
        self.raw["lines"].pop(6)
        self.refused()
        self.raw = word_fixture(); self.accounting_row()
        self.raw["lines"].append(entry("900", 330, 340))
        self.refused()

    def test_accounting_unrelated_row_token_and_escaped_prefix_are_not_fixed(self):
        self.accounting_row()
        self.raw["lines"][-1] = {"line": span("$", 365, 76, 370, 90), "words": [span("$", 365, 76, 370, 79)]}
        self.refused("word_row_mismatch")
        self.raw = word_fixture(); self.accounting_row()
        self.raw["lines"][-1]["words"][0]["bbox"]["x0"] -= .001
        self.refused("word_outside_parent")

    def test_prefix_parent_without_words_is_not_dropped(self):
        self.accounting_row()
        self.raw["lines"][-1]["words"] = []
        self.refused("missing_word_evidence")

    def test_ordinary_group_8point_gap_and_order_constraints_are_unchanged(self):
        self.raw["lines"][5] = {"line": span("$1,200", 250, 80, 320, 90),
            "words": [span("$", 250, 80, 256, 90), span("1,200", 280, 80, 320, 90)]}
        self.refused("word_symbol_gap")
        self.raw = word_fixture()
        self.raw["lines"][5:7] = [{"line": span("800 1,200", 280, 80, 415, 90),
            "words": [span("800", 390, 80, 415, 90), span("1,200", 280, 80, 320, 90)]}]
        self.refused("word_order_or_overlap")

    def test_unrelated_bad_footer_does_not_reject_whole_page(self):
        self.raw["lines"].append({"line": span("Footer", 20, 660, 150, 675, .1), "words": []})
        self.assertEqual(self.role()["status"], "selected")
        self.assertNotIn(10, [p["line_index"] for p in self.role()["parent_evidence"]])

    def test_duplicate_label_missing_unit_or_year_keeps_refusal(self):
        self.raw["lines"].append(entry("Operating cash flow", 20, 180, top=300, bottom=310))
        self.assertEqual(self.refused()["reason"], "duplicate_row_label")
        self.raw = word_fixture(); self.raw["lines"].pop(0)
        self.assertEqual(self.refused()["reason"], "unit_missing")
        self.raw = word_fixture(); self.raw["lines"].pop(3)
        self.assertEqual(self.refused()["reason"], "missing_year_group")

    def test_roles_disjoint_and_derived_confidence_uses_parent_and_native_minimum(self):
        self.accounting_row()
        self.raw["lines"][5]["line"]["confidence"] = .97
        self.raw["lines"][-2]["words"][0]["confidence"] = .91
        role = self.role()
        selected = next(e["span"] for e in role["evidence"]["amounts"] if e["span_index"] == role["selected_span_index"])
        self.assertEqual(selected["confidence"], .91)
        anchors = {e["line_index"] for e in role["evidence_roles"]["line_anchors"]}
        self.assertFalse(anchors & set(role["evidence_roles"]["amount_parents"]))
        self.assertIn("not_calibrated", role["confidence_basis"])
        self.assertNotIn("derived_page", role)

    def test_no_reference_argument_and_invalid_models_not_accepted(self):
        page = WordPage.model_validate(self.raw)
        with self.assertRaises(TypeError):
            lookup_role_layout(page, row_label="Operating cash flow", year=2025, expected_amount=1200)
        with self.assertRaises(TableLookupError):
            lookup_role_layout(page.model_copy(update={"width": -1}), row_label="Operating cash flow", year=2025)
        with self.assertRaises(TableLookupError):
            lookup_role_layout(page, row_label="Operating cash flow", year=True)


if __name__ == "__main__":
    unittest.main()
