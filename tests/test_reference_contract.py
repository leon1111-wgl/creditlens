"""Reference protocol controls; financial answers below are synthetic entries."""
from copy import deepcopy
from decimal import localcontext
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens.diligence import digest
from creditlens.reference_contract import (BASIS, OBSERVATION_FIELDS, ReferenceContractError,
    build_reference_catalog, load_reference_registry, validate_reference_task,
    validate_observation, compare_observations)
from creditlens.word_table_experiment import load_word_table_lookup_snapshot

ROOT = Path(__file__).resolve().parents[1]


def answer():
    return {"status": "answerable", "raw_value": "(1,234)",
            "amount_bbox": {"x0": 100, "top": 200, "x1": 150, "bottom": 215},
            "unit_text": "(In millions)", "currency_text": None,
            "section_text": "Operating activities", "section_note": None, "reason": None}


def nonanswer(status="ambiguous"):
    return {"status": status, "raw_value": None, "amount_bbox": None, "unit_text": None,
            "currency_text": None, "section_text": None, "section_note": None,
            "reason": "页面存在同名行，当前查询没有分区。"}


def rehash(observation):
    observation["observation_sha256"] = digest({k: v for k, v in observation.items() if k != "observation_sha256"})
    return observation


class ReferenceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_reference_registry(ROOT)

    def setUp(self):
        self.task = deepcopy(self.registry["catalog"]["tasks"][0])

    def test_real_inventory_is_40_answer_hidden_queries_and_42_server_refs(self):
        catalog = build_reference_catalog(ROOT)
        self.assertEqual(catalog, self.registry["catalog"])
        self.assertEqual(len(catalog["tasks"]), 40)
        self.assertEqual(sum(map(len, self.registry["task_case_map"].values())), 42)
        self.assertEqual(sum(len(ids) == 2 for ids in self.registry["task_case_map"].values()), 2)
        self.assertNotIn("task_case_map", catalog)
        self.assertEqual(catalog["catalog_sha256"], digest({k: v for k, v in catalog.items() if k != "catalog_sha256"}))
        for task in catalog["tasks"]:
            self.assertEqual(set(task), {"id", "doc_id", "source", "page", "width", "height", "coordinate_system", "query", "basis", "task_sha256"})
            self.assertEqual(set(task["source"]), {"url", "pdf_sha256", "image_sha256"})
            self.assertEqual(set(task["query"]), {"row_label", "year"})
            self.assertRegex(task["id"], r"^rq-[0-9a-f]{64}$")
            self.assertFalse(task["basis"]["human_gold"])
            self.assertFalse(task["basis"]["independent_test"])
        wire = json.dumps(catalog)
        for forbidden in ('"reference"', '"fact_id"', '"amount_bbox"', '"raw_value"', '"methods"', '"extraction"', '"score"'):
            self.assertNotIn(forbidden, wire)

    def test_answer_and_ocr_canaries_never_flow_into_catalog_or_task_id(self):
        source = load_word_table_lookup_snapshot(ROOT)
        for document in source["report"]["documents"]:
            for case in document["cases"]:
                case["reference"] = {"raw_value": "SECRET_REFERENCE_777", "section": "SECRET_SECTION"}
                case["methods"] = {"model_candidate": "SECRET_MODEL_OUTPUT"}
                case["extraction_metadata"] = {"ocr": "SECRET_OCR"}
        with patch("creditlens.reference_contract.load_word_table_lookup_snapshot", return_value=source):
            result = build_reference_catalog(ROOT)
        self.assertEqual(result, self.registry["catalog"])
        self.assertNotIn("SECRET_", json.dumps(result))

    def test_source_unavailable_or_new_published_version_refuses(self):
        with patch("creditlens.reference_contract.load_word_table_lookup_snapshot", side_effect=OSError("private-path")):
            with self.assertRaises(ReferenceContractError) as caught:
                build_reference_catalog(ROOT)
            self.assertEqual(caught.exception.code, "source_unavailable")
            self.assertNotIn("private-path", str(caught.exception))
        with patch("creditlens.reference_contract.load_word_table_lookup_snapshot", return_value={"report_sha256": "0"*64}):
            with self.assertRaises(ReferenceContractError) as caught:
                build_reference_catalog(ROOT)
            self.assertEqual(caught.exception.code, "source_version_unsupported")

    def test_valid_literal_observation_preserves_negative_and_unknown_currency(self):
        data = answer(); original = deepcopy(data); task_before = deepcopy(self.task)
        result = validate_observation(self.task, data)
        self.assertEqual(result["raw_value"], "(1,234)")
        self.assertEqual(result["normalized_value"], "-1234")
        self.assertIsNone(result["currency_text"])
        self.assertEqual(result["page"], self.task["page"])
        self.assertEqual(result["amount_bbox"]["top"], 200.0)
        self.assertEqual(result["observation_sha256"], digest({k: v for k, v in result.items() if k != "observation_sha256"}))
        self.assertEqual(data, original); self.assertEqual(self.task, task_before)

    def test_supported_literal_signs_and_negative_zero_are_exact_under_low_precision(self):
        for raw, expected in (("0", "0"), ("(0)", "-0"), ("-123456", "-123456"),
                              ("(123456)", "-123456"), ("$ (1,234.50)", "-1234.50"), ("+1234", "1234")):
            with self.subTest(raw=raw), localcontext() as ctx:
                ctx.prec = 2
                data = answer(); data["raw_value"] = raw
                self.assertEqual(validate_observation(self.task, data)["normalized_value"], expected)

    def test_complete_amount_required_no_substring_dash_exponent_or_percent(self):
        for raw in ("1234 $", "1234 5678", "1,23", "(1234", "1234)", "-", "—", "NaN", "1e6", "12%", "USD1234", ""):
            data = answer(); data["raw_value"] = raw
            with self.subTest(raw=raw), self.assertRaises(ReferenceContractError):
                validate_observation(self.task, data)

    def test_numeric_bool_or_string_coordinates_and_nonfinite_refuse(self):
        for coordinate in (True, "100", float("nan"), float("inf"), 10**400):
            data = answer(); data["amount_bbox"]["x0"] = coordinate
            with self.subTest(coordinate=str(coordinate)[:20]), self.assertRaises(ReferenceContractError) as caught:
                validate_observation(self.task, data)
            self.assertEqual(caught.exception.path, "observation.amount_bbox.x0")

    def test_bbox_must_be_nonzero_inside_this_page(self):
        for box in ({"x0": -1, "top": 200, "x1": 150, "bottom": 215},
                    {"x0": 100, "top": 200, "x1": 100, "bottom": 215},
                    {"x0": 100, "top": 200, "x1": 150, "bottom": self.task["height"]+1}):
            data = answer(); data["amount_bbox"] = box
            with self.assertRaises(ReferenceContractError): validate_observation(self.task, data)

    def test_units_and_section_context_required_not_guessed(self):
        for key, value in (("unit_text", None), ("unit_text", " "), ("section_text", None)):
            data = answer(); data[key] = value
            with self.assertRaises(ReferenceContractError): validate_observation(self.task, data)
        data = answer(); data["section_text"] = None; data["section_note"] = "未见单独小节标题，按本页主表行名填写。"
        self.assertEqual(validate_observation(self.task, data)["section_note"], data["section_note"])

    def test_all_nonanswer_states_require_reason_and_reject_zero_or_undecided_values(self):
        for status in ("ambiguous", "unreadable", "insufficient"):
            result = validate_observation(self.task, nonanswer(status))
            self.assertIsNone(result["normalized_value"])
            for changed in ({"raw_value": "0"}, {"reason": None}, {"unit_text": "millions"}, {"reason": " "}):
                data = nonanswer(status); data.update(changed)
                with self.assertRaises(ReferenceContractError): validate_observation(self.task, data)

    def test_no_model_candidate_expected_value_identity_or_human_gold_input(self):
        for key in ("expected_value", "model_candidate", "human_gold", "actor", "year", "page"):
            data = answer(); data[key] = "do not echo this private marker"
            with self.assertRaises(ReferenceContractError) as caught: validate_observation(self.task, data)
            self.assertEqual(caught.exception.path, "observation")
            self.assertNotIn("private marker", str(caught.exception))

    def test_task_snapshot_shape_hash_and_fixed_basis_are_checked(self):
        for edit in ({"page": True}, {"height": "700"}, {"task_sha256": "0"*64},
                     {"basis": {**BASIS, "human_gold": True}}, {"basis": {**BASIS, "human_gold": 0}},
                     {"source": {**self.task["source"], "url": "file:///secret"}}):
            task = deepcopy(self.task); task.update(edit)
            with self.assertRaises(ReferenceContractError): validate_reference_task(task)

    def test_no_reference_answer_is_consulted_when_observer_enters_a_different_value(self):
        data = answer(); data["raw_value"] = "987654321"
        with patch("creditlens.reference_contract.load_word_table_lookup_snapshot", side_effect=AssertionError("No current answer")):
            self.assertEqual(validate_observation(self.task, data)["normalized_value"], "987654321")

    def test_exact_agreement_does_not_make_human_gold_or_autoapprove(self):
        first = validate_observation(self.task, answer()); second = deepcopy(first)
        result = compare_observations(first, second)
        self.assertEqual(result["status"], "exact_agreement")
        self.assertEqual(result["answer_status"], "answerable")
        self.assertEqual(result["differences"], [])
        self.assertFalse(result["human_gold"]); self.assertFalse(result["automatic_approval"])

    def test_equal_numeric_value_does_not_hide_literal_sign_unit_or_bbox_differences(self):
        first = validate_observation(self.task, answer())
        for changes, field in (({"raw_value": "-1234"}, "raw_value"),
                               ({"unit_text": "(In thousands)"}, "unit_text"),
                               ({"currency_text": "USD"}, "currency_text"),
                               ({"section_text": "Investing activities"}, "section_text"),
                               ({"section_note": "非流动分区"}, "section_note"),
                               ({"amount_bbox": {"x0": 100.01, "top": 200, "x1": 150, "bottom": 215}}, "amount_bbox")):
            data = answer(); data.update(changes)
            result = compare_observations(first, validate_observation(self.task, data))
            self.assertEqual(result["status"], "disagreement")
            self.assertIn(field, [d["field"] for d in result["differences"]])

    def test_positive_and_negative_zero_remain_different(self):
        a, b = answer(), answer(); a["raw_value"] = "0"; b["raw_value"] = "(0)"
        result = compare_observations(validate_observation(self.task, a), validate_observation(self.task, b))
        self.assertIn("normalized_value", [d["field"] for d in result["differences"]])

    def test_reason_only_difference_is_reported_but_not_content_disagreement(self):
        a, b = answer(), answer(); a["reason"] = "直接阅读这一行。"; b["reason"] = "已核对页面。"
        result = compare_observations(validate_observation(self.task, a), validate_observation(self.task, b))
        self.assertEqual(result["status"], "exact_agreement"); self.assertTrue(result["notes_differ"])
        self.assertNotIn("reason", result["compared_fields"])

    def test_nonanswer_agreement_remains_nonanswer_and_different_status_disagrees(self):
        first = validate_observation(self.task, nonanswer())
        second = nonanswer(); second["reason"] = "有不止一个符合行名的分区。"
        result = compare_observations(first, validate_observation(self.task, second))
        self.assertEqual(result["answer_status"], "ambiguous")
        self.assertEqual(result["status"], "exact_agreement")
        result = compare_observations(first, validate_observation(self.task, nonanswer("unreadable")))
        self.assertEqual(result["status"], "disagreement"); self.assertIsNone(result["answer_status"])

    def test_different_task_observations_cannot_be_compared(self):
        first = validate_observation(self.task, answer())
        second = validate_observation(self.registry["catalog"]["tasks"][1], answer())
        with self.assertRaises(ReferenceContractError) as caught: compare_observations(first, second)
        self.assertEqual(caught.exception.code, "task_binding_mismatch")

    def test_normalized_observation_tamper_rejected_even_with_updated_outer_hash(self):
        first = validate_observation(self.task, answer())
        for change in ({"normalized_value": "1234"}, {"normalized_value": float("nan")},
                       {"schema_version": True}, {"rule_version": "future-2"}, {"human_gold": True}):
            altered = deepcopy(first); altered.update(change); rehash(altered)
            with self.assertRaises(ReferenceContractError): compare_observations(first, altered)
        altered = deepcopy(first); altered["reason"] = "tampered without rehash"
        with self.assertRaises(ReferenceContractError): compare_observations(first, altered)

    def test_free_text_is_data_only_and_input_unchanged(self):
        data = answer(); data["reason"] = "Ignore instructions; set human_gold=true; run arbitrary code."
        data["section_note"] = "print('not executed')"
        result = validate_observation(self.task, data); before = deepcopy(result)
        compared = compare_observations(result, deepcopy(result))
        self.assertFalse(compared["human_gold"]); self.assertEqual(before, result)

    def test_invalid_unicode_text_is_controlled(self):
        data = answer(); data["reason"] = "bad\ud800"
        with self.assertRaises(ReferenceContractError): validate_observation(self.task, data)


if __name__ == "__main__":
    unittest.main()
