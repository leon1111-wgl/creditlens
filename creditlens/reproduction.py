"""Read-only comparison of candidate reports with a reviewed release snapshot.

This validates published evidence, not unavailable complete OCR/image bytes or
whether the declared provider really ran. No manifest or release file is changed.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from .diligence import PdfBox, digest
from .experiments import FILES, _stress_checks, _word_checks, experiment_view, indexed, require
from .experiment_paths import default_report_path
from .visual import OcrPage, compare_region
from .visual_store import VisualArtifactError, VisualStore

KINDS = ("line", "word", "stress")
SCOPE = {
    "checked": ["reviewed release manifest and registered sources", "candidate profile and complete identity sets",
                "published line observations rechecked by current line rules",
                "word/stress reference bindings, field structure and recomputed summaries",
                "all published fact fields including observations, parent lines and guards",
                "declared OCR digests compared separately from run metadata"],
    "not_checked": ["complete original OCR/WordPage bytes and their declared digests",
                    "source/variant image bytes or the claimed inference execution/provider",
                    "unpublished consistent stress-field observations", "unseen-document accuracy or performance reproduction"],
    "matched_means": "The same published report evidence and decisions; runtime metadata may differ. Not proof of identical algorithms, execution, performance or complete raw OCR.",
}


def default_candidates(root: Path) -> dict[str, Path]:
    return {kind: default_report_path(root, experiment)
            for kind, experiment in zip(KINDS, ("document-ai", "document-ai-words", "visual-robustness"))}


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value):
    raise VisualArtifactError("Non-finite JSON number")


def _finite_float(value):
    result = float(value)
    require(math.isfinite(result), "Non-finite JSON number")
    return result


def _read_candidate(path: Path):
    require(path.suffix.lower() == ".json", "Candidate must be a JSON report")
    require(path.is_file(), "Candidate report missing")
    require(path.stat().st_size <= 25_000_000, "Candidate report too large")
    raw = path.read_bytes()
    report = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant, parse_float=_finite_float)
    require(isinstance(report, dict), "Candidate report must be an object")
    return report, sha256(raw).hexdigest()


def _keys(value, reference, *, optional=()):
    require(isinstance(value, dict) and set(reference) <= set(value) <= set(reference) | set(optional),
            "Missing or unexpected report fields")


def _hash(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "Invalid declared SHA-256")


def _spans(items, page):
    require(isinstance(items, list), "Observations must be a list")
    return OcrPage(page=page["page"], width=page["width"], height=page["height"], spans=items)


def _word_fields(document, baseline_document, min_confidence):
    """Validate public word evidence structure; full token reconstruction is unavailable."""
    pages = indexed(document["pages"], "page")
    references = indexed(baseline_document["facts"], "fact_id")
    parents_by_page = {}
    for fact in document["facts"]:
        _keys(fact, references[fact["fact_id"]])
        page = pages[fact["page"]]
        require(fact["current_line_status"] in {"consistent", "conflict", "insufficient"}, "Invalid current line state")
        for check in fact["checks"]:
            require(set(check) == {"field", "expected", "region", "observations", "status", "reason",
                                   "parent_lines", "confidence_basis", "word_guards", "guard_observations"},
                    "Word field evidence incomplete")
            observed = _spans(check["observations"], page)
            require(check["confidence_basis"] == "parent_line_score", "Word confidence basis changed")
            require(isinstance(check["reason"], str) and 0 < len(check["reason"]) <= 200, "Invalid field reason")
            require(isinstance(check["word_guards"], list) and all(isinstance(g, str) for g in check["word_guards"])
                    and check["word_guards"] == sorted(set(check["word_guards"])), "Invalid word guards")
            if check["word_guards"]:
                require(check["status"] == "insufficient" and check["reason"] == check["word_guards"][0],
                        "Guarded word field must remain insufficient")
            require(isinstance(check["parent_lines"], list), "Parent lines must be present")
            require(not check["observations"] or check["parent_lines"], "Observed words require parent line evidence")
            parent_ids = set()
            for parent in check["parent_lines"]:
                require(set(parent) == {"line_index", "line", "selected_word_indices"}, "Parent line evidence incomplete")
                line_id = parent["line_index"]
                require(type(line_id) is int and line_id >= 0 and line_id not in parent_ids, "Duplicate or invalid parent line")
                parent_ids.add(line_id)
                words = parent["selected_word_indices"]
                require(isinstance(words, list) and all(type(w) is int and w >= 0 for w in words)
                        and len(words) == len(set(words)), "Invalid parent word indices")
                _spans([parent["line"]], page)
                identity = (page["page"], line_id)
                require(identity not in parents_by_page or parents_by_page[identity] == parent["line"],
                        "Conflicting public parent line observations")
                parents_by_page[identity] = parent["line"]
            require(isinstance(check["guard_observations"], list), "Guard evidence must be present")
            for guard in check["guard_observations"]:
                require(set(guard) == {"reason", "observation"} and guard["reason"] in check["word_guards"],
                        "Guard observation lacks its declared guard")
                _spans([guard["observation"]], page)
            replay = compare_region(observed, field=check["field"], target=PdfBox.model_validate(check["region"]),
                                    expected=check["expected"], min_confidence=min_confidence)
            if check["word_guards"]:
                replay.update(status="insufficient", reason=check["word_guards"][0])
            require(replay == {key: check[key] for key in replay}, "Public word field does not reproduce")


def _validate_candidate(kind, candidate, frozen, view, root):
    _keys(candidate, frozen, optional=("implementation_sha256",))
    for key in ("schema_version", "rule_version", "suite_version", "experiment"):
        if key in frozen:
            require(candidate[key] == frozen[key], "Report schema or rule changed")
    require(candidate["profile"] == frozen["profile"] and candidate["profile_sha256"] == digest(candidate["profile"]),
            "Candidate profile mismatch")
    require(candidate["environment"]["providers"] == frozen["environment"]["providers"], "Declared provider mismatch")
    if "implementation_sha256" in candidate:
        require(isinstance(candidate["implementation_sha256"], dict) and candidate["implementation_sha256"],
                "Implementation hashes must be a nonempty mapping")
        for value in candidate["implementation_sha256"].values():
            _hash(value)
    for value in candidate["environment"].get("rapidocr_source_sha256", {}).values():
        _hash(value)
    baseline = view["baseline"]
    if kind == "line":
        VisualStore(root)._validate(candidate)
    elif kind == "word":
        require(candidate["baseline_report_sha256"] == view["release_manifest"]["files"][FILES[0]] and
                candidate["baseline_generated_at"] == baseline["generated_at"], "Word must reference the frozen baseline")
        _word_checks(candidate, baseline, frozen["profile"])
    else:
        _stress_checks(candidate, baseline, frozen["config"])
    originals = {(d["id"], d.get("page")): d for d in frozen["documents"]}
    for document in candidate["documents"]:
        original = originals[(document["id"], document.get("page"))]
        _keys(document, original)
        if kind == "stress":
            conditions = indexed(original["conditions"], "id")
            source_doc = next(d for d in baseline["documents"] if d["id"] == document["id"])
            refs = indexed(source_doc["facts"], "fact_id")
            for condition in document["conditions"]:
                _keys(condition, conditions[condition["id"]])
                require(condition["image_sha256"] == conditions[condition["id"]]["image_sha256"], "Variant image hash changed")
                _hash(condition["ocr_sha256"])
                for fact in condition["facts"]:
                    require(set(fact) == {"fact_id", "label", "page", "status", "automatic_approval", "checks"},
                            "Stress fact fields incomplete")
                    reference_checks = indexed(refs[fact["fact_id"]]["checks"], "field")
                    for check in fact["checks"]:
                        required = {"field", "status", "reason"}
                        if check["status"] != "consistent":
                            required |= {"expected", "region", "observations"}
                        require(set(check) == required, "Stress field evidence incomplete")
                        if check["status"] == "consistent":
                            require(check["reason"] == "same_displayed_content", "Inconsistent successful stress reason")
                        else:
                            ref = reference_checks[check["field"]]
                            require(check["expected"] == ref["expected"] and check["region"] == ref["region"], "Stress reference changed")
                            observed = _spans(check["observations"], document)
                            actual = compare_region(observed, field=check["field"], target=PdfBox.model_validate(check["region"]),
                                                    expected=check["expected"], min_confidence=candidate["profile"]["min_confidence"])
                            require(actual == check, "Public stress failure does not reproduce")
        else:
            pages = indexed(original["pages"], "page")
            for page in document["pages"]:
                reference = pages[page["page"]]
                _keys(page, reference)
                for key in ("page", "image_sha256", "width", "height", "image_width", "image_height"):
                    require(page[key] == reference[key], "Registered page/image mismatch")
                for key, value in page.items():
                    if key.endswith("sha256"):
                        _hash(value)
            if kind == "word":
                _word_fields(document, original, candidate["profile"]["min_confidence"])


def _partition(report):
    """Only explicitly listed run metadata is separated; all other fields are compared."""
    semantic = deepcopy(report)
    metadata = {key: semantic.pop(key) for key in ("generated_at", "environment", "implementation_sha256") if key in semantic}
    evidence = {}
    documents = {}
    for document in semantic["documents"]:
        doc_key = document["id"] + (":"+str(document["page"]) if "page" in document else "")
        documents[doc_key] = document
        if "retrieved_at" in document:
            metadata[doc_key+".retrieved_at"] = document.pop("retrieved_at")
        for collection, identity in (("pages", "page"), ("conditions", "id")):
            if collection not in document:
                continue
            mapped = {}
            for item in document[collection]:
                item_key = str(item[identity])
                mapped[item_key] = item
                prefix = doc_key+"."+collection+"."+item_key
                for key in ("elapsed_seconds", "word_inference_elapsed_seconds"):
                    if key in item:
                        metadata[prefix+"."+key] = item.pop(key)
                for key in ("ocr_sha256", "word_ocr_sha256", "current_line_ocr_sha256", "ocr_lines", "word_tokens",
                            "line_output_matches_saved_baseline"):
                    if key in item:
                        evidence[prefix+"."+key] = item.pop(key)
                if "facts" in item:
                    item["facts"] = _fact_map(item["facts"])
            document[collection] = mapped
        if "facts" in document:
            document["facts"] = _fact_map(document["facts"])
    semantic["documents"] = documents
    return semantic, evidence, metadata


def _fact_map(facts):
    return {f["fact_id"]: dict(f, checks={c["field"]: c for c in f["checks"]}) for f in facts}


def _difference_entry(before, after, path, *, before_present=True, after_present=True, include_values=False):
    change = {"path": path, "baseline_present": before_present, "candidate_present": after_present,
              "baseline_sha256": digest(before) if before_present else None,
              "candidate_sha256": digest(after) if after_present else None}
    if include_values or path.rsplit(".", 1)[-1] in {"status", "reason", "current_line_status", "saved_baseline_status", "automatic_approval"}:
        for key, value in (("before", before), ("after", after)):
            if value is None or isinstance(value, (bool, int, float)) or isinstance(value, str) and len(value) <= 200:
                change[key] = value
    return change


def _differences(before, after, path="", *, include_values=False):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            location = (path+"." if path else "")+key
            if key not in before or key not in after:
                changes.append(_difference_entry(before.get(key), after.get(key), location,
                               before_present=key in before, after_present=key in after, include_values=include_values))
            else:
                changes.extend(_differences(before[key], after[key], location, include_values=include_values))
        return changes
    return [_difference_entry(before, after, path, include_values=include_values)]


def _status_changes(before, after):
    changes = []
    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in a.keys() & b.keys():
                if key == "status" and a[key] != b[key]:
                    changes.append({"path": path, "before": a[key], "after": b[key]})
                else:
                    walk(a[key], b[key], path+"."+key)
    walk(before, after, "report")
    return sorted(changes, key=lambda item: item["path"])


def review_reproduction(root: Path, candidates: dict[str, Path]) -> dict:
    """Return matched/changed/invalid without writing files or running inference.

    Word/stress always bind to the frozen public line report, not its candidate.
    Invalid inputs are data in the result; callers must inspect status/exit code.
    """
    result = {"schema_version": 1, "status": "invalid", "read_only": True,
              "validation_scope": deepcopy(SCOPE), "reports": {}}
    try:
        require(set(candidates) == set(KINDS), "Exactly line, word and stress candidate paths are required")
        view = experiment_view(root)
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as exc:
        result["error"] = "Frozen release or input mapping invalid: " + (str(exc) if isinstance(exc, VisualArtifactError) else type(exc).__name__)
        return result
    result["release_manifest_canonical_sha256"] = digest(view["release_manifest"])
    for kind, frozen in zip(KINDS, (view["baseline"], view["word"], view["robustness"])):
        item = {"status": "invalid"}
        result["reports"][kind] = item
        try:
            candidate, checksum = _read_candidate(Path(candidates[kind]))
            item["candidate_file_sha256"] = checksum
            _validate_candidate(kind, candidate, frozen, view, root)
            previous, current = _partition(frozen), _partition(candidate)
            for index, label in enumerate(("semantic", "evidence", "run_metadata")):
                changes = _differences(previous[index], current[index], include_values=index == 2)
                item[label+"_changes"] = changes
                item[label+"_change_count"] = len(changes)
            item["status_changes"] = _status_changes(previous[0], current[0])
            item["status"] = "changed" if item["semantic_changes"] or item["evidence_changes"] else "matched"
            item["published_evidence_equal"] = item["status"] == "matched"
        except (ValueError, KeyError, TypeError, OSError, StopIteration, IndexError, AttributeError, RecursionError) as exc:
            # Do not print raw candidate text, arbitrary file contents or paths.
            item["error"] = str(exc) if isinstance(exc, VisualArtifactError) else type(exc).__name__
    statuses = {item["status"] for item in result["reports"].values()}
    result["status"] = "invalid" if "invalid" in statuses else "changed" if "changed" in statuses else "matched"
    return result


def exit_code(result: dict) -> int:
    return {"matched": 0, "changed": 2, "invalid": 1}[result["status"]]
