"""Frozen, known-location transfer checks with independently registered facts.

Public evidence is a spatially selected subset of full-page OCR. Recalculation
checks that subset, not the completeness or authenticity of the original OCR.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, HttpUrl, PrivateAttr, model_validator

from .diligence import Fact, StrictModel, digest
from .visual import OcrSpan, VISUAL_RULE_VERSION, crosscheck_fact
from .word_evidence import WORD_RULE_VERSION, WordLine, WordPage, crosscheck_word_fact

VERSION = "document-transfer-1"
SCOPE = "Known-location new-issuer development transfer; single-annotator references, not blind extraction accuracy or document authenticity."
STATES = ("consistent", "conflict", "insufficient")
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,60}$")]
REQUIRED_FILES = frozenset({
    "configs/document-ai.json", "configs/document-ai-words.json",
    "creditlens/diligence.py", "creditlens/visual.py", "creditlens/word_evidence.py",
    "creditlens/experiment_paths.py", "creditlens/document_transfer.py",
    "scripts/evaluate_document_transfer.py", "scripts/evaluate_word_evidence.py",
    "scripts/evaluate_visual_robustness.py",
})


class TransferSource(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    sha256: Hash
    wrapper_publication_date: date | None
    underlying_filing_date: date | None
    source_date_basis: Literal["wrapper_publication_unknown"]
    point_in_time_eligible: Literal[False]

    @model_validator(mode="after")
    def unknown_wrapper(self):
        if self.wrapper_publication_date is not None:
            raise ValueError("This transfer protocol records the wrapper publication date as unknown")
        return self


class TransferPack(StrictModel):
    schema_version: Literal[1]
    purpose: Literal["known_location_transfer"]
    entity_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    sources: list[TransferSource] = Field(min_length=1, max_length=1)
    facts: list[Fact] = Field(min_length=1, max_length=100)
    fiscal_calendar: Literal["week_52_53"] | None = None

    @model_validator(mode="after")
    def references(self):
        sources = {source.id for source in self.sources}
        if len(sources) != len(self.sources) or len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError("Duplicate reference identifiers")
        if any(f.source_id not in sources or f.pdf_location is None for f in self.facts):
            raise ValueError("Every transfer fact must reference the registered source and a PDF location")
        return self


class PdfRegistration(StrictModel):
    url: HttpUrl
    sha256: Hash
    filename: str = Field(pattern=r"^[a-z][a-z0-9-]{1,100}\.pdf$")
    allowed_hosts: list[str] = Field(min_length=1, max_length=5)
    underlying_filing_date: date | None
    wrapper_published_at: date | None
    publication_note: str = Field(min_length=10, max_length=3000)
    point_in_time_eligible: Literal[False] = False
    retrieved_at: AwareDatetime | None = None
    accession: str | None = Field(default=None, max_length=100)
    acceptance_as_displayed: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def url_boundary(self):
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", h) or "." not in h
               for h in self.allowed_hosts):
            raise ValueError("Only explicit public DNS host names are accepted")
        for host in self.allowed_hosts:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if host.endswith((".local", ".localhost", ".internal")):
                    raise ValueError("Private host names are not official PDF sources")
            else:
                raise ValueError("IP literals are not official PDF host names")
        check_download_url(str(self.url), self.allowed_hosts)
        if self.filename.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}:
            raise ValueError("Reserved PDF filename")
        return self


class PageRegistration(StrictModel):
    page: int = Field(strict=True, ge=1, le=20000)
    width: float = Field(gt=0, le=20000, allow_inf_nan=False)
    height: float = Field(gt=0, le=20000, allow_inf_nan=False)
    image_width: int = Field(strict=True, gt=0, le=40000)
    image_height: int = Field(strict=True, gt=0, le=40000)
    image_sha256: Hash
    fact_ids: list[str] = Field(min_length=1, max_length=100)


class DocumentRegistration(StrictModel):
    id: Identifier
    pack_path: str = Field(pattern=r"^data/examples/[a-z][a-z0-9-]{1,100}\.json$")
    pack_sha256: Hash
    pdf: PdfRegistration
    pages: list[PageRegistration] = Field(min_length=1, max_length=10)


class TransferManifest(StrictModel):
    schema_version: Literal[1]
    suite_version: Literal["document-transfer-1"]
    experiment_id: Identifier
    frozen_at: AwareDatetime
    selection_reason: str = Field(min_length=10, max_length=3000)
    reference_status: Literal["single_annotator_development_reference"]
    file_hash_basis: Literal["python_utf8_lf_other_raw"]
    files: dict[str, Hash]
    reference_files: dict[str, Hash] = Field(min_length=1, max_length=10)
    documents: list[DocumentRegistration] = Field(min_length=1, max_length=5)
    _snapshot_sha256: str = PrivateAttr(default="")
    _profile: dict = PrivateAttr(default_factory=dict)

    @property
    def snapshot_sha256(self) -> str:
        return self._snapshot_sha256

    @property
    def profile(self) -> dict:
        return deepcopy(self._profile)

    @model_validator(mode="after")
    def fixed_members(self):
        if set(self.files) != REQUIRED_FILES:
            raise ValueError("Manifest must freeze exactly the required config, rules and implementation files")
        if (not any(re.fullmatch(r"scripts/build_[a-z0-9_]+\.py", path) for path in self.reference_files) or
            any(not re.fullmatch(r"(?:scripts/build_[a-z0-9_]+|creditlens/[a-z0-9_]+)\.py", path)
                for path in self.reference_files) or set(self.reference_files) & REQUIRED_FILES):
            raise ValueError("Reference construction must freeze a builder and optional separate creditlens helpers")
        if len({d.id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate transfer documents")
        for doc in self.documents:
            if doc.id in {"msft", "aapl", "microsoft", "apple", "con", "prn", "aux", "nul", "com1", "lpt1"}:
                raise ValueError("Transfer requires a new issuer and a safe document identifier")
        return self


def check_download_url(url: str, allowed_hosts: list[str]) -> str:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in allowed_hosts or parts.username or parts.password or
        parts.port not in (None, 443) or parts.fragment):
        raise ValueError("PDF URL or redirect is outside the registered HTTPS host allowlist")
    return url


def file_hash(path: Path) -> str:
    return hash_bytes(path, path.read_bytes())


def hash_bytes(path: Path, raw: bytes) -> str:
    if path.suffix == ".py":
        raw = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _registered_file(root: Path, relative: str, directory: str) -> Path:
    path = (root/relative).resolve()
    if not path.is_relative_to((root/directory).resolve()):
        raise ValueError("Registered file escaped its repository directory")
    return path


def load_manifest(root: Path, manifest_path: Path | None = None) -> tuple[TransferManifest, dict[str, TransferPack]]:
    root = Path(root)
    path = Path(manifest_path) if manifest_path is not None else root/"configs/document-transfer.json"
    if not path.resolve().is_relative_to((root/"configs").resolve()):
        raise ValueError("Transfer manifest must be inside the repository configs directory")
    if path.stat().st_size > 1_000_000:
        raise ValueError("Transfer manifest exceeds size limit")
    manifest_raw = path.read_bytes()
    manifest = TransferManifest.model_validate_json(manifest_raw)
    manifest._snapshot_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    if manifest.frozen_at > datetime.now(timezone.utc):
        raise ValueError("Transfer freeze time cannot be in the future")
    frozen_files = {}
    for relative, expected in {**manifest.files, **manifest.reference_files}.items():
        file = _registered_file(root, relative, relative.split("/")[0])
        raw = file.read_bytes()
        if hash_bytes(file, raw) != expected:
            raise ValueError("Frozen transfer dependency changed: "+relative)
        frozen_files[relative] = raw
    baseline = json.loads(frozen_files["configs/document-ai.json"])
    word = json.loads(frozen_files["configs/document-ai-words.json"])
    manifest._profile = baseline
    if any(word.get(k) != v for k, v in baseline.items() if k != "profile_version"):
        raise ValueError("Line and word methods must use the same frozen OCR configuration")
    if (baseline["engine"], baseline["execution_provider"], baseline["use_cls"], word["return_word_box"],
        word["return_single_char_box"], word["word_rule_version"]) != (
            "onnxruntime", "CPUExecutionProvider", False, True, False, WORD_RULE_VERSION):
        raise ValueError("Unsupported transfer OCR profile")
    packs = {}
    old_hashes = set()
    for name in ("msft-fy2025-pdf.json", "aapl-fy2025-pdf.json"):
        old = root/"data/examples"/name
        if old.is_file():
            old_hashes.update(s["sha256"] for s in json.loads(old.read_text(encoding="utf-8"))["sources"])
    for doc in manifest.documents:
        pack_path = _registered_file(root, doc.pack_path, "data/examples")
        pack_raw = pack_path.read_bytes()
        if hashlib.sha256(pack_raw).hexdigest() != doc.pack_sha256:
            raise ValueError("Frozen transfer reference pack changed")
        pack = TransferPack.model_validate_json(pack_raw)
        source = pack.sources[0]
        if (source.sha256 != doc.pdf.sha256 or str(source.url) != str(doc.pdf.url) or
            source.underlying_filing_date != doc.pdf.underlying_filing_date or
            source.wrapper_publication_date != doc.pdf.wrapper_published_at):
            raise ValueError("Pack source does not match the registered PDF and publication boundary")
        if source.sha256 in old_hashes or re.search(r"microsoft|apple|微软|苹果", pack.company, re.I):
            raise ValueError("Existing development issuers cannot be presented as new transfer documents")
        facts = {f.id: f for f in pack.facts}
        registered_ids = [fid for p in doc.pages for fid in p.fact_ids]
        if (len({p.page for p in doc.pages}) != len(doc.pages) or len(set(registered_ids)) != len(registered_ids)
            or set(registered_ids) != set(facts)):
            raise ValueError("Manifest pages must include every reference fact exactly once")
        for page in doc.pages:
            for fid in page.fact_ids:
                loc = facts[fid].pdf_location
                if (loc.page, loc.page_width, loc.page_height) != (page.page, page.width, page.height):
                    raise ValueError("Reference fact page or dimensions disagree with registration")
        packs[doc.id] = pack
    return manifest, packs


def summarize(pairs: list[dict]) -> dict:
    return {method: {state: sum(p[method]["status"] == state for p in pairs) for state in STATES}
            for method in ("line", "word")}


def compact_page(page: WordPage, facts: list[Fact], threshold: float) -> tuple[list[dict], list[dict]]:
    """Select evidence after inference by existing checks, never expected values."""
    full = [{"fact_id": f.id, "page": page.page,
             "line": crosscheck_fact(f, page.line_page(), min_confidence=threshold),
             "word": crosscheck_word_fact(f, page, min_confidence=threshold)} for f in facts]
    selected, parent_hashes, guard_hashes = set(), set(), set()
    for pair in full:
        for check in pair["line"]["checks"]:
            parent_hashes.update(digest(span) for span in check["observations"])
        for check in pair["word"]["checks"]:
            selected.update(p["line_index"] for p in check["parent_lines"])
            guard_hashes.update(digest(g["observation"]) for g in check["guard_observations"])
    for index, line in enumerate(page.lines):
        if digest(line.line.model_dump(mode="json")) in parent_hashes or any(
            digest(word.model_dump(mode="json")) in guard_hashes for word in line.words):
            selected.add(index)
    indices = sorted(selected)
    evidence = [{"source_line_index": index, **page.lines[index].model_dump(mode="json")} for index in indices]
    compact = WordPage(page=page.page, width=page.width, height=page.height, lines=[page.lines[i] for i in indices])
    pairs = [{"fact_id": f.id, "page": page.page,
              "line": crosscheck_fact(f, compact.line_page(), min_confidence=threshold),
              "word": crosscheck_word_fact(f, compact, min_confidence=threshold)} for f in facts]
    mapping = {old: new for new, old in enumerate(indices)}
    normalized = deepcopy(full)
    for pair in normalized:
        for check in pair["word"]["checks"]:
            for parent in check["parent_lines"]:
                parent["line_index"] = mapping[parent["line_index"]]
    if normalized != pairs:
        raise ValueError("Minimal evidence failed to reproduce the complete OCR checks")
    return evidence, pairs


class EvidenceLine(WordLine):
    source_line_index: int = Field(strict=True, ge=0, le=4999)


class TransferPageResult(PageRegistration):
    full_line_ocr_sha256: Hash
    full_word_ocr_sha256: Hash
    evidence_sha256: Hash
    ocr_line_count: int = Field(strict=True, ge=0, le=5000)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    evidence_lines: list[EvidenceLine] = Field(max_length=5000)


class FactPair(StrictModel):
    fact_id: str
    page: int = Field(strict=True, ge=1)
    line: dict
    word: dict


class MethodCounts(StrictModel):
    consistent: int = Field(strict=True, ge=0)
    conflict: int = Field(strict=True, ge=0)
    insufficient: int = Field(strict=True, ge=0)


class MethodSummary(StrictModel):
    line: MethodCounts
    word: MethodCounts


class TransferDocumentResult(StrictModel):
    id: Identifier
    company: str
    source: TransferSource
    source_publication: PdfRegistration
    pack_sha256: Hash
    raw_pdf_sha256: Hash
    pages: list[TransferPageResult] = Field(min_length=1, max_length=10)
    facts: list[FactPair] = Field(min_length=1, max_length=100)
    summary: MethodSummary


class TransferReport(StrictModel):
    schema_version: Literal[1]
    suite_version: Literal["document-transfer-1"]
    experiment_id: Identifier
    generated_at: AwareDatetime
    manifest_sha256: Hash
    rule_versions: dict[str, str]
    environment: dict
    scope: Literal[SCOPE]
    documents: list[TransferDocumentResult] = Field(min_length=1, max_length=5)
    summary: MethodSummary


def validate_transfer_report(root: Path, report: dict, manifest_path: Path | None = None) -> dict:
    manifest, packs = load_manifest(root, manifest_path)
    return _validate_loaded_report(report, manifest, packs)


def _validate_loaded_report(report: dict, manifest: TransferManifest, packs: dict[str, TransferPack]) -> dict:
    checked = TransferReport.model_validate(report)
    if (checked.manifest_sha256 != manifest.snapshot_sha256 or
        checked.experiment_id != manifest.experiment_id or checked.generated_at < manifest.frozen_at or
        checked.rule_versions != {"line": VISUAL_RULE_VERSION, "word": WORD_RULE_VERSION}):
        raise ValueError("Transfer report is not bound to the frozen experiment")
    if checked.environment.get("providers") != {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]}:
        raise ValueError("Transfer report must record actual CPU-only providers")
    if [d.id for d in checked.documents] != [d.id for d in manifest.documents]:
        raise ValueError("Report must include every registered document in order")
    threshold = manifest.profile["min_confidence"]
    all_pairs = []
    for result, registration in zip(checked.documents, manifest.documents):
        pack = packs[result.id]
        if (result.company != pack.company or result.source != pack.sources[0] or result.source_publication != registration.pdf or
            result.pack_sha256 != registration.pack_sha256 or result.raw_pdf_sha256 != registration.pdf.sha256):
            raise ValueError("Transfer source or reference registration changed")
        if [p.page for p in result.pages] != [p.page for p in registration.pages]:
            raise ValueError("Transfer result pages are missing, duplicated or reordered")
        expected_pairs = []
        by_id = {f.id: f for f in pack.facts}
        for page, registered in zip(result.pages, registration.pages):
            if {k: page.model_dump(mode="json")[k] for k in PageRegistration.model_fields} != registered.model_dump(mode="json"):
                raise ValueError("Transfer page dimensions, image or fact membership changed")
            ids = [line.source_line_index for line in page.evidence_lines]
            if ids != sorted(set(ids)) or any(i >= page.ocr_line_count for i in ids):
                raise ValueError("Original OCR line mapping is invalid")
            compact = WordPage(page=page.page, width=page.width, height=page.height,
                               lines=[WordLine(line=e.line, words=e.words) for e in page.evidence_lines])
            if page.evidence_sha256 != digest(compact.model_dump(mode="json")):
                raise ValueError("Transfer compact evidence hash changed")
            for fid in registered.fact_ids:
                fact = by_id[fid]
                expected_pairs.append({"fact_id": fid, "page": page.page,
                    "line": crosscheck_fact(fact, compact.line_page(), min_confidence=threshold),
                    "word": crosscheck_word_fact(fact, compact, min_confidence=threshold)})
        if [p.model_dump(mode="json") for p in result.facts] != expected_pairs:
            raise ValueError("Transfer financial fields or outcomes do not reproduce from published evidence")
        if result.summary.model_dump() != summarize(expected_pairs):
            raise ValueError("Transfer document totals changed")
        all_pairs.extend(expected_pairs)
    if checked.summary.model_dump() != summarize(all_pairs):
        raise ValueError("Transfer overall totals changed")
    return checked.model_dump(mode="json")


def transfer_view(root: Path, report_path: Path | None = None, manifest_path: Path | None = None) -> dict:
    path = Path(report_path) if report_path is not None else Path(root)/"artifacts/document-transfer-report.json"
    if path.stat().st_size > 10_000_000:
        raise ValueError("Transfer report exceeds size limit")
    manifest, packs = load_manifest(root, manifest_path)
    report = _validate_loaded_report(json.loads(path.read_text(encoding="utf-8")), manifest, packs)
    return {"report": report, "manifest": manifest.model_dump(mode="json"), "read_only": True,
            "validation_scope": "Recomputes both methods from published necessary parent lines and their complete word groups. Full-page OCR/image/PDF hashes and omitted OCR content require the local raw evidence; published fragments alone cannot prove completeness or authenticity."}
