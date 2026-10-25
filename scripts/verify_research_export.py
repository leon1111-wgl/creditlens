"""Check a research export's internal hashes and bindings without a server.

This is an unsigned consistency check, not a signature, source-authenticity,
financial-correctness or principal-identity verification. Only the given JSON
path is read; no application, database, additional file or HTTP/API request is
used. This is not an operating-system network sandbox for mapped file systems.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys


MAX_FILE_BYTES = 2 * 1024 * 1024
_SHA = re.compile(r"^[0-9a-f]{64}$")
_EXPORT_KEYS = {"schema_version", "task_id", "snapshot", "review", "review_history",
                "editable_draft_included", "automatic_approval", "scope"}
_SNAPSHOT_KEYS = {"id", "sha256", "created_at", "submitted_by", "revision", "content"}
_REVIEW_KEYS = {"actor", "at", "decision", "note", "revision", "snapshot_id", "snapshot_sha256"}
_CONTENT_KEYS = {"schema_version", "store_version", "task_id", "tenant_id", "task_revision",
                 "submitted_by", "submitted_at", "question", "selection_reason", "material_mode", "as_of",
                 "bundle_id", "bundle", "bundle_sha256", "materials_frozen_at", "decisions", "hypotheses",
                 "hypothesis_scope", "coverage_note", "comparison", "created_by", "contributors", "automatic_approval"}


class ExportError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ExportError(code, message)


def canonical_bytes(value) -> bytes:
    """Exactly ResearchTaskStore's Python JSON encoding, including 792.0."""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ExportError("invalid_json_value", "Content is not finite UTF-8 JSON") from exc


def _object(value, keys: set[str], label: str) -> None:
    _require(type(value) is dict and set(value) == keys, "invalid_fields", f"Unexpected or missing {label} fields")


def _string(value, label: str, limit=4000) -> None:
    _require(type(value) is str and bool(value.strip()) and len(value) <= limit,
             "invalid_field_type", f"Invalid {label}")


def _positive_int(value, label: str) -> None:
    _require(type(value) is int and value > 0, "invalid_field_type", f"Invalid {label}")


def _timestamp(value, label: str) -> None:
    _string(value, label, 100)
    try:
        parsed = datetime.fromisoformat(value)
        _require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
                 "invalid_field_type", f"Timezone missing in {label}")
    except ValueError as exc:
        if isinstance(exc, ExportError):
            raise
        raise ExportError("invalid_field_type", f"Invalid {label}") from exc


def _hash(value, label: str) -> None:
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             "invalid_field_type", f"Invalid {label}")


