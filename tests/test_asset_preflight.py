"""Asset identity controls use small registered fixtures, never PDF downloads."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens import asset_preflight as preflight
from creditlens.document_transfer import load_manifest
from tests import test_document_transfer as fixtures


class AssetPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TransferTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root

    def variant(self):
        """A second registered PDF version of the same issuer, not a new company."""
        pack = deepcopy(self.fixture.pack)
        pack["sources"][0]["sha256"] = sha256(b"different synthetic PDF version").hexdigest()
        doc = deepcopy(self.fixture.manifest["documents"][0])
        doc.update(id="testco-revision", pack_path="data/examples/independent-revision.json")
        doc["pdf"]["sha256"] = pack["sources"][0]["sha256"]
        path = self.root/doc["pack_path"]
        self.fixture.write(path, pack)
        doc["pack_sha256"] = sha256(path.read_bytes()).hexdigest()
        self.fixture.manifest["documents"].append(doc)
        self.fixture.save_manifest()
        return pack, doc

    def assert_code(self, code, call):
        with self.assertRaises(preflight.AssetPreflightError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.path)
        return caught.exception

    def test_real_loader_duplicate_pdf_alias_is_now_rejected(self):
        duplicate = deepcopy(self.fixture.manifest["documents"][0])
        duplicate["id"] = "same-content-new-name"
        self.fixture.manifest["documents"].append(duplicate)
        self.fixture.save_manifest()
        frozen, packs = load_manifest(self.root)
        self.assertEqual(len(frozen.documents), 2)  # Demonstrates the legacy gap.
        self.assertEqual(len(packs), 2)
        error = self.assert_code("duplicate_pdf_content", lambda: preflight.preflight_transfer_assets(self.root))
        self.assertEqual(error.path, "documents[1].pdf.sha256")

    def test_wrapper_counts_and_mapping_without_pdf_or_image_reads(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = preflight.preflight_transfer_assets(self.root)
        self.assertEqual(result["counts"], {"documents": 1, "unique_pdf_assets": 1, "unique_reference_packs": 1,
                         "unique_source_pages": 1, "unique_page_images": 1, "unique_fact_evidence": 1, "unique_issuers": 1})
        self.assertEqual(result["manifest_sha256"], sha256(self.fixture.manifest_path.read_bytes()).hexdigest())
        self.assertTrue(result["validation"]["registered_files_checked"])
        self.assertTrue(result["validation"]["strict_json_checked"])
        self.assertTrue(result["validation"]["path_aliases_checked"])
        self.assertFalse(result["validation"]["pdf_bytes_checked"])
        doc = result["documents"][0]
        self.assertEqual(doc["source_pdf_sha256"], self.fixture.pack["sources"][0]["sha256"])
        self.assertEqual(doc["pages"][0]["fact_ids"], [self.fixture.pack["facts"][0]["id"]])
        serialized = json.dumps(result)
        self.assertNotIn('"raw_value"', serialized)
        self.assertNotIn('"value"', serialized)
        self.assertEqual(before, {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_same_issuer_different_pdf_versions_and_shared_page_content_allowed(self):
        self.variant()
        result = preflight.preflight_transfer_assets(self.root)
        self.assertEqual(result["counts"]["documents"], 2)
        self.assertEqual(result["counts"]["unique_issuers"], 1)
        self.assertEqual(result["counts"]["unique_pdf_assets"], 2)
        self.assertEqual(result["counts"]["unique_source_pages"], 2)
        self.assertEqual(result["counts"]["unique_page_images"], 1)
        self.assertEqual(len(result["same_page_content_groups"]), 1)
        self.assertEqual(len(result["same_page_content_groups"][0]["registrations"]), 2)

    def test_equal_values_on_distinct_cells_are_not_duplicates(self):
        new = deepcopy(self.fixture.pack["facts"][0])
        new["id"] += "-another-cell"
        new["pdf_location"]["amount_bbox"]["x0"] -= 30
        new["pdf_location"]["amount_bbox"]["x1"] -= 30
        self.fixture.pack["facts"].append(new)
        self.fixture.manifest["documents"][0]["pages"][0]["fact_ids"].append(new["id"])
        self.fixture.save_pack(); self.fixture.save_manifest()
        result = preflight.preflight_transfer_assets(self.root)
        self.assertEqual(result["counts"]["unique_fact_evidence"], 2)

    def test_identical_cell_renamed_fact_rejected_after_legacy_loading(self):
        new = deepcopy(self.fixture.pack["facts"][0]); new["id"] += "-alias"
        self.fixture.pack["facts"].append(new)
        self.fixture.manifest["documents"][0]["pages"][0]["fact_ids"].append(new["id"])
        self.fixture.save_pack(); self.fixture.save_manifest()
        load_manifest(self.root)
        self.assert_code("duplicate_fact_evidence", lambda: preflight.preflight_transfer_assets(self.root))

    def test_pure_inspection_does_not_claim_disk_validation(self):
        manifest, packs = load_manifest(self.root)
        first = preflight.inspect_transfer_assets(manifest, packs)
        second = preflight.inspect_transfer_assets(manifest.model_dump(mode="json"), {k:v.model_dump(mode="json") for k,v in packs.items()})
        self.assertEqual(first, second)
        self.assertIsNone(first["manifest_sha256"])
        self.assertFalse(any(v for k,v in first["validation"].items() if k != "mode"))
        with patch.object(preflight, "load_manifest", side_effect=AssertionError("Pure inspection must not load files")):
            self.assertEqual(first, preflight.inspect_transfer_assets(manifest, packs))

    def test_pure_duplicate_pack_paths_bytes_and_canonical_content(self):
        self.variant()
        manifest, packs = load_manifest(self.root)
        original = manifest.model_dump(mode="json")
        for field, code in (("pack_path", "duplicate_pack_path"), ("pack_sha256", "duplicate_pack_raw_content")):
            changed = deepcopy(original); changed["documents"][1][field] = changed["documents"][0][field]
            self.assert_code(code, lambda: preflight.inspect_transfer_assets(changed, packs))
        # Parsed-only controls make no claim that these two raw hash declarations
        # were checked on disk: identical semantic objects still cannot count twice.
        same_packs = dict(packs); same_packs["testco-revision"] = packs["testco"]
        self.assert_code("duplicate_pack_content", lambda: preflight.inspect_transfer_assets(original, same_packs))

    def test_resolved_alias_claims_rejected_without_exposing_machine_path(self):
        self.variant()
        manifest, packs = load_manifest(self.root)
        aliases = {doc.id: "D:/private-machine/reference.json" for doc in manifest.documents}
        error = self.assert_code("duplicate_pack_path_alias", lambda: preflight.inspect_transfer_assets(manifest, packs, resolved_pack_paths=aliases))
        self.assertNotIn("private-machine", str(error))
        self.assert_code("path_inventory_mismatch", lambda: preflight.inspect_transfer_assets(manifest, packs, resolved_pack_paths={}))

    def test_pure_duplicate_page_and_fact_registration_or_missing_fact(self):
        manifest, packs = load_manifest(self.root)
        base = manifest.model_dump(mode="json")
        duplicate_page = deepcopy(base)
        duplicate_page["documents"][0]["pages"].append(deepcopy(duplicate_page["documents"][0]["pages"][0]))
        self.assert_code("duplicate_page_registration", lambda: preflight.inspect_transfer_assets(duplicate_page, packs))
        duplicate_fact = deepcopy(base); duplicate_fact["documents"][0]["pages"][0]["fact_ids"] *= 2
        self.assert_code("duplicate_fact_registration", lambda: preflight.inspect_transfer_assets(duplicate_fact, packs))
        unknown = deepcopy(base); unknown["documents"][0]["pages"][0]["fact_ids"] = ["unknown"]
        self.assert_code("unknown_fact", lambda: preflight.inspect_transfer_assets(unknown, packs))
        p = packs["testco"].model_dump(mode="json"); extra = deepcopy(p["facts"][0]); extra["id"] += "-missing"; p["facts"].append(extra)
        self.assert_code("fact_inventory_mismatch", lambda: preflight.inspect_transfer_assets(base, {"testco":p}))

    def test_pure_wrong_source_dimension_or_pack_set_rejected(self):
        manifest, packs = load_manifest(self.root)
        self.assert_code("pack_inventory_mismatch", lambda: preflight.inspect_transfer_assets(manifest, {}))
        m = manifest.model_dump(mode="json"); m["documents"][0]["pdf"]["sha256"] = "f"*64
        self.assert_code("source_binding_mismatch", lambda: preflight.inspect_transfer_assets(m, packs))
        m = manifest.model_dump(mode="json"); m["documents"][0]["pages"][0]["width"] += 1
        self.assert_code("fact_binding_mismatch", lambda: preflight.inspect_transfer_assets(m, packs))
        m = manifest.model_dump(mode="json"); m["schema_version"] = True
        self.assert_code("invalid_inventory", lambda: preflight.inspect_transfer_assets(m, packs))

    def test_wrapper_rejects_strict_duplicate_keys_in_manifest_and_pack(self):
        path = self.fixture.manifest_path
        original = path.read_text(encoding="utf-8")
        path.write_text(original.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1), encoding="utf-8")
        load_manifest(self.root)  # The existing loader's permissive JSON parsing.
        self.assert_code("invalid_registered_json", lambda: preflight.preflight_transfer_assets(self.root))
        path.write_text(original, encoding="utf-8")
        pack_path = self.root/self.fixture.manifest["documents"][0]["pack_path"]
        pack_path.write_text(pack_path.read_text(encoding="utf-8").replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1), encoding="utf-8")
        self.fixture.manifest["documents"][0]["pack_sha256"] = sha256(pack_path.read_bytes()).hexdigest()
        self.fixture.save_manifest()
        load_manifest(self.root)
        self.assert_code("invalid_registered_json", lambda: preflight.preflight_transfer_assets(self.root))

    def test_changed_manifest_or_pack_between_reads_fails_closed(self):
        original_load = load_manifest
        def mutate_manifest(root, path):
            result = original_load(root, path)
            path.write_bytes(path.read_bytes()+b"\n")
            return result
        with patch.object(preflight, "load_manifest", side_effect=mutate_manifest):
            self.assert_code("source_changed_during_preflight", lambda: preflight.preflight_transfer_assets(self.root))
        def mutate_pack(root, path):
            result = original_load(root, path)
            p = root/self.fixture.manifest["documents"][0]["pack_path"]
            p.write_bytes(p.read_bytes()+b"\n")
            return result
        with patch.object(preflight, "load_manifest", side_effect=mutate_pack):
            self.assert_code("source_changed_during_preflight", lambda: preflight.preflight_transfer_assets(self.root))

    def test_nonfinite_or_changed_frozen_dependency_is_rejected(self):
        path = self.root/"creditlens/word_evidence.py"
        path.write_bytes(path.read_bytes()+b"\n")
        self.assert_code("legacy_binding_validation_failed", lambda: preflight.preflight_transfer_assets(self.root))
        path = self.fixture.manifest_path
        path.write_text('{"value":NaN}', encoding="utf-8")
        self.assert_code("invalid_registered_json", lambda: preflight._strict_read(path, 10000, "manifest"))

    def test_manifest_path_and_root_relative_selection(self):
        self.assertEqual(preflight.preflight_transfer_assets(self.root),
                         preflight.preflight_transfer_assets(self.root, Path("configs/document-transfer.json")))
        with tempfile.TemporaryDirectory() as external:
            path = Path(external)/"manifest.json"; path.write_bytes(self.fixture.manifest_path.read_bytes())
            self.assert_code("legacy_binding_validation_failed", lambda: preflight.preflight_transfer_assets(self.root, path))


if __name__ == "__main__":
    unittest.main()
