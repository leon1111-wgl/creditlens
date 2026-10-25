"""Local preparation of registered raw OCR; no inference, indexing or approval."""
from __future__ import annotations

from pathlib import Path

from .asset_preflight import AssetPreflightError, preflight_transfer_assets
from .diligence import digest
from .ocr_input import OcrInputError, inspect_raw_ocr

VERSION = "registered-document-input-1"


class DocumentInputError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        super().__init__(message)
        self.code, self.path = code, path


def prepare_document_input(root: Path, value: dict, *, manifest_path: Path | None = None) -> dict:
    """Match declarations to a validated registration, then validate raw geometry.

    Matching a caller-supplied source hash is not verification of PDF/image bytes
    or actual model execution. The prepared page is never made queryable here.
    """
    expected = {"schema_version", "provenance", "document_id", "source_pdf_sha256", "image_sha256", "ocr_input"}
    if not isinstance(value, dict) or set(value) != expected or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise DocumentInputError("invalid_input_schema", "$", "Expected a versioned registration and raw OCR envelope")
    if not isinstance(value["document_id"], str) or not isinstance(value["ocr_input"], dict):
        raise DocumentInputError("invalid_input_schema", "document_id/ocr_input", "Expected a document identity and OCR object")
    if value["provenance"] not in ("synthetic", "caller_declared_model_output"):
        raise DocumentInputError("invalid_input_provenance", "provenance", "Input provenance must be explicit; it is not independently verified")
    inventory = preflight_transfer_assets(root, manifest_path)
    selected = next((d for d in inventory["documents"] if d["id"] == value["document_id"]), None)
    if selected is None:
        raise DocumentInputError("unregistered_document", "document_id", "Document is not registered in this input manifest")
    if value["source_pdf_sha256"] != selected["source_pdf_sha256"]:
        raise DocumentInputError("source_declaration_mismatch", "source_pdf_sha256", "Declared PDF hash differs from registration")
    raw = value["ocr_input"]
    if type(raw.get("page")) is not int:
        raise DocumentInputError("invalid_page", "ocr_input.page", "Physical page must be an integer")
    page = next((p for p in selected["pages"] if p["page"] == raw["page"]), None)
    if page is None:
        raise DocumentInputError("unregistered_page", "ocr_input.page", "Page is not registered for this document")
    if value["image_sha256"] != page["image_sha256"]:
        raise DocumentInputError("image_declaration_mismatch", "image_sha256", "Declared image hash differs from registration")
    for name in ("width", "height", "image_width", "image_height"):
        if isinstance(raw.get(name), bool) or raw.get(name) != page[name]:
            raise DocumentInputError("registered_geometry_mismatch", "ocr_input."+name, "Page dimensions differ from registration")
    checked = inspect_raw_ocr(raw)
    return {"schema_version": 1, "version": VERSION, "status": "prepared_not_indexed",
            "provenance": value["provenance"], "input_sha256": digest(value), "asset_preflight": inventory,
            "registration": {"document_id": selected["id"], "source_pdf_sha256": selected["source_pdf_sha256"],
                             "image_sha256": page["image_sha256"], "page": page["page"], "fact_ids": page["fact_ids"]},
            "ocr": checked, "automatic_approval": False, "queryable": False,
            "scope": {"registered_declarations_match": True, "source_pdf_bytes_verified": False,
                      "source_image_bytes_verified": False, "model_execution_verified": False,
                      "financial_content_verified": False, "database_written": False}}


def input_error(exc: Exception) -> dict:
    """Report structured failure without echoing caller documents or file paths."""
    if isinstance(exc, (OcrInputError, AssetPreflightError, DocumentInputError)):
        return {"code": exc.code, "path": exc.path, "stage": "input_preflight"}
    return {"code": "unreadable_or_invalid_input", "path": "$", "stage": "input_preflight"}
