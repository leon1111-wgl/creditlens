"""Compare a verified historical attempt with current registered metadata.

The caller must first run verify_preparation_export. This module assesses
applicability of that result; it neither authenticates exports nor reruns OCR.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from .asset_preflight import AssetPreflightError, VERSION as ASSET_VERSION, preflight_transfer_assets
from .batch_attempt_store import STORE_VERSION
from .batch_input import VERSION as BATCH_VERSION
from .diligence import digest
from .document_input import VERSION as DOCUMENT_INPUT_VERSION
from .ocr_input import OCR_INPUT_VERSION

VERSION = "preparation-applicability-1"
_BINDING_FIELDS = ("manifest_sha256", "inventory_sha256", "document_id", "source_pdf_sha256",
                   "pack_sha256", "pack_canonical_sha256", "registered_document_sha256")
_DOCUMENT_FIELDS = ("document_id", "source_pdf_sha256", "pack_sha256", "pack_canonical_sha256", "registered_document_sha256")
_VERIFIED_FIELDS = {"schema_version", "verification_version", "status", "task_id", "attempt_id", "revision", "document_id",
                    "provenance", "preparation_status", "material_binding", "expected_pages", "page_registrations", "versions", "store_version",
                    "export_sha256", "report_sha256", "input_sha256", "material_binding_sha256", "limitations",
                    "authenticated", "source_bytes_verified", "ocr_quality_verified", "queryable", "automatic_approval"}
_FALSE_FIELDS = ("authenticated", "source_bytes_verified", "ocr_quality_verified", "queryable", "automatic_approval")
SCOPE = {"assessment": "registered_metadata_applicability_at_this_explicit_check",
         "historical_export_rewritten": False, "source_bytes_verified": False, "ocr_quality_verified": False,
         "authenticated": False, "financial_content_verified": False, "queryable": False, "automatic_approval": False,
         "operating_system_lock": False}
LIMITATIONS = [
    "The verified summary must come from verify_preparation_export; accepting a summary is not authentication of its author or source.",
    "Matching registration and versions only permit the next content review of a historically prepared attempt; no financial fact is approved or indexed.",
    "Changed or absent registration describes local metadata availability, not a new official filing, PDF revision, nondisclosure or invalidation of historical records.",
    "Two equal reads reduce mixed-version use but are not an operating-system lock, do not detect every ABA change, and cannot guarantee future applicability.",
    "PDF/image bytes and raw OCR were not inspected; a missing current registration does not undo historical internal consistency.",
]


class PreparationApplicabilityError(ValueError):
    def __init__(self, code: str, path: str):
        self.code, self.path = code, path
        super().__init__(f"{code} at {path}")


def _require(condition, path):
    if not condition:
        raise PreparationApplicabilityError("invalid_verified_preparation", path)


def _sha(value):
    return type(value) is str and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _verified(value):
    _require(type(value) is dict and set(value) == _VERIFIED_FIELDS, "verified")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and type(value["verification_version"]) is str and value["verification_version"] == "preparation-export-1"
             and type(value["status"]) is str and value["status"] == "internally_consistent", "verified.status")
    _require(type(value["revision"]) is int and value["revision"] >= 1, "verified.revision")
    for key in ("task_id", "attempt_id"):
        _require(type(value[key]) is str and bool(re.fullmatch(r"[0-9a-f]{32}", value[key])), "verified."+key)
    _require(type(value["document_id"]) is str and 0 < len(value["document_id"]) <= 256, "verified.document_id")
    _require(type(value["provenance"]) is str and value["provenance"] in ("synthetic", "caller_declared_model_output"), "verified.provenance")
    _require(type(value["preparation_status"]) is str and value["preparation_status"] in ("blocked", "prepared_not_indexed"),
             "verified.preparation_status")
    for key in _FALSE_FIELDS:
        _require(value[key] is False, "verified."+key)
    for key in ("export_sha256", "report_sha256", "input_sha256", "material_binding_sha256"):
        _require(_sha(value[key]), "verified."+key)
    binding = value["material_binding"]
    _require(type(binding) is dict and set(binding) == set(_BINDING_FIELDS), "verified.material_binding")
    _require(type(binding["document_id"]) is str and binding["document_id"] == value["document_id"]
             and all(_sha(binding[k]) for k in _BINDING_FIELDS if k != "document_id")
             and digest(binding) == value["material_binding_sha256"], "verified.material_binding")
    versions = value["versions"]
    _require(type(versions) is dict and set(versions) == {"batch", "document_input", "ocr_input", "asset_preflight"}
             and all(type(v) is str and 0 < len(v) <= 256 for v in versions.values()), "verified.versions")
    _require(type(value["store_version"]) is str and 0 < len(value["store_version"]) <= 256, "verified.store_version")
    pages = value["expected_pages"]
    _require(type(pages) is list and 1 <= len(pages) <= 32 and all(type(p) is int and 1 <= p <= 20000 for p in pages), "verified.expected_pages")
    _require(pages == sorted(set(pages)), "verified.expected_pages")
    registrations = value["page_registrations"]
    _require(type(registrations) is list and len(registrations) <= 32, "verified.page_registrations")
    previous_index = -1
    for index, item in enumerate(registrations):
        path = f"verified.page_registrations[{index}]"
        _require(type(item) is dict and set(item) == {"input_index", "page", "document_id", "source_pdf_sha256", "image_sha256", "fact_ids"}, path)
        _require(type(item["input_index"]) is int and previous_index < item["input_index"] <= 31, path+".input_index")
        previous_index = item["input_index"]
        _require(type(item["page"]) is int and 1 <= item["page"] <= 20000, path+".page")
        _require(type(item["document_id"]) is str and item["document_id"] == value["document_id"]
                 and item["source_pdf_sha256"] == binding["source_pdf_sha256"] and _sha(item["image_sha256"]), path)
        _require(type(item["fact_ids"]) is list and 1 <= len(item["fact_ids"]) <= 100
                 and all(type(fid) is str and 0 < len(fid) <= 256 for fid in item["fact_ids"]), path+".fact_ids")
        _require(len(item["fact_ids"]) == len(set(item["fact_ids"])), path+".fact_ids")
    if value["preparation_status"] == "prepared_not_indexed":
        _require(sorted(item["page"] for item in registrations) == pages, "verified.page_registrations")
    _require(type(value["limitations"]) is list and len(value["limitations"]) <= 30
             and all(type(v) is str and len(v) <= 2000 for v in value["limitations"]), "verified.limitations")
    return deepcopy(value)


def _versions():
    return {"batch": BATCH_VERSION, "document_input": DOCUMENT_INPUT_VERSION,
            "ocr_input": OCR_INPUT_VERSION, "asset_preflight": ASSET_VERSION}


def _current_binding(inventory, document_id):
    if (type(inventory) is not dict or inventory.get("status") != "passed" or not _sha(inventory.get("manifest_sha256"))
            or type(inventory.get("documents")) is not list):
        raise ValueError("Invalid current registration")
    validation = inventory.get("validation", {})
    if any(validation.get(k) is not True for k in ("registered_files_checked", "strict_json_checked", "pack_bytes_checked", "path_aliases_checked")):
        raise ValueError("Current registration was not read from its registered snapshot")
    matches = [d for d in inventory["documents"] if d["id"] == document_id]
    if len(matches) > 1:
        raise ValueError("Duplicate current document")
    if not matches:
        return None
    doc = matches[0]
    binding = {"manifest_sha256": inventory["manifest_sha256"], "inventory_sha256": digest(inventory),
               "document_id": doc["id"], "source_pdf_sha256": doc["source_pdf_sha256"],
               "pack_sha256": doc["pack_sha256"], "pack_canonical_sha256": doc["pack_canonical_sha256"],
               "registered_document_sha256": digest(doc)}
    if not all(_sha(binding[k]) for k in _BINDING_FIELDS if k != "document_id"):
        raise ValueError("Invalid current document binding")
    return binding


def _current_pages(inventory, document_id):
    document = next((d for d in inventory["documents"] if d["id"] == document_id), None)
    if document is None:
        return None
    return [{"page": p["page"], "document_id": document_id, "source_pdf_sha256": document["source_pdf_sha256"],
             "image_sha256": p["image_sha256"], "fact_ids": deepcopy(p["fact_ids"])}
            for p in sorted(document["pages"], key=lambda p:p["page"])]


def assess_preparation_applicability(root: Path, verified: dict, manifest_path: Path | None = None) -> dict:
    """Return a deterministic current check while preserving the historical input.

