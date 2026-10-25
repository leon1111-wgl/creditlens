"""Serve reviewed, frozen experiment records; never run a model in a request.

The release manifest binds the public files to an explicitly reviewed snapshot.
It detects stale/mixed artifacts, not a malicious administrator rewriting both
the files and manifest. Full OCR reproduction remains an offline operation.
"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

from .diligence import digest
from .visual_store import VisualArtifactError, VisualStore
from .visual_robustness import STATES, RobustnessConfig, compare_conditions, summarize

FILES = (
    "artifacts/document-ai-report.json", "artifacts/document-ai-word-report.json",
    "artifacts/visual-robustness-report.json", "configs/document-ai.json",
    "configs/document-ai-words.json", "configs/visual-robustness.json",
)


def require(condition, message):
    if not condition:
        raise VisualArtifactError(message)


def indexed(items, field):
    result = {item[field]: item for item in items}
    require(len(result) == len(items), "Duplicate experiment entry")
    return result


def _word_checks(word, baseline, profile):
    require(word["profile"] == profile and word["profile_sha256"] == digest(profile), "Word profile changed")
    require(word["baseline_summary"] == baseline["summary"], "Word baseline counts changed")
    originals = indexed(baseline["documents"], "id")
    documents = indexed(word["documents"], "id")
    require(documents.keys() == originals.keys(), "Word documents incomplete")
    changes, all_facts, all_lines = [], [], []
    for doc_id, document in documents.items():
        original = originals[doc_id]
        for key in ("source", "company", "pack_sha256"):
            require(document[key] == original[key], "Word source mismatch")
        pages, reference_pages = indexed(document["pages"], "page"), indexed(original["pages"], "page")
        require(pages.keys() == reference_pages.keys(), "Word pages incomplete")
        for number, page in pages.items():
            for key in ("image_sha256", "ocr_sha256", "width", "height", "image_width", "image_height"):
                require(page[key] == reference_pages[number][key], "Word baseline page mismatch")
            require(page["line_output_matches_saved_baseline"] ==
                    (page["current_line_ocr_sha256"] == page["ocr_sha256"]), "Word line comparison mismatch")
        facts, refs = indexed(document["facts"], "fact_id"), indexed(original["facts"], "fact_id")
        require(facts.keys() == refs.keys(), "Word facts incomplete")
        lines, changed = [], []
        for key, fact in facts.items():
            reference = refs[key]
            require(fact["automatic_approval"] is False, "Experiments cannot approve facts")
            for field in ("label", "page", "raw_value", "value", "unit", "period_start", "period_end"):
                require(fact[field] == reference[field], "Word fact metadata mismatch")
            checks, expected = indexed(fact["checks"], "field"), indexed(reference["checks"], "field")
            require(checks.keys() == expected.keys(), "Word fields incomplete")
            for field, check in checks.items():
                require(check["status"] in STATES, "Unknown field state")
                for name in ("expected", "region"):
                    require(check[name] == expected[field][name], "Word reference mismatch")
            states = {c["status"] for c in checks.values()}
            state = "conflict" if "conflict" in states else "insufficient" if "insufficient" in states else "consistent"
            require(fact["status"] == state and fact["saved_baseline_status"] == reference["status"], "Word state mismatch")
            line = {"fact_id": key, "status": fact["current_line_status"]}
            lines.append(line)
            if fact["status"] != reference["status"]:
                changed.append({"document_id": doc_id, "fact_id": key,
                                "saved_baseline_status": reference["status"],
                                "current_line_status": line["status"], "word_status": fact["status"]})
        require(document["baseline_summary"] == original["summary"] and
                document["current_line_summary"] == summarize(lines) and
                document["word_summary"] == summarize(list(facts.values())), "Word document counts mismatch")
        require(document["changes"] == changed, "Word changed-fact list mismatch")
        changes.extend(changed); all_facts.extend(facts.values()); all_lines.extend(lines)
    # IDs can recur between issuers; count per document above, aggregate states here.
    for name, facts in (("word_summary", all_facts), ("current_line_summary", all_lines)):
        count = Counter(f["status"] for f in facts)
        require(word[name] == {s: count[s] for s in STATES}, "Word overall counts mismatch")
    require(word["changes"] == changes, "Word overall changes mismatch")


def _stress_checks(stress, baseline, config):
    normalized = RobustnessConfig.model_validate(config).model_dump(mode="json")
    require(stress["config"] == normalized and stress["config_sha256"] == digest(normalized), "Stress config mismatch")
    require(normalized["source_report_sha256"] == digest(baseline), "Stress baseline changed")
    require(stress["profile"] == baseline["profile"] and stress["profile_sha256"] == baseline["profile_sha256"], "Stress profile mismatch")
    variants = indexed(normalized["variants"], "id")
    clean_id = next(k for k, v in variants.items() if v["operation"] == "identity")
    pages = {(p["document_id"], p["page"]): p for p in normalized["pages"]}
    require(len(stress["documents"]) == len(pages) and
            {(d["id"], d["page"]) for d in stress["documents"]} == pages.keys(), "Stress pages incomplete")
    totals = {key: Counter() for key in variants}; transitions = {key: Counter() for key in variants}
    for document in stress["documents"]:
        original = next(d for d in baseline["documents"] if d["id"] == document["id"])
        for key in ("company", "source", "pack_sha256"):
            require(document[key] == original[key], "Stress source mismatch")
        registered = pages[(document["id"], document["page"])]
        page = next(p for p in original["pages"] if p["page"] == document["page"])
        require(document["pack_sha256"] == registered["pack_sha256"] and
                document["source_image_sha256"] == registered["image_sha256"] == page["image_sha256"], "Stress source registration mismatch")
        for key in ("width", "height", "image_width", "image_height"):
            require(document[key] == page[key], "Stress page dimensions mismatch")
        refs = {f["fact_id"]: f for f in original["facts"] if f["page"] == document["page"]}
        require(document["registered_fact_ids"] == list(refs), "Stress registered facts mismatch")
        conditions = indexed(document["conditions"], "id")
        require(conditions.keys() == variants.keys(), "Stress conditions incomplete")
        clean = conditions[clean_id]["facts"]
        for key, condition in conditions.items():
            require(condition["transform"] == {k: v for k, v in variants[key].items() if v is not None} and
                    condition["synthetic_degradation"] is (key != clean_id), "Stress transformation mismatch")
            if key == clean_id:
                require(condition["image_sha256"] == registered["image_sha256"], "Clean image changed")
                require(condition["compared_with_saved_baseline"] == compare_conditions(list(refs.values()), condition["facts"]), "Saved baseline comparison mismatch")
            facts = indexed(condition["facts"], "fact_id")
            require(facts.keys() == refs.keys(), "Stress facts incomplete")
            for fid, fact in facts.items():
                reference = refs[fid]
                require(fact["page"] == reference["page"] and fact["label"] == reference["label"] and
                        fact["automatic_approval"] is False, "Stress fact mismatch")
                checks = indexed(fact["checks"], "field")
                require(checks.keys() == indexed(reference["checks"], "field").keys(), "Stress fields incomplete")
                states = {c["status"] for c in checks.values()}
                require(states <= set(STATES), "Unknown stress field state")
                require(fact["status"] == ("conflict" if "conflict" in states else "insufficient" if "insufficient" in states else "consistent"), "Stress state mismatch")
            require(condition["summary"] == summarize(list(facts.values())) and
                    condition["compared_with_clean"] == compare_conditions(clean, list(facts.values())), "Stress statistics mismatch")
            totals[key].update(condition["summary"])
            transitions[key].update(condition["compared_with_clean"]["transitions"])
    expected = [{"id": key, "fact_count": sum(totals[key].values()),
                 "summary": {s: totals[key][s] for s in STATES},
                 "transitions_from_clean": dict(transitions[key])} for key in variants]
    require(stress["summary_by_variant"] == expected, "Stress overall counts mismatch")


def experiment_view(root: Path):
    try:
        manifest = json.loads((root/"configs/experiment-artifacts.json").read_text(encoding="utf-8"))
        require(manifest["schema_version"] == 1 and set(manifest["files"]) == set(FILES), "Invalid experiment manifest")
        records = {}
        for name in FILES:
            raw = (root/name).read_bytes()
            require(sha256(raw).hexdigest() == manifest["files"][name], "Experiment snapshot changed; review and freeze it before serving")
            records[name] = json.loads(raw)
        baseline = VisualStore(root)._validate(records[FILES[0]])
        word, stress = records[FILES[1]], records[FILES[2]]
        require(word["baseline_report_sha256"] == manifest["files"][FILES[0]], "Word baseline changed")
        _word_checks(word, baseline, records[FILES[4]])
        _stress_checks(stress, baseline, records[FILES[5]])
        return {"schema_version": 1, "baseline": baseline, "word": word, "robustness": stress,
                "release_manifest": manifest, "read_only": True,
                "validation_scope": "Reviewed snapshot hashes, source bindings and recalculated counts. Full OCR reproduction is offline; this is not a signature or live inference."}
    except FileNotFoundError:
        raise
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise VisualArtifactError("Experiment validation failed: " + str(exc)) from exc
