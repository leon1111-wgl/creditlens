"""Registered synthetic page groups; no model, original PDF or database writes."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens.asset_preflight import AssetPreflightError, preflight_transfer_assets
from creditlens.batch_input import BatchInputError, inspect_document_batch
from creditlens.diligence import digest
from creditlens.document_input import prepare_document_input
from creditlens.input_controls import raw_fixture
from tests import test_document_transfer as fixtures


class BatchInputTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TransferTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        fact = deepcopy(self.fixture.pack["facts"][0])
        fact["id"] += "-second-page"
        fact["pdf_location"]["page"] += 2
        self.fixture.pack["facts"].append(fact)
        second = deepcopy(self.fixture.manifest["documents"][0]["pages"][0])
        second.update(page=fact["pdf_location"]["page"], fact_ids=[fact["id"]], image_sha256=sha256(b"second synthetic page").hexdigest())
        self.fixture.manifest["documents"][0]["pages"].append(second)
        self.fixture.save_pack()
        self.fixture.save_manifest()
        doc = self.fixture.manifest["documents"][0]
        pages = []
        for page in doc["pages"]:
            raw = raw_fixture()
            for field in ("page", "width", "height", "image_width", "image_height"):
                raw[field] = page[field]
            raw["result"]["txts"][0] = "不应出现在摘要中的完整原文"
            raw["result"]["word_results"][0][0][0] = "不应出现在摘要中的完整原文"
            pages.append({"schema_version": 1, "provenance": "synthetic", "document_id": doc["id"],
                          "source_pdf_sha256": doc["pdf"]["sha256"], "image_sha256": page["image_sha256"], "ocr_input": raw})
        self.value = {"schema_version": 1, "document_id": doc["id"], "provenance": "synthetic", "pages": pages}
        self.expected = [p["page"] for p in doc["pages"]]

    def inspect(self, value=None):
        return inspect_document_batch(self.root, self.value if value is None else value)

    def test_all_registered_pages_prepared_deterministically_without_writes_or_raw_output(self):
        original = deepcopy(self.value)
        files = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = self.inspect()
        self.assertEqual(result, self.inspect())
        self.assertEqual(original, self.value)
        self.assertEqual(files, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(result["status"], "prepared_not_indexed")
        self.assertEqual(result["expected_pages"], self.expected)
        self.assertEqual(result["input_sha256"], digest(self.value))
        self.assertEqual(result["summary"], {"expected_page_count": 2, "submitted_page_count": 2,
            "unique_submitted_page_count": 2, "unique_expected_page_count": 2, "structure_passed_page_count": 2,
            "prepared_page_count": 2, "blocked_page_count": 0, "empty_recognition_page_count": 0})
        for key in ("queryable", "automatic_approval", "database_written"):
            self.assertIs(result[key], False)
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
        for forbidden in ("不应出现在摘要中的完整原文", '"boxes"', '"txts"', '"word_results"', '"bbox"', '"text"'):
            self.assertNotIn(forbidden, serialized)
        self.assertFalse(result["scope"]["cross_page_tables_understood"])

    def test_source_binding_contains_inventory_and_reference_digests(self):
        result = self.inspect()
        inventory = preflight_transfer_assets(self.root)
        selected = inventory["documents"][0]
        binding = result["material_binding"]
        self.assertEqual(binding["inventory_sha256"], digest(inventory))
        self.assertEqual(binding["registered_document_sha256"], digest(selected))
        for key in ("source_pdf_sha256", "pack_sha256", "pack_canonical_sha256"):
            self.assertEqual(binding[key], selected[key])
        self.assertEqual(binding["manifest_sha256"], sha256(self.fixture.manifest_path.read_bytes()).hexdigest())
        self.assertEqual(result, inspect_document_batch(self.root, self.value, Path("configs/document-transfer.json")))

    def test_reordered_inputs_are_allowed_but_have_distinct_input_digest(self):
        first = self.inspect()
        self.value["pages"].reverse()
        second = self.inspect()
        self.assertEqual(second["status"], "prepared_not_indexed")
        self.assertEqual(first["material_binding"], second["material_binding"])
        self.assertNotEqual(first["input_sha256"], second["input_sha256"])
        self.assertEqual([p["page"] for p in second["page_results"]], self.expected[::-1])

    def test_missing_page_and_empty_batch_block(self):
        self.value["pages"].pop()
        result = self.inspect()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["missing_pages"], [self.expected[1]])
        self.assertEqual(result["summary"]["prepared_page_count"], 1)
        self.value["pages"] = []
        result = self.inspect()
        self.assertEqual(result["missing_pages"], self.expected)
        self.assertEqual(result["status"], "blocked")

    def test_duplicate_pages_are_never_selected_or_collapsed(self):
        self.value["pages"].append(deepcopy(self.value["pages"][0]))
        result = self.inspect()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["duplicate_pages"], [self.expected[0]])
        self.assertEqual(len(result["page_results"]), 3)
        self.assertEqual(result["summary"]["structure_passed_page_count"], 3)
        self.assertEqual([p["status"] for p in result["page_results"]], ["blocked", "prepared_not_indexed", "blocked"])

    def test_duplicate_good_and_bad_pages_still_block_both_occurrences(self):
        self.value["pages"].append(deepcopy(self.value["pages"][0]))
        self.value["pages"][2]["ocr_input"]["result"]["boxes"][0][2][0] = -1
        result = self.inspect()
        self.assertEqual(result["page_results"][0]["status"], "blocked")
        self.assertEqual(result["page_results"][2]["status"], "blocked")
        self.assertEqual(result["page_results"][2]["structure_status"], "failed")

    def test_unexpected_page_keeps_all_required_pages_but_blocks_group(self):
        other = deepcopy(self.value["pages"][0]); other["ocr_input"]["page"] = 1000
        self.value["pages"].append(other)
        result = self.inspect()
        self.assertEqual(result["unexpected_pages"], [1000])
        self.assertEqual(result["missing_pages"], [])
        self.assertEqual(result["summary"]["unique_submitted_page_count"], 3)
        self.assertEqual(result["summary"]["unique_expected_page_count"], 2)
        self.assertEqual(result["status"], "blocked")

    def test_outer_document_and_provenance_must_match_every_page(self):
        for key, changed in (("document_id", "other"), ("provenance", "caller_declared_model_output")):
            with self.subTest(key=key):
                value = deepcopy(self.value); value["pages"][1][key] = changed
                result = self.inspect(value)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["page_results"][1]["structure_status"], "not_checked")
                self.assertEqual(result["page_results"][1]["errors"][0]["path"], "pages[1]." + key)

    def test_caller_declared_model_provenance_does_not_establish_execution(self):
        self.value["provenance"] = "caller_declared_model_output"
        for p in self.value["pages"]: p["provenance"] = self.value["provenance"]
        result = self.inspect()
        self.assertEqual(result["status"], "prepared_not_indexed")
        self.assertFalse(result["scope"]["model_execution_verified"])

    def test_page_hash_source_image_and_geometry_errors_are_retained(self):
        for field, bad in (("source_pdf_sha256", "f"*64), ("image_sha256", "f"*64), ("height", 1)):
            with self.subTest(field=field):
                value = deepcopy(self.value)
                target = value["pages"][1]["ocr_input"] if field == "height" else value["pages"][1]
                target[field] = bad
                result = self.inspect(value)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["page_results"][0]["status"], "prepared_not_indexed")
                self.assertEqual(result["page_results"][1]["structure_status"], "failed")
                self.assertTrue(result["page_results"][1]["errors"][0]["path"].startswith("pages[1]."))

    def test_nonfinite_raw_input_is_blocked_without_a_repaired_hash(self):
        self.value["pages"][0]["ocr_input"]["result"]["boxes"][0][2][0] = float("nan")
        result = self.inspect()
        self.assertIsNone(result["input_sha256"])
        self.assertIsNone(result["page_results"][0]["input_sha256"])
        self.assertEqual(result["input_hash_status"], "unavailable_noncanonical_json")
        self.assertIn({"code": "nonfinite_number", "path": "pages[0].ocr_input.result.boxes[0][2][0]", "stage": "page_preflight"}, result["page_results"][0]["errors"])
        json.dumps(result, allow_nan=False)

    def test_empty_recognition_is_structure_passed_but_insufficient(self):
        for empty in (dict(boxes=[], txts=[], scores=[], word_results=[]),
                      dict(boxes=None, txts=None, scores=None, word_results=None),
                      dict(boxes=None, txts=None, scores=None, word_results=[["", 1.0, None]])):
            with self.subTest(empty=empty):
                value = deepcopy(self.value); value["pages"][0]["ocr_input"]["result"] = empty
                result = self.inspect(value)
                self.assertEqual(result["status"], "blocked")
                page = result["page_results"][0]
                self.assertEqual(page["structure_status"], "passed")
                self.assertTrue(page["summary"]["empty"])
                self.assertEqual(page["errors"][0]["code"], "empty_recognition")
                self.assertEqual(result["summary"]["empty_recognition_page_count"], 1)

    def test_one_parent_without_words_blocks_even_if_other_parent_has_words(self):
        raw = self.value["pages"][0]["ocr_input"]["result"]
        for key in ("boxes", "txts", "scores"):
            raw[key].append(deepcopy(raw[key][0]))
        raw["word_results"].append([])
        result = self.inspect()
        self.assertEqual(result["page_results"][0]["structure_status"], "passed")
        self.assertEqual(result["page_results"][0]["errors"][0]["code"], "missing_word_evidence")
        self.assertEqual(result["status"], "blocked")

    def test_malformed_page_and_page_number_not_counted_as_expected(self):
        for wrong in (None, [], "private text", {"ocr_input": {"page": True}}, {"ocr_input": {"page": "53"}}):
            with self.subTest(wrong=wrong):
                value = deepcopy(self.value); value["pages"][0] = wrong
                result = self.inspect(value)
                self.assertEqual(result["status"], "blocked")
                self.assertIsNone(result["page_results"][0]["page"])
                self.assertEqual(result["missing_pages"], [self.expected[0]])
                self.assertNotIn("private text", json.dumps(result))

    def test_unknown_keys_and_text_never_leak_via_error_paths(self):
        self.value["pages"][0]["ocr_input"]["result"]["秘密账户数据"] = "sensitive"
        result = self.inspect()
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("秘密账户数据", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("sensitive", json.dumps(result))

    def test_bad_outer_contract_rejected_before_any_material_read(self):
        changes = [None, {}, {**self.value, "schema_version": True}, {**self.value, "schema_version": 2},
                   {**self.value, "document_id": ""}, {**self.value, "document_id": True},
                   {**self.value, "provenance": "verified"}, {**self.value, "pages": tuple(self.value["pages"])},
                   {**self.value, "pages": self.value["pages"]*17}, {**self.value, "queryable": True}]
        with patch("creditlens.batch_input.preflight_transfer_assets", side_effect=AssertionError("Must validate schema first")):
            for wrong in changes:
                with self.subTest(wrong=type(wrong)), self.assertRaises(BatchInputError):
                    inspect_document_batch(self.root, wrong)

    def test_unknown_document_and_unavailable_registration_are_typed(self):
        self.value["document_id"] = "unregistered"
        with self.assertRaises(BatchInputError) as caught: self.inspect()
        self.assertEqual(caught.exception.code, "unregistered_document")
        with patch("creditlens.batch_input.preflight_transfer_assets", side_effect=AssetPreflightError("x", "private", "secret")):
            with self.assertRaises(BatchInputError) as caught: self.inspect()
        self.assertEqual(caught.exception.path, "material_binding")
        self.assertNotIn("secret", str(caught.exception))

    def test_changed_inventory_on_one_page_invalidates_entire_group(self):
        def changed(root, value, **kwargs):
            prepared = prepare_document_input(root, value, **kwargs)
            if value["ocr_input"]["page"] == self.expected[1]:
                prepared["asset_preflight"]["manifest_sha256"] = "f"*64
            return prepared
        with patch("creditlens.batch_input.prepare_document_input", side_effect=changed):
            result = self.inspect()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["scope"]["same_inventory_checked"])
        self.assertEqual(result["summary"]["prepared_page_count"], 0)

    def test_final_registration_change_or_failure_invalidates_earlier_successes(self):
        original = preflight_transfer_assets(self.root)
        changed = deepcopy(original); changed["manifest_sha256"] = "e"*64
        for final in (changed, AssetPreflightError("changed", "manifest", "secret")):
            with self.subTest(final=type(final)), patch("creditlens.batch_input.preflight_transfer_assets", side_effect=[original, final]):
                result = self.inspect()
            self.assertEqual(result["summary"]["prepared_page_count"], 0)
            self.assertEqual(result["status"], "blocked")

    def test_transient_page_registration_failure_still_invalidates_group(self):
        with patch("creditlens.batch_input.prepare_document_input", side_effect=[
                prepare_document_input(self.root, self.value["pages"][0]), AssetPreflightError("changed", "manifest", "secret")]):
            result = self.inspect()
        self.assertFalse(result["scope"]["same_inventory_checked"])
        self.assertEqual(result["summary"]["prepared_page_count"], 0)

    def test_canonical_json_input_hash_rejects_implicit_tuple_coercion(self):
        raw = self.value["pages"][0]["ocr_input"]["result"]
        raw["boxes"][0] = tuple(raw["boxes"][0])
        result = self.inspect()
        self.assertEqual(result["status"], "blocked")
        self.assertIsNone(result["input_sha256"])
        self.assertEqual(result["page_results"][0]["structure_status"], "passed")

    def test_current_registered_document_with_synthetic_input_is_not_model_evidence(self):
        root = Path(__file__).resolve().parents[1]
        inventory = preflight_transfer_assets(root)
        selected = inventory["documents"][0]
        pages = []
        for registered in selected["pages"]:
            raw = raw_fixture()
            raw.update({key: registered[key] for key in ("page", "width", "height", "image_width", "image_height")})
            pages.append({"schema_version": 1, "document_id": selected["id"], "provenance": "synthetic",
                          "source_pdf_sha256": selected["source_pdf_sha256"], "image_sha256": registered["image_sha256"], "ocr_input": raw})
        result = inspect_document_batch(root, {"schema_version": 1, "document_id": selected["id"], "provenance": "synthetic", "pages": pages})
        self.assertEqual(result["status"], "prepared_not_indexed")
        self.assertEqual(result["expected_pages"], sorted(p["page"] for p in selected["pages"]))
        self.assertEqual(result["material_binding"]["inventory_sha256"], digest(inventory))
        for field in ("source_pdf_bytes_verified", "source_image_bytes_verified", "model_execution_verified", "financial_content_verified"):
            self.assertFalse(result["scope"][field])
