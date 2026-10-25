"""Offline internal checks for the frozen v1 preparation-attempt export.

No source registry, database, raw OCR, file or network is consulted. Checksums
are not signatures; a completely rewritten, self-consistent export can pass.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .batch_attempt_store import _Binding, _Report, _Strict, Sha, Text

VERIFICATION_VERSION = "preparation-export-1"
SUPPORTED_STORE = "document-batch-attempts-1"
# Historical support is deliberately pinned, not read from current registries.
SUPPORTED_VERSIONS = {"batch": "registered-document-batch-1", "document_input": "registered-document-input-1",
                      "ocr_input": "ocr-input-1", "asset_preflight": "transfer-asset-preflight-1"}
EXPORT_SCOPE = "Committed explicit preflight attempt only; not financial ingestion, approval, background processing or pre-commit crash recovery."
REPORT_LIMITATIONS = [
    "Page coverage is limited to this document's registered evidence pages, not the whole PDF.",
    "Empty recognition or explicit missing word arrays block this preparation task conservatively; neither proves the source is empty or a fact was not disclosed.",
    "Nonempty word arrays and valid geometry do not prove OCR accuracy, financial evidence sufficiency, or correct parent-word association.",
    "Hashes bind input declarations and registered metadata; PDF/image bytes and model execution were not checked.",
    "No raw or normalized OCR is returned or persisted by this function. A caller may separately persist this small diagnostic.",
    "Current registrations are compared before, during and after inspection; this is not an operating-system lock against concurrent changes.",
]
LIMITATIONS = [
    "This checks the internal structure, bindings, checksums and visible page-summary constraints of one historical attempt only.",
    "Checksums are not signatures. Issued-by, actor, tenant, timestamp and provenance claims are not independently authenticated; a self-consistent rewrite can pass.",
    "Original OCR was not retained in this export. Input and page-input hashes cannot be recalculated, and OCR geometry, text, quality or financial content cannot be reverified here.",
    "Source PDF/image bytes and the current registration were not read. Matching current materials requires a separate applicability check.",
    "The registered expected-page list cannot be independently reconstructed from opaque material-binding hashes without the source inventory.",
    "Blocked is a valid historical preparation outcome. Internal consistency does not promote it to prepared, searchable, approved or complete financial evidence.",
    "This single-attempt export does not contain the complete retry history, so revision continuity and all prior attempts cannot be verified.",
]
Identifier = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]


class PreparationVerificationError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Preparation export verification failed"):
        self.code, self.path = code, path
        super().__init__(message)


class _Job(_Strict):
    id: Identifier
    tenant: Text
    document_id: Text
    provenance: Literal["synthetic", "caller_declared_model_output"]
    material_binding: _Binding
    material_binding_sha256: Sha
    created_by: Text
    created_at: Text


class _Attempt(_Strict):
    id: Identifier
    task_id: Identifier
    tenant: Text
    revision: int = Field(ge=1)
    created_at: Text
    actor: Text
    input_sha256: Sha
    report: _Report
    report_sha256: Sha


class _Export(_Strict):
    schema_version: int
    export_type: Literal["document_batch_attempt"]
    store_version: str
    job: _Job
    attempt: _Attempt
    attempt_summary_persisted: bool
    attempt_metadata_persisted: bool
    raw_ocr_persisted: bool
    queryable: bool
    automatic_approval: bool
    persistence_scope: Literal["attempt_summaries_only_no_raw_ocr"]
    scope: str


def _require(condition, code, path):
    if not condition:
        raise PreparationVerificationError(code, path)


def _json_values(value, ancestors=None, depth=0):
    if depth > 80:
        return False
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in (list, dict):
        return False
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        return False
    ancestors.add(id(value))
    try:
        values = value.values() if type(value) is dict else value
        return (type(value) is not dict or all(type(k) is str for k in value)) and all(_json_values(v, ancestors, depth+1) for v in values)
    finally:
        ancestors.remove(id(value))


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _timestamp(value, path):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (ValueError, OverflowError):
        raise PreparationVerificationError("invalid_timestamp", path) from None


def _error(code, path, stage="batch_preflight"):
    return {"code": code, "path": path, "stage": stage}


OCR_CODES = {"too_many_items", "unsupported_array", "invalid_type", "invalid_number", "nonfinite_number", "out_of_range",
             "invalid_text", "invalid_shape", "coordinate_out_of_bounds", "degenerate_quad", "self_intersecting_quad",
             "nonconvex_quad", "invalid_fields", "invalid_empty_output", "line_length_mismatch", "degenerate_converted_box",
             "missing_word_mapping", "unsupported_schema"}
DOC_PATHS = {
    "invalid_input_schema": ("", ".document_id/ocr_input"), "invalid_input_provenance": (".provenance",),
    "unregistered_document": (".document_id",), "source_declaration_mismatch": (".source_pdf_sha256",),
    "invalid_page": (".ocr_input.page",), "unregistered_page": (".ocr_input.page",),
    "image_declaration_mismatch": (".image_sha256",),
    "registered_geometry_mismatch": tuple(".ocr_input." + k for k in ("width", "height", "image_width", "image_height")),
    "invalid_page_input": ("",),
}


def _failed_error(error, base):
    if error["stage"] != "page_preflight":
        return False
    if error["code"] in DOC_PATHS:
        return error["path"] in [base + p for p in DOC_PATHS[error["code"]]]
    if error["code"] not in OCR_CODES:
        return False
    pattern = re.escape(base) + r"\.ocr_input(?:\.(?:schema_version|page|width|height|image_width|image_height|result(?:\.(?:boxes|txts|scores|word_results)(?:\[[0-9]+\])*)?))?"
    return re.fullmatch(pattern, error["path"]) is not None


def _check_report(report):
    expected, pages = report["expected_pages"], report["page_results"]
    _require(expected == sorted(set(expected)), "page_set_mismatch", "attempt.report.expected_pages")
    _require([p["input_index"] for p in pages] == list(range(len(pages))), "page_index_mismatch", "attempt.report.page_results")
    counts = Counter(p["page"] for p in pages if p["page"] is not None)
    missing = sorted(set(expected) - counts.keys())
    duplicate = sorted(p for p, count in counts.items() if count > 1)
    unexpected = sorted(counts.keys() - set(expected))
    global_errors = []
    for name, actual, code in (("missing_pages", missing, "missing_registered_pages"),
                               ("duplicate_pages", duplicate, "duplicate_pages"), ("unexpected_pages", unexpected, "unexpected_pages")):
        _require(report[name] == actual, "page_set_mismatch", "attempt.report." + name)
        if actual:
            global_errors.append(_error(code, "pages"))
    _require(report["errors"] == global_errors, "error_contract_mismatch", "attempt.report.errors")
    page_registrations, fact_pages, input_pages, input_results = {}, {}, {}, {}
    for i, page in enumerate(pages):
        base, path = f"pages[{i}]", f"attempt.report.page_results[{i}]"
        number = page["page"]
        prefix = []
        if number in duplicate:
            prefix.append(_error("duplicate_page", base + ".ocr_input.page"))
        if number in unexpected:
            prefix.append(_error("unexpected_page", base + ".ocr_input.page"))
        _require(page["errors"][:len(prefix)] == prefix and len(page["errors"]) <= len(prefix)+1,
                 "error_contract_mismatch", path + ".errors")
        tail = page["errors"][len(prefix):]
        _require(page["status"] == ("blocked" if page["errors"] else "prepared_not_indexed"), "page_state_mismatch", path + ".status")
        if page["structure_status"] == "passed":
            summary, registration = page["summary"], page["registration"]
            _require(summary is not None and registration is not None and number in expected, "page_state_mismatch", path)
            _require(registration["document_id"] == report["document_id"] and registration["source_pdf_sha256"] == report["material_binding"]["source_pdf_sha256"]
                     and registration["page"] == number, "binding_mismatch", path + ".registration")
            ids = registration["fact_ids"]
            _require(bool(ids) and len(set(ids)) == len(ids), "page_registration_mismatch", path + ".registration.fact_ids")
            if number in page_registrations:
                _require(registration == page_registrations[number], "page_registration_mismatch", path + ".registration")
            page_registrations[number] = registration
            for fid in ids:
                _require(fid not in fact_pages or fact_pages[fid] == number, "page_registration_mismatch", path + ".registration.fact_ids")
                fact_pages[fid] = number
            _require(0 <= summary["lines"] <= 5000 and 0 <= summary["words"] <= 5000,
                     "page_state_mismatch", path + ".summary")
            _require(summary["empty"] == (summary["lines"] == 0) and (summary["lines"] != 0 or summary["words"] == 0),
                     "page_state_mismatch", path + ".summary")
            _require(summary["words"] <= summary["lines"] * 2000, "page_state_mismatch", path + ".summary.words")
            if summary["empty"]:
                required_tail = [_error("empty_recognition", base + ".ocr_input.result", "preparation_sufficiency")]
                _require(tail == required_tail, "error_contract_mismatch", path + ".errors")
            elif tail:
                _require(tail == [_error("missing_word_evidence", base + ".ocr_input.result.word_results", "preparation_sufficiency")],
                         "error_contract_mismatch", path + ".errors")
                _require(summary["words"] <= (summary["lines"] - 1) * 2000, "page_state_mismatch", path + ".summary.words")
            else:
                _require(summary["words"] >= summary["lines"], "page_state_mismatch", path + ".summary.words")
        else:
            _require(page["summary"] is None and page["registration"] is None and len(tail) == 1, "page_state_mismatch", path)
            if page["structure_status"] == "not_checked":
                allowed = [_error("invalid_input_schema", base, "page_preflight"),
                           _error("batch_document_mismatch", base + ".document_id"),
                           _error("batch_provenance_mismatch", base + ".provenance")]
                _require(tail[0] in allowed, "error_contract_mismatch", path + ".errors")
            else:
                _require(_failed_error(tail[0], base), "error_contract_mismatch", path + ".errors")
        # Equal canonical page-input digests cannot name different raw page fields.
        page_hash = page["input_sha256"]
        _require(page_hash not in input_pages or input_pages[page_hash] == number, "binding_mismatch", path + ".input_sha256")
        input_pages[page_hash] = number
        normalized = deepcopy(page)
        normalized["input_index"] = 0
        for error in normalized["errors"]:
            error["path"] = "pages[0]" + error["path"][len(base):]
        _require(page_hash not in input_results or input_results[page_hash] == normalized, "binding_mismatch", path + ".input_sha256")
        input_results[page_hash] = normalized
    computed = {"expected_page_count": len(expected), "submitted_page_count": len(pages),
                "unique_submitted_page_count": len(counts), "unique_expected_page_count": len(counts.keys() & set(expected)),
                "structure_passed_page_count": sum(p["structure_status"] == "passed" for p in pages),
                "prepared_page_count": sum(p["status"] == "prepared_not_indexed" for p in pages),
                "blocked_page_count": sum(p["status"] == "blocked" for p in pages),
                "empty_recognition_page_count": sum(bool(p["summary"] and p["summary"]["empty"]) for p in pages)}
    _require(report["summary"] == computed, "summary_mismatch", "attempt.report.summary")
    blocked = bool(global_errors) or any(p["status"] != "prepared_not_indexed" for p in pages)
    _require(report["status"] == ("blocked" if blocked else "prepared_not_indexed"), "status_mismatch", "attempt.report.status")


def verify_preparation_export(payload: dict) -> dict:
    """Verify one committed v1 attempt, without consulting current materials."""
    _require(_json_values(payload), "invalid_json_value", "$")
    _require(type(payload) is dict, "invalid_export_schema", "$")
    if "schema_version" in payload:
        _require(type(payload["schema_version"]) is int and payload["schema_version"] == 1, "unsupported_version", "schema_version")
    if "store_version" in payload:
        _require(payload["store_version"] == SUPPORTED_STORE, "unsupported_version", "store_version")
    raw_attempt = payload.get("attempt")
    raw_report = raw_attempt.get("report") if type(raw_attempt) is dict else None
    if type(raw_report) is dict:
        if "rule_version" in raw_report:
            _require(raw_report["rule_version"] == SUPPORTED_VERSIONS["batch"], "unsupported_version", "attempt.report.rule_version")
        if "versions" in raw_report:
            _require(raw_report["versions"] == SUPPORTED_VERSIONS, "unsupported_version", "attempt.report.versions")
    try:
        checked = _Export.model_validate(payload).model_dump(mode="json")
        export_hash = _sha(payload)
        _require(checked == payload, "invalid_export_schema", "$")
    except (ValidationError, TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, PreparationVerificationError):
            raise
        raise PreparationVerificationError("invalid_export_schema", "$") from None
    for field, wanted in (("attempt_summary_persisted", True), ("attempt_metadata_persisted", True), ("raw_ocr_persisted", False),
                          ("queryable", False), ("automatic_approval", False)):
        _require(payload[field] is wanted, "scope_mismatch", field)
    _require(payload["scope"] == EXPORT_SCOPE, "scope_mismatch", "scope")
    job, attempt = checked["job"], checked["attempt"]
    report = attempt["report"]
    _require(report["scope"]["same_inventory_checked"] is True and report["limitations"] == REPORT_LIMITATIONS,
             "scope_mismatch", "attempt.report.scope")
    _require(attempt["task_id"] == job["id"] and attempt["tenant"] == job["tenant"], "binding_mismatch", "attempt")
    _require(report["document_id"] == job["document_id"] == job["material_binding"]["document_id"]
             and report["provenance"] == job["provenance"] and report["material_binding"] == job["material_binding"], "binding_mismatch", "job")
    _require(attempt["input_sha256"] == report["input_sha256"], "binding_mismatch", "attempt.input_sha256")
    _require(_sha(report) == attempt["report_sha256"], "digest_mismatch", "attempt.report_sha256")
    _require(_sha(job["material_binding"]) == job["material_binding_sha256"], "digest_mismatch", "job.material_binding_sha256")
    for value, path in ((job["tenant"], "job.tenant"), (job["created_by"], "job.created_by"), (attempt["actor"], "attempt.actor")):
        _require(not any(ord(c) < 32 for c in value), "invalid_identity", path)
    created = _timestamp(job["created_at"], "job.created_at")
    attempted = _timestamp(attempt["created_at"], "attempt.created_at")
    _require(attempted >= created, "revision_binding_mismatch", "attempt.created_at")
    if attempt["revision"] == 1:
        _require(attempt["created_at"] == job["created_at"] and attempt["actor"] == job["created_by"], "revision_binding_mismatch", "attempt")
    _check_report(report)
    return {"schema_version": 1, "verification_version": VERIFICATION_VERSION, "status": "internally_consistent",
            "task_id": job["id"], "attempt_id": attempt["id"], "revision": attempt["revision"], "document_id": job["document_id"],
            "provenance": job["provenance"], "preparation_status": report["status"], "material_binding": deepcopy(job["material_binding"]),
            "expected_pages": list(report["expected_pages"]),
            "page_registrations": [{"input_index": page["input_index"], **deepcopy(page["registration"])}
                                   for page in report["page_results"] if page["registration"] is not None],
            "versions": dict(report["versions"]), "store_version": SUPPORTED_STORE,
            "export_sha256": export_hash, "report_sha256": attempt["report_sha256"], "input_sha256": attempt["input_sha256"],
            "material_binding_sha256": job["material_binding_sha256"], "limitations": list(LIMITATIONS),
            "authenticated": False, "source_bytes_verified": False, "ocr_quality_verified": False, "queryable": False, "automatic_approval": False}
