"""Replay unconditional amount guards on published evidence and synthetic controls.

The source release remains unchanged. No model, network, omitted OCR or source
image is inspected; synthetic expectations are design requirements, not gold.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from . import currency_diagnostic as baseline
from .currency_evidence import crosscheck_currency_fact
from .currency_store import REPORT as CURRENCY_REPORT, RELEASE as CURRENCY_RELEASE, currency_snapshot
from .date_diagnostic import read_json
from .diligence import Fact, digest
from .document_transfer import file_hash
from .word_evidence import WordLine, WordPage

VERSION = "evidence-guard-diagnostic-1"
CATALOG = "data/evaluation/evidence-guard-cases.json"
IMPLEMENTATION_FILES = tuple(dict.fromkeys((*baseline.IMPLEMENTATION_FILES,
    "creditlens/evidence_guard.py", "creditlens/evidence_guard_diagnostic.py",
    "scripts/evaluate_evidence_guard.py", "creditlens/currency_store.py",
    "creditlens/date_diagnostic.py", "creditlens/experiment_paths.py")))
SCOPE = {
    "operation": "unconditional_amount_guard_on_published_evidence",
    "evidence": "published_necessary_parent_lines_and_complete_word_groups",
    "evaluation": "already_observed_known_location_development_diagnostic",
    "model_inference_performed": False, "blind_evaluation": False,
    "full_ocr_verified": False, "image_pdf_bytes_verified": False,
    "source_model_execution_verified": False, "independent_human_gold_count": 0,
    "controls": "synthetic_fixed_design_expectations_not_model_accuracy",
    "observed_old_false_agreements_meaning": "legacy consistent but guard blocked; not independently adjudicated false financial facts",
    "not_checked": ["omitted_full_ocr", "source_image_and_pdf_bytes", "actual_model_execution",
                    "document_authenticity", "unseen_issuer_generalization"],
}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def control_catalog(root: Path) -> tuple[bytes, dict]:
    raw, value = read_json(Path(root)/CATALOG, limit=1_000_000)
    expected_keys = {"schema_version", "id", "provenance", "human_confirmation",
                     "independent_human_gold_count", "expectation_basis", "cases"}
    _require(set(value) == expected_keys and type(value["schema_version"]) is int and value["schema_version"] == 1,
             "Invalid guard control catalog")
    _require(value["id"] == "evidence-guard-development-controls-1" and value["provenance"] == "synthetic"
             and value["human_confirmation"] == "pending" and type(value["independent_human_gold_count"]) is int
             and value["independent_human_gold_count"] == 0
             and isinstance(value["expectation_basis"], str) and value["expectation_basis"].strip(),
             "Control provenance or human confirmation changed")
    _require(isinstance(value["cases"], list) and 1 <= len(value["cases"]) <= 100, "Invalid control inventory")
    ids = set()
    for case in value["cases"]:
        _require(isinstance(case, dict) and set(case) == {"id", "title", "modification", "expected", "provenance",
                                                        "human_confirmation", "input"}, "Invalid control fields")
        _require(all(isinstance(case[k], str) and 0 < len(case[k]) <= 2000 for k in ("id", "title", "modification")),
                 "Invalid control identity or description")
        _require(case["id"] not in ids, "Duplicate control identity"); ids.add(case["id"])
        _require(case["provenance"] == "synthetic" and case["human_confirmation"] == "pending", "Control provenance changed")
        _require(isinstance(case["expected"], dict) and set(case["expected"]) == {"overall_status"}
                 and isinstance(case["expected"]["overall_status"], str)
                 and case["expected"]["overall_status"] in {"consistent", "not_consistent"}, "Invalid fixed expectation")
        _require(isinstance(case["input"], dict) and set(case["input"]) == {"fact", "page"}, "Invalid control input")
        fact, page = Fact.model_validate(case["input"]["fact"]), WordPage.model_validate(case["input"]["page"])
        _require(fact.pdf_location is not None and fact.pdf_location.page == page.page
                 and fact.pdf_location.page_width == page.width and fact.pdf_location.page_height == page.height,
                 "Control reference/page geometry mismatch")
    return raw, value


def _source(root):
    source, provenance, packs, threshold = baseline._source(root)
    captured = currency_snapshot(root)
    raw, report = read_json(root/CURRENCY_REPORT)
    raw_release, release = read_json(root/CURRENCY_RELEASE, limit=100_000)
    _require(captured["source"] == source and captured["report"] == report
             and digest(captured["report"]) == digest(report)
             and release.get("files") == {CURRENCY_REPORT: sha256(raw).hexdigest()},
             "Currency release changed while capturing guard input")
    provenance = {**provenance, "currency_report_path": CURRENCY_REPORT, "currency_report_sha256": sha256(raw).hexdigest(),
                  "currency_release_path": CURRENCY_RELEASE, "currency_release_sha256": sha256(raw_release).hexdigest(),
                  "currency_report_generated_at": report["generated_at"]}
    return source, provenance, packs, threshold, report


def _unchanged(old, new):
    return (baseline._unchanged(old, new) and old["currency_association"] == new["currency_association"]
            and old["currency_rule_version"] == new["currency_rule_version"])


def _contract(old, new, version):
    return bool(_unchanged(old, new) and new.get("legacy_currency_result") == old
                and new.get("evidence_guard_rule_version") == version and new.get("automatic_approval") is False
                and not (old["status"] != "consistent" and new["status"] == "consistent"))


def _summary(items, pages, documents):
    return {"facts": len(items), "distinct_pages": pages, "distinct_documents": documents,
            "currency": baseline._counts(items, "currency"), "guarded": baseline._counts(items, "guarded"),
            "guard_blocked": sum(i["guarded"]["evidence_guard"]["status"] == "blocked" for i in items),
            "old_false_agreements": sum(i["currency"]["status"] == "consistent" and i["guarded"]["status"] != "consistent" for i in items),
            "non_amount_unchanged": all(i["non_amount_unchanged"] for i in items),
            "currency_to_guarded": {f"{old}->{new}": sum(i["currency"]["status"] == old and i["guarded"]["status"] == new for i in items)
                                    for old in baseline.STATES for new in baseline.STATES}}


def _controls(catalog, threshold, rule, version):
    cases = []
    for spec in catalog["cases"]:
        fact = Fact.model_validate(spec["input"]["fact"])
        page = WordPage.model_validate(spec["input"]["page"])
        old, guarded = crosscheck_currency_fact(fact, page, min_confidence=threshold), rule(fact, page, min_confidence=threshold)
        expected = spec["expected"]["overall_status"]
        correct = guarded["status"] == "consistent" if expected == "consistent" else guarded["status"] != "consistent"
        cases.append({**deepcopy(spec), "input_sha256": digest(spec["input"]), "currency": old, "guarded": guarded,
                      "non_amount_unchanged": _unchanged(old, guarded), "passed": bool(correct and _contract(old, guarded, version))})
    return {"scope": "synthetic_fixed_development_controls", "catalog_sha256": digest(catalog),
            "independent_human_gold_count": 0,
            "summary": {"cases": len(cases), "passed": sum(c["passed"] for c in cases), "failed": sum(not c["passed"] for c in cases),
                        "positive_cases": sum(c["expected"]["overall_status"] == "consistent" for c in cases),
                        "negative_cases": sum(c["expected"]["overall_status"] == "not_consistent" for c in cases),
                        "old_false_agreements": sum(c["expected"]["overall_status"] == "not_consistent" and c["currency"]["status"] == "consistent" for c in cases)},
            "cases": cases}


def _build(root, source, provenance, packs, threshold, previous, generated_at=None):
    from .evidence_guard import EVIDENCE_GUARD_RULE_VERSION, crosscheck_guarded_fact
    raw_catalog, catalog = control_catalog(root)
    documents, all_items, seen_sources, seen_pages = [], [], set(), set()
    previous_docs = {d["id"]: d for d in previous["documents"]}
    _require(len(previous_docs) == len(previous["documents"]), "Duplicate currency document identity")
    _require(set(previous_docs) == {d["id"] for d in source["report"]["documents"]}, "Currency document inventory differs")
    for document in source["report"]["documents"]:
        source_sha = document["raw_pdf_sha256"]
        _require(source_sha not in seen_sources, "Duplicate source PDF cannot count as another document"); seen_sources.add(source_sha)
        pack = packs[document["id"]]
        _require(len(pack.sources) == 1 and pack.sources[0].sha256 == source_sha, "Reference source identity differs")
        by_id = {f.id: f for f in pack.facts}
        old_items = previous_docs[document["id"]]["facts"]
        old_by_id = {item["fact_id"]: item for item in old_items}
        _require(len(old_by_id) == len(old_items) and set(old_by_id) == set(by_id), "Incomplete currency fact inventory")
        items, pages, seen_facts = [], [], set()
        for registered in document["pages"]:
            page_key = (source_sha, registered["page"])
            _require(page_key not in seen_pages, "Duplicate source page"); seen_pages.add(page_key)
            page = WordPage(page=registered["page"], width=registered["width"], height=registered["height"],
                            lines=[WordLine.model_validate({k: line[k] for k in ("line", "words")}) for line in registered["evidence_lines"]])
            _require(digest(page.model_dump(mode="json")) == registered["evidence_sha256"], "Published evidence changed")
            pages.append({k: deepcopy(registered[k]) for k in ("page", "width", "height", "image_sha256", "evidence_sha256",
                          "full_line_ocr_sha256", "full_word_ocr_sha256", "fact_ids")})
            for fid in registered["fact_ids"]:
                _require(fid in by_id and fid not in seen_facts, "Missing or duplicate reference fact"); seen_facts.add(fid)
                fact, old = by_id[fid], old_by_id[fid]
                _require(fact.source_id == pack.sources[0].id and fact.pdf_location.page == page.page and old["page"] == page.page,
                         "Fact source/page binding changed")
                currency = crosscheck_currency_fact(fact, page, min_confidence=threshold)
                _require(currency == old["currency"] and digest(currency) == digest(old["currency"]), "Previous currency evidence does not replay")
                guarded = crosscheck_guarded_fact(fact, page, min_confidence=threshold)
                _require(_contract(currency, guarded, EVIDENCE_GUARD_RULE_VERSION), "Guard altered legacy or non-amount evidence or upgraded insufficiency")
                items.append({"fact_id": fid, "page": page.page, "date_group_id": old["date_group_id"],
                              "legacy_date": deepcopy(old["legacy_date"]), "currency": currency, "guarded": guarded,
                              "non_amount_unchanged": _unchanged(currency, guarded)})
        _require(seen_facts == set(by_id), "Reference inventory not fully checked")
        documents.append({"id": document["id"], "company": document["company"], "raw_pdf_sha256": source_sha,
                          "pages": pages, "facts": items, "summary": _summary(items, len(pages), 1)})
        all_items.extend(items)
    return {"schema_version": 1, "suite_version": VERSION,
            "generated_at": baseline._generated(generated_at, previous["generated_at"]), "source": provenance,
            "rule_versions": {**previous["rule_versions"], "evidence_guard": EVIDENCE_GUARD_RULE_VERSION},
            "implementation": {"hash_basis": "python_utf8_lf", "files": {p: file_hash(root/p) for p in IMPLEMENTATION_FILES}},
            "control_catalog": {"path": CATALOG, "raw_sha256": sha256(raw_catalog).hexdigest(), "canonical_sha256": digest(catalog)},
            "min_confidence": threshold, "scope": deepcopy(SCOPE), "automatic_approval": False,
            "documents": documents, "summary": _summary(all_items, len(seen_pages), len(documents)),
            "controls": _controls(catalog, threshold, crosscheck_guarded_fact, EVIDENCE_GUARD_RULE_VERSION)}


def build_evidence_guard_diagnostic(root: Path, *, generated_at=None) -> dict:
    root = Path(root)
    return _build(root, *_source(root), generated_at)


def evidence_guard_diagnostic_view(root: Path, report: dict) -> dict:
    _require(isinstance(report, dict) and isinstance(report.get("generated_at"), str), "Diagnostic generated_at is required")
    root = Path(root)
    captured = _source(root)
    expected = _build(root, *captured, report["generated_at"])
    _require(report == expected and digest(report) == digest(expected), "Evidence guard report does not reproduce field by field")
    return {"report": expected, "source": captured[0], "read_only": True,
            "validation_scope": "All published observations and all fixed synthetic controls are recomputed. Omitted full-page OCR and source PDF/image bytes are not checked. Counts describe this observed diagnostic, not model accuracy or independent human gold."}


def validate_evidence_guard_diagnostic(root: Path, report: dict) -> dict:
    return evidence_guard_diagnostic_view(root, report)["report"]
