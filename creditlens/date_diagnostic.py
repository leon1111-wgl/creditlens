"""Recompute an observed date-rule diagnostic from reviewed public evidence.

This does not run OCR, inspect omitted OCR, or verify source image/PDF bytes.
The existing transfer release stays the immutable comparison input.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from .date_evidence import DATE_RULE_VERSION, crosscheck_date_fact
from .diligence import digest
from .document_transfer import TransferPack, file_hash
from .transfer_store import MANIFEST, RELEASE, REPORT, transfer_snapshot
from .word_evidence import WordLine, WordPage

VERSION = "date-diagnostic-1"
STATES = ("consistent", "conflict", "insufficient")
IMPLEMENTATION_FILES = (
    "creditlens/date_evidence.py", "creditlens/date_diagnostic.py",
    "creditlens/transfer_store.py", "scripts/evaluate_date_diagnostic.py",
)
SCOPE = {
    "operation": "date_rule_diagnostic_on_published_evidence",
    "evidence": "published_necessary_parent_lines_and_complete_word_groups",
    "evaluation": "observed_known_location_development_diagnostic",
    "model_inference_performed": False,
    "blind_evaluation": False,
    "full_ocr_verified": False,
    "image_pdf_bytes_verified": False,
    "source_model_execution_verified": False,
    "date_group_status_basis": "year_check_after_optional_complete_date_association",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _pairs(items):
    value = {}
    for key, item in items:
        _require(key not in value, "Duplicate JSON key")
        value[key] = item
    return value


def _float(text):
    value = float(text)
    _require(math.isfinite(value), "Non-finite JSON number")
    return value


def _constant(text):
    raise ValueError("Non-finite JSON number: " + text)


def read_json(path: Path, *, limit: int = 15_000_000) -> tuple[bytes, dict]:
    """Bounded strict JSON read; duplicate keys and non-finite values fail closed."""
    path = Path(path)
    _require(path.is_file() and path.stat().st_size <= limit, "Missing or oversized JSON input")
    raw = path.read_bytes()
    _require(len(raw) <= limit, "Oversized JSON input")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_float=_float, parse_constant=_constant)
    _require(isinstance(value, dict), "JSON report must be an object")
    return raw, value


def _source(root: Path) -> tuple[dict, dict, dict[str, TransferPack], float]:
    """Bind returned public evidence to raw bytes, rejecting changed snapshots."""
    source = transfer_snapshot(root)
    report_raw, report = read_json(root/REPORT, limit=10_000_000)
    manifest_raw, manifest = read_json(root/MANIFEST, limit=1_000_000)
    release_raw, release = read_json(root/RELEASE, limit=1_000_000)
    hashes = {REPORT: hashlib.sha256(report_raw).hexdigest(),
              MANIFEST: hashlib.sha256(manifest_raw).hexdigest()}
    _require(source["report"] == report and source["manifest"] == manifest,
             "Published transfer snapshot changed during diagnostic input capture")
    _require(release.get("schema_version") == 1 and release.get("files") == hashes and
             report["manifest_sha256"] == hashes[MANIFEST],
             "Captured source bytes no longer match the reviewed release")
    packs = {}
    references = {}
    for doc in manifest["documents"]:
        path = (root/doc["pack_path"]).resolve()
        _require(path.is_relative_to((root/"data/examples").resolve()), "Reference path escaped its directory")
        raw, value = read_json(path, limit=5_000_000)
        actual = hashlib.sha256(raw).hexdigest()
        _require(actual == doc["pack_sha256"], "Source reference pack changed")
        packs[doc["id"]] = TransferPack.model_validate(value)
        references[doc["id"]] = {"path": doc["pack_path"], "sha256": actual}
    profile_raw, profile = read_json(root/"configs/document-ai.json", limit=1_000_000)
    _require(hashlib.sha256(profile_raw).hexdigest() == manifest["files"]["configs/document-ai.json"],
             "Source OCR profile changed")
    provenance = {
        "report_path": REPORT, "report_sha256": hashes[REPORT],
        "manifest_path": MANIFEST, "manifest_sha256": hashes[MANIFEST],
        "release_path": RELEASE, "release_sha256": hashlib.sha256(release_raw).hexdigest(),
        "experiment_id": report["experiment_id"], "report_generated_at": report["generated_at"],
        "reference_packs": references,
    }
    return source, provenance, packs, profile["min_confidence"]


def _generated_at(value, source_time: str) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    elif isinstance(value, str):
        value = datetime.fromisoformat(value)
    _require(isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None,
             "Diagnostic generated_at must include a timezone")
    _require(datetime.fromisoformat(source_time) <= value <= datetime.now(timezone.utc),
             "Diagnostic time must follow the source report and cannot be in the future")
    return value.astimezone(timezone.utc).isoformat()


def _year(result: dict) -> dict:
    checks = [check for check in result["checks"] if check["field"] == "year"]
    _require(len(checks) == 1, "Exactly one year check is required")
    return checks[0]


def _counts(items: list[dict], method: str) -> dict:
    return {state: sum(item[method]["status"] == state for item in items) for state in STATES}


def _summary(items: list[dict], groups: list[dict], pages: int, documents: int) -> dict:
    return {
        "facts": len(items), "distinct_documents": documents, "distinct_pages": pages,
        "distinct_date_groups": len(groups),
        "legacy_line": _counts(items, "legacy_line"), "legacy_word": _counts(items, "legacy_word"),
        "date": _counts(items, "date"),
        "word_to_date": {f"{old}->{new}": sum(item["legacy_word"]["status"] == old and
                           item["date"]["status"] == new for item in items)
                         for old in STATES for new in STATES},
        "date_group_status": {state: sum(group["status"] == state for group in groups) for state in STATES},
        "date_association_applied": sum(item["date"]["date_association"]["applied"] for item in items),
        "non_year_unchanged": all(item["non_year_unchanged"] for item in items),
    }


def _build(root: Path, source: dict, provenance: dict, packs: dict, threshold: float, generated_at=None) -> dict:
    report = source["report"]
    documents, all_items, all_groups = [], [], []
    page_count = 0
    for doc in report["documents"]:
        pack = packs[doc["id"]]
        by_id = {fact.id: fact for fact in pack.facts}
        legacy = {pair["fact_id"]: pair for pair in doc["facts"]}
        items, groups, pages = [], {}, []
        for registered in doc["pages"]:
            page = WordPage(page=registered["page"], width=registered["width"], height=registered["height"],
                            lines=[WordLine.model_validate({k: line[k] for k in ("line", "words")})
                                   for line in registered["evidence_lines"]])
            _require(digest(page.model_dump(mode="json")) == registered["evidence_sha256"],
                     "Published evidence digest changed")
            pages.append({k: deepcopy(registered[k]) for k in (
                "page", "width", "height", "image_sha256", "evidence_sha256", "full_line_ocr_sha256",
                "full_word_ocr_sha256", "fact_ids")})
            for fid in registered["fact_ids"]:
                fact, old = by_id[fid], legacy[fid]
                loc = fact.pdf_location
                result = crosscheck_date_fact(fact, page, min_confidence=threshold)
                unchanged = ([c for c in old["word"]["checks"] if c["field"] != "year"] ==
                             [c for c in result["checks"] if c["field"] != "year"])
                _require(unchanged, "Date diagnostic changed a non-year field")
                _require(result.get("automatic_approval") is False and
                         result.get("date_rule_version") == DATE_RULE_VERSION and
                         result["date_association"]["legacy_year"] == _year(old["word"]),
                         "Date rule lost its legacy check or approval/version boundary")
                identity = {"document_id": doc["id"], "source_pdf_sha256": doc["raw_pdf_sha256"],
                            "page": page.page, "year_bbox": loc.year_bbox.model_dump(mode="json"),
                            "period_bbox": loc.period_bbox.model_dump(mode="json")}
                group_id = digest(identity)
                expected = {"year": loc.year, "period_start": fact.period_start.isoformat() if fact.period_start else None,
                            "period_end": fact.period_end.isoformat()}
                if group_id not in groups:
                    groups[group_id] = {"id": group_id, **identity, "expected": expected,
                                        "fact_ids": [], "year_statuses": [], "date_association_applied_facts": 0}
                group = groups[group_id]
                _require(group["expected"] == expected, "Shared date header has conflicting reference periods")
                group["fact_ids"].append(fid)
                group["year_statuses"].append(_year(result)["status"])
                group["date_association_applied_facts"] += int(result["date_association"]["applied"])
                items.append({"fact_id": fid, "page": page.page, "date_group_id": group_id,
                              "legacy_line": deepcopy(old["line"]), "legacy_word": deepcopy(old["word"]),
                              "date": result, "non_year_unchanged": unchanged})
        for group in groups.values():
            statuses = group.pop("year_statuses")
            group["status"] = ("conflict" if "conflict" in statuses else
                               "insufficient" if "insufficient" in statuses else "consistent")
        group_list = list(groups.values())
        documents.append({"id": doc["id"], "company": doc["company"], "pages": pages,
                          "date_groups": group_list, "facts": items,
                          "summary": _summary(items, group_list, len(pages), 1)})
        all_items.extend(items); all_groups.extend(group_list); page_count += len(pages)
    return {
        "schema_version": 1, "suite_version": VERSION,
        "generated_at": _generated_at(generated_at, report["generated_at"]),
        "source": provenance, "rule_versions": {**report["rule_versions"], "date": DATE_RULE_VERSION},
        "implementation": {"hash_basis": "python_utf8_lf", "files": {
            relative: file_hash(root/relative) for relative in IMPLEMENTATION_FILES}},
        "min_confidence": threshold, "scope": deepcopy(SCOPE), "automatic_approval": False,
        "documents": documents, "summary": _summary(all_items, all_groups, page_count, len(documents)),
    }


def build_date_diagnostic(root: Path, *, generated_at=None) -> dict:
    root = Path(root)
    source, provenance, packs, threshold = _source(root)
    return _build(root, source, provenance, packs, threshold, generated_at)


def date_diagnostic_view(root: Path, report: dict) -> dict:
    """Recompute every field and return the same verified source used to do so."""
    _require(isinstance(report, dict) and isinstance(report.get("generated_at"), str),
             "Diagnostic must contain a generated_at string")
    root = Path(root)
    source, provenance, packs, threshold = _source(root)
    expected = _build(root, source, provenance, packs, threshold, report["generated_at"])
    _require(report == expected and digest(report) == digest(expected),
             "Date diagnostic does not reproduce from the current reviewed source and rules")
    return {"report": expected, "source": source, "read_only": True,
            "validation_scope": "Recomputed every date-rule outcome and unchanged non-year field from reviewed published necessary evidence. Omitted OCR, original image/PDF bytes and actual model execution are not checked; this is an observed development diagnostic, not a new inference or blind evaluation."}


def validate_date_diagnostic(root: Path, report: dict) -> dict:
    return date_diagnostic_view(root, report)["report"]
