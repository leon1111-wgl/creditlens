"""Versioned company-statement attachment and unsigned offline consistency rules.

Only the builder accesses current registered material. Validators operate on the
embedded data and v1 fingerprints; they do not open the live source registry.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
from pathlib import Path

from scripts.verify_research_export import ExportError, canonical_bytes, verify_export as verify_parent_export


EVIDENCE_VERSION = "explanation-evidence-1"
STORE_VERSION = "explanation-review-1"
SUPPORT_SCOPE = "company_statement_only"
CATALOG_SHA256 = "16e5435d1d906011d74639c1ee75160e933afcb8c158584e6739b396d4bf3a33"
PARENT_BUNDLE_SHA256 = "1630349586512dfa0af32bf963f04d17ef2890ab0015c9cb12b161fd68861602"
_STATIC_ENTRY_SHA256 = "0967a1075de6e2c01f3f76dc47d73dfdbdc9bc292975394256a098dbfe93a5ad"
_NUMERIC_SHA256 = "b36ac61b66a915ec047adb2e564eb7275413dd3e0ca8c81e570472ae04a76206"
_COVERAGE_SHA256 = "50ebb53f360df2ab733c62ed70185f653d632bb32d708696cb398ad7c1cc239e"
_STATIC_KEYS = {"id", "issuer_id", "company", "metric", "periods", "source", "paraphrase_zh", "qualifiers",
                "independently_verified", "automatic_approval"}
_ENTRY_KEYS = _STATIC_KEYS | {"numeric_checks", "evidence_ids", "input_adoption", "link_status", "gaps"}
_PARENT_KEYS = {"task_id", "tenant_id", "task_revision", "snapshot_id", "snapshot_sha256", "snapshot_revision",
                "review_actor", "review_revision", "parent_export_sha256"}
_EVIDENCE_KEYS = {"schema_version", "evidence_version", "support_scope", "parent", "parent_export", "attribution",
                  "limitations", "independently_verified", "automatic_approval", "parent_snapshot_rewritten"}
_CONTENT_KEYS = {"schema_version", "store_version", "attachment_id", "tenant_id", "task_revision", "submitted_by",
                 "submitted_at", "created_by", "contributors", "parent_task_id", "parent_revision",
                 "parent_snapshot_sha256", "evidence", "evidence_sha256", "decisions", "coverage_note",
                 "support_scope", "independently_verified", "automatic_approval"}
_REVIEW_KEYS = {"actor", "at", "decision", "note", "revision", "snapshot_id", "snapshot_sha256"}
_LIMITATIONS = [
    "采用范围仅为公司明示陈述，不表示原因已独立证实，也不对解释各项贡献分配金额。",
    "本附件独立冻结和复核，不修改父底稿、父审批或其旧验收结果。",
    "来源网页与 PDF 是同份年报的两种载体，不是两个独立证据源。",
    "离线核验仅检查内嵌记录和版本规则的一致性，不提供签名、来源真实性或人员身份验证。",
]


class ExplanationEvidenceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def _require(condition, code, message):
    if not condition:
        raise ExplanationEvidenceError(code, message)


def _bytes(value):
    try:
        return canonical_bytes(value)
    except ExportError as exc:
        raise ExplanationEvidenceError(exc.code, str(exc)) from exc


def evidence_sha256(value) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _same(left, right):
    return _bytes(left) == _bytes(right)


def _object(value, keys, name):
    _require(type(value) is dict and set(value) == keys, "invalid_fields", f"Unexpected or missing {name} fields")


def _text(value, name, minimum=1, maximum=4000):
    _require(type(value) is str and minimum <= len(value.strip()) and len(value) <= maximum,
             "invalid_field_type", f"Invalid {name}")


def _positive(value, name):
    _require(type(value) is int and value > 0, "invalid_field_type", f"Invalid {name}")


def _flags(value):
    _require(value.get("support_scope") == SUPPORT_SCOPE and value.get("independently_verified") is False
             and value.get("automatic_approval") is False, "support_scope_violation", "Company statements cannot be promoted to verified causes or automatic approval")


def _parent(document):
    try:
        summary = verify_parent_export(document)
    except ExportError as exc:
        raise ExplanationEvidenceError("parent_export_invalid", f"{exc.code}: {exc}") from exc
    content, review = document["snapshot"]["content"], document["review"]
    _require(type(review) is dict and review["decision"] == "approve", "parent_not_reviewed", "An approved parent snapshot is required")
    _require(content["bundle_sha256"] == PARENT_BUNDLE_SHA256, "unsupported_parent_material", "This attachment version supports only its frozen material bundle")
    _require(content["created_by"] in content["contributors"] and content["submitted_by"] in content["contributors"],
             "parent_contributor_mismatch", "Parent authors must be included in contributors")
    for item in document["review_history"]:
        _require(item["actor"] not in content["contributors"], "reviewer_not_independent", "Parent reviewer is also a parent contributor")
    return content, review, summary


def _parent_binding(document):
    content, review, _ = _parent(document)
    snapshot = document["snapshot"]
    return {"task_id": document["task_id"], "tenant_id": content["tenant_id"], "task_revision": review["revision"],
            "snapshot_id": snapshot["id"], "snapshot_sha256": snapshot["sha256"], "snapshot_revision": snapshot["revision"],
            "review_actor": review["actor"], "review_revision": review["revision"], "parent_export_sha256": evidence_sha256(document)}


def _entry_gaps(entry, adoption):
    ids = entry["evidence_ids"]
    gaps = [{"code": "independent_corroboration_missing", "message": "公司解释尚未独立核实；数值相符不能证明原因。", "observation_ids": ids}]
    if entry["link_status"] != "consistent_rounded":
        blocked = entry["link_status"] == "blocked"
        gaps.append({"code": "input_rejected" if blocked else "input_not_adopted",
                     "message": "相关 CFO 被拒绝用于此任务。" if blocked else "须明确采用两期 CFO 后才能关联本任务。",
                     "observation_ids": [item["observation_id"] for item in adoption if item["status"] != "adopted"]})
    return gaps


def validate_explanation_evidence(evidence: dict) -> dict:
    """Validate embedded v1 evidence without current files, network or a database."""
    _bytes(evidence)
    _object(evidence, _EVIDENCE_KEYS, "evidence")
    _require(type(evidence["schema_version"]) is int and evidence["schema_version"] == 1
             and evidence["evidence_version"] == EVIDENCE_VERSION, "unsupported_schema", "Unsupported evidence version")
    _flags(evidence)
    _require(evidence["parent_snapshot_rewritten"] is False and evidence["limitations"] == _LIMITATIONS,
             "support_scope_violation", "Attachment limitations or parent immutability flags changed")
    parent_content, _, _ = _parent(evidence["parent_export"])
    _object(evidence["parent"], _PARENT_KEYS, "parent binding")
    _require(_same(evidence["parent"], _parent_binding(evidence["parent_export"])), "parent_binding_mismatch", "Parent metadata does not match its embedded export")
    attribution = evidence["attribution"]
    _object(attribution, {"rule_version", "catalog_sha256", "entries", "coverage"}, "attribution")
    _require(attribution["rule_version"] == "company-attribution-1" and attribution["catalog_sha256"] == CATALOG_SHA256,
             "unsupported_source_version", "Unsupported frozen statement catalog")
    _require(evidence_sha256(attribution["coverage"]) == _COVERAGE_SHA256, "source_evidence_mismatch", "Statement coverage differs from frozen registration")
    entries = attribution["entries"]
    _require(type(entries) is list and len(entries) == 1, "source_evidence_mismatch", "Exactly one frozen company statement is supported")
    entry = entries[0]
    _object(entry, _ENTRY_KEYS, "entry")
    _require(evidence_sha256({k: entry[k] for k in _STATIC_KEYS}) == _STATIC_ENTRY_SHA256,
             "source_evidence_mismatch", "Company, source, periods, text or qualifiers differ from frozen registration")
    _require(evidence_sha256(entry["numeric_checks"]) == _NUMERIC_SHA256,
             "numeric_evidence_mismatch", "Numeric evidence differs from the frozen precision checks")
    observations = sorted((o for o in parent_content["bundle"]["observations"]
                           if o["issuer_id"] == entry["issuer_id"] and o["metric"] == entry["metric"]), key=lambda o: o["year"])
    _require(len(observations) == 2 and [o["year"] for o in observations] == [2024, 2025],
             "nested_fact_binding_mismatch", "Expected two CFO years")
    ids = [o["id"] for o in observations]
    _require(entry["evidence_ids"] == ids, "nested_fact_binding_mismatch", "Company statement refers to foreign parent observations")
    for index, observation in enumerate(observations):
        _require(observation["period"] == entry["periods"]["previous" if index == 0 else "current"]
                 and observation["unit"] == "USD_million", "nested_fact_binding_mismatch", "Period or unit differs from the parent")
    for check in entry["numeric_checks"]:
        used = observations[1:] if check["id"] == "current_cfo" else observations
        expected_inputs = [{"observation_id": o["id"], "issuer_id": o["issuer_id"], "year": o["year"], "value": o["normalized_value"],
                            "raw_value": o["raw_value"], "unit": o["unit"], "period": o["period"], "source": o["source"]} for o in used]
        _require(_same(check["inputs"], expected_inputs) and check["input_ids"] == [o["id"] for o in used],
                 "nested_fact_binding_mismatch", "Numeric inputs do not match parent evidence")
        with localcontext() as context:
            context.prec = 40
            exact = Decimal(observations[1]["normalized_value"])
            if check["id"] == "cfo_change":
                exact -= Decimal(observations[0]["normalized_value"])
            _require(Decimal(check["exact_value"]) == exact and (exact / Decimal(1000)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
                     == Decimal(check["reported_rounded_value"]), "numeric_evidence_mismatch", "Precision arithmetic is inconsistent")
    decisions = parent_content["decisions"]
    observation_ids = {o["id"] for o in parent_content["bundle"]["observations"]}
    _require(type(decisions) is list and len(decisions) == len(observation_ids), "parent_decisions_invalid", "Parent must assess every observation")
    by_id = {}
    for decision in decisions:
        _object(decision, {"observation_id", "decision", "note"}, "parent decision")
        _require(type(decision["observation_id"]) is str and decision["observation_id"] in observation_ids
                 and decision["observation_id"] not in by_id and decision["decision"] in ("adopted", "rejected"),
                 "parent_decisions_invalid", "Unknown, repeated or unsupported parent decision")
        _text(decision["note"], "parent decision note", maximum=2000)
        by_id[decision["observation_id"]] = decision["decision"]
    adoption = [{"observation_id": oid, "status": by_id[oid]} for oid in ids]
    status = "blocked" if any(d["status"] == "rejected" for d in adoption) else "consistent_rounded"
    _require(entry["input_adoption"] == adoption and entry["link_status"] == status
             and _same(entry["gaps"], _entry_gaps(entry, adoption)), "adoption_binding_mismatch", "Statement association does not match parent adoption decisions")
    return {"ok": True, "entry_ids": [entry["id"]], "parent": deepcopy(evidence["parent"]),
            "evidence_sha256": evidence_sha256(evidence), "support_scope": SUPPORT_SCOPE, "independently_verified": False}


def build_explanation_evidence(parent_task: dict, parent_export: dict, root: Path | None = None) -> dict:
    """Server-only builder; requires the current reviewed parent and its export."""
    binding = _parent_binding(parent_export)
    _require(type(parent_task) is dict and parent_task.get("status") == "reviewed", "parent_not_reviewed", "Parent must currently be reviewed")
    _require(parent_task.get("id") == binding["task_id"] and parent_task.get("tenant_id") == binding["tenant_id"]
             and parent_task.get("revision") == binding["task_revision"]
             and parent_task.get("snapshot_id") == binding["snapshot_id"] and parent_task.get("snapshot_sha256") == binding["snapshot_sha256"]
             and ("snapshot" not in parent_task or _same(parent_task["snapshot"], parent_export["snapshot"]))
             and _same(parent_task.get("review"), parent_export["review"]), "parent_binding_mismatch", "Current parent and approved export differ")
    frozen = parent_export["snapshot"]["content"]
    special = {"schema_version", "task_id", "task_revision", "submitted_at"}
    _require(all(_same(parent_task.get(key), value) for key, value in frozen.items() if key not in special),
             "parent_binding_mismatch", "Current parent data differs from frozen content")
    from .company_attribution import CompanyAttributionError, build_company_attribution
    try:
        preview = build_company_attribution(parent_task, root=root)
    except CompanyAttributionError as exc:
        raise ExplanationEvidenceError(exc.code, exc.message) from exc
    entries = deepcopy(preview["entries"])
    for entry in entries:
        entry["gaps"] = [gap for gap in entry["gaps"] if gap["code"] != "supplement_not_in_frozen_snapshot"]
    evidence = {"schema_version": 1, "evidence_version": EVIDENCE_VERSION, "support_scope": SUPPORT_SCOPE,
                "parent": binding, "parent_export": deepcopy(parent_export),
                "attribution": {"rule_version": preview["rule_version"], "catalog_sha256": preview["catalog_sha256"],
                                "entries": entries, "coverage": deepcopy(preview["coverage"])},
                "limitations": list(_LIMITATIONS), "independently_verified": False,
                "automatic_approval": False, "parent_snapshot_rewritten": False}
    validate_explanation_evidence(evidence)
    return evidence


def validate_explanation_decisions(evidence: dict, decisions: list[dict], coverage_note: str, for_submit: bool = False) -> dict:
    summary = validate_explanation_evidence(evidence)
    _require(type(for_submit) is bool, "invalid_field_type", "Invalid submission mode")
    _require(type(decisions) is list and len(decisions) <= len(summary["entry_ids"]), "invalid_decisions", "Invalid decision inventory")
    _require(type(coverage_note) is str and len(coverage_note) <= 4000 and (not for_submit or len(coverage_note.strip()) >= 10),
             "coverage_incomplete", "Submission needs a coverage note of at least 10 characters")
    entries = {e["id"]: e for e in evidence["attribution"]["entries"]}
    seen = set()
    for decision in decisions:
        _object(decision, {"entry_id", "decision", "note", "support_scope"}, "explanation decision")
        key = decision["entry_id"]
        _require(type(key) is str and key in entries and key not in seen, "invalid_decisions", "Unknown or repeated statement entry")
        _require(decision["decision"] in ("adopt_as_company_statement", "rejected", "needs_evidence")
                 and decision["support_scope"] == SUPPORT_SCOPE, "support_scope_violation", "Decision must remain a company-statement decision")
        _text(decision["note"], "decision note", minimum=10, maximum=2000)
        if decision["decision"] == "adopt_as_company_statement":
            _require(entries[key]["link_status"] == "consistent_rounded"
                     and all(c["status"] == "consistent_rounded" for c in entries[key]["numeric_checks"])
                     and all(a["status"] == "adopted" for a in entries[key]["input_adoption"]),
                     "adoption_precondition_failed", "Cannot adopt a statement without its adopted CFO inputs and precision checks")
        seen.add(key)
    unassessed = sorted(set(entries) - seen)
    _require(not for_submit or not unassessed, "decisions_incomplete", "Assess each registered explanation before submitting")
    return {"decisions": deepcopy(decisions), "coverage_note": coverage_note, "unassessed_entry_ids": unassessed,
            "support_scope": SUPPORT_SCOPE, "independently_verified": False}


def validate_explanation_export(document: dict) -> dict:
    """Unsigned export consistency, including nested evidence and recorded roles."""
    from scripts.verify_research_export import _timestamp
    _bytes(document)
    _object(document, {"schema_version", "export_type", "attachment_id", "snapshot", "review", "review_history", "editable_draft_included",
                       "support_scope", "independently_verified", "automatic_approval", "scope"}, "explanation export")
    _require(type(document["schema_version"]) is int and document["schema_version"] == 1
             and document["export_type"] == "explanation_review", "unsupported_schema", "Unsupported explanation export")
    _flags(document)
    _require(document["editable_draft_included"] is False, "invalid_export_scope", "Export must contain frozen content only")
    _text(document["scope"], "scope")
    snapshot = document["snapshot"]
    _object(snapshot, {"id", "sha256", "created_at", "submitted_by", "revision", "content"}, "snapshot")
    _text(snapshot["id"], "snapshot id", maximum=256)
    content = snapshot["content"]
    _object(content, _CONTENT_KEYS, "snapshot content")
    _require(type(content["schema_version"]) is int and content["schema_version"] == 1 and content["store_version"] == STORE_VERSION,
             "unsupported_schema", "Unsupported explanation snapshot")
    _flags(content)
    for key in ("attachment_id", "tenant_id", "submitted_by", "created_by", "parent_task_id"):
        _text(content[key], key, maximum=256)
    _positive(content["task_revision"], "task_revision")
    _positive(content["parent_revision"], "parent_revision")
    _require(evidence_sha256(content) == snapshot["sha256"], "snapshot_hash_mismatch", "Frozen content digest mismatch")
    _require(evidence_sha256(content["evidence"]) == content["evidence_sha256"], "evidence_hash_mismatch", "Evidence digest mismatch")
    evidence = validate_explanation_evidence(content["evidence"])
    parent = evidence["parent"]
    _require(content["parent_task_id"] == parent["task_id"] and content["parent_revision"] == parent["task_revision"]
             and content["parent_snapshot_sha256"] == parent["snapshot_sha256"] and content["tenant_id"] == parent["tenant_id"],
             "parent_binding_mismatch", "Child content refers to a different parent or tenant")
    _require(document["attachment_id"] == content["attachment_id"] and snapshot["revision"] == content["task_revision"]
             and type(snapshot["revision"]) is int and snapshot["submitted_by"] == content["submitted_by"]
             and snapshot["created_at"] == content["submitted_at"], "snapshot_binding_mismatch", "Snapshot metadata differs from content")
    decisions = validate_explanation_decisions(content["evidence"], content["decisions"], content["coverage_note"], for_submit=True)
    contributors = content["contributors"]
    _require(type(contributors) is list and bool(contributors), "invalid_contributors", "Contributors required")
    for actor in contributors:
        _text(actor, "contributor", maximum=256)
    _require(len(contributors) == len(set(contributors)) and content["created_by"] in contributors and content["submitted_by"] in contributors,
             "invalid_contributors", "Authors must be recorded among unique contributors")
    forbidden = set(contributors) | set(content["evidence"]["parent_export"]["snapshot"]["content"]["contributors"])
    history = document["review_history"]
    _require(type(history) is list, "invalid_fields", "Review history must be a list")
    revision = content["task_revision"]
    try:
        _timestamp(content["submitted_at"], "submitted_at")
        for review in history:
            _object(review, _REVIEW_KEYS, "review")
            _text(review["actor"], "review actor", maximum=256)
            _text(review["note"], "review note", minimum=10, maximum=2000)
            _timestamp(review["at"], "review at")
            _positive(review["revision"], "review revision")
            _require(review["decision"] in ("approve", "return") and review["revision"] > revision,
                     "review_revision_mismatch", "Invalid review action or revision")
            _require(review["actor"] not in forbidden, "reviewer_not_independent", "Reviewer contributed to the parent or attachment")
            _require(review["snapshot_id"] == snapshot["id"] and review["snapshot_sha256"] == snapshot["sha256"],
                     "review_binding_mismatch", "Review refers to another snapshot")
            revision = review["revision"]
    except ExportError as exc:
        raise ExplanationEvidenceError(exc.code, str(exc)) from exc
    _require(_same(document["review"], history[-1] if history else None), "review_history_mismatch", "Selected review is not the last bound review")
    return {"ok": True, "attachment_id": document["attachment_id"], "snapshot_id": snapshot["id"],
            "content_sha256": snapshot["sha256"], "evidence_sha256": content["evidence_sha256"], "parent": parent,
            "review_count": len(history), "decision_counts": {key: sum(d["decision"] == key for d in decisions["decisions"])
               for key in ("adopt_as_company_statement", "rejected", "needs_evidence")},
            "support_scope": SUPPORT_SCOPE, "independently_verified": False, "automatic_approval": False,
            "scope": "Unsigned internal consistency and recorded role separation only; no signature, source authenticity or principal identity verification"}
