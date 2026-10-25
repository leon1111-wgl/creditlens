"""Bounded, read-only preparation of every registered page of one document.

This does not interpret tables across pages or make any page searchable. Raw
and normalized OCR are transient inputs and never included in the result.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path

from .asset_preflight import AssetPreflightError, VERSION as ASSET_VERSION, preflight_transfer_assets
from .diligence import digest
from .document_input import DocumentInputError, VERSION as PAGE_VERSION, prepare_document_input
from .ocr_input import OCR_INPUT_VERSION, OcrInputError

VERSION = "registered-document-batch-1"
MAX_PAGES = 32
PROVENANCES = ("synthetic", "caller_declared_model_output")


class BatchInputError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.code, self.path = code, path
        super().__init__(message)


def _require(condition, code, path, message):
    if not condition:
        raise BatchInputError(code, path, message)


def _is_json(value, seen=None):
    """Do not coerce tuples, non-string keys, numpy values or NaN for hashing."""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in (list, dict):
        return False
    seen = set() if seen is None else seen
    if id(value) in seen:
        return False
    seen.add(id(value))
    try:
        if type(value) is dict:
            return all(type(k) is str and _is_json(v, seen) for k, v in value.items())
        return all(_is_json(v, seen) for v in value)
    finally:
        seen.remove(id(value))


def _hash(value):
    try:
        if _is_json(value):
            return digest(value), "canonical_json"
    except (ValueError, TypeError, OverflowError, RecursionError):
        pass
    return None, "unavailable_noncanonical_json"


def _error(code, path, stage="batch_preflight"):
    return {"code": code, "path": path, "stage": stage}


def _page_number(value):
    raw = value.get("ocr_input") if type(value) is dict else None
    number = raw.get("page") if type(raw) is dict else None
    return number if type(number) is int and 1 <= number <= 20000 else None


def _page_error(exc, index):
    # Existing typed errors use fixed schema paths. Unknown dictionary keys and
    # OCR text are never interpolated by these validators.
    prefix = f"pages[{index}]"
    if isinstance(exc, OcrInputError):
        path = prefix + ".ocr_input" + ("" if exc.path == "$" else "." + exc.path)
    elif isinstance(exc, AssetPreflightError):
        return _error("material_registration_failed", prefix, "asset_preflight")
    else:
        path = prefix + ("" if exc.path == "$" else "." + exc.path)
    return _error(exc.code, path, "page_preflight")


def inspect_document_batch(root: Path, payload: dict, manifest_path: Path | None = None) -> dict:
    """Inspect one explicit document batch; return no OCR text or word arrays.

    Missing/duplicate/unexpected pages and per-page failures return ``blocked``.
    Invalid outer contracts or unavailable initial registration raise a typed
    error. The expected pages are registered evidence pages, not all PDF pages.
    """
    fields = {"schema_version", "document_id", "provenance", "pages"}
    _require(type(payload) is dict and set(payload) == fields, "invalid_batch_schema", "$", "Expected the exact batch envelope")
    _require(type(payload["schema_version"]) is int and payload["schema_version"] == 1,
             "unsupported_batch_schema", "schema_version", "Only batch schema 1 is supported")
    _require(type(payload["document_id"]) is str and 0 < len(payload["document_id"]) <= 200,
             "invalid_document_id", "document_id", "Expected a bounded registered document identity")
    _require(type(payload["provenance"]) is str and payload["provenance"] in PROVENANCES,
             "invalid_batch_provenance", "provenance", "Provenance must be explicit and remains unverified")
    _require(type(payload["pages"]) is list and len(payload["pages"]) <= MAX_PAGES,
             "invalid_batch_pages", "pages", "Expected at most 32 single-page envelopes")
    try:
        inventory = preflight_transfer_assets(root, manifest_path)
    except AssetPreflightError as exc:
        raise BatchInputError("material_registration_failed", "material_binding", "Initial registered material validation failed") from exc
    selected = next((d for d in inventory["documents"] if d["id"] == payload["document_id"]), None)
    _require(selected is not None, "unregistered_document", "document_id", "Document is absent from the registered inventory")
    expected = sorted(p["page"] for p in selected["pages"])
    _require(0 < len(expected) <= MAX_PAGES, "unsupported_registered_page_count", "material_binding",
             "The registered document exceeds this batch size limit")
    inventory_hash = digest(inventory)
    binding = {"manifest_sha256": inventory["manifest_sha256"], "inventory_sha256": inventory_hash,
               "document_id": selected["id"], "source_pdf_sha256": selected["source_pdf_sha256"],
               "pack_sha256": selected["pack_sha256"], "pack_canonical_sha256": selected["pack_canonical_sha256"],
               "registered_document_sha256": digest(selected)}
    input_hash, input_hash_status = _hash(payload)
    page_numbers = [_page_number(p) for p in payload["pages"]]
    counts = Counter(p for p in page_numbers if p is not None)
    missing = sorted(set(expected) - counts.keys())
    duplicate = sorted(p for p, count in counts.items() if count > 1)
    unexpected = sorted(counts.keys() - set(expected))
    errors = []
    if missing:
        errors.append(_error("missing_registered_pages", "pages"))
    if duplicate:
        errors.append(_error("duplicate_pages", "pages"))
    if unexpected:
        errors.append(_error("unexpected_pages", "pages"))
    if input_hash is None:
        errors.append(_error("noncanonical_batch_input", "$"))
    page_results = []
    material_changed = False
    for index, value in enumerate(payload["pages"]):
        number = page_numbers[index]
        page_hash, hash_status = _hash(value)
        result = {"input_index": index, "page": number, "input_sha256": page_hash, "input_hash_status": hash_status,
                  "status": "blocked", "structure_status": "not_checked", "summary": None,
                  "registration": None, "errors": []}
        page_results.append(result)
        base = f"pages[{index}]"
        if number in duplicate:
            result["errors"].append(_error("duplicate_page", base + ".ocr_input.page"))
        if number in unexpected:
            result["errors"].append(_error("unexpected_page", base + ".ocr_input.page"))
        if page_hash is None:
            result["errors"].append(_error("noncanonical_page_input", base))
        if type(value) is not dict:
            result["errors"].append(_error("invalid_input_schema", base, "page_preflight"))
            continue
        if value.get("document_id") != payload["document_id"]:
            result["errors"].append(_error("batch_document_mismatch", base + ".document_id"))
            continue
        if value.get("provenance") != payload["provenance"]:
            result["errors"].append(_error("batch_provenance_mismatch", base + ".provenance"))
            continue
        try:
            prepared = prepare_document_input(root, value, manifest_path=manifest_path)
        except (DocumentInputError, OcrInputError, AssetPreflightError) as exc:
            result["structure_status"] = "failed"
            result["errors"].append(_page_error(exc, index))
            if isinstance(exc, AssetPreflightError):
                material_changed = True
            continue
        except (ValueError, TypeError, OverflowError, RecursionError):
            # E.g. noncanonical values that cannot be hashed by the frozen
            # single-page preparer. No original exception text is exposed.
            result["structure_status"] = "failed"
            result["errors"].append(_error("invalid_page_input", base, "page_preflight"))
            continue
        result["structure_status"] = "passed"
        if digest(prepared["asset_preflight"]) != inventory_hash:
            material_changed = True
            result["errors"].append(_error("material_changed_during_batch", base, "asset_preflight"))
            continue
        result["summary"] = {key: prepared["ocr"]["summary"][key] for key in ("lines", "words", "empty")}
        result["registration"] = {key: prepared["registration"][key] for key in
                                  ("document_id", "source_pdf_sha256", "image_sha256", "page", "fact_ids")}
        if result["summary"]["empty"]:
            result["errors"].append(_error("empty_recognition", base + ".ocr_input.result", "preparation_sufficiency"))
        elif any(not line["words"] for line in prepared["ocr"]["page"]["lines"]):
            result["errors"].append(_error("missing_word_evidence", base + ".ocr_input.result.word_results", "preparation_sufficiency"))
        if not result["errors"]:
            result["status"] = "prepared_not_indexed"
    try:
        if digest(preflight_transfer_assets(root, manifest_path)) != inventory_hash:
            material_changed = True
    except AssetPreflightError:
        material_changed = True
    if material_changed:
        errors.append(_error("material_changed_during_batch", "material_binding", "asset_preflight"))
        for result in page_results:
            result["status"] = "blocked"
            result["errors"].append(_error("batch_material_binding_invalidated", f"pages[{result['input_index']}]", "asset_preflight"))
    blocked = bool(errors) or any(p["status"] != "prepared_not_indexed" for p in page_results)
    return {"schema_version": 1, "rule_version": VERSION, "status": "blocked" if blocked else "prepared_not_indexed",
            "document_id": payload["document_id"], "provenance": payload["provenance"],
            "input_sha256": input_hash, "input_hash_status": input_hash_status, "material_binding": binding,
            "expected_pages": expected, "missing_pages": missing, "duplicate_pages": duplicate, "unexpected_pages": unexpected,
            "page_results": page_results, "errors": errors,
            "summary": {"expected_page_count": len(expected), "submitted_page_count": len(payload["pages"]),
                        "unique_submitted_page_count": len(counts),
                        "unique_expected_page_count": len(counts.keys() & set(expected)),
                        "structure_passed_page_count": sum(p["structure_status"] == "passed" for p in page_results),
                        "prepared_page_count": sum(p["status"] == "prepared_not_indexed" for p in page_results),
                        "blocked_page_count": sum(p["status"] == "blocked" for p in page_results),
                        "empty_recognition_page_count": sum(bool(p["summary"] and p["summary"]["empty"]) for p in page_results)},
            "versions": {"batch": VERSION, "document_input": PAGE_VERSION, "ocr_input": OCR_INPUT_VERSION, "asset_preflight": ASSET_VERSION},
            "scope": {"expected_pages_basis": "all_registered_evidence_pages_not_entire_pdf", "same_inventory_checked": not material_changed,
                      "raw_ocr_returned": False, "normalized_ocr_returned": False, "source_pdf_bytes_verified": False,
                      "source_image_bytes_verified": False, "model_execution_verified": False, "financial_content_verified": False,
                      "cross_page_tables_understood": False},
            "limitations": ["Page coverage is limited to this document's registered evidence pages, not the whole PDF.",
                            "Empty recognition or explicit missing word arrays block this preparation task conservatively; neither proves the source is empty or a fact was not disclosed.",
                            "Nonempty word arrays and valid geometry do not prove OCR accuracy, financial evidence sufficiency, or correct parent-word association.",
                            "Hashes bind input declarations and registered metadata; PDF/image bytes and model execution were not checked.",
                            "No raw or normalized OCR is returned or persisted by this function. A caller may separately persist this small diagnostic.",
                            "Current registrations are compared before, during and after inspection; this is not an operating-system lock against concurrent changes."],
            "queryable": False, "automatic_approval": False, "database_written": False}