Current registration problems are results, not failures of historical export
verification. Invalid purported verification summaries raise a typed error.
"""
    old = _verified(verified)
    versions = _versions()
    store_version = STORE_VERSION
    version_changes = ["versions."+k for k in sorted(versions) if versions[k] != old["versions"][k]]
    if store_version != old["store_version"]:
        version_changes.append("store_version")
    result = {"schema_version": 1, "assessment_version": VERSION, "status": "registration_unavailable",
              "reason_code": "current_registration_unavailable", "changed_fields": list(version_changes),
              "task_id": old["task_id"], "attempt_id": old["attempt_id"], "revision": old["revision"],
              "export_sha256": old["export_sha256"], "report_sha256": old["report_sha256"],
              "historical_preparation_status": old["preparation_status"],
              "historical": {"material_binding": old["material_binding"], "material_binding_sha256": old["material_binding_sha256"],
                             "expected_pages": old["expected_pages"], "page_registrations": old["page_registrations"],
                             "versions": old["versions"], "store_version": old["store_version"]},
              "current": {"material_binding": None, "material_binding_sha256": None, "manifest_sha256": None,
                          "inventory_sha256": None, "registered_pages": None, "page_registrations": None,
                          "versions": versions, "store_version": store_version},
              "registration_stable": False, "versions_match": not version_changes, "ready_for_content_review": False,
              "scope": deepcopy(SCOPE), "limitations": list(LIMITATIONS)}
    try:
        first = preflight_transfer_assets(Path(root), manifest_path)
        current = _current_binding(first, old["document_id"])
    except (AssetPreflightError, OSError, ValueError, TypeError, KeyError, RuntimeError):
        return result
    try:
        second = preflight_transfer_assets(Path(root), manifest_path)
        after = _current_binding(second, old["document_id"])
        versions_stable = versions == _versions() and store_version == STORE_VERSION
        if not versions_stable:
            result["versions_match"] = False
        if (digest(first) != digest(second) or current != after or not versions_stable):
            raise ValueError("Current registration changed during assessment")
    except (AssetPreflightError, OSError, ValueError, TypeError, KeyError, RuntimeError):
        result["reason_code"] = "registration_changed_during_assessment"
        result["changed_fields"] = ["current_registration_snapshot"]+version_changes
        return result
    result["registration_stable"] = True
    page_registrations = _current_pages(first, old["document_id"])
    result["current"].update(material_binding=current,
                             material_binding_sha256=digest(current) if current is not None else None,
                             manifest_sha256=first["manifest_sha256"], inventory_sha256=digest(first),
                             page_registrations=page_registrations,
                             registered_pages=[p["page"] for p in page_registrations] if page_registrations is not None else None)
    if current is None:
        result.update(status="document_not_registered", reason_code="document_not_in_current_registration",
                      changed_fields=["document_registration"]+version_changes)
        return result
    binding_changes = ["material_binding."+key for key in _BINDING_FIELDS if current[key] != old["material_binding"][key]]
    page_changes = []
    if result["current"]["registered_pages"] != old["expected_pages"]:
        page_changes.append("expected_pages")
    by_page = {p["page"]:p for p in page_registrations}
    for index, registered in enumerate(old["page_registrations"]):
        current_page = by_page.get(registered["page"])
        if current_page is None:
            page_changes.append(f"page_registrations[{index}].page")
        else:
            page_changes.extend(f"page_registrations[{index}].{key}" for key in current_page if current_page[key] != registered[key])
    result["changed_fields"] = binding_changes+page_changes+version_changes
    if any(current[key] != old["material_binding"][key] for key in _DOCUMENT_FIELDS):
        result.update(status="document_registration_changed", reason_code="selected_document_registration_changed")
    elif binding_changes:
        result.update(status="inventory_changed", reason_code="global_inventory_changed")
    else:
        result["status"] = "matching_registration"
        if "expected_pages" in page_changes:
            result["reason_code"] = "historical_page_set_mismatch"
        elif page_changes:
            result["reason_code"] = "historical_page_inventory_mismatch"
        elif version_changes:
            result["reason_code"] = "preparation_versions_changed"
        elif old["preparation_status"] != "prepared_not_indexed":
            result["reason_code"] = "historical_preparation_blocked"
        else:
            result.update(reason_code="matching_registration", ready_for_content_review=True)
    return result
