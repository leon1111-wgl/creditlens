"""Offline unsigned-export tests; workflow identities are synthetic."""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.explanation_evidence import (
    ExplanationEvidenceError, SUPPORT_SCOPE, build_explanation_evidence, evidence_sha256, validate_explanation_export,
)
from scripts.verify_explanation_export import main, verify_file, MAX_FILE_BYTES
from tests.test_explanation_evidence import parent_fixture, decision_fixture


def export_fixture(decision="adopt_as_company_statement"):
    parent, exported = parent_fixture()
    evidence = build_explanation_evidence(parent, exported)
    binding = evidence["parent"]
    content = {"schema_version": 1, "store_version": "explanation-review-1", "attachment_id": "synthetic-supplement",
               "tenant_id": binding["tenant_id"], "task_revision": 3, "submitted_by": "supplement-analyst",
               "submitted_at": "2026-10-25T14:00:00+00:00", "created_by": "supplement-analyst", "contributors": ["supplement-analyst"],
               "parent_task_id": binding["task_id"], "parent_revision": binding["task_revision"], "parent_snapshot_sha256": binding["snapshot_sha256"],
               "evidence": evidence, "evidence_sha256": evidence_sha256(evidence), "decisions": decision_fixture(evidence, decision),
               "coverage_note": "合成附件示范，只收录公司陈述，不验证原因。", "support_scope": SUPPORT_SCOPE,
               "independently_verified": False, "automatic_approval": False}
    snapshot = {"id": "synthetic-snapshot", "sha256": evidence_sha256(content), "created_at": content["submitted_at"],
                "submitted_by": content["submitted_by"], "revision": 3, "content": content}
    review = {"actor": "supplement-reviewer", "at": "2026-10-25T14:01:00+00:00", "decision": "approve",
              "note": "合成复核：确认收录范围和待补记录，不证实原因。", "revision": 4,
              "snapshot_id": snapshot["id"], "snapshot_sha256": snapshot["sha256"]}
    return {"schema_version": 1, "export_type": "explanation_review", "attachment_id": content["attachment_id"],
            "snapshot": snapshot, "review": deepcopy(review), "review_history": [review], "editable_draft_included": False,
            "support_scope": SUPPORT_SCOPE, "independently_verified": False, "automatic_approval": False,
            "scope": "Synthetic example of frozen company-statement decisions; no independent causal verification"}


def rehash(document):
    content = document["snapshot"]["content"]
    content["evidence_sha256"] = evidence_sha256(content["evidence"])
    document["snapshot"]["sha256"] = evidence_sha256(content)
    for review in document["review_history"]:
        review["snapshot_sha256"] = document["snapshot"]["sha256"]
    document["review"] = deepcopy(document["review_history"][-1]) if document["review_history"] else None