def verify_export(document: dict) -> dict:
    """Validate the current store export envelope, content and review bindings.

    The financial bundle and calculation contents are hashed, not rerun. The
    export has no external tenant identity or separate signed review digest.
    """
    canonical_bytes(document)  # Reject nonfinite values even outside content.
    _object(document, _EXPORT_KEYS, "export")
    _require(type(document["schema_version"]) is int and document["schema_version"] == 1,
             "unsupported_schema", "Expected export schema_version 1")
    _string(document["task_id"], "task_id", 256)
    _string(document["scope"], "scope")
    _require(document["editable_draft_included"] is False and document["automatic_approval"] is False,
             "invalid_export_scope", "Export must contain a frozen snapshot with no automatic approval")
    snapshot = document["snapshot"]
    _object(snapshot, _SNAPSHOT_KEYS, "snapshot")
    _string(snapshot["id"], "snapshot id", 256)
    _hash(snapshot["sha256"], "snapshot sha256")
    _positive_int(snapshot["revision"], "snapshot revision")
    _string(snapshot["submitted_by"], "submitted_by", 256)
    _timestamp(snapshot["created_at"], "snapshot created_at")
    content = snapshot["content"]
    _object(content, _CONTENT_KEYS, "snapshot content")
    _require(type(content["schema_version"]) is int and content["schema_version"] == 1
             and content["store_version"] == "research-handoff-1",
             "unsupported_schema", "Unsupported research snapshot version")
    for key in ("task_id", "tenant_id", "submitted_by", "created_by", "bundle_id"):
        _string(content[key], key, 256)
    for key in ("question", "selection_reason", "hypothesis_scope"):
        _string(content[key], key)
    _require(type(content["coverage_note"]) is str and len(content["coverage_note"]) <= 4000,
             "invalid_field_type", "Invalid coverage_note")
    _timestamp(content["submitted_at"], "submitted_at")
    _timestamp(content["materials_frozen_at"], "materials_frozen_at")
    _positive_int(content["task_revision"], "task_revision")
    _hash(content["bundle_sha256"], "bundle_sha256")
    _require(type(content["bundle"]) is dict and type(content["comparison"]) is dict,
             "invalid_field_type", "Bundle and comparison must be JSON objects")
    for key in ("decisions", "hypotheses", "contributors"):
        _require(type(content[key]) is list, "invalid_field_type", f"Invalid {key}")
    _require(bool(content["contributors"]), "invalid_field_type", "Contributors must not be empty")
    for actor in content["contributors"]:
        _string(actor, "contributor", 256)
    _require(len(content["contributors"]) == len(set(content["contributors"])), "invalid_fields", "Duplicate contributors")
    _require(content["material_mode"] == "current_verified_materials" and content["as_of"] is None
             and content["automatic_approval"] is False,
             "invalid_export_scope", "Unsupported historical or automatically approved snapshot")
    _require(document["task_id"] == content["task_id"] and snapshot["revision"] == content["task_revision"]
             and snapshot["submitted_by"] == content["submitted_by"] and snapshot["created_at"] == content["submitted_at"],
             "snapshot_binding_mismatch", "Snapshot metadata does not match its content")
    calculated = hashlib.sha256(canonical_bytes(content)).hexdigest()
    _require(calculated == snapshot["sha256"], "snapshot_hash_mismatch", "Snapshot content SHA-256 does not match")
    _require(hashlib.sha256(canonical_bytes(content["bundle"])).hexdigest() == content["bundle_sha256"],
             "bundle_hash_mismatch", "Embedded bundle SHA-256 does not match")
    history = document["review_history"]
    _require(type(history) is list, "invalid_field_type", "review_history must be a list")
    previous_revision = snapshot["revision"]
    for review in history:
        _object(review, _REVIEW_KEYS, "review")
        _string(review["actor"], "review actor", 256)
        _string(review["note"], "review note", 2000)
        _timestamp(review["at"], "review at")
        _positive_int(review["revision"], "review revision")
        _hash(review["snapshot_sha256"], "review snapshot_sha256")
        _require(review["decision"] in ("approve", "return"), "invalid_field_type", "Invalid review decision")
        _require(review["snapshot_id"] == snapshot["id"] and review["snapshot_sha256"] == snapshot["sha256"],
                 "review_binding_mismatch", "Review refers to another snapshot or hash")
        _require(review["revision"] > previous_revision, "review_revision_mismatch", "Review revisions are not after the snapshot in increasing order")
        previous_revision = review["revision"]
    expected_review = history[-1] if history else None
    _require(canonical_bytes(document["review"]) == canonical_bytes(expected_review),
             "review_history_mismatch", "Selected review does not match the last review for this snapshot")
    return {
        "ok": True, "task_id": document["task_id"], "snapshot_id": snapshot["id"],
        "content_sha256": calculated, "review_count": len(history),
        "checks": ["python_canonical_content_sha256", "embedded_bundle_sha256", "snapshot_metadata_binding", "review_snapshot_binding"],
        "scope": "Unsigned internal consistency only; no signature, source authenticity, financial calculation or principal identity verification",
        "tenant_scope": "tenant_id is covered by the content hash; no external tenant identity is available",
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExportError("duplicate_json_key", "JSON contains a duplicate object key")
        result[key] = value
    return result


def _finite_float(value: str) -> float:
    result = float(value)
    _require(math.isfinite(result), "nonfinite_number", "JSON contains a nonfinite number")
    return result


def _constant(_value: str):
    raise ExportError("nonfinite_number", "NaN and Infinity are not JSON export values")


def verify_file(path: Path) -> dict:
    raw_path = str(path)
    _require(not raw_path.startswith(("\\\\", "//")) and "://" not in raw_path,
             "nonlocal_path", "Only a local JSON file is accepted")
    try:
        _require(path.is_file(), "invalid_file", "Input is not a regular file")
        _require(path.stat().st_size <= MAX_FILE_BYTES, "file_too_large", "Export exceeds the 2 MiB limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        _require(len(raw) <= MAX_FILE_BYTES, "file_too_large", "Export exceeds the 2 MiB limit")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_float=_finite_float, parse_constant=_constant)
    except ExportError:
        raise
    except OSError as exc:
        raise ExportError("invalid_file", "Cannot read the local export") from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ExportError("invalid_json", "Input is not valid bounded UTF-8 JSON") from exc
    return verify_export(document)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Local exported research JSON (maximum 2 MiB)")
    args = parser.parse_args(argv)
    try:
        result = verify_file(args.export)
        status = 0
    except ExportError as exc:
        result = {"ok": False, "code": exc.code, "error": str(exc),
                  "scope": "Unsigned internal consistency check only"}
        status = 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return status


if __name__ == "__main__":
    sys.exit(main())
