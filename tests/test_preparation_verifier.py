"""Offline summary consistency, not authentication or OCR re-execution."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens.batch_attempt_store import BatchAttemptStore
from creditlens.preparation_verifier import PreparationVerificationError, verify_preparation_export
from creditlens.workflow import Actor
from tests import test_batch_input as fixtures

ROOT = Path(__file__).resolve().parents[1]


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def rehash(value):
    value["attempt"]["report_sha256"] = sha(value["attempt"]["report"])
    value["job"]["material_binding_sha256"] = sha(value["job"]["material_binding"])
    return value


class PreparationVerifierTests(unittest.TestCase):
    def setUp(self):
        replay = json.loads((ROOT/"artifacts/document-preparation-replay.json").read_text(encoding="utf-8"))
        self.exports = replay["attempt_exports"]
        self.good = deepcopy(self.exports[-1])
        self.blocked = deepcopy(self.exports[0])

    def refuse(self, value, code=None):
        with self.assertRaises(PreparationVerificationError) as caught:
            verify_preparation_export(value)
        if code:
            self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.path)
        self.assertNotIn("secret-canary", str(caught.exception))
        return caught.exception

    def test_three_historical_public_exports_pass_without_promoting_blocked(self):
        before = deepcopy(self.exports)
        checked = [verify_preparation_export(value) for value in self.exports]
        self.assertEqual(self.exports, before)
        self.assertEqual([v["preparation_status"] for v in checked], ["blocked", "blocked", "prepared_not_indexed"])
        self.assertEqual([v["revision"] for v in checked], [1, 2, 3])
        for source, result in zip(self.exports, checked):
            self.assertEqual(result["status"], "internally_consistent")
            self.assertEqual(result["export_sha256"], sha(source))
            self.assertEqual(result["report_sha256"], source["attempt"]["report_sha256"])
            self.assertEqual(result["material_binding"], source["job"]["material_binding"])
            for flag in ("authenticated", "source_bytes_verified", "ocr_quality_verified", "queryable", "automatic_approval"):
                self.assertIs(result[flag], False)
        checked[0]["material_binding"]["document_id"] = "changed"
        self.assertEqual(self.exports, before)

    def test_page_registration_output_is_minimal_ordered_and_independently_copied(self):
        result = verify_preparation_export(self.good)
        expected = [{"input_index": page["input_index"], **deepcopy(page["registration"])}
                    for page in self.good["attempt"]["report"]["page_results"] if page["registration"] is not None]
        self.assertEqual(result["page_registrations"], expected)
        self.assertEqual(set(result["page_registrations"][0]),
                         {"input_index", "page", "document_id", "source_pdf_sha256", "image_sha256", "fact_ids"})
        result["page_registrations"][0]["fact_ids"].append("changed-output")
        self.assertNotIn("changed-output", self.good["attempt"]["report"]["page_results"][0]["registration"]["fact_ids"])
        failed = verify_preparation_export(self.exports[1])
        self.assertEqual([p["input_index"] for p in failed["page_registrations"]], [0])

    def test_self_consistent_registration_rewrite_is_exposed_for_current_material_checks(self):
        original = verify_preparation_export(self.good)
        for field, value in (("image_sha256", "0"*64), ("fact_ids", ["unregistered-fact"])):
            with self.subTest(field=field):
                rewritten = deepcopy(self.good)
                rewritten["attempt"]["report"]["page_results"][0]["registration"][field] = value
                result = verify_preparation_export(rehash(rewritten))
                # With no registry bytes this is internally coherent. The next
                # applicability layer must compare these explicit registrations,
                # not just the unchanged opaque material-binding digest.
                self.assertEqual(result["status"], "internally_consistent")
                self.assertFalse(result["authenticated"])
                self.assertEqual(result["material_binding"], original["material_binding"])
                self.assertNotEqual(result["page_registrations"], original["page_registrations"])
                self.assertEqual(result["page_registrations"][0][field], value)

    def test_no_current_registry_file_network_or_database_read(self):
        with patch("builtins.open", side_effect=AssertionError("No file reads")), \
             patch("pathlib.Path.open", side_effect=AssertionError("No file reads")), \
             patch("sqlite3.connect", side_effect=AssertionError("No database")), \
             patch("creditlens.asset_preflight.preflight_transfer_assets", side_effect=AssertionError("No current registry")):
            result = verify_preparation_export(self.blocked)
        self.assertEqual(result["preparation_status"], "blocked")

    def test_digest_changes_fail_even_when_schema_stays_valid(self):
        for change in (lambda v: v["attempt"].update(report_sha256="0"*64),
                       lambda v: v["job"].update(material_binding_sha256="0"*64),
                       lambda v: v["attempt"]["report"]["page_results"][0]["summary"].update(words=2)):
            value = deepcopy(self.good); change(value)
            self.refuse(value, "digest_mismatch")

    def test_job_attempt_report_identity_and_input_hash_are_bound(self):
        changes = [lambda v: v["attempt"].update(task_id="0"*32), lambda v: v["attempt"].update(tenant="other"),
                   lambda v: v["job"].update(document_id="other"), lambda v: v["job"].update(provenance="caller_declared_model_output"),
                   lambda v: v["attempt"].update(input_sha256="0"*64),
                   lambda v: v["job"]["material_binding"].update(source_pdf_sha256="0"*64)]
        for change in changes:
            value = deepcopy(self.good); change(value)
            self.refuse(rehash(value), "binding_mismatch")

    def test_unknown_versions_are_unsupported_not_silently_current(self):
        for path in (("store_version",), ("attempt", "report", "rule_version"),
                     *[("attempt", "report", "versions", name) for name in ("batch", "document_input", "ocr_input", "asset_preflight")]):
            with self.subTest(path=path):
                value = deepcopy(self.good); target = value
                for key in path[:-1]: target = target[key]
                target[path[-1]] = "future-version"
                self.refuse(rehash(value), "unsupported_version")

    def test_exact_schema_rejects_extra_body_at_every_level(self):
        paths = [(), ("job",), ("attempt",), ("attempt", "report"), ("attempt", "report", "page_results", 0),
                 ("attempt", "report", "page_results", 0, "summary"), ("attempt", "report", "page_results", 0, "registration")]
        for path in paths:
            value = deepcopy(self.good); target = value
            for key in path: target = target[key]
            target["secret-canary"] = "full OCR body"
            self.refuse(rehash(value), "invalid_export_schema")

    def test_nonfinite_nonjson_and_boolean_numbers_reject(self):
        for bad in (float("nan"), float("inf"), float("-inf"), object()):
            value = deepcopy(self.good); value["attempt"]["revision"] = bad
            self.refuse(value, "invalid_json_value")
        for path in (("attempt", "revision"), ("attempt", "report", "summary", "prepared_page_count"),
                     ("attempt", "report", "page_results", 0, "input_index"), ("attempt", "report", "expected_pages", 0)):
            value = deepcopy(self.good); target = value
            for key in path[:-1]: target = target[key]
            target[path[-1]] = True
            self.refuse(rehash(value), "invalid_export_schema")
        value = deepcopy(self.good); value["queryable"] = 0
        self.refuse(value, "invalid_export_schema")
        value = deepcopy(self.good); value["attempt"]["report"]["page_results"] = tuple(value["attempt"]["report"]["page_results"])
        self.refuse(value, "invalid_json_value")

    def test_recursive_and_excessively_nested_input_reject(self):
        cyclic = {}; cyclic["self"] = cyclic
        self.refuse(cyclic, "invalid_json_value")
        nested = {}; root = nested
        for _ in range(90): root["next"] = {}; root = root["next"]
        self.refuse(nested, "invalid_json_value")

    def test_promoted_scope_and_removed_limitations_reject_after_rehash(self):
        for flag in ("queryable", "automatic_approval", "raw_ocr_persisted"):
            value = deepcopy(self.good); value[flag] = True
            self.refuse(value, "scope_mismatch")
        for flag in ("queryable", "automatic_approval", "database_written"):
            value = deepcopy(self.good); value["attempt"]["report"][flag] = True
            self.refuse(rehash(value), "invalid_export_schema")
        for flag in ("source_pdf_bytes_verified", "source_image_bytes_verified", "model_execution_verified",
                     "financial_content_verified", "cross_page_tables_understood", "raw_ocr_returned", "normalized_ocr_returned"):
            value = deepcopy(self.good); value["attempt"]["report"]["scope"][flag] = True
            self.refuse(rehash(value), "invalid_export_schema")
        value = deepcopy(self.good); value["attempt"]["report"]["scope"]["same_inventory_checked"] = False
        self.refuse(rehash(value), "scope_mismatch")
        value = deepcopy(self.good); value["attempt"]["report"]["limitations"] = []
        self.refuse(rehash(value), "scope_mismatch")
        value = deepcopy(self.good); value["scope"] = "financial content proven"
        self.refuse(value, "scope_mismatch")

    def test_changed_status_with_rehashed_report_does_not_hide_missing_pages(self):
        self.blocked["attempt"]["report"]["status"] = "prepared_not_indexed"
        self.refuse(rehash(self.blocked), "status_mismatch")
        self.good["attempt"]["report"]["status"] = "blocked"
        self.refuse(rehash(self.good), "status_mismatch")

    def test_missing_duplicate_unexpected_page_sets_are_recomputed(self):
        for field, values in (("missing_pages", [148]), ("duplicate_pages", [146]), ("unexpected_pages", [999])):
            value = deepcopy(self.good); value["attempt"]["report"][field] = values
            self.refuse(rehash(value), "page_set_mismatch")
        value = deepcopy(self.blocked); value["attempt"]["report"]["missing_pages"] = []
        self.refuse(rehash(value), "page_set_mismatch")
        for expected in ([148, 146], [146, 146, 148]):
            value = deepcopy(self.good); value["attempt"]["report"]["expected_pages"] = expected
            self.refuse(rehash(value), "page_set_mismatch")

    def test_indices_cannot_be_reordered_or_reused(self):
        for indices in ([0, 0], [1, 0], [0, 2]):
            value = deepcopy(self.good)
            for result, index in zip(value["attempt"]["report"]["page_results"], indices): result["input_index"] = index
            self.refuse(rehash(value), "page_index_mismatch")

    def test_each_aggregate_counter_is_recomputed(self):
        for key in self.good["attempt"]["report"]["summary"]:
            value = deepcopy(self.good); value["attempt"]["report"]["summary"][key] += 1
            self.refuse(rehash(value), "summary_mismatch")

    def test_page_counts_cannot_hide_empty_or_missing_words(self):
        changes = [dict(empty=True), dict(words=0), dict(lines=0), dict(words=5001), dict(words=2001), dict(lines=5001)]
        for change in changes:
            value = deepcopy(self.good); value["attempt"]["report"]["page_results"][0]["summary"].update(change)
            self.refuse(rehash(value), "page_state_mismatch")

    def test_passed_pages_require_registration_and_summary(self):
        for key in ("registration", "summary"):
            value = deepcopy(self.good); value["attempt"]["report"]["page_results"][0][key] = None
            self.refuse(rehash(value), "page_state_mismatch")

    def test_failed_pages_cannot_retain_passed_summary_or_change_to_prepared(self):
        for change in (lambda p: p.update(status="prepared_not_indexed"),
                       lambda p: p.update(summary={"lines": 1, "words": 1, "empty": False})):
            value = deepcopy(self.exports[1]); change(value["attempt"]["report"]["page_results"][1])
            self.refuse(rehash(value), "page_state_mismatch")

    def test_registration_page_source_fact_memberships_and_reused_inputs_reject(self):
        for key, bad in (("page", 148), ("source_pdf_sha256", "0"*64), ("document_id", "other")):
            value = deepcopy(self.good); value["attempt"]["report"]["page_results"][0]["registration"][key] = bad
            self.refuse(rehash(value), "binding_mismatch")
        value = deepcopy(self.good); value["attempt"]["report"]["page_results"][0]["registration"]["fact_ids"] *= 2
        self.refuse(rehash(value), "page_registration_mismatch")
        value = deepcopy(self.good); rows = value["attempt"]["report"]["page_results"]
        rows[1]["registration"]["fact_ids"] = rows[0]["registration"]["fact_ids"]
        self.refuse(rehash(value), "page_registration_mismatch")
        value = deepcopy(self.good); rows = value["attempt"]["report"]["page_results"]
        rows[1]["input_sha256"] = rows[0]["input_sha256"]
        self.refuse(rehash(value), "binding_mismatch")

    def test_errors_must_match_fixed_page_index_stage_and_known_code(self):
        for key, changed in (("path", "pages[0].ocr_input.result.boxes[0][2][0]"), ("stage", "approved"), ("code", "secret_canary")):
            value = deepcopy(self.exports[1]); value["attempt"]["report"]["page_results"][1]["errors"][0][key] = changed
            self.refuse(rehash(value), "error_contract_mismatch")
        value = deepcopy(self.blocked); value["attempt"]["report"]["errors"] = []
        self.refuse(rehash(value), "error_contract_mismatch")

    def test_first_revision_identity_time_and_date_types_are_bound(self):
        value = deepcopy(self.blocked); value["attempt"]["actor"] = "other"
        self.refuse(value, "revision_binding_mismatch")
        for stamp in ("not a date", "2026-10-25T00:00:00"):
            value = deepcopy(self.good); value["attempt"]["created_at"] = stamp
            self.refuse(value, "invalid_timestamp")
        value = deepcopy(self.good); value["attempt"]["created_at"] = "2000-01-01T00:00:00+00:00"
        self.refuse(value, "revision_binding_mismatch")
        value = deepcopy(self.good); value["attempt"]["actor"] = "x\nsecret-canary"
        self.refuse(value, "invalid_identity")

    def test_self_consistent_identity_or_input_rewrite_is_not_authentication(self):
        value = deepcopy(self.blocked)
        value["job"]["created_by"] = value["attempt"]["actor"] = "unauthenticated-other-actor"
        value["attempt"]["input_sha256"] = value["attempt"]["report"]["input_sha256"] = "0"*64
        checked = verify_preparation_export(rehash(value))
        self.assertFalse(checked["authenticated"])
        self.assertNotEqual(checked["export_sha256"], verify_preparation_export(self.blocked)["export_sha256"])
        self.assertTrue(any("self-consistent rewrite" in text for text in checked["limitations"]))

    def test_real_store_exports_cover_valid_empty_duplicate_unexpected_and_bad_pages(self):
        fixture = fixtures.BatchInputTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        store = BatchAttemptStore(fixture.root/".run/offline-verifier.sqlite3", fixture.root)
        actor = Actor("synthetic-analyst", "synthetic-tenant", "analyst")
        cases = []
        cases.append(deepcopy(fixture.value))
        empty = deepcopy(fixture.value); empty["pages"] = []; cases.append(empty)
        duplicate = deepcopy(fixture.value); duplicate["pages"].append(deepcopy(duplicate["pages"][0])); cases.append(duplicate)
        extra = deepcopy(fixture.value); page = deepcopy(extra["pages"][0]); page["ocr_input"]["page"] = 999; extra["pages"].append(page); cases.append(extra)
        for mode in ("empty", "missing_words", "bad_quad", "bad_document", "bad_page"):
            value = deepcopy(fixture.value); page = value["pages"][0]
            if mode == "empty": page["ocr_input"]["result"] = dict(boxes=[], txts=[], scores=[], word_results=[])
            if mode == "missing_words": page["ocr_input"]["result"]["word_results"] = [[]]
            if mode == "bad_quad": page["ocr_input"]["result"]["boxes"][0][2][0] = -1
            if mode == "bad_document": page["document_id"] = "other"
            if mode == "bad_page": value["pages"][0] = None
            cases.append(value)
        exports = []
        for i, value in enumerate(cases):
            job = store.create(actor, value, f"case-{i}")
            exported = store.export(job["id"], actor); exports.append(exported)
            with self.subTest(case=i):
                result = verify_preparation_export(exported)
                self.assertEqual(result["preparation_status"], "prepared_not_indexed" if i == 0 else "blocked")
        # Same raw page hash cannot produce two contradictory successful counts.
        duplicated = deepcopy(exports[2]); duplicated["attempt"]["report"]["page_results"][2]["summary"]["words"] = 2
        self.refuse(rehash(duplicated), "binding_mismatch")
        # A missing-word diagnostic on a one-parent page cannot still have words.
        missing = deepcopy(exports[5]); missing["attempt"]["report"]["page_results"][0]["summary"]["words"] = 1
        self.refuse(rehash(missing), "page_state_mismatch")