class ExplanationExportTests(unittest.TestCase):
    def setUp(self):
        self.document = export_fixture()

    def reject(self, code, document=None):
        with self.assertRaises(ExplanationEvidenceError) as raised:
            validate_explanation_export(document if document is not None else self.document)
        self.assertEqual(raised.exception.code, code)

    def test_all_three_dispositions_survive_offline_review(self):
        for kind in ("adopt_as_company_statement", "rejected", "needs_evidence"):
            result = validate_explanation_export(export_fixture(kind))
            self.assertTrue(result["ok"])
            self.assertEqual(result["decision_counts"][kind], 1)
            self.assertFalse(result["independently_verified"])

    def test_no_live_files_or_registry_used(self):
        with patch("creditlens.company_attribution.load_company_attribution_catalog", side_effect=AssertionError("live file")):
            self.assertTrue(validate_explanation_export(self.document)["ok"])

    def test_snapshot_and_evidence_digests_checked(self):
        self.document["snapshot"]["content"]["coverage_note"] += " changed"
        self.reject("snapshot_hash_mismatch")
        self.document = export_fixture()
        self.document["snapshot"]["content"]["evidence_sha256"] = "a" * 64
        self.document["snapshot"]["sha256"] = evidence_sha256(self.document["snapshot"]["content"])
        self.reject("evidence_hash_mismatch")

    def test_self_rehashed_nested_fake_source_still_refused(self):
        self.document["snapshot"]["content"]["evidence"]["attribution"]["entries"][0]["source"]["url"] = "https://example.invalid/fake"
        rehash(self.document)
        self.reject("source_evidence_mismatch")

    def test_self_rehashed_promoted_flags_and_extra_approval_refused(self):
        self.document["snapshot"]["content"]["independently_verified"] = True
        rehash(self.document)
        self.reject("support_scope_violation")
        self.document = export_fixture()
        self.document["review"]["verified_cause"] = True
        self.document["review_history"][-1]["verified_cause"] = True
        self.reject("invalid_fields")

    def test_parent_and_child_contributors_cannot_review(self):
        content = self.document["snapshot"]["content"]
        actors = [content["contributors"][0], content["evidence"]["parent_export"]["snapshot"]["content"]["contributors"][0]]
        for actor in actors:
            document = deepcopy(self.document)
            document["review_history"][-1]["actor"] = actor
            document["review"] = deepcopy(document["review_history"][-1])
            self.reject("reviewer_not_independent", document)

    def test_missing_contributor_and_tenant_rebinding_refused(self):
        self.document["snapshot"]["content"]["contributors"] = ["other"]
        rehash(self.document)
        self.reject("invalid_contributors")
        self.document = export_fixture()
        self.document["snapshot"]["content"]["tenant_id"] = "other-tenant"
        rehash(self.document)
        self.reject("parent_binding_mismatch")

    def test_review_binding_order_and_selected_review_refused(self):
        document = deepcopy(self.document)
        document["review_history"][0]["snapshot_sha256"] = "0" * 64
        self.reject("review_binding_mismatch", document)
        document = deepcopy(self.document)
        document["review_history"][0]["revision"] = document["snapshot"]["revision"]
        self.reject("review_revision_mismatch", document)
        document = deepcopy(self.document)
        document["review"] = None
        self.reject("review_history_mismatch", document)

    def test_pending_export_has_no_fake_approval(self):
        self.document.update(review=None, review_history=[])
        self.assertEqual(validate_explanation_export(self.document)["review_count"], 0)
        self.document["review"] = {"decision": "approve"}
        self.reject("review_history_mismatch")

    def test_float_integer_roundtrip_change_detected(self):
        # These real source coordinates are covered by the embedded parent hash.
        raw = json.dumps(self.document, ensure_ascii=False)
        self.assertIn("792.0", raw)
        altered = json.loads(raw.replace("792.0", "792", 1))
        self.reject("snapshot_hash_mismatch", altered)

    def write_file(self, raw):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "explanation.json"
        path.write_bytes(raw)
        return path

    def test_file_and_cli_emit_small_result(self):
        path = self.write_file(json.dumps(self.document, ensure_ascii=False).encode("utf-8"))
        self.assertTrue(verify_file(path)["ok"])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([str(path)]), 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["ok"])
        self.assertNotIn("parent_export", result)

    def test_duplicate_nonfinite_and_oversize_files_refused(self):
        for raw, code in ((b'{"a":1,"a":2}', "duplicate_json_key"), (b'{"a":NaN}', "nonfinite_number"),
                          (b'{"a":1e999}', "nonfinite_number"), (b' ' * (MAX_FILE_BYTES + 1), "file_too_large")):
            with self.subTest(code=code):
                path = self.write_file(raw)
                with self.assertRaises(ExplanationEvidenceError) as raised:
                    verify_file(path)
                self.assertEqual(raised.exception.code, code)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([str(self.write_file(b'{"a":NaN}'))]), 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])


if __name__ == "__main__":
    unittest.main()
