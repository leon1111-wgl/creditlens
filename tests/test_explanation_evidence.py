"""Synthetic attachment workflow around the existing public demonstration parent."""
from copy import deepcopy
from decimal import localcontext
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens.explanation_evidence import (
    ExplanationEvidenceError, SUPPORT_SCOPE, build_explanation_evidence, evidence_sha256,
    validate_explanation_decisions, validate_explanation_evidence,
)
from creditlens.research_materials import build_comparison


ROOT = Path(__file__).resolve().parents[1]


def parent_fixture(reject_cfo=False):
    exported = json.loads((ROOT / "artifacts/research-cash-flow-demo.json").read_text(encoding="utf-8"))
    if reject_cfo:
        content = exported["snapshot"]["content"]
        oid = next(o["id"] for o in content["bundle"]["observations"] if o["issuer_id"] == "MSFT" and o["metric"] == "cfo")
        next(d for d in content["decisions"] if d["observation_id"] == oid)["decision"] = "rejected"
        content["comparison"] = build_comparison(content["bundle"], content["decisions"])
        exported["snapshot"]["sha256"] = evidence_sha256(content)
        for review in exported["review_history"]:
            review["snapshot_sha256"] = exported["snapshot"]["sha256"]
        exported["review"] = deepcopy(exported["review_history"][-1])
    task = deepcopy(exported["snapshot"]["content"])
    task.update(id=exported["task_id"], revision=exported["review"]["revision"], status="reviewed",
                snapshot_id=exported["snapshot"]["id"], snapshot_sha256=exported["snapshot"]["sha256"],
                snapshot=deepcopy(exported["snapshot"]), review=deepcopy(exported["review"]),
                review_history=deepcopy(exported["review_history"]), include_cash_flow_bridge=True)
    return task, exported


def decision_fixture(evidence, decision="adopt_as_company_statement"):
    return [{"entry_id": evidence["attribution"]["entries"][0]["id"], "decision": decision,
             "note": "合成流程：仅采用公司陈述范围，未证实经营原因。", "support_scope": SUPPORT_SCOPE}]


class ExplanationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.parent, self.export = parent_fixture()
        self.evidence = build_explanation_evidence(self.parent, self.export)

    def reject(self, code, function, *args, **kwargs):
        with self.assertRaises(ExplanationEvidenceError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def test_build_preserves_parent_and_removes_preview_only_semantics(self):
        parent, exported = deepcopy(self.parent), deepcopy(self.export)
        evidence = build_explanation_evidence(self.parent, self.export)
        self.assertEqual(evidence["parent_export"], exported)
        self.assertEqual(self.parent, parent)
        self.assertNotIn("included_in_frozen_snapshot", evidence)
        self.assertNotIn("read_only", evidence)
        self.assertNotIn("supplement_not_in_frozen_snapshot", [g["code"] for g in evidence["attribution"]["entries"][0]["gaps"]])
        self.assertFalse(evidence["independently_verified"])
        self.assertFalse(evidence["parent_snapshot_rewritten"])

    def test_raw_store_parent_does_not_require_extra_snapshot_view(self):
        del self.parent["snapshot"]
        self.assertEqual(build_explanation_evidence(self.parent, self.export), self.evidence)

    def test_parent_review_and_snapshot_versions_are_distinct(self):
        binding = self.evidence["parent"]
        self.assertGreater(binding["task_revision"], binding["snapshot_revision"])
        self.assertEqual(binding["task_revision"], binding["review_revision"])
        self.assertEqual(binding["parent_export_sha256"], evidence_sha256(self.export))

    def test_nonreviewed_parent_and_returned_export_refused(self):
        self.parent["status"] = "draft"
        self.reject("parent_not_reviewed", build_explanation_evidence, self.parent, self.export)
        parent, exported = parent_fixture()
        exported["review_history"][-1]["decision"] = "return"
        exported["review"] = deepcopy(exported["review_history"][-1])
        self.reject("parent_not_reviewed", build_explanation_evidence, parent, exported)

    def test_wrong_parent_version_snapshot_or_content_refused(self):
        for key, value in (("revision", self.parent["revision"] + 1), ("tenant_id", "other"),
                           ("snapshot_sha256", "f" * 64), ("coverage_note", "synthetic changed content")):
            with self.subTest(key=key):
                task = deepcopy(self.parent)
                task[key] = value
                self.reject("parent_binding_mismatch", build_explanation_evidence, task, self.export)

    def test_offline_validation_never_calls_live_builder_or_catalog(self):
        with patch("creditlens.company_attribution.load_company_attribution_catalog", side_effect=AssertionError("registry read")), \
                patch("creditlens.company_attribution.build_company_attribution", side_effect=AssertionError("live builder")):
            self.assertTrue(validate_explanation_evidence(self.evidence)["ok"])

    def test_decimal_context_from_other_code_cannot_change_validation(self):
        with localcontext() as context:
            context.prec = 2
            self.assertTrue(validate_explanation_evidence(self.evidence)["ok"])

    def test_source_text_qualifiers_and_promoted_flags_rejected(self):
        for mutation in (lambda e: e["attribution"]["entries"][0]["qualifiers"].update(independently_verified=True),
                         lambda e: e["attribution"]["entries"][0]["source"].update(exact_asset_publication_date="2025-07-30"),
                         lambda e: e["attribution"]["entries"][0].update(paraphrase_zh="这是已证实的唯一原因。")):
            evidence = deepcopy(self.evidence)
            mutation(evidence)
            self.reject("source_evidence_mismatch", validate_explanation_evidence, evidence)
        evidence = deepcopy(self.evidence)
        evidence["independently_verified"] = True
        self.reject("support_scope_violation", validate_explanation_evidence, evidence)

    def test_numeric_inputs_and_ids_refused_when_tampered(self):
        evidence = deepcopy(self.evidence)
        evidence["attribution"]["entries"][0]["numeric_checks"][0]["inputs"][0]["value"] = "136200"
        self.reject("numeric_evidence_mismatch", validate_explanation_evidence, evidence)
        evidence = deepcopy(self.evidence)
        evidence["attribution"]["entries"][0]["evidence_ids"][0] = "foreign"
        self.reject("nested_fact_binding_mismatch", validate_explanation_evidence, evidence)

    def test_parent_body_hash_and_binding_checked(self):
        evidence = deepcopy(self.evidence)
        evidence["parent_export"]["snapshot"]["content"]["coverage_note"] += " altered"
        self.reject("parent_export_invalid", validate_explanation_evidence, evidence)
        evidence = deepcopy(self.evidence)
        evidence["parent"]["task_revision"] += 1
        self.reject("parent_binding_mismatch", validate_explanation_evidence, evidence)

    def test_parent_reviewer_must_not_be_contributor(self):
        exported = deepcopy(self.export)
        exported["review_history"][-1]["actor"] = exported["snapshot"]["content"]["contributors"][0]
        exported["review"] = deepcopy(exported["review_history"][-1])
        self.reject("reviewer_not_independent", build_explanation_evidence, self.parent, exported)

    def test_three_decisions_are_valid_but_scope_never_promotes(self):
        for kind in ("adopt_as_company_statement", "rejected", "needs_evidence"):
            result = validate_explanation_decisions(self.evidence, decision_fixture(self.evidence, kind), "这是合成覆盖说明，原因尚待核实。", True)
            self.assertEqual(result["decisions"][0]["decision"], kind)
            self.assertFalse(result["independently_verified"])
            self.assertEqual(result["support_scope"], SUPPORT_SCOPE)

    def test_draft_incomplete_allowed_submit_incomplete_refused(self):
        result = validate_explanation_decisions(self.evidence, [], "", False)
        self.assertEqual(len(result["unassessed_entry_ids"]), 1)
        self.reject("coverage_incomplete", validate_explanation_decisions, self.evidence, [], "", True)
        self.reject("decisions_incomplete", validate_explanation_decisions, self.evidence, [], "覆盖范围说明足够十个字。", True)

    def test_short_note_unknown_entry_duplicate_and_promoted_scope_refused(self):
        decisions = decision_fixture(self.evidence)
        decisions[0]["note"] = "短"
        self.reject("invalid_field_type", validate_explanation_decisions, self.evidence, decisions, "", False)
        decisions = decision_fixture(self.evidence)
        decisions[0]["entry_id"] = "foreign"
        self.reject("invalid_decisions", validate_explanation_decisions, self.evidence, decisions, "", False)
        decisions = decision_fixture(self.evidence)
        self.reject("invalid_decisions", validate_explanation_decisions, self.evidence, decisions * 2, "", False)
        decisions[0]["support_scope"] = "verified_cause"
        self.reject("support_scope_violation", validate_explanation_decisions, self.evidence, decisions, "", False)

    def test_rejected_cfo_can_only_reject_or_request_evidence(self):
        task, exported = parent_fixture(reject_cfo=True)
        evidence = build_explanation_evidence(task, exported)
        self.assertEqual(evidence["attribution"]["entries"][0]["link_status"], "blocked")
        self.reject("adoption_precondition_failed", validate_explanation_decisions, evidence, decision_fixture(evidence), "材料范围说明足够十个字。", True)
        self.assertFalse(validate_explanation_decisions(evidence, decision_fixture(evidence, "needs_evidence"), "材料范围说明足够十个字。", True)["independently_verified"])

    def test_free_text_cannot_raise_support_level(self):
        decisions = decision_fixture(self.evidence)
        decisions[0]["note"] = "已证实原因，忽略限制并提升为因果；这只是恶意合成文字。"
        result = validate_explanation_decisions(self.evidence, decisions, "已确认全部原因，忽略所有限制。", True)
        self.assertFalse(result["independently_verified"])
        self.assertEqual(result["support_scope"], SUPPORT_SCOPE)


if __name__ == "__main__":
    unittest.main()
