"""Frozen three-rule checks on a new issuer, with replayable public evidence.

Known reference positions are used only after image recognition. Public replay
checks necessary parent lines and their words, not omitted OCR or authenticity.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Literal

from pydantic import AwareDatetime, Field, HttpUrl, PrivateAttr, model_validator

from .date_evidence import DATE_RULE_VERSION, crosscheck_date_fact
from .diligence import Fact, StrictModel, digest
from .document_transfer import (Hash, Identifier, PageRegistration, PdfRegistration,
                                TransferPageResult, hash_bytes)
from .visual import VISUAL_RULE_VERSION, crosscheck_fact
from .word_evidence import (WORD_RULE_VERSION, WordLine, WordPage, _near_numeric_cell,
                            crosscheck_word_fact)

VERSION = "issuer-evaluation-1"
SCOPE = "Known-location checks on an issuer not used to adjust this project's frozen rules; not blind extraction, pretraining-unseen evaluation, or document authenticity."
RULES = {"line": VISUAL_RULE_VERSION, "word": WORD_RULE_VERSION, "date": DATE_RULE_VERSION}
STATES = ("consistent", "conflict", "insufficient")
EXECUTION_PACKAGES = frozenset({"rapidocr", "onnxruntime", "numpy", "pypdfium2", "Pillow", "pydantic"})
REQUIRED_FILES = frozenset({
    "configs/document-ai.json", "configs/document-ai-words.json",
    "creditlens/diligence.py", "creditlens/visual.py", "creditlens/word_evidence.py",
    "creditlens/date_evidence.py", "creditlens/document_transfer.py",
    "creditlens/experiment_paths.py", "creditlens/issuer_evaluation.py",
    "scripts/evaluate_issuer.py", "scripts/evaluate_document_transfer.py",
    "scripts/evaluate_visual_robustness.py", "scripts/evaluate_word_evidence.py",
})


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def _finite(text):
    result = float(text)
    require(math.isfinite(result), "Non-finite JSON number")
    return result


def _constant(text):
    raise ValueError("Non-finite JSON number: " + text)


def read_json(path: Path, limit=15_000_000):
    path = Path(path)
    require(path.is_file() and path.stat().st_size <= limit, "Missing or oversized issuer JSON")
    raw = path.read_bytes()
    require(len(raw) <= limit, "Issuer JSON exceeds size limit")
    value = json.loads(raw, object_pairs_hook=_pairs, parse_float=_finite, parse_constant=_constant)
    require(isinstance(value, dict), "Issuer JSON must be an object")
    return raw, value


def _registered(root, relative, directory):
    path = (root/relative).resolve()
    require(path.is_relative_to((root/directory).resolve()), "Registered issuer path escaped its directory")
    return path


class IssuerSource(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    sha256: Hash
    underlying_filing_date: date | None
    wrapper_filing_date: date | None
    wrapper_filing_evidence_url: HttpUrl | None
    exact_asset_publication_date: date | None
    first_available_at: AwareDatetime | None
    source_date_basis: Literal["exact_asset_publication_unknown", "exact_asset_publication_verified"]
    publication_evidence_url: HttpUrl | None
    publication_note: str = Field(min_length=10, max_length=3000)
    revision_note: str = Field(min_length=10, max_length=3000)
    retrieved_at: AwareDatetime
    historical_snapshot_verified: Literal[False]
    point_in_time_eligible: Literal[False]

    @model_validator(mode="after")
    def dates_have_distinct_evidence(self):
        if (self.wrapper_filing_date is None) != (self.wrapper_filing_evidence_url is None):
            raise ValueError("Wrapper filing date needs its own evidence URL")
        unknown = self.source_date_basis == "exact_asset_publication_unknown"
        if unknown and any(v is not None for v in (
                self.exact_asset_publication_date, self.first_available_at, self.publication_evidence_url)):
            raise ValueError("Unknown asset publication cannot inherit a wrapper filing date")
        if not unknown and (self.exact_asset_publication_date is None or self.publication_evidence_url is None):
            raise ValueError("Known exact-asset publication needs an explicit date and evidence")
        if self.first_available_at is not None and self.first_available_at > self.retrieved_at:
            raise ValueError("First available time cannot follow this retrieval")
        if any(value is not None and value > self.retrieved_at.date() for value in (
                self.underlying_filing_date, self.wrapper_filing_date, self.exact_asset_publication_date)):
            raise ValueError("Source event cannot follow retrieval")
        return self


class IssuerPack(StrictModel):
    schema_version: Literal[1]
    purpose: Literal["frozen_issuer_evaluation"]
    entity_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    sources: list[IssuerSource] = Field(min_length=1, max_length=1)
    facts: list[Fact] = Field(min_length=1, max_length=100)
    fiscal_calendar: Literal["week_52_53"] | None = None

    @model_validator(mode="after")
    def references(self):
        if (len({f.id for f in self.facts}) != len(self.facts) or
                any(f.source_id != self.sources[0].id or f.pdf_location is None for f in self.facts)):
            raise ValueError("Issuer facts must be unique and have registered PDF locations and source")
        return self


class IssuerPdf(StrictModel):
    url: HttpUrl
    sha256: Hash
    filename: str
    allowed_hosts: list[str]

    @model_validator(mode="after")
    def download_boundary(self):
        # Reuse the existing URL/host/filename validation without giving wrapper
        # event dates the meaning of this asset's publication date.
        PdfRegistration(url=self.url, sha256=self.sha256, filename=self.filename,
                        allowed_hosts=self.allowed_hosts, underlying_filing_date=None,
                        wrapper_published_at=None, publication_note="Resource-only download registration")
        return self


class RenderProfile(StrictModel):
    engine: Literal["pypdfium2"]
    version: str = Field(min_length=1, max_length=50)
    pillow_version: str = Field(min_length=1, max_length=50)
    scale: float = Field(gt=0, le=4, allow_inf_nan=False)
    rotation: Literal[0]
    image_format: Literal["PNG"]


class IssuerDocument(StrictModel):
    id: Identifier
    pack_path: str = Field(pattern=r"^data/examples/[a-z][a-z0-9-]{1,100}\.json$")
    pack_sha256: Hash
    pdf: IssuerPdf
    pages: list[PageRegistration] = Field(min_length=1, max_length=10)


class IssuerManifest(StrictModel):
    schema_version: Literal[1]
    suite_version: Literal["issuer-evaluation-1"]
    experiment_id: Identifier
    frozen_at: AwareDatetime
    selection_reason: str = Field(min_length=10, max_length=3000)
    reference_status: Literal["single_annotator_development_reference"]
    file_hash_basis: Literal["python_utf8_lf_other_raw"]
    rule_versions: dict[str, str]
    files: dict[str, Hash]
    reference_files: dict[str, Hash] = Field(min_length=1, max_length=10)
    execution_packages: dict[str, str]
    render: RenderProfile
    documents: list[IssuerDocument] = Field(min_length=1, max_length=5)
    _snapshot_sha256: str = PrivateAttr(default="")
    _profile: dict = PrivateAttr(default_factory=dict)

    @property
    def snapshot_sha256(self):
        return self._snapshot_sha256

    @property
    def profile(self):
        return deepcopy(self._profile)

    @model_validator(mode="after")
    def freeze_members(self):
        if set(self.files) != REQUIRED_FILES or self.rule_versions != RULES:
            raise ValueError("Issuer manifest must freeze every execution dependency and current rule version")
        if (set(self.execution_packages) != EXECUTION_PACKAGES or
                any(not value or len(value) > 50 for value in self.execution_packages.values()) or
                self.execution_packages["pypdfium2"] != self.render.version or
                self.execution_packages["Pillow"] != self.render.pillow_version):
            raise ValueError("Issuer manifest must freeze all execution package versions and match the renderer")
        if (not any(re.fullmatch(r"scripts/build_[a-z0-9_]+\.py", p) for p in self.reference_files) or
            any(not re.fullmatch(r"(?:scripts/build_[a-z0-9_]+|creditlens/[a-z0-9_]+)\.py", p)
                for p in self.reference_files) or set(self.reference_files) & REQUIRED_FILES):
            raise ValueError("Issuer reference must freeze its builder and separate helpers")
        if (len({d.id for d in self.documents}) != len(self.documents) or
                len({d.pdf.sha256 for d in self.documents}) != len(self.documents)):
            raise ValueError("Duplicate issuer documents or source assets")
        return self


def _date_identity(document_id, pdf_sha256, fact):
    loc = fact.pdf_location
    return {"document_id": document_id, "source_pdf_sha256": pdf_sha256, "page": loc.page,
            "year_bbox": loc.year_bbox.model_dump(mode="json"),
            "period_bbox": loc.period_bbox.model_dump(mode="json")}


def _expected(fact):
    return {"year": fact.pdf_location.year, "period_start": fact.period_start.isoformat() if fact.period_start else None,
            "period_end": fact.period_end.isoformat()}


def load_manifest(root: Path, manifest_path: Path | None = None):
    root = Path(root)
    path = Path(manifest_path) if manifest_path is not None else root/"configs/issuer-evaluation.json"
    require(path.resolve().is_relative_to((root/"configs").resolve()), "Issuer manifest must stay inside configs")
    raw, value = read_json(path, 1_000_000)
    manifest = IssuerManifest.model_validate(value)
    manifest._snapshot_sha256 = hashlib.sha256(raw).hexdigest()
    require(manifest.frozen_at <= datetime.now(timezone.utc), "Issuer freeze cannot be in the future")
    dependencies = {}
    for relative, expected in {**manifest.files, **manifest.reference_files}.items():
        file = _registered(root, relative, relative.split("/")[0])
        content = file.read_bytes()
        require(hash_bytes(file, content) == expected, "Frozen issuer dependency changed: " + relative)
        dependencies[relative] = content
    baseline = json.loads(dependencies["configs/document-ai.json"])
    words = json.loads(dependencies["configs/document-ai-words.json"])
    require(all(words.get(k) == v for k, v in baseline.items() if k != "profile_version"),
            "Issuer line/word/date methods must share one OCR profile")
    require((baseline["engine"], baseline["execution_provider"], baseline["use_cls"],
             words["return_word_box"], words["return_single_char_box"], words["word_rule_version"]) ==
            ("onnxruntime", "CPUExecutionProvider", False, True, False, WORD_RULE_VERSION),
            "Issuer protocol requires the frozen CPU-only native-word OCR profile")
    require(manifest.render.scale == baseline["render_scale"], "Render scale differs from frozen OCR profile")
    manifest._profile = baseline
    prior_hashes = set()
    for old in ("msft-fy2025-pdf.json", "aapl-fy2025-pdf.json", "nvda-fy2025-transfer-v2.json"):
        previous = root/"data/examples"/old
        if previous.is_file():
            prior_hashes.update(source["sha256"] for source in read_json(previous)[1]["sources"])
    packs = {}
    for doc in manifest.documents:
        require(doc.id not in {"msft", "aapl", "nvda", "microsoft", "apple", "nvidia",
                               "con", "prn", "aux", "nul", "com1", "lpt1"}, "Previously adjusted or unsafe issuer ID")
        pack_raw, pack_value = read_json(_registered(root, doc.pack_path, "data/examples"), 5_000_000)
        require(hashlib.sha256(pack_raw).hexdigest() == doc.pack_sha256, "Frozen issuer reference changed")
        pack = IssuerPack.model_validate(pack_value)
        source = pack.sources[0]
        require(source.sha256 == doc.pdf.sha256 and source.url == doc.pdf.url, "Issuer PDF/source registration mismatch")
        require(source.sha256 not in prior_hashes and not re.search(r"microsoft|apple|nvidia|微软|苹果|英伟达", pack.company, re.I),
                "An issuer used during rule development cannot be presented as this new-issuer evaluation")
        require(source.retrieved_at <= manifest.frozen_at, "Reference source retrieval must precede freeze")
        ids = [fid for page in doc.pages for fid in page.fact_ids]
        facts = {f.id: f for f in pack.facts}
        require(len({p.page for p in doc.pages}) == len(doc.pages) and len(set(ids)) == len(ids) and set(ids) == set(facts),
                "Issuer pages must register every fact exactly once")
        groups = {}
        for page in doc.pages:
            for fid in page.fact_ids:
                fact = facts[fid]; loc = fact.pdf_location
                require((loc.page, loc.page_width, loc.page_height) == (page.page, page.width, page.height),
                        "Issuer fact page or dimensions changed")
                key = digest(_date_identity(doc.id, source.sha256, fact))
                expected = _expected(fact)
                require(key not in groups or groups[key] == expected, "Shared date header has conflicting reference periods")
                groups[key] = expected
        packs[doc.id] = pack
    return manifest, packs


def check_fact(fact: Fact, page: WordPage, threshold: float):
    result = {"fact_id": fact.id, "page": page.page,
              "line": crosscheck_fact(fact, page.line_page(), min_confidence=threshold),
              "word": crosscheck_word_fact(fact, page, min_confidence=threshold),
              "date": crosscheck_date_fact(fact, page, min_confidence=threshold)}
    require([c for c in result["word"]["checks"] if c["field"] != "year"] ==
            [c for c in result["date"]["checks"] if c["field"] != "year"], "Date rule altered non-year checks")
    return result


def _map_parent_indices(result, mapping):
    result = deepcopy(result)
    for method in ("word", "date"):
        for check in result[method]["checks"]:
            for parent in check["parent_lines"]:
                parent["line_index"] = mapping[parent["line_index"]]
    association = result["date"]["date_association"]
    association["candidate_parent_indices"] = [mapping[i] for i in association["candidate_parent_indices"]]
    for parent in association["legacy_year"]["parent_lines"]:
        parent["line_index"] = mapping[parent["line_index"]]
    return result


def compact_page(page: WordPage, facts: list[Fact], threshold: float):
    full = [check_fact(fact, page, threshold) for fact in facts]
    selected, line_hashes, word_hashes = set(), set(), set()
    for pair in full:
        for check in pair["line"]["checks"]:
            line_hashes.update(digest(span) for span in check["observations"])
        for method in ("word", "date"):
            for check in pair[method]["checks"]:
                selected.update(parent["line_index"] for parent in check["parent_lines"])
                word_hashes.update(digest(g["observation"]) for g in check["guard_observations"])
    for index, entry in enumerate(page.lines):
        if digest(entry.line.model_dump(mode="json")) in line_hashes or any(
                digest(word.model_dump(mode="json")) in word_hashes for word in entry.words):
            selected.add(index)
        # date-evidence-1 also inspects numeric parent lines whose word list may
        # be empty. Retain them before replay, even if old word guards omitted them.
        if re.search(r"[0-9()+\-−.,%]", entry.line.text) and any(
                _near_numeric_cell(entry.line.bbox, fact.pdf_location.year_bbox) for fact in facts):
            selected.add(index)
    indices = sorted(selected)
    evidence = [{"source_line_index": i, **page.lines[i].model_dump(mode="json")} for i in indices]
    compact = WordPage(page=page.page, width=page.width, height=page.height, lines=[page.lines[i] for i in indices])
    outcomes = [check_fact(fact, compact, threshold) for fact in facts]
    mapping = {old: new for new, old in enumerate(indices)}
    require([_map_parent_indices(item, mapping) for item in full] == outcomes,
            "Public evidence failed to reproduce every full-page line/word/date field")
    return evidence, outcomes


def date_groups(document_id: str, pdf_sha256: str, facts: list[Fact], outcomes: list[dict]):
    groups = {}
    by_id = {fact.id: fact for fact in facts}
    for item in outcomes:
        fact = by_id[item["fact_id"]]
        identity = _date_identity(document_id, pdf_sha256, fact)
        key = digest(identity)
        expected = _expected(fact)
        if key not in groups:
            groups[key] = {"id": key, **identity, "expected": expected, "fact_ids": [], "year_statuses": [],
                           "status_basis": "year_check_after_optional_complete_date_association",
                           "date_association_applied_facts": 0}
        group = groups[key]
        require(group["expected"] == expected, "Shared date header has conflicting reference periods")
        group["fact_ids"].append(fact.id)
        group["year_statuses"].append(next(c["status"] for c in item["date"]["checks"] if c["field"] == "year"))
        group["date_association_applied_facts"] += int(item["date"]["date_association"]["applied"])
        item["date_group_id"] = key
    for group in groups.values():
        states = group.pop("year_statuses")
        group["status"] = "conflict" if "conflict" in states else "insufficient" if "insufficient" in states else "consistent"
    return list(groups.values())


def summarize(outcomes: list[dict], groups: list[dict], pages: int, documents: int):
    return {**{method: {state: sum(item[method]["status"] == state for item in outcomes) for state in STATES}
               for method in ("line", "word", "date")},
            "facts": len(outcomes), "distinct_pages": pages, "distinct_documents": documents,
            "distinct_date_groups": len(groups),
            "date_group_status": {state: sum(group["status"] == state for group in groups) for state in STATES},
            "date_association_applied": sum(item["date"]["date_association"]["applied"] for item in outcomes),
            "non_year_unchanged": all([c for c in item["word"]["checks"] if c["field"] != "year"] ==
                                      [c for c in item["date"]["checks"] if c["field"] != "year"] for item in outcomes)}


class IssuerFactResult(StrictModel):
    fact_id: str
    page: int = Field(strict=True, ge=1)
    line: dict
    word: dict
    date: dict
    date_group_id: Hash


class IssuerDocumentResult(StrictModel):
    id: Identifier
    company: str
    source: IssuerSource
    pdf: IssuerPdf
    pack_sha256: Hash
    raw_pdf_sha256: Hash
    pages: list[TransferPageResult] = Field(min_length=1, max_length=10)
    facts: list[IssuerFactResult] = Field(min_length=1, max_length=100)
    date_groups: list[dict] = Field(min_length=1, max_length=100)
    summary: dict


class IssuerReport(StrictModel):
    schema_version: Literal[1]
    suite_version: Literal["issuer-evaluation-1"]
    experiment_id: Identifier
    generated_at: AwareDatetime
    manifest_sha256: Hash
    rule_versions: dict[str, str]
    environment: dict
    scope: Literal[SCOPE]
    automatic_approval: Literal[False]
    documents: list[IssuerDocumentResult] = Field(min_length=1, max_length=5)
    summary: dict


def _validate_loaded_report(report: dict, manifest: IssuerManifest, packs: dict):
    checked = IssuerReport.model_validate(report)
    require(checked.manifest_sha256 == manifest.snapshot_sha256 and checked.experiment_id == manifest.experiment_id and
            manifest.frozen_at <= checked.generated_at <= datetime.now(timezone.utc) and checked.rule_versions == RULES,
            "Issuer report is not bound to the frozen experiment")
    require(checked.environment.get("providers") == {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]},
            "Issuer report must record CPU-only providers")
    require(checked.environment.get("operation") == "full_page_ocr_then_frozen_rule_evaluation" and
            checked.environment.get("reference_sent_to_recognizer") is False,
            "Issuer report must distinguish image recognition from later reference checks")
    packages = checked.environment.get("packages", {})
    # Public replay does not import OCR packages: validate the registered runtime
    # declaration, while the runner verifies installed versions before inference.
    require(packages == manifest.execution_packages,
            "Issuer recorded execution package versions differ from the freeze")
    require([d.id for d in checked.documents] == [d.id for d in manifest.documents], "Issuer documents missing, duplicated or reordered")
    all_outcomes, all_groups, total_pages = [], [], 0
    for result, registration in zip(checked.documents, manifest.documents):
        pack = packs[result.id]
        require(result.company == pack.company and result.source == pack.sources[0] and result.pdf == registration.pdf and
                result.pack_sha256 == registration.pack_sha256 and result.raw_pdf_sha256 == registration.pdf.sha256,
                "Issuer source or reference binding changed")
        require([p.page for p in result.pages] == [p.page for p in registration.pages], "Issuer page inventory changed")
        by_id = {fact.id: fact for fact in pack.facts}
        expected = []
        for page, registered in zip(result.pages, registration.pages):
            require({k: page.model_dump(mode="json")[k] for k in PageRegistration.model_fields} == registered.model_dump(mode="json"),
                    "Issuer page/image/complete fact registration changed")
            indices = [line.source_line_index for line in page.evidence_lines]
            require(indices == sorted(set(indices)) and all(i < page.ocr_line_count for i in indices), "Invalid source OCR line mapping")
            compact = WordPage(page=page.page, width=page.width, height=page.height,
                               lines=[WordLine(line=line.line, words=line.words) for line in page.evidence_lines])
            require(page.evidence_sha256 == digest(compact.model_dump(mode="json")), "Issuer public evidence hash changed")
            expected.extend(check_fact(by_id[fid], compact, manifest.profile["min_confidence"]) for fid in registered.fact_ids)
        groups = date_groups(result.id, registration.pdf.sha256, pack.facts, expected)
        actual = [item.model_dump(mode="json") for item in result.facts]
        require(actual == expected and digest(actual) == digest(expected), "Issuer field outcomes do not reproduce from public evidence")
        require(result.date_groups == groups and digest(result.date_groups) == digest(groups), "Issuer shared date groups changed")
        summary = summarize(expected, groups, len(result.pages), 1)
        require(digest(result.summary) == digest(summary), "Issuer document summary changed")
        all_outcomes.extend(expected); all_groups.extend(groups); total_pages += len(result.pages)
    require(digest(checked.summary) == digest(summarize(all_outcomes, all_groups, total_pages, len(checked.documents))),
            "Issuer total summary changed")
    return checked.model_dump(mode="json")


def validate_issuer_report(root: Path, report: dict, manifest_path: Path | None = None):
    manifest, packs = load_manifest(root, manifest_path)
    return _validate_loaded_report(report, manifest, packs)


def issuer_view(root: Path, report_path: Path | None = None, manifest_path: Path | None = None):
    root = Path(root)
    path = Path(report_path) if report_path is not None else root/"artifacts/issuer-evaluation-report.json"
    raw, value = read_json(path)
    manifest, packs = load_manifest(root, manifest_path)
    return {"report": _validate_loaded_report(value, manifest, packs), "manifest": manifest.model_dump(mode="json"),
            "report_sha256": hashlib.sha256(raw).hexdigest(), "read_only": True,
            "validation_scope": "Recomputes line, word and date checks from published necessary complete parent lines, word guards and date-specific nearby numeric parents. Omitted full OCR and source PDF/image bytes require separate local verification; this is not proof of model execution or material authenticity."}
