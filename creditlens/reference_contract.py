"""Answer-hidden development review tasks and observer-entered evidence.

This contract validates structure and agreement, not truth, human identity,
independence, OCR quality or a new held-out evaluation. It performs no network
requests and never compares an observation with a prior financial answer.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from .diligence import digest
from .table_lookup import normalize_row_label, parse_lookup_amount, TableLookupError
from .word_table_experiment import load_word_table_lookup_snapshot

REFERENCE_CONTRACT_VERSION = "reference-review-1"
SOURCE_REPORT_SHA256 = "5526c6d496a304c877ebd2b9121ef314bdf3a36c67fa8023ae34505b68255460"
BASIS = {
    "material_status": "previously_observed_development",
    "prior_reference_hidden_in_task": True,
    "prior_model_output_hidden_in_task": True,
    "independent_test": False,
    "human_gold": False,
    "source_authenticity_verified_by_contract": False,
}
OBSERVATION_FIELDS = ("status", "raw_value", "amount_bbox", "unit_text", "currency_text",
                      "section_text", "section_note", "reason")
COMPARED_FIELDS = ("status", "raw_value", "normalized_value", "amount_bbox", "unit_text",
                   "currency_text", "section_text", "section_note")
_TASK_FIELDS = {"id", "doc_id", "source", "page", "width", "height", "coordinate_system", "query", "basis", "task_sha256"}
_NORMAL_FIELDS = {*OBSERVATION_FIELDS, "schema_version", "rule_version", "task_id", "task_sha256", "page",
                  "normalized_value", "observation_sha256"}
_HEX = re.compile(r"[0-9a-f]{64}")


class ReferenceContractError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Reference review contract check failed"):
        self.code, self.path = code, path
        super().__init__(message)


def _require(condition, code, path):
    if not condition:
        raise ReferenceContractError(code, path)


def _object(value, keys, path):
    _require(type(value) is dict and set(value) == set(keys), "invalid_fields", path)


def _text(value, maximum, path, *, nullable=False):
    if value is None and nullable:
        return
    _require(type(value) is str and 0 < len(value) <= maximum and bool(value.strip())
             and not any(ord(c) < 32 and c not in "\t\n\r" for c in value), "invalid_text", path)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ReferenceContractError("invalid_text", path) from None


def _sha(value, path):
    _require(type(value) is str and bool(_HEX.fullmatch(value)), "invalid_sha256", path)


def _number(value, path):
    _require(type(value) in (int, float), "invalid_number", path)
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, "invalid_number", path)
    return float(value)


def _task_id(task):
    identity = [task["doc_id"], task["source"]["pdf_sha256"], task["page"],
                normalize_row_label(task["query"]["row_label"]), task["query"]["year"]]
    return "rq-" + digest(identity)


def validate_reference_task(task: dict) -> dict:
    """Validate a task snapshot without consulting current source files."""
    _object(task, _TASK_FIELDS, "task")
    _require(task["doc_id"] in ("msft", "aapl"), "invalid_document", "task.doc_id")
    _require(type(task["page"]) is int and 1 <= task["page"] <= 20000, "invalid_page", "task.page")
    for name in ("width", "height"):
        value = _number(task[name], "task." + name)
        _require(0 < value <= 20000, "invalid_dimensions", "task." + name)
    _require(task["coordinate_system"] == "pdf_points_top_left", "invalid_coordinates", "task.coordinate_system")
    _object(task["query"], {"row_label", "year"}, "task.query")
    _text(task["query"]["row_label"], 500, "task.query.row_label")
    _require(bool(normalize_row_label(task["query"]["row_label"])), "invalid_query", "task.query.row_label")
    _require(type(task["query"]["year"]) is int and 1900 <= task["query"]["year"] <= 2199, "invalid_year", "task.query.year")
    _object(task["source"], {"url", "pdf_sha256", "image_sha256"}, "task.source")
    _text(task["source"]["url"], 2000, "task.source.url")
    try:
        url = urlsplit(task["source"]["url"])
        valid_url = url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None
    except ValueError:
        valid_url = False
    _require(valid_url, "invalid_source_url", "task.source.url")
    for name in ("pdf_sha256", "image_sha256"):
        _sha(task["source"][name], "task.source." + name)
    _object(task["basis"], set(BASIS), "task.basis")
    _require(all(type(task["basis"][k]) is type(v) and task["basis"][k] == v for k, v in BASIS.items()),
             "invalid_basis", "task.basis")
    _require(task["id"] == _task_id(task), "task_id_mismatch", "task.id")
    _sha(task["task_sha256"], "task.task_sha256")
    _require(digest({k: v for k, v in task.items() if k != "task_sha256"}) == task["task_sha256"], "task_hash_mismatch", "task.task_sha256")
    return deepcopy(task)


def load_reference_registry(root: Path) -> dict:
    """Server-only source registry. Never send task_case_map to the blind form."""
    try:
        snapshot = load_word_table_lookup_snapshot(Path(root))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReferenceContractError("source_unavailable", "source") from exc
    _require(snapshot["report_sha256"] == SOURCE_REPORT_SHA256, "source_version_unsupported", "source")
    tasks, case_map = {}, {}
    for document in snapshot["report"]["documents"]:
        pages = {p["page"]: p for p in document["pages"]}
        for case in document["cases"]:
            page = pages[case["page"]]
            # Explicit DTO allowlist. Do not copy reference, section, fact id,
            # extraction, candidate spans, score, unit answer, or original count.
            task = {
                "doc_id": document["id"],
                "source": {"url": document["source"]["url"], "pdf_sha256": document["source"]["sha256"],
                           "image_sha256": page["image_sha256"]},
                "page": page["page"], "width": page["width"], "height": page["height"],
                "coordinate_system": "pdf_points_top_left",
                "query": {"row_label": case["query"]["row_label"], "year": case["query"]["year"]},
                "basis": deepcopy(BASIS),
            }
            task["id"] = _task_id(task)
            task["task_sha256"] = digest(task)
            validate_reference_task(task)
            if task["id"] in tasks:
                _require(digest(tasks[task["id"]]) == digest(task), "inconsistent_query_identity", "source")
            tasks[task["id"]] = task
            case_map.setdefault(task["id"], []).append(case["id"])
    _require(len(tasks) == 40 and sum(map(len, case_map.values())) == 42, "source_inventory_changed", "source")
    catalog = {"schema_version": 1, "contract_version": REFERENCE_CONTRACT_VERSION,
               "source_report_sha256": SOURCE_REPORT_SHA256, "basis": deepcopy(BASIS),
               "tasks": sorted(tasks.values(), key=lambda t: (t["doc_id"], t["page"], t["query"]["row_label"], t["query"]["year"]))}
    catalog["catalog_sha256"] = digest(catalog)
    return {"catalog": catalog, "task_case_map": case_map}


def build_reference_catalog(root: Path) -> dict:
    """Answer-hidden catalog only; registry mappings stay on the server."""
    return load_reference_registry(root)["catalog"]


def _normalize_data(data, width, height):
    _object(data, OBSERVATION_FIELDS, "observation")
    _require(type(data["status"]) is str and data["status"] in {"answerable", "ambiguous", "unreadable", "insufficient"},
             "invalid_status", "observation.status")
    result = deepcopy(data)
    result["normalized_value"] = None
    if data["status"] != "answerable":
        _require(all(data[k] is None for k in OBSERVATION_FIELDS if k not in {"status", "reason"}),
                 "nonanswer_contains_value", "observation")
        _text(data["reason"], 2000, "observation.reason")
        return result
    _text(data["raw_value"], 100, "observation.raw_value")
    try:
        result["normalized_value"] = parse_lookup_amount(data["raw_value"])
    except TableLookupError:
        raise ReferenceContractError("invalid_literal_amount", "observation.raw_value") from None
    # Parsing is complete and signed. No expected answer or currency inference.
    _require(Decimal(result["normalized_value"]).is_finite(), "invalid_literal_amount", "observation.raw_value")
    _object(data["amount_bbox"], {"x0", "top", "x1", "bottom"}, "observation.amount_bbox")
    box = {k: _number(v, "observation.amount_bbox." + k) for k, v in data["amount_bbox"].items()}
    _require(0 <= box["x0"] < box["x1"] <= width and 0 <= box["top"] < box["bottom"] <= height,
             "bbox_out_of_bounds", "observation.amount_bbox")
    result["amount_bbox"] = box
    _text(data["unit_text"], 1000, "observation.unit_text")
    _text(data["currency_text"], 100, "observation.currency_text", nullable=True)
    _text(data["section_text"], 1000, "observation.section_text", nullable=True)
    _text(data["section_note"], 2000, "observation.section_note", nullable=True)
    _require(data["section_text"] is not None or data["section_note"] is not None, "section_context_missing", "observation")
    _text(data["reason"], 2000, "observation.reason", nullable=True)
    return result


def validate_observation(task: dict, data: dict) -> dict:
    """Normalize observer input; this does not decide whether it matches the PDF."""
    task = validate_reference_task(task)
    content = _normalize_data(data, task["width"], task["height"])
    result = {"schema_version": 1, "rule_version": REFERENCE_CONTRACT_VERSION,
              "task_id": task["id"], "task_sha256": task["task_sha256"], "page": task["page"], **content}
    result["observation_sha256"] = digest(result)
    return result


def _validate_normalized(observation):
    _object(observation, _NORMAL_FIELDS, "observation")
    _require(type(observation["schema_version"]) is int and observation["schema_version"] == 1,
             "unsupported_version", "observation.schema_version")
    _require(observation["rule_version"] == REFERENCE_CONTRACT_VERSION, "unsupported_version", "observation.rule_version")
    _require(type(observation["task_id"]) is str and re.fullmatch(r"rq-[0-9a-f]{64}", observation["task_id"]),
             "invalid_task_id", "observation.task_id")
    _sha(observation["task_sha256"], "observation.task_sha256")
    _require(type(observation["page"]) is int and 1 <= observation["page"] <= 20000, "invalid_page", "observation.page")
    expected = _normalize_data({k: observation[k] for k in OBSERVATION_FIELDS}, 20000, 20000)
    _require(type(observation["normalized_value"]) is type(expected["normalized_value"])
             and observation["normalized_value"] == expected["normalized_value"],
             "observation_normalization_mismatch", "observation.normalized_value")
    _require(digest(expected) == digest({k: observation[k] for k in [*OBSERVATION_FIELDS, "normalized_value"]}),
             "observation_normalization_mismatch", "observation")
    _sha(observation["observation_sha256"], "observation.observation_sha256")
    _require(digest({k: v for k, v in observation.items() if k != "observation_sha256"}) == observation["observation_sha256"],
             "observation_hash_mismatch", "observation.observation_sha256")


def compare_observations(first: dict, second: dict) -> dict:
    """Strict content agreement; workflow identities are checked by the store."""
    _validate_normalized(first)
    _validate_normalized(second)
    _require(all(first[k] == second[k] for k in ("task_id", "task_sha256", "page")), "task_binding_mismatch", "observations")
    differences = [{"field": k, "first": deepcopy(first[k]), "second": deepcopy(second[k])}
                   for k in COMPARED_FIELDS if digest(first[k]) != digest(second[k])]
    return {"schema_version": 1, "rule_version": REFERENCE_CONTRACT_VERSION,
            "status": "disagreement" if differences else "exact_agreement",
            "answer_status": first["status"] if first["status"] == second["status"] else None,
            "task_id": first["task_id"], "task_sha256": first["task_sha256"],
            "first_sha256": first["observation_sha256"], "second_sha256": second["observation_sha256"],
            "compared_fields": list(COMPARED_FIELDS), "differences": differences,
            "notes_differ": first["reason"] != second["reason"],
            "human_gold": False, "automatic_approval": False,
            "limitations": ["Agreement is limited to the listed content fields; free reason notes are retained but do not decide agreement.",
                            "No identity, source authenticity or observation correctness is established by matching content or hashes.",
                            "Both observations concern previously used development material; agreement is not a new independent benchmark."]}
