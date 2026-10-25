"""Synthetic guard regressions and an already-observed issuer compatibility check."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from creditlens.currency_evidence import crosscheck_currency_fact
from creditlens.diligence import Fact
from creditlens.evidence_guard import EVIDENCE_GUARD_RULE_VERSION, REASON_LABELS, crosscheck_guarded_fact
from creditlens.issuer_evaluation import IssuerPack
from creditlens.word_evidence import WordLine, WordPage
from tests.test_word_evidence import controlled_page, fact_named, nearby
from tests.test_currency_evidence import controlled as currency_page, change_amount


ROOT = Path(__file__).resolve().parents[1]


def separate_left(raw, text, *, words=False):
    box = deepcopy(raw["lines"][0]["line"]["bbox"])
    box["x1"], box["x0"] = box["x0"] - 2, box["x0"] - 6
    line = {"text": text, "confidence": .99, "bbox": box}
    raw["lines"].append({"line": line, "words": [deepcopy(line)] if words else []})


class EvidenceGuardTests(unittest.TestCase):
    def check(self, fact=None, raw=None):
        fact = fact or fact_named()
        raw = raw or controlled_page(fact)
        page = WordPage.model_validate(raw)
        before = page.model_dump(mode="json")
        legacy = crosscheck_currency_fact(fact, page)
        result = crosscheck_guarded_fact(fact, page)
        self.assertEqual(result["legacy_currency_result"], legacy)
        self.assertEqual([c for c in result["checks"] if c["field"] != "amount"],
                         [c for c in legacy["checks"] if c["field"] != "amount"])
        self.assertEqual(page.model_dump(mode="json"), before)
        self.assertFalse(result["automatic_approval"])
        self.assertEqual(result["evidence_guard_rule_version"], EVIDENCE_GUARD_RULE_VERSION)
        self.assertIn(result["evidence_guard"]["reason"], REASON_LABELS)
        return result

    def test_normal_complete_amount_and_unmarked_currency_not_inferred(self):
        result = self.check()
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(result["evidence_guard"]["status"], "passed")
        self.assertEqual(result["evidence_guard"]["currency_markers"][0]["markers"], [])

    def test_shifted_parent_cannot_retain_legacy_false_consistency(self):
        fact = fact_named()
        raw = controlled_page(fact)
        for key in ("x0", "x1"):
            raw["lines"][0]["line"]["bbox"][key] += 60
        result = self.check(fact, raw)
        self.assertEqual(result["legacy_currency_result"]["status"], "consistent")
        self.assertEqual(result["status"], "insufficient")
        self.assertIn("word_outside_its_parent_geometry", [i["code"] for i in result["evidence_guard"]["issues"]])

    def test_parent_vertical_and_small_meaningful_overhang_refused(self):
        for key, amount in (("top", 1), ("x0", 1)):
            raw = controlled_page(fact_named())
            raw["lines"][0]["line"]["bbox"][key] += amount
            self.assertNotEqual(self.check(raw=raw)["status"], "consistent")

    def test_yen_full_token_conflicts_with_registered_usd(self):
        fact = fact_named()
        for marker in ("¥", "￥", "€", "£", "₹"):
            raw = controlled_page(fact)
            raw["lines"][0]["line"]["text"] = raw["lines"][0]["words"][0]["text"] = marker + fact.raw_value
            result = self.check(fact, raw)
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(result["evidence_guard"]["reason"], "currency_marker_conflicts_with_registered_usd")

    def test_parent_only_nearby_minus_blocks_legacy_false_consistency(self):
        raw = controlled_page(fact_named())
        separate_left(raw, "-")
        result = self.check(raw=raw)
        self.assertEqual(result["legacy_currency_result"]["status"], "consistent")
        self.assertEqual(result["status"], "insufficient")
        issue = next(i for i in result["evidence_guard"]["issues"] if i["code"] == "nearby_parent_without_words")
        self.assertEqual(issue["observation"]["text"], "-")

    def test_parent_only_symbols_letters_and_currency_prefixes_never_disappear(self):
        for text in ("-", "(", ")", ".", "%", "C", "CAD", "EUR", "HK", "0", "¥", "USD"):
            for has_words in (False, True):
                with self.subTest(text=text, has_words=has_words):
                    raw = controlled_page(fact_named())
                    separate_left(raw, text, words=has_words)
                    self.assertNotEqual(self.check(raw=raw)["status"], "consistent")

    def test_candidate_extension_neighborhood_is_also_guarded(self):
        fact, raw = currency_page()
        separate_left(raw, "C")
        result = self.check(fact, raw)
        self.assertEqual(result["evidence_guard"]["status"], "blocked")

    def test_complete_dollar_and_negative_forms_remain_compatible(self):
        for reference, observed in (("1,234", "$1,234"), ("(1,234)", "$(1,234)"),
                                    ("(1,234)", "$-1,234"), ("(1,234)", "($1,234)")):
            fact, raw = currency_page(reference, observed)
            result = self.check(fact, raw)
            self.assertEqual(result["status"], "consistent")
            self.assertEqual(result["evidence_guard"]["status"], "passed")

    def test_negative_zero_sign_is_not_dropped(self):
        fact, raw = currency_page("(0)", "$0")
        result = self.check(fact, raw)
        self.assertNotEqual(result["status"], "consistent")
        fact, raw = currency_page("(0)", "$(0)")
        self.assertEqual(self.check(fact, raw)["status"], "consistent")

    def test_wrong_digit_or_sign_is_not_repaired(self):
        for observed in ("$1,235", "$-1,234", "$(1,234)", "$1,234)", "C$1,234", "USD1,234", "$1,234%"):
            fact, raw = currency_page("1,234", observed)
            self.assertNotEqual(self.check(fact, raw)["status"], "consistent")

    def test_duplicate_and_missing_candidates_are_not_selected_by_expected_value(self):
        raw = controlled_page(fact_named())
        raw["lines"].append(deepcopy(raw["lines"][0]))
        self.assertNotEqual(self.check(raw=raw)["status"], "consistent")
        raw = controlled_page(fact_named())
        raw["lines"][0]["words"] = []
        self.assertEqual(self.check(raw=raw)["evidence_guard"]["status"], "blocked")

    def test_parent_text_missing_sign_and_low_parent_confidence_block(self):
        raw = controlled_page(fact_named())
        raw["lines"][0]["line"]["text"] = "-" + raw["lines"][0]["line"]["text"]
        result = self.check(raw=raw)
        self.assertIn("parent_text_not_conserved", [i["code"] for i in result["evidence_guard"]["issues"]])
        raw = controlled_page(fact_named())
        raw["lines"][0]["line"]["confidence"] = .3
        self.assertNotEqual(self.check(raw=raw)["status"], "consistent")

    def test_remote_word_does_not_become_nearby_guard(self):
        raw = controlled_page(fact_named())
        nearby(raw["lines"][0], "6,789", gap=20)
        self.assertEqual(self.check(raw=raw)["status"], "consistent")

    def split_prefix(self, text="$", value=None, gap=4):
        fact = fact_named()
        raw = controlled_page(fact)
        entry = raw["lines"][0]
        amount = entry["words"][0]
        if value is not None:
            amount["text"] = value
        prefix = deepcopy(amount)
        prefix["text"] = text
        prefix["bbox"]["x1"] = amount["bbox"]["x0"] - gap
        prefix["bbox"]["x0"] = prefix["bbox"]["x1"] - 3
        entry["words"] = [prefix, amount]
        entry["line"]["text"] = text + " " + amount["text"]
        entry["line"]["bbox"]["x0"] = prefix["bbox"]["x0"]
        return fact, raw

    def test_same_parent_complete_split_prefix_is_explicitly_associated(self):
        fact, raw = self.split_prefix()
        result = self.check(fact, raw)
        self.assertEqual(result["status"], "consistent")
        association = result["evidence_guard"]["prefix_association"]
        self.assertTrue(association["applied"])
        self.assertEqual(association["prefix"]["text"], "$")
        self.assertEqual(association["complete_text"], "$ " + fact.raw_value)

    def test_split_prefix_wrong_currency_far_distance_suffix_and_third_word_refused(self):
        fact, raw = self.split_prefix("¥")
        self.assertEqual(self.check(fact, raw)["status"], "conflict")
        fact, raw = self.split_prefix(gap=20)
        self.assertNotEqual(self.check(fact, raw)["status"], "consistent")
        raw = controlled_page(fact)
        nearby(raw["lines"][0], "$", gap=20)
        self.assertNotEqual(self.check(fact, raw)["status"], "consistent")
        fact, raw = self.split_prefix()
        nearby(raw["lines"][0], "note", gap=20)
        self.assertNotEqual(self.check(fact, raw)["status"], "consistent")

    def test_split_prefix_negative_reference_needs_its_complete_sign(self):
        fact, raw = self.split_prefix(value="-" + fact_named().raw_value)
        reference = fact.model_dump(mode="json")
        reference.update(metric="cfo", raw_value="-" + fact.raw_value, value="-" + str(fact.value))
        negative = Fact.model_validate(reference)
        self.assertEqual(self.check(negative, raw)["status"], "consistent")
        fact, raw = self.split_prefix()
        self.assertNotEqual(self.check(negative, raw)["status"], "consistent")

    def test_split_prefix_neighborhood_includes_prefix_outer_edge(self):
        fact, raw = self.split_prefix()
        separate_left(raw, "C")
        self.assertNotEqual(self.check(fact, raw)["status"], "consistent")

    def test_legacy_non_amount_failure_is_not_upgraded(self):
        raw = controlled_page(fact_named())
        raw["lines"][1]["line"]["text"] = raw["lines"][1]["words"][0]["text"] = "2023"
        result = self.check(raw=raw)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["evidence_guard"]["status"], "passed")

    def test_legacy_amount_conflict_cannot_be_downgraded_to_insufficient(self):
        raw = controlled_page(fact_named())
        raw["lines"][0]["line"]["text"] = raw["lines"][0]["words"][0]["text"] = "123"
        separate_left(raw, "USD")
        result = self.check(raw=raw)
        self.assertEqual(result["legacy_currency_result"]["checks"][0]["status"], "conflict")
        self.assertEqual(result["checks"][0]["status"], "conflict")

    def test_observed_amd_public_parents_remain_compatible(self):
        # Already-observed development evidence, not a blind accuracy estimate.
        report = json.loads((ROOT / "artifacts/issuer-evaluation-report.json").read_text(encoding="utf-8"))
        pack = IssuerPack.model_validate_json((ROOT / "data/examples/amd-fy2025-evaluation.json").read_text(encoding="utf-8"))
        by_id = {fact.id: fact for fact in pack.facts}
        count = 0
        for document in report["documents"]:
            for registered in document["pages"]:
                page = WordPage(page=registered["page"], width=registered["width"], height=registered["height"],
                                lines=[WordLine.model_validate({k: line[k] for k in ("line", "words")}) for line in registered["evidence_lines"]])
                for fid in registered["fact_ids"]:
                    result = self.check(by_id[fid], page.model_dump(mode="json"))
                    self.assertEqual(result["status"], result["legacy_currency_result"]["status"], fid)
                    self.assertEqual(result["evidence_guard"]["status"], "passed", fid)
                    count += 1
        self.assertEqual(count, 14)


if __name__ == "__main__":
    unittest.main()
