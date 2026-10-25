"""Synthetic currency controls and an explicitly observed AMD diagnostic.

These check deterministic rules on saved OCR evidence, not model accuracy.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from creditlens.currency_evidence import CURRENCY_REASON_LABELS, CURRENCY_RULE_VERSION, crosscheck_currency_fact
from creditlens.date_evidence import crosscheck_date_fact
from creditlens.diligence import Fact
from creditlens.issuer_evaluation import IssuerPack
from creditlens.word_evidence import WordPage


ROOT = Path(__file__).resolve().parents[1]


def box(x0, top, x1, bottom):
    return dict(x0=x0, top=top, x1=x1, bottom=bottom)


def span(text, bounds, confidence=.99):
    return dict(text=text, bbox=bounds, confidence=confidence)


def line(text, bounds):
    return dict(line=span(text, bounds), words=[span(text, deepcopy(bounds))])


def controlled(raw_value="1,234", observed="$1,234"):
    """Hand-designed geometry, independent of AMD's cells and values."""
    fact = Fact.model_validate(dict(
        id="cfo-2025", metric="cfo", label="Operating cash", value=raw_value.replace(",", "").replace("(", "-").replace(")", ""),
        raw_value=raw_value, unit="USD_million", period_start="2024-12-29", period_end="2025-12-27",
        source_id="synthetic", locator="Synthetic post-OCR rule control, not a real issuer",
        pdf_location=dict(page=1,page_width=500,page_height=700,coordinate_system="pdf_points_top_left",
            amount_bbox=box(205,100,230,110),row_label="Operating cash",row_label_bbox=box(40,100,100,110),
            year=2025,year_bbox=box(200,50,220,60),period_header="December 27, 2025",
            period_bbox=box(185,35,240,65),unit_header="In millions",unit_bbox=box(300,15,360,25),
            parser_version="synthetic-currency-test-1")))
    amount = line(observed,box(188,98,234,112))
    amount["words"][0]["bbox"] = box(190,98,232,112)
    raw = dict(page=1,width=500,height=700,lines=[
        amount,
        line("Operating cash",box(40,100,100,110)),
        dict(line=span("December 27,",box(185,35,240,45)),words=[
            span("December",box(185,35,222,45)),span("27,",box(223,35,240,45))]),
        line("2025",box(198,48,222,62)),
        line("2024",box(298,48,322,62)),
        line("In millions",box(300,15,360,25)),
    ])
    raw["lines"][3]["words"][0]["bbox"] = box(200,48,220,62)
    raw["lines"][4]["words"][0]["bbox"] = box(300,48,320,62)
    return fact, raw


def change_amount(raw, text, parent_text=None):
    raw["lines"][0]["line"]["text"] = text if parent_text is None else parent_text
    raw["lines"][0]["words"][0]["text"] = text


