"""Frozen-response comparison, strict parsing and limits of unsigned exports."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creditlens import row_selection_verification as module
from creditlens.row_disambiguation_routes import install_row_disambiguation_routes

ROOT = Path(__file__).resolve().parents[1]


def encoded(value, **options):
    return json.dumps(value, ensure_ascii=False, **options).encode("utf8")


class RowSelectionVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = module.load_row_disambiguation_snapshot(ROOT)
        cls.exports = [module.expected_selection_payload(cls.snapshot, q["id"], p["row_index"])
                       for q in cls.snapshot["report"]["queries"] for p in q["paths"]]

    def setUp(self):
        self.payload = deepcopy(self.exports[0])

    def verify(self, payload=None):
        return module.verify_row_selection(self.payload if payload is None else payload, self.snapshot)

    def mismatch(self, code=None, path=None):
        result = self.verify()
        self.assertEqual(result["status"], "mismatch")
        self.assertIsNone(result["bound_selection"])
        if code:
            self.assertTrue(any(i["code"] == code and (path is None or i["path"] == path) for i in result["issues"]), result["issues"])
        return result

    def parse_error(self, raw, code):
        with self.assertRaises(module.RowSelectionVerificationError) as captured:
            module.parse_selection_bytes(raw)
        self.assertEqual(captured.exception.code, code)
        self.assertEqual(captured.exception.path, "$")
        return captured.exception

    def test_all_eight_real_paths_match_without_mutation_or_claiming_approval(self):
        self.assertEqual(len(self.exports), 8)
        for payload in self.exports:
            before = deepcopy(payload)
            result = self.verify(payload)
            self.assertEqual(result["status"], "matched")
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["bound_selection"]["query_id"], payload["query_id"])
            self.assertEqual(result["bound_selection"]["row_index"], payload["result"]["selection"]["row_index"])
            self.assertEqual(result["trusted_report_sha256"], self.snapshot["report_sha256"])
            self.assertIsNone(result["raw_sha256"])
            for flag in ("persisted", "automatic_approval", "full_ocr_replayed", "source_bytes_checked", "source_authenticity_verified", "independent_financial_correctness", "human_gold"):
                self.assertIs(result["scope"][flag], False)
            self.assertEqual(payload, before)

    def test_expected_envelopes_equal_actual_frozen_get_response(self):
        app = FastAPI()
        install_row_disambiguation_routes(app, lambda: None, ROOT)
        with patch("creditlens.row_disambiguation_routes.load_row_disambiguation_snapshot", return_value=self.snapshot), TestClient(app) as client:
            for payload in self.exports:
                response = client.get("/api/row-disambiguation/queries/"+payload["query_id"]+"/selection",
                    params={"row_index": payload["result"]["selection"]["row_index"], "expected_report_sha256": self.snapshot["report_sha256"]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(module.parse_selection_bytes(response.content), payload)
                self.assertEqual(self.verify(response.json())["status"], "matched")

    def test_byte_and_canonical_hashes_have_distinct_meaning(self):
        raw_a = encoded(self.payload, separators=(",", ":"))
        raw_b = encoded(dict(reversed(list(self.payload.items()))), indent=2)
        with patch.object(module, "load_row_disambiguation_snapshot", return_value=self.snapshot):
            a = module.load_and_verify_row_selection(raw_a, ROOT)
            b = module.load_and_verify_row_selection(raw_b, ROOT)
        self.assertEqual(a["status"], b["status"])
        self.assertEqual(a["canonical_sha256"], b["canonical_sha256"])
        self.assertNotEqual(a["raw_sha256"], b["raw_sha256"])
        self.assertEqual(a["raw_sha256"], sha256(raw_a).hexdigest())

    def test_other_complete_registered_export_is_valid_but_not_proof_of_original_file(self):
        before = self.verify(self.exports[0])
        replacement = self.verify(self.exports[-1])
        self.assertEqual(replacement["status"], "matched")
        self.assertNotEqual(before["bound_selection"], replacement["bound_selection"])
        self.assertIn("完整替换", " ".join(replacement["limitations"]))
        self.assertIn("不是数字签名", " ".join(replacement["limitations"]))

    def test_amount_changes_even_with_all_public_copies_and_new_hashes_do_not_match(self):
        def mutate(value):
            if isinstance(value, dict):
                for key in list(value):
                    if key == "normalized_value" and value[key] is not None:
                        value[key] = "999999"
                    elif key == "raw_value" and value[key] is not None:
                        value[key] = "999,999"
                    elif key.endswith("sha256"):
                        value[key] = "a"*64
                    else:
                        mutate(value[key])
            elif isinstance(value, list):
                for item in value:
                    mutate(item)
        mutate(self.payload)
        self.payload["report_sha256"] = sha256(encoded(self.payload)).hexdigest()
        result = self.mismatch("unregistered_report", "$.report_sha256")
        self.assertEqual(result["trusted_report_sha256"], self.snapshot["report_sha256"])

    def test_original_row_query_source_and_page_metadata_are_compared(self):
        mutations = (
            lambda p: p["result"]["extraction"]["evidence"]["row_label"][0]["span"].update(text="A different row"),
            lambda p: p["query"].update(year=2023),
            lambda p: p["source"].update(url="https://private.invalid/secret-marker"),
            lambda p: p["page_metadata"].update(image_sha256="0"*64),
        )
        for change in mutations:
            self.payload = deepcopy(self.exports[0]); change(self.payload)
            result = self.mismatch("value_mismatch")
            self.assertNotIn("secret-marker", encoded(result).decode())

    def test_missing_extra_candidates_and_flags_are_not_ignored(self):
        self.payload["discovery"]["candidates"].pop()
        self.mismatch("length_mismatch", "$.discovery.candidates")
        self.payload = deepcopy(self.exports[0]); del self.payload["source"]["sha256"]
        self.mismatch("missing_field", "$.source.sha256")
        self.payload = deepcopy(self.exports[0]); self.payload["source"]["sensitive-private-key"] = "private-value"
        result = self.mismatch("extra_field", "$.source")
        self.assertNotIn("sensitive-private-key", encoded(result).decode())
        self.assertNotIn("private-value", encoded(result).decode())
        self.payload = deepcopy(self.exports[0]); self.payload["automatic_approval"] = True
        self.mismatch("value_mismatch", "$.automatic_approval")

    def test_nested_bool_int_float_types_do_not_collapse(self):
        mutations = (
            (lambda p: p.update(schema_version=True), "$.schema_version"),
            (lambda p: p["result"]["selection"].update(row_index=float(p["result"]["selection"]["row_index"])), "$.result.selection.row_index"),
            (lambda p: p["result"]["selection"].update(explicit=1), "$.result.selection.explicit"),
            (lambda p: p["page_metadata"].update(height=792), "$.page_metadata.height"),
            (lambda p: p["discovery"].update(candidate_count=2.0), "$.discovery.candidate_count"),
        )
        for mutate, path in mutations:
            self.payload = deepcopy(self.exports[0]); mutate(self.payload)
            self.mismatch("type_mismatch", path)

    def test_unsupported_version_report_query_and_row_have_distinct_codes(self):
        mutations = (
            (lambda p: p.update(schema_version=2), "unsupported_schema_version"),
            (lambda p: p["result"].update(rule_version="row-disambiguation-999"), "unsupported_rule_version"),
            (lambda p: p.update(report_sha256="0"*64), "unregistered_report"),
            (lambda p: p.update(role_report_sha256="0"*64), "unregistered_role_report"),
            (lambda p: p.update(query_id="private-query-name"), "unregistered_query"),
            (lambda p: p["result"]["selection"].update(row_index=9999), "unregistered_row"),
        )
        for mutate, code in mutations:
            self.payload = deepcopy(self.exports[0]); mutate(self.payload)
            result = self.mismatch(code)
            self.assertNotIn("private-query-name", encoded(result).decode())

    def test_missing_or_malformed_routing_fields_are_mismatches_not_crashes(self):
        for field in ("query_id", "result"):
            self.payload = deepcopy(self.exports[0]); del self.payload[field]
            self.mismatch("missing_field")
        for value in (None, [], "private-marker"):
            self.payload = deepcopy(self.exports[0]); self.payload["result"]["selection"] = value
            result = self.mismatch("type_mismatch")
            self.assertNotIn("private-marker", encoded(result).decode())

    def test_issue_count_is_capped_without_dumping_payload(self):
        def replace(value):
            if type(value) is dict:
                for key, child in value.items():
                    if type(child) is str and key not in ("query_id",):
                        value[key] = "private-value"
                    else:
                        replace(child)
            elif type(value) is list:
                for child in value: replace(child)
        replace(self.payload)
        result = self.mismatch()
        self.assertEqual(len(result["issues"]), module.MAX_ISSUES)
        self.assertTrue(result["issues_truncated"])
        self.assertNotIn("private-value", encoded(result).decode())
        self.assertNotIn("payload", result)

    def test_strict_utf8_json_object_and_duplicate_key_errors(self):
        for raw, code in ((b"\xff", "invalid_utf8"), (b"", "invalid_json"), (b"[]", "invalid_root"),
                          (b"null", "invalid_root"), (b'{"a":1,"a":2}', "duplicate_key"),
                          (b'{"a":{"private-key":1,"private-key":2}}', "duplicate_key"),
                          (b'{"a":1} trailing', "invalid_json"), (b'\xef\xbb\xbf{}', "invalid_json")):
            self.parse_error(raw, code)
        self.parse_error("{}", "invalid_bytes")

    def test_nonfinite_overflow_and_lossy_underflow_numbers_are_rejected(self):
        for literal in ("NaN", "Infinity", "-Infinity", "1e400", "-1e309"):
            self.parse_error(('{"x":'+literal+'}').encode(), "non_finite_number")
        for literal in ("1e-9999", "1.0000000000000000001", "9"*1025):
            self.parse_error(('{"x":'+literal+'}').encode(), "invalid_number")
        self.assertEqual(module.parse_selection_bytes(b'{"x":1.0,"y":1e0,"z":1}') , {"x":1.0,"y":1.0,"z":1})
        self.assertIs(type(module.parse_selection_bytes(b'{"x":1e0}')["x"]), float)

    def test_unicode_surrogates_and_depth_errors_are_controlled(self):
        self.parse_error(br'{"x":"\ud800"}', "invalid_unicode")
        self.parse_error(br'{"\udfff":1}', "invalid_unicode")
        self.assertEqual(module.parse_selection_bytes(br'{"x":"\ud83d\ude00"}')["x"], "😀")
        self.parse_error(b'{"x":'+b'['*64+b'0'+b']'*64+b'}', "excessive_depth")
        self.assertEqual(module.parse_selection_bytes(b'{"x":"[[[\\\"{{{"}')["x"], '[[["{{{')

    def test_byte_size_exact_boundary_and_node_limit(self):
        raw = encoded(self.payload, separators=(",", ":"))
        padded = raw+b" "*(module.MAX_BYTES-len(raw))
        self.assertEqual(self.verify(module.parse_selection_bytes(padded))["status"], "matched")
        error = self.parse_error(padded+b" ", "input_too_large")
        self.assertEqual(error.http_status, 413)
        self.parse_error(b'{"x":['+b'0,'*20000+b'0]}', "excessive_nodes")

    def test_pure_function_rejects_non_json_values_cycles_and_nonfinite(self):
        for value, code in (({"x": float("nan")}, "non_finite_number"), ({"x": (1,2)}, "invalid_json"),
                            ({True: 1}, "invalid_json"), ({"x": "\ud800"}, "invalid_unicode")):
            with self.assertRaises(module.RowSelectionVerificationError) as captured:
                self.verify(value)
            self.assertEqual(captured.exception.code, code)
        cyclic = {}; cyclic["cycle"] = cyclic
        with self.assertRaises(module.RowSelectionVerificationError): self.verify(cyclic)
        shared = {}
        self.assertEqual(self.verify({"one": shared, "two": shared})["status"], "mismatch")

    def test_invalid_bytes_fail_before_source_loading_and_source_errors_are_separate(self):
        with patch.object(module, "load_row_disambiguation_snapshot") as loader:
            with self.assertRaises(module.RowSelectionVerificationError):
                module.load_and_verify_row_selection(b'{"x":NaN}', ROOT)
            loader.assert_not_called()
        for error in (FileNotFoundError("private/directory"), ValueError("private/path")):
            with patch.object(module, "load_row_disambiguation_snapshot", side_effect=error):
                with self.assertRaises(module.RowSelectionSourceError) as captured:
                    module.load_and_verify_row_selection(encoded(self.payload), ROOT)
            self.assertEqual(captured.exception.http_status, 409)
            self.assertNotIn("private", str(captured.exception))

    def test_loader_is_read_only_and_rejects_unsupported_source_dto(self):
        result = module.load_and_verify_row_selection(encoded(self.payload), ROOT)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["raw_sha256"], sha256(encoded(self.payload)).hexdigest())
        broken = deepcopy(self.snapshot); broken["report"]["experiment_version"] = "future-unknown"
        with self.assertRaises(module.RowSelectionSourceError) as captured:
            module.verify_row_selection(self.payload, broken)
        self.assertEqual(captured.exception.code, "unsupported_source_version")
        with self.assertRaises(module.RowSelectionSourceError):
            module.verify_row_selection(self.payload, {})


if __name__ == "__main__":
    unittest.main()
