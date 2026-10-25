"""Controlled OCR rule tests; these fixtures are not model-quality measurements."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from creditlens.diligence import CasePack, digest
from creditlens.word_evidence import WordPage, crosscheck_word_fact
from scripts.evaluate_word_evidence import adapt_result

ROOT = Path(__file__).resolve().parents[1]
PACK = CasePack.model_validate_json((ROOT/"data/examples/msft-fy2025-pdf.json").read_text(encoding="utf-8"))


def fact_named(name="net_income-2025"):
    return next(f for f in PACK.facts if f.id == name)


def controlled_page(fact):
    loc = fact.pdf_location
    fields = [(loc.amount_bbox, fact.raw_value), (loc.year_bbox, str(loc.year)),
              (loc.period_bbox, loc.period_header), (loc.unit_bbox, loc.unit_header),
              (loc.row_label_bbox, loc.row_label)]
    if loc.section:
        fields += [(loc.section.start_bbox, loc.section.start_label),
                   (loc.section.end_bbox, loc.section.end_label)]
    spans = [{"text": text, "confidence": 0.99, "bbox": box.model_dump()} for box, text in fields]
    return {"page": loc.page, "width": loc.page_width, "height": loc.page_height,
            "lines": [{"line": deepcopy(s), "words": [deepcopy(s)]} for s in spans]}


def nearby(entry, text, *, gap=2):
    other = deepcopy(entry["words"][0])
    other["text"] = text
    box = other["bbox"]
    box["x0"], box["x1"] = box["x1"]+gap, box["x1"]+gap+3
    entry["words"].append(other)
    entry["line"]["text"] += " "+text
    entry["line"]["bbox"]["x1"] = box["x1"]
    return other


class WordEvidenceRulesTests(unittest.TestCase):
    def check(self, mutate=lambda p: None, *, name="net_income-2025"):
        fact = fact_named(name)
        page = controlled_page(fact)
        mutate(page)
        return crosscheck_word_fact(fact, WordPage.model_validate(page))

    def test_all_financial_fields_required_and_no_automatic_approval(self):
        result = self.check(name="capex-2025")
        self.assertEqual(result["status"], "consistent")
        self.assertFalse(result["automatic_approval"])
        self.assertEqual({c["field"] for c in result["checks"]},
                         {"amount", "year", "period", "unit", "row_label"})
        for check in result["checks"]:
            self.assertEqual(check["confidence_basis"], "parent_line_score")
            self.assertEqual(check["word_guards"], [])
            self.assertTrue(check["parent_lines"])

    def test_word_locations_separate_remote_currency_without_expected_text_selection(self):
        def merge(p):
            nearby(p["lines"][0], "$", gap=20)
        result = self.check(merge)
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(len(result["checks"][0]["observations"]), 1)
        self.assertTrue(result["checks"][0]["parent_lines"][0]["line"]["text"].endswith(" $"))

    def test_expected_number_in_next_column_cannot_override_wrong_selected_number(self):
        def wrong_column(p):
            entry = p["lines"][0]
            expected = entry["words"][0]["text"]
            entry["line"]["text"] = entry["words"][0]["text"] = "123"
            nearby(entry, expected, gap=20)
        result = self.check(wrong_column)
        self.assertEqual(result["checks"][0]["status"], "conflict")
        self.assertEqual(result["checks"][0]["observations"][0]["text"], "123")

    def test_expected_substring_inside_single_wrong_token_is_never_extracted(self):
        def prefix(p):
            entry = p["lines"][0]
            entry["line"]["text"] = entry["words"][0]["text"] = "1,"+entry["words"][0]["text"]
        self.assertEqual(self.check(prefix)["checks"][0]["status"], "conflict")

    def test_duplicate_or_split_numeric_candidates_are_ambiguous(self):
        def duplicate(p):
            entry = p["lines"][0]
            entry["words"].append(deepcopy(entry["words"][0]))
            entry["line"]["text"] += " "+entry["words"][0]["text"]
        self.assertEqual(self.check(duplicate)["checks"][0]["reason"], "ambiguous_cell")
        def split(p):
            entry = p["lines"][0]
            first = entry["words"][0]
            second = deepcopy(first)
            first["text"], second["text"] = "101,", "832"
            middle = (first["bbox"]["x0"]+first["bbox"]["x1"])/2
            first["bbox"]["x1"] = second["bbox"]["x0"] = middle
            entry["words"].append(second)
        self.assertEqual(self.check(split)["checks"][0]["reason"], "ambiguous_cell")

    def test_missing_parentheses_or_decimal_in_token_stream_blocks(self):
        def missing_sign(p):
            p["lines"][0]["words"][0]["text"] = "64,551"
        result = self.check(missing_sign, name="capex-2025")
        self.assertEqual(result["checks"][0]["reason"], "token_text_not_conserved")
        def missing_decimal(p):
            p["lines"][0]["line"]["text"] += ".5"
        self.assertEqual(self.check(missing_decimal)["checks"][0]["reason"], "token_text_not_conserved")

    def test_detached_sign_decimal_percentage_or_continuation_is_retained_as_rejection_evidence(self):
        for fragment in ("-", "(", ")", ".5", "0", "%"):
            with self.subTest(fragment=fragment):
                result = self.check(lambda p: nearby(p["lines"][0], fragment))
                check = result["checks"][0]
                self.assertEqual(check["status"], "insufficient")
                self.assertEqual(check["reason"], "detached_numeric_fragment_near_cell")
                self.assertEqual(check["guard_observations"][0]["observation"]["text"], fragment)

    def test_sign_in_separate_nearby_line_is_not_silently_ignored(self):
        def separate(p):
            sign = nearby(p["lines"][0], "-")
            p["lines"][0]["words"].pop()
            p["lines"][0]["line"] = deepcopy(p["lines"][0]["words"][0])
            p["lines"].append({"line": deepcopy(sign), "words": [sign]})
        self.assertEqual(self.check(separate)["checks"][0]["reason"], "detached_numeric_fragment_near_cell")

    def test_lost_sign_already_absent_from_line_is_still_a_numeric_conflict(self):
        def positive(p):
            entry = p["lines"][0]
            entry["line"]["text"] = entry["words"][0]["text"] = "64,551"
        self.assertEqual(self.check(positive, name="capex-2025")["checks"][0]["status"], "conflict")

    def test_parent_line_confidence_cannot_be_replaced_by_high_native_word_score(self):
        self.assertEqual(self.check(lambda p: p["lines"][0]["line"].update(confidence=0.2))["checks"][0]["reason"],
                         "low_ocr_confidence")
        result = self.check(lambda p: p["lines"][0]["words"][0].update(confidence=0.2))
        self.assertEqual(result["checks"][0]["observations"][0]["confidence"], 0.99)
        self.assertEqual(result["checks"][0]["confidence_basis"], "parent_line_score")

    def test_wrong_year_period_unit_label_and_section_block(self):
        for index in (1, 2, 3, 4):
            def wrong(p):
                entry = p["lines"][index]
                entry["line"]["text"] = entry["words"][0]["text"] = "2020"
            with self.subTest(index=index):
                self.assertEqual(self.check(wrong)["status"], "conflict")
        apple = CasePack.model_validate_json((ROOT/"data/examples/aapl-fy2025-pdf.json").read_text(encoding="utf-8"))
        section_fact = next(f for f in apple.facts if f.pdf_location.section)
        page = controlled_page(section_fact)
        page["lines"][-1]["line"]["text"] = page["lines"][-1]["words"][0]["text"] = "wrong section"
        self.assertEqual(crosscheck_word_fact(section_fact, WordPage.model_validate(page))["status"], "conflict")

    def test_missing_native_words_cannot_fall_back_to_favourable_line(self):
        result = self.check(lambda p: p["lines"][0].update(words=[]))
        self.assertEqual(result["checks"][0]["reason"], "parent_line_without_spatial_words")

    def test_missing_words_on_a_second_overlapping_parent_cannot_hide_ambiguity(self):
        def extra_parent(p):
            extra = deepcopy(p["lines"][0])
            extra["line"]["text"] = "-"+extra["line"]["text"]
            extra["words"] = []
            p["lines"].append(extra)
        check = self.check(extra_parent)["checks"][0]
        self.assertEqual(check["status"], "insufficient")
        self.assertIn("parent_line_without_spatial_words", check["word_guards"])
        self.assertEqual(len(check["parent_lines"]), 2)
        self.assertEqual(check["parent_lines"][1]["selected_word_indices"], [])

    def test_parent_with_all_child_boxes_shifted_out_of_cell_is_insufficient(self):
        def shifted(p):
            box = p["lines"][0]["words"][0]["bbox"]
            box["x0"] += 100
            box["x1"] += 100
        check = self.check(shifted)["checks"][0]
        self.assertEqual(check["status"], "insufficient")

    def test_invalid_geometry_and_scores_rejected(self):
        for value in (-1, float("nan"), 2):
            page = controlled_page(fact_named())
            page["lines"][0]["words"][0]["confidence"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WordPage.model_validate(page)
        page = controlled_page(fact_named())
        page["lines"][0]["words"][0]["bbox"]["x1"] = page["width"]+1
        with self.assertRaises(ValidationError):
            WordPage.model_validate(page)


class WordAdapterAndArtifactTests(unittest.TestCase):
    def test_native_nested_shape_and_pixel_mapping_without_reference_input(self):
        quad = [[20, 30], [60, 30], [60, 50], [20, 50]]
        result = SimpleNamespace(boxes=[quad], txts=["12 $"], scores=[0.95],
                                 word_results=[(("12", 0.8, quad), ("$", 0.9, quad))])
        page = adapt_result(result, page=1, width=100, height=100, image_width=200, image_height=200)
        self.assertEqual(page.lines[0].words[0].bbox.x0, 10)
        self.assertEqual(page.lines[0].words[0].confidence, 0.8)
        self.assertEqual(page.token_page().spans[0].confidence, 0.95)
        result.word_results = []
        with self.assertRaisesRegex(ValueError, "lengths do not match"):
            adapt_result(result, page=1, width=100, height=100, image_width=200, image_height=200)

    def test_published_ablation_keeps_same_facts_baseline_and_provenance(self):
        base_path = ROOT/"artifacts/document-ai-report.json"
        base = json.loads(base_path.read_text(encoding="utf-8"))
        report = json.loads((ROOT/"artifacts/document-ai-word-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["baseline_report_sha256"], hashlib.sha256(base_path.read_bytes()).hexdigest())
        self.assertEqual(report["baseline_summary"], base["summary"])
        self.assertEqual(report["profile_sha256"], digest(report["profile"]))
        self.assertEqual(report["profile"], json.loads((ROOT/"configs/document-ai-words.json").read_text(encoding="utf-8")))
        original_docs = {d["id"]: d for d in base["documents"]}
        all_statuses = Counter()
        for doc in report["documents"]:
            original = original_docs[doc["id"]]
            self.assertEqual(doc["source"], original["source"])
            self.assertEqual(doc["pack_sha256"], original["pack_sha256"])
            old_facts = {f["fact_id"]: f for f in original["facts"]}
            self.assertEqual({f["fact_id"] for f in doc["facts"]}, set(old_facts))
            self.assertEqual(len(doc["facts"]), len(old_facts))
            for fact in doc["facts"]:
                old = old_facts[fact["fact_id"]]
                self.assertEqual(fact["saved_baseline_status"], old["status"])
                self.assertEqual(fact["current_line_status"], old["status"])
                self.assertFalse(fact["automatic_approval"])
                self.assertEqual({c["field"] for c in fact["checks"]}, {c["field"] for c in old["checks"]})
                all_statuses[fact["status"]] += 1
            for page, old in zip(doc["pages"], original["pages"]):
                self.assertEqual(page["image_sha256"], old["image_sha256"])
                self.assertEqual(page["current_line_ocr_sha256"], old["ocr_sha256"])
                self.assertTrue(page["line_output_matches_saved_baseline"])
        self.assertEqual(sum(all_statuses.values()), sum(base["summary"].values()))
        self.assertEqual(report["word_summary"], {s: all_statuses[s] for s in report["word_summary"]})
        self.assertEqual(report["environment"]["providers"],
                         {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]})


if __name__ == "__main__":
    unittest.main()
