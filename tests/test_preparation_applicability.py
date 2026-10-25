"""Historical verification stays separate from a current metadata check."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens import preparation_applicability as applicability
from creditlens.asset_preflight import AssetPreflightError, preflight_transfer_assets
from creditlens.diligence import digest
from creditlens.preparation_verifier import verify_preparation_export
from tests import test_batch_attempt_store as fixtures


class PreparationApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.BatchAttemptStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.store = self.fixture.store
        self.transfer = self.fixture.fixture.fixture
        self.job = self.store.create(fixtures.ANALYST, self.fixture.good, "ready")
        self.export = self.store.export(self.job["id"], fixtures.VIEWER)
        self.verified = verify_preparation_export(self.export)

    def assess(self, verified=None):
        return applicability.assess_preparation_applicability(self.root, self.verified if verified is None else verified)

    def add_other_document(self):
        pack = deepcopy(self.transfer.pack)
        pack.update(entity_id="another-issuer", company="Another synthetic issuer")
        pack["sources"][0]["sha256"] = sha256(b"different fixture PDF").hexdigest()
        doc = deepcopy(self.transfer.manifest["documents"][0])
        doc.update(id="another-doc", pack_path="data/examples/another-document.json")
        doc["pdf"]["sha256"] = pack["sources"][0]["sha256"]
        file = self.root/doc["pack_path"]
        self.transfer.write(file, pack)
        doc["pack_sha256"] = sha256(file.read_bytes()).hexdigest()
        self.transfer.manifest["documents"].append(doc)
        self.transfer.save_manifest()

    def test_matching_registration_only_readies_prepared_content_review(self):
        before = deepcopy(self.verified)
        result = self.assess()
        self.assertEqual(result["status"], "matching_registration")
        self.assertEqual(result["reason_code"], "matching_registration")
        self.assertTrue(result["ready_for_content_review"])
        self.assertTrue(result["registration_stable"])
        self.assertTrue(result["versions_match"])
        self.assertEqual(result["changed_fields"], [])
        self.assertEqual(result["historical_preparation_status"], "prepared_not_indexed")
        self.assertEqual(result["historical"]["material_binding"], result["current"]["material_binding"])
        for name in ("task_id", "attempt_id", "revision", "report_sha256", "export_sha256"):
            self.assertEqual(result[name], self.verified[name])
        self.assertEqual(result, self.assess())
        self.assertEqual(before, self.verified)
        self.assertEqual(self.export, self.store.export(self.job["id"], fixtures.VIEWER))
        for name in ("authenticated", "source_bytes_verified", "ocr_quality_verified", "financial_content_verified", "queryable", "automatic_approval"):
            self.assertIs(result["scope"][name], False)

    def test_matching_current_registration_never_promotes_old_blocked_attempt(self):
        job = self.store.create(fixtures.ANALYST, self.fixture.bad, "blocked")
        old_export = self.store.export(job["id"], fixtures.VIEWER)
        verified = verify_preparation_export(old_export)
        result = self.assess(verified)
        self.assertEqual(result["status"], "matching_registration")
        self.assertEqual(result["reason_code"], "historical_preparation_blocked")
        self.assertFalse(result["ready_for_content_review"])
        self.assertEqual(result["historical_preparation_status"], "blocked")
        self.assertEqual(old_export, self.store.export(job["id"], fixtures.VIEWER))

    def test_unrelated_manifest_description_change_is_inventory_only(self):
        self.transfer.manifest["selection_reason"] += " Another selection note."
        self.transfer.save_manifest()
        result = self.assess()
        self.assertEqual(result["status"], "inventory_changed")
        self.assertEqual(result["reason_code"], "global_inventory_changed")
        self.assertFalse(result["ready_for_content_review"])
        self.assertEqual(set(result["changed_fields"]), {"material_binding.manifest_sha256", "material_binding.inventory_sha256"})
        self.assertEqual(result["current"]["material_binding"]["source_pdf_sha256"], self.verified["material_binding"]["source_pdf_sha256"])

    def test_unrelated_document_added_does_not_claim_selected_pdf_changed(self):
        self.add_other_document()
        result = self.assess()
        self.assertEqual(result["status"], "inventory_changed")
        self.assertNotIn("material_binding.registered_document_sha256", result["changed_fields"])
        self.assertNotIn("material_binding.source_pdf_sha256", result["changed_fields"])
        self.assertFalse(result["ready_for_content_review"])

    def test_selected_pack_edit_is_registration_change_not_pdf_authentication(self):
        self.transfer.pack["title"] += " Revised reference title."
        self.transfer.save_pack(); self.transfer.save_manifest()
        result = self.assess()
        self.assertEqual(result["status"], "document_registration_changed")
        self.assertIn("material_binding.pack_sha256", result["changed_fields"])
        self.assertIn("material_binding.pack_canonical_sha256", result["changed_fields"])
        self.assertIn("material_binding.registered_document_sha256", result["changed_fields"])
        self.assertNotIn("material_binding.source_pdf_sha256", result["changed_fields"])
        self.assertFalse(result["scope"]["source_bytes_verified"])

    def test_selected_pdf_identity_change_is_visible(self):
        changed = sha256(b"updated fixture PDF declaration").hexdigest()
        self.transfer.pack["sources"][0]["sha256"] = changed
        self.transfer.manifest["documents"][0]["pdf"]["sha256"] = changed
        self.transfer.save_pack(); self.transfer.save_manifest()
        result = self.assess()
        self.assertEqual(result["status"], "document_registration_changed")
        self.assertIn("material_binding.source_pdf_sha256", result["changed_fields"])
        self.assertFalse(result["ready_for_content_review"])

    def test_selected_document_absent_only_when_current_inventory_is_valid(self):
        self.add_other_document()
        self.transfer.manifest["documents"].pop(0)
        self.transfer.save_manifest()
        result = self.assess()
        self.assertEqual(result["status"], "document_not_registered")
        self.assertEqual(result["reason_code"], "document_not_in_current_registration")
        self.assertTrue(result["registration_stable"])
        self.assertIsNone(result["current"]["material_binding"])
        self.assertIsNotNone(result["current"]["inventory_sha256"])
        self.assertFalse(result["ready_for_content_review"])

    def test_missing_or_bad_current_manifest_does_not_invalidate_historical_export(self):
        original = deepcopy(self.export)
        self.transfer.manifest_path.unlink()
        missing = self.assess()
        self.assertEqual(missing["status"], "registration_unavailable")
        self.assertEqual(missing["reason_code"], "current_registration_unavailable")
        self.assertEqual(missing["historical_preparation_status"], "prepared_not_indexed")
        self.assertIsNone(missing["current"]["material_binding"])
        self.transfer.manifest_path.write_text('{"schema_version":NaN}', encoding="utf-8")
        malformed = self.assess()
        self.assertEqual(malformed["status"], "registration_unavailable")
        self.assertEqual(self.export, original)
        self.assertEqual(verify_preparation_export(original), self.verified)

    def test_corrupt_pack_is_unavailable_not_document_removed(self):
        file = self.root/self.transfer.manifest["documents"][0]["pack_path"]
        file.write_bytes(file.read_bytes()+b"\n")
        result = self.assess()
        self.assertEqual(result["status"], "registration_unavailable")
        self.assertFalse(result["ready_for_content_review"])
        self.assertEqual(result["report_sha256"], self.verified["report_sha256"])

    def test_version_mismatch_does_not_falsely_label_registration_changed(self):
        with patch.object(applicability, "OCR_INPUT_VERSION", "ocr-input-future"):
            result = self.assess()
        self.assertEqual(result["status"], "matching_registration")
        self.assertEqual(result["reason_code"], "preparation_versions_changed")
        self.assertEqual(result["changed_fields"], ["versions.ocr_input"])
        self.assertFalse(result["versions_match"])
        self.assertFalse(result["ready_for_content_review"])
        with patch.object(applicability, "STORE_VERSION", "store-future"):
            result = self.assess()
        self.assertEqual(result["changed_fields"], ["store_version"])
        self.assertFalse(result["ready_for_content_review"])

    def test_self_consistent_rewritten_expected_pages_cannot_borrow_current_binding(self):
        job = self.store.create(fixtures.ANALYST, self.fixture.bad, "missing-page")
        rewritten = self.store.export(job["id"], fixtures.VIEWER)
        report = rewritten["attempt"]["report"]
        report["expected_pages"] = [report["page_results"][0]["page"]]
        report["missing_pages"] = []
        report["errors"] = []
        report["status"] = "prepared_not_indexed"
        report["summary"]["expected_page_count"] = 1
        rewritten["attempt"]["report_sha256"] = digest(report)
        verified = verify_preparation_export(rewritten)
        result = self.assess(verified)
        self.assertEqual(result["status"], "matching_registration")
        self.assertEqual(result["reason_code"], "historical_page_set_mismatch")
        self.assertIn("expected_pages", result["changed_fields"])
        self.assertFalse(result["ready_for_content_review"])
        self.assertEqual(result["current"]["registered_pages"], self.verified["expected_pages"])

    def test_self_consistent_rewritten_page_image_or_fact_ids_not_currently_applicable(self):
        for field, value in (("image_sha256", "f"*64), ("fact_ids", ["plausible-but-not-registered-fact"])):
            with self.subTest(field=field):
                rewritten = deepcopy(self.export)
                report = rewritten["attempt"]["report"]
                report["page_results"][0]["registration"][field] = value
                rewritten["attempt"]["report_sha256"] = digest(report)
                verified = verify_preparation_export(rewritten)
                result = self.assess(verified)
                self.assertEqual(result["status"], "matching_registration")
                self.assertEqual(result["reason_code"], "historical_page_inventory_mismatch")
                self.assertIn(f"page_registrations[0].{field}", result["changed_fields"])
                self.assertFalse(result["ready_for_content_review"])
    def test_current_registration_change_between_reads_never_returns_stale_green(self):
        calls = []
        def read(root, path):
            value = preflight_transfer_assets(root, path)
            calls.append(value)
            if len(calls) == 1:
                self.transfer.manifest["selection_reason"] += " Change between reads."
                self.transfer.save_manifest()
            return value
        with patch.object(applicability, "preflight_transfer_assets", side_effect=read):
            result = self.assess()
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(digest(calls[0]), digest(calls[1]))
        self.assertEqual(result["status"], "registration_unavailable")
        self.assertEqual(result["reason_code"], "registration_changed_during_assessment")
        self.assertFalse(result["registration_stable"])
        self.assertFalse(result["ready_for_content_review"])
        self.assertIsNone(result["current"]["material_binding"])

    def test_second_read_failure_preserves_historical_result_and_no_partial_binding(self):
        good = preflight_transfer_assets(self.root)
        error = AssetPreflightError("legacy_binding_validation_failed", "manifest", "sensitive local detail")
        with patch.object(applicability, "preflight_transfer_assets", side_effect=[good,error]):
            result = self.assess()
        self.assertEqual(result["reason_code"], "registration_changed_during_assessment")
        self.assertNotIn("sensitive local detail", json.dumps(result))
        self.assertIsNone(result["current"]["inventory_sha256"])
        self.assertEqual(result["historical"], self.assess()["historical"])

    def test_runtime_version_change_during_reads_is_unavailable(self):
        versions = applicability._versions()
        changed = {**versions,"ocr_input":"next-during-check"}
        with patch.object(applicability,"_versions",side_effect=[versions,changed]):
            result = self.assess()
        self.assertEqual(result["reason_code"],"registration_changed_during_assessment")
        self.assertFalse(result["ready_for_content_review"])

    def test_invalid_verified_summary_rejected_before_reading_current_material(self):
        changes = [lambda v:v.update(schema_version=True), lambda v:v.update(revision=True),
                   lambda v:v.update(authenticated=True), lambda v:v.update(raw="not permitted"),
                   lambda v:v.update(material_binding_sha256="0"*64), lambda v:v.update(status="unchecked"),
                   lambda v:v["expected_pages"].append(v["expected_pages"][0])]
        for change in changes:
            value=deepcopy(self.verified);change(value)
            with patch.object(applicability,"preflight_transfer_assets",side_effect=AssertionError("must validate first")):
                with self.assertRaises(applicability.PreparationApplicabilityError):
                    self.assess(value)

    def test_assessment_is_read_only_and_root_relative_manifest_supported(self):
        paths = [self.transfer.manifest_path, self.root/self.transfer.manifest["documents"][0]["pack_path"], self.fixture.path]
        before = {path:path.read_bytes() for path in paths}
        first = self.assess()
        second = applicability.assess_preparation_applicability(self.root,self.verified,Path("configs/document-transfer.json"))
        self.assertEqual(first,second)
        self.assertEqual(before,{path:path.read_bytes() for path in paths})


if __name__ == "__main__":
    unittest.main()
