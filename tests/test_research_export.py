"""Offline export checks with synthetic Chinese, signed and float evidence."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts import verify_research_export as verifier


ROOT = Path(__file__).resolve().parents[1]


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def export_fixture(reviewed=True):
    bundle = {"id": "synthetic", "observations": [{"page_height": 792.0, "value": -123.5, "label": "经营现金流（合成测试）"}]}
    content = {
        "schema_version": 1, "store_version": "research-handoff-1", "task_id": "task-a", "tenant_id": "tenant-a",
        "task_revision": 3, "submitted_by": "analyst", "submitted_at": "2026-10-25T10:00:00+00:00",
        "question": "现金流如何变化？", "selection_reason": "合成测试，不代表真实披露",
        "material_mode": "current_verified_materials", "as_of": None, "bundle_id": "synthetic", "bundle": bundle,
        "bundle_sha256": sha(bundle), "materials_frozen_at": "2026-10-25T09:00:00+00:00",
        "decisions": [], "hypotheses": [], "hypothesis_scope": "Associated material only",
        "coverage_note": "只检查导出内部一致性，不验证财务内容。", "comparison": {"absolute_change": "-2"},
        "created_by": "analyst", "contributors": ["analyst"], "automatic_approval": False,
    }
    snapshot = {"id": "snapshot-a", "sha256": sha(content), "revision": 3, "created_at": content["submitted_at"],
                "submitted_by": "analyst", "content": content}
    review = {"actor": "reviewer", "at": "2026-10-25T10:01:00+00:00", "decision": "approve", "note": "已复核（合成）",
              "revision": 4, "snapshot_id": "snapshot-a", "snapshot_sha256": snapshot["sha256"]} if reviewed else None
    return {"schema_version": 1, "task_id": "task-a", "snapshot": snapshot, "review": review,
            "review_history": [deepcopy(review)] if reviewed else [], "editable_draft_included": False,
            "automatic_approval": False, "scope": "Frozen research content and its recorded review"}


class ResearchExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "export.json"

    def write(self, document):
        self.path.write_text(json.dumps(document, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        return self.path

    def test_valid_reviewed_export_keeps_chinese_float_and_negative(self):
        fixture = export_fixture()
        self.write(fixture)
        self.assertIn('792.0', self.path.read_text(encoding="utf-8"))
        self.assertIn('-123.5', self.path.read_text(encoding="utf-8"))
        result = verifier.verify_file(self.path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["content_sha256"], fixture["snapshot"]["sha256"])
        self.assertEqual(result["review_count"], 1)
        self.assertIn("no signature", result["scope"])

    def test_submitted_without_review_is_valid_and_not_reported_as_approved(self):
        result = verifier.verify_file(self.write(export_fixture(False)))
        self.assertTrue(result["ok"])
        self.assertEqual(result["review_count"], 0)
        self.assertNotIn("approved", result)

    def test_python_roundtrip_and_whitespace_do_not_change_canonical_hash(self):
        fixture = json.loads(json.dumps(export_fixture(), ensure_ascii=True))
        self.path.write_bytes(json.dumps(fixture, indent=2, ensure_ascii=False).replace("\n", "\r\n").encode("utf-8"))
        self.assertTrue(verifier.verify_file(self.path)["ok"])

    def test_javascript_integer_float_rewrite_breaks_snapshot_hash(self):
        fixture = export_fixture()
        original = json.dumps(fixture, ensure_ascii=False, separators=(",", ":"))
        self.assertIn('"page_height":792.0', original)
        # JSON.stringify(JSON.parse(...)) emits 792, changing Python canonical bytes.
        rewritten = json.loads(original)
        rewritten["snapshot"]["content"]["bundle"]["observations"][0]["page_height"] = 792
        with self.assertRaisesRegex(verifier.ExportError, "Snapshot content SHA"):
            verifier.verify_file(self.write(rewritten))

    def test_changed_content_without_rehash_is_rejected(self):
        fixture = export_fixture()
        fixture["snapshot"]["content"]["question"] += "被修改"
        with self.assertRaises(verifier.ExportError) as ctx:
            verifier.verify_file(self.write(fixture))
        self.assertEqual(ctx.exception.code, "snapshot_hash_mismatch")

    def test_wrong_task_or_metadata_binding_is_rejected(self):
        mutations = [
            lambda x: x.update(task_id="other-task"),
            lambda x: x["snapshot"].update(revision=5),
            lambda x: x["snapshot"].update(submitted_by="other-analyst"),
            lambda x: x["snapshot"].update(created_at="2026-10-25T11:00:00+00:00"),
        ]
        for change in mutations:
            fixture = export_fixture()
            change(fixture)
            with self.subTest(change=change), self.assertRaises(verifier.ExportError) as ctx:
                verifier.verify_file(self.write(fixture))
            self.assertEqual(ctx.exception.code, "snapshot_binding_mismatch")

    def test_review_id_and_hash_must_bind_to_selected_snapshot(self):
        for key, value in (("snapshot_id", "other-snapshot"), ("snapshot_sha256", "0" * 64)):
            fixture = export_fixture()
            fixture["review"][key] = value
            fixture["review_history"][0][key] = value
            with self.subTest(key=key), self.assertRaises(verifier.ExportError) as ctx:
                verifier.verify_file(self.write(fixture))
            self.assertEqual(ctx.exception.code, "review_binding_mismatch")

    def test_selected_review_must_equal_history_tail(self):
        fixture = export_fixture()
        fixture["review"]["note"] = "different note"
        with self.assertRaises(verifier.ExportError) as ctx:
            verifier.verify_file(self.write(fixture))
        self.assertEqual(ctx.exception.code, "review_history_mismatch")

    def test_review_revision_must_follow_snapshot(self):
        fixture = export_fixture()
        fixture["review_history"][0]["revision"] = 3
        fixture["review"] = fixture["review_history"][0]
        with self.assertRaises(verifier.ExportError) as ctx:
            verifier.verify_file(self.write(fixture))
        self.assertEqual(ctx.exception.code, "review_revision_mismatch")

    def test_bundle_hash_is_checked_even_when_snapshot_rehashed(self):
        fixture = export_fixture(False)
        fixture["snapshot"]["content"]["bundle_sha256"] = "0" * 64
        fixture["snapshot"]["sha256"] = sha(fixture["snapshot"]["content"])
        with self.assertRaises(verifier.ExportError) as ctx:
            verifier.verify_file(self.write(fixture))
        self.assertEqual(ctx.exception.code, "bundle_hash_mismatch")

    def test_hash_is_not_a_signature_and_rehashed_self_consistent_content_can_pass(self):
        fixture = export_fixture(False)
        fixture["snapshot"]["content"]["question"] = "自洽替换仍可能通过，这不是数字签名"
        fixture["snapshot"]["sha256"] = sha(fixture["snapshot"]["content"])
        result = verifier.verify_file(self.write(fixture))
        self.assertTrue(result["ok"])
        self.assertIn("no signature", result["scope"])

    def test_missing_unknown_and_mistyped_fields_fail(self):
        mutations = [
            lambda x: x.pop("review_history"),
            lambda x: x.update(tenant_id="untrusted-outer-identity"),
            lambda x: x.update(schema_version=True),
            lambda x: x.update(editable_draft_included=True),
            lambda x: x.update(automatic_approval=0),
            lambda x: x["snapshot"].update(id=None),
            lambda x: x["snapshot"].update(revision=True),
            lambda x: x["snapshot"].update(sha256="not-a-hash"),
            lambda x: x["snapshot"]["content"].update(tenant_id=""),
            lambda x: x["snapshot"]["content"].update(submitted_at="2026-10-25T10:00:00"),
            lambda x: x["snapshot"]["content"].update(contributors=["analyst", "analyst"]),
            lambda x: x.update(review_history={}),
        ]
        for change in mutations:
            fixture = export_fixture()
            change(fixture)
            with self.subTest(change=change), self.assertRaises(verifier.ExportError):
                verifier.verify_file(self.write(fixture))

    def test_duplicate_keys_at_any_depth_fail_before_hash_check(self):
        raw = json.dumps(export_fixture(), ensure_ascii=False, separators=(",", ":"))
        cases = [raw.replace('"schema_version":1', '"schema_version":1,"schema_version":1', 1),
                 raw.replace('"page_height":792.0', '"page_height":792.0,"page_height":792.0')]
        for value in cases:
            self.path.write_text(value, encoding="utf-8")
            with self.assertRaises(verifier.ExportError) as ctx:
                verifier.verify_file(self.path)
            self.assertEqual(ctx.exception.code, "duplicate_json_key")

    def test_nonfinite_literals_and_exponent_overflow_fail(self):
        raw = json.dumps(export_fixture(), ensure_ascii=False, separators=(",", ":"))
        for value in ("NaN", "Infinity", "-Infinity", "1e400"):
            self.path.write_text(raw.replace('792.0', value), encoding="utf-8")
            with self.subTest(value=value), self.assertRaises(verifier.ExportError) as ctx:
                verifier.verify_file(self.path)
            self.assertEqual(ctx.exception.code, "nonfinite_number")
        direct = export_fixture()
        direct["snapshot"]["content"]["bundle"]["observations"][0]["page_height"] = float("nan")
        with self.assertRaises(verifier.ExportError):
            verifier.verify_export(direct)

    def test_size_missing_file_bad_utf8_and_invalid_json_fail(self):
        self.path.write_bytes(b" " * (verifier.MAX_FILE_BYTES + 1))
        with self.assertRaises(verifier.ExportError) as ctx:
            verifier.verify_file(self.path)
        self.assertEqual(ctx.exception.code, "file_too_large")
        for value in (b"\xff", b"{broken", b"[" * 3000 + b"]" * 3000):
            self.path.write_bytes(value)
            with self.assertRaises(verifier.ExportError):
                verifier.verify_file(self.path)
        with self.assertRaises(verifier.ExportError):
            verifier.verify_file(self.path.parent / "missing.json")

    def test_cli_runs_without_application_import_and_failure_exits_nonzero(self):
        self.write(export_fixture())
        script = ROOT / "scripts/verify_research_export.py"
        result = subprocess.run([sys.executable, "-I", str(script), str(self.path)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["ok"])
        self.path.write_text('{}', encoding="utf-8")
        result = subprocess.run([sys.executable, "-I", str(script), str(self.path)], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertEqual(result.stderr, "")

    def test_main_has_concise_structured_failure(self):
        self.path.write_text('{}', encoding="utf-8")
        output = StringIO()
        with redirect_stdout(output):
            status = verifier.main([str(self.path)])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["code"], "invalid_fields")


if __name__ == "__main__":
    unittest.main()