class CurrencyEvidenceTests(unittest.TestCase):
    def evaluate(self, fact, raw):
        page = WordPage.model_validate(raw)
        before = page.model_dump(mode="json")
        baseline = crosscheck_date_fact(fact, page)
        result = crosscheck_currency_fact(fact, page)
        self.assertEqual([c for c in baseline["checks"] if c["field"] != "amount"],
                         [c for c in result["checks"] if c["field"] != "amount"])
        self.assertEqual(result["date_association"], baseline["date_association"])
        self.assertEqual(result["currency_association"]["legacy_amount"], baseline["checks"][0])
        self.assertEqual(page.model_dump(mode="json"), before)
        self.assertFalse(result["automatic_approval"])
        self.assertIn(result["currency_association"]["reason"], CURRENCY_REASON_LABELS)
        return result

    def refused(self, fact, raw):
        result = self.evaluate(fact, raw)
        self.assertFalse(result["currency_association"]["applied"])
        self.assertNotEqual(result["status"], "consistent")
        self.assertEqual(result["checks"][0], result["currency_association"]["legacy_amount"])
        return result

    def test_complete_currency_token_explains_only_left_extension(self):
        fact, raw = controlled()
        result = self.evaluate(fact, raw)
        self.assertEqual(result["currency_rule_version"], CURRENCY_RULE_VERSION)
        self.assertEqual(result["status"], "consistent")
        a = result["currency_association"]
        self.assertTrue(a["applied"])
        self.assertEqual(a["observed_text"], "$1,234")
        self.assertEqual(a["parsed_signed_amount"], "1234")
        self.assertEqual(len(a["column_headers"]), 2)
        self.assertEqual(a["registered_currency"], "USD")

    def test_legitimate_negative_amount_is_fully_parsed_with_sign(self):
        for text in ("$-1,234", "$(1,234)", "($1,234)"):
            with self.subTest(text=text):
                fact, raw = controlled("(1,234)", text)
                result = self.evaluate(fact, raw)
                self.assertEqual(result["status"], "consistent")
                self.assertEqual(result["currency_association"]["parsed_signed_amount"], "-1234")

    def test_deleted_flipped_incomplete_or_extra_sign_is_never_repaired(self):
        for text in ("$1,234", "$+1,234", "$(1,234", "$1,234)", "$(-1,234)", "$1,234-", "$−1,234"):
            with self.subTest(text=text):
                fact, raw = controlled("(1,234)", text)
                self.refused(fact, raw)

    def test_wrong_value_extra_digits_and_percent_are_not_substring_selected(self):
        for text in ("$1,235", "$11,234", "$1,234 5", "$1,234%", "$1,23", "$1,234.5"):
            with self.subTest(text=text):
                fact, raw = controlled(observed=text)
                self.refused(fact, raw)

    def test_currency_prefixes_and_nonusd_registry_are_refused(self):
        for text in ("€1,234", "¥1,234", "£1,234", "C$1,234", "US$1,234", "HK$1,234", "USD1,234"):
            with self.subTest(text=text):
                fact, raw = controlled(observed=text)
                self.refused(fact, raw)
        fact, raw = controlled()
        self.refused(fact.model_copy(update={"unit":"CNY_million"}), raw)

    def test_negative_zero_does_not_lose_its_registered_sign(self):
        fact, raw = controlled("(0)", "$0")
        self.assertEqual(self.refused(fact, raw)["currency_association"]["reason"], "signed_amount_differs_from_registered_fact")

    def test_parent_words_missing_conflicting_or_low_confidence_are_refused(self):
        for mutation in ("empty", "different", "extra", "low", "outside"):
            with self.subTest(mutation=mutation):
                fact, raw = controlled()
                e = raw["lines"][0]
                if mutation == "empty": e["words"] = []
                elif mutation == "different": e["line"]["text"] = "$-1,234"
                elif mutation == "extra": e["words"].append(span("7",box(233,100,234,110)))
                elif mutation == "low": e["line"]["confidence"] = .3
                else: e["words"][0]["bbox"]["x0"] = 187
                self.refused(fact, raw)

    def test_duplicate_parent_and_wrong_column_candidate_are_refused(self):
        fact, raw = controlled()
        raw["lines"].append(deepcopy(raw["lines"][0]))
        self.refused(fact, raw)
        fact, raw = controlled()
        for s in (raw["lines"][0]["line"], *raw["lines"][0]["words"]):
            s["bbox"]["x0"] += 100
            s["bbox"]["x1"] += 100
        self.refused(fact, raw)

    def test_external_nearby_sign_currency_prefix_and_empty_word_parent_are_not_ignored(self):
        for text in ("-", ")", "7", "€", "C", "CAD", "HK", "EUR", "footnote"):
            for with_words in (False, True):
                with self.subTest(text=text, with_words=with_words):
                    fact, raw = controlled()
                    extra = line(text,box(183,100,189,110))
                    if not with_words: extra["words"] = []
                    raw["lines"].append(extra)
                    result = self.refused(fact, raw)
                    self.assertTrue(result["currency_association"]["guard_observations"])

    def test_reference_guard_neighborhood_and_expanded_word_guard_are_both_kept(self):
        fact, raw = controlled()
        extra = line("-",box(232,100,235,110))
        raw["lines"].append(extra)
        result = self.refused(fact, raw)
        self.assertIn("detached_numeric_fragment_near_cell", result["checks"][0]["word_guards"])

    def test_neighbor_years_require_complete_text_position_and_confidence(self):
        for mutation in ("missing", "no_words", "suffix", "mismatch", "repeat", "outside", "low", "separate_suffix"):
            with self.subTest(mutation=mutation):
                fact, raw = controlled()
                e = raw["lines"][4]
                if mutation == "missing": raw["lines"].pop(4)
                elif mutation == "no_words": e["words"] = []
                elif mutation == "suffix": e["line"]["text"] = e["words"][0]["text"] = "2024 USD"
                elif mutation == "mismatch": e["line"]["text"] = "2023"
                elif mutation == "repeat": e["line"]["text"] = e["words"][0]["text"] = "2025"
                elif mutation == "outside": e["words"][0]["bbox"]["x0"] = 290
                elif mutation == "low": e["line"]["confidence"] = .3
                else: raw["lines"].append(line("USD",box(323,50,330,60)))
                self.refused(fact, raw)

    def test_wrong_year_unit_row_or_period_cannot_be_hidden_by_amount_association(self):
        for index, text in ((3,"2024"),(5,"In thousands"),(1,"Borrowing"),(2,"December 28,")):
            with self.subTest(index=index):
                fact, raw = controlled()
                raw["lines"][index] = line(text, raw["lines"][index]["line"]["bbox"])
                self.refused(fact, raw)

    def test_boundaries_and_nonleft_extensions_fail_closed(self):
        for key, value in (("x1",250),("top",85),("bottom",125),("x0",180)):
            with self.subTest(key=key):
                fact, raw = controlled()
                raw["lines"][0]["line"]["bbox"][key] = value
                raw["lines"][0]["words"][0]["bbox"][key] = value
                self.refused(fact, raw)
        fact, raw = controlled()
        fact = fact.model_copy(update={"pdf_location": fact.pdf_location.model_copy(update={
            "year_bbox": fact.pdf_location.year_bbox.model_copy(update={"x1":210})})})
        self.refused(fact, raw)

    def test_already_consistent_amount_is_preserved_not_claimed_as_new_validation(self):
        fact, raw = controlled()
        raw["lines"][0] = line("1,234",box(205,100,230,110))
        result = self.evaluate(fact, raw)
        self.assertEqual(result["status"], "consistent")
        self.assertFalse(result["currency_association"]["applied"])
        self.assertEqual(result["checks"][0], result["currency_association"]["legacy_amount"])

    def test_invalid_threshold_is_rejected(self):
        fact, raw = controlled()
        for score in (float("nan"), -.1, 1.1):
            with self.assertRaises(ValueError):
                crosscheck_currency_fact(fact, WordPage.model_validate(raw), min_confidence=score)

    def test_saved_amd_public_evidence_is_observed_diagnostic_not_held_out_accuracy(self):
        pack = IssuerPack.model_validate_json((ROOT/"data/examples/amd-fy2025-evaluation.json").read_text(encoding="utf-8"))
        report = json.loads((ROOT/"artifacts/issuer-evaluation-report.json").read_text(encoding="utf-8"))
        pages = {p["page"]:dict(page=p["page"],width=p["width"],height=p["height"],
                 lines=[{key:e[key] for key in ("line","words")} for e in p["evidence_lines"]])
                 for p in report["documents"][0]["pages"]}
        changed = []
        for fact in pack.facts:
            result = self.evaluate(fact, pages[fact.pdf_location.page])
            if result["currency_association"]["applied"]:
                changed.append(fact.id)
        self.assertEqual(changed, ["cash_equivalents-2025", "cash_equivalents-2024"])


if __name__ == "__main__":
    unittest.main()
