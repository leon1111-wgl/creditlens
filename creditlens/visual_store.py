"""Read registered public OCR examples and hash-bound local page previews."""
from __future__ import annotations

import copy
from collections import Counter
import hashlib
import json
from pathlib import Path

from .diligence import CasePack, digest
from .visual import OcrPage, OcrSpan, VISUAL_RULE_VERSION, crosscheck_fact

REGISTERED_PACKS = {"msft": "msft-fy2025-pdf.json", "aapl": "aapl-fy2025-pdf.json"}


class VisualArtifactError(ValueError):
    pass


class VisualStore:
    def __init__(self, root: Path, local_dir: Path | None = None):
        self.root = root
        self.local_dir = local_dir or root/".run/document-ai"

    def report(self):
        path = self.root/"artifacts/document-ai-report.json"
        if not path.is_file():
            raise FileNotFoundError("Run scripts/evaluate_document_ai.py first")
        try:
            return self._validate(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise VisualArtifactError("Visual report validation failed: " + str(exc)) from exc

    def _validate(self, report):
        if report.get("schema_version") != 1:
            raise VisualArtifactError("Unsupported visual report version")
        if {d["id"] for d in report["documents"]} != set(REGISTERED_PACKS) or len(report["documents"]) != 2:
            raise VisualArtifactError("Unregistered document report")
        profile = json.loads((self.root/"configs/document-ai.json").read_text(encoding="utf-8"))
        if (report["profile"] != profile or report["profile_sha256"] != digest(profile) or
                report["rule_version"] != VISUAL_RULE_VERSION):
            raise VisualArtifactError("Visual configuration or rules changed; rebuild the report")
        totals = Counter()
        for doc in report["documents"]:
            pack = CasePack.model_validate_json((self.root/"data/examples"/REGISTERED_PACKS[doc["id"]]).read_text(encoding="utf-8"))
            if doc["pack_sha256"] != digest(pack.model_dump(mode="json")):
                raise VisualArtifactError("Source facts changed; rebuild the visual report")
            if doc["source"] != pack.sources[0].model_dump(mode="json") or doc["company"] != pack.company:
                raise VisualArtifactError("Document source does not match its registered pack")
            expected = {f.id: f for f in pack.facts}
            if set(expected) != {f["fact_id"] for f in doc["facts"]} or len(expected) != len(doc["facts"]):
                raise VisualArtifactError("Report must contain every registered fact exactly once")
            pages = {f.pdf_location.page for f in pack.facts}
            if {p["page"] for p in doc["pages"]} != pages or len(pages) != len(doc["pages"]):
                raise VisualArtifactError("Report pages do not match the registered facts")
            # Re-evaluate the published observations against the source facts.
            # This detects broken references/statistics, not forgery by a local
            # administrator able to rewrite the entire report and model outputs.
            for page in doc["pages"]:
                facts = [f for f in doc["facts"] if f["page"] == page["page"]]
                spans = {}
                multiplicity = Counter()
                for fact in facts:
                    for check in fact["checks"]:
                        occurrences = Counter()
                        for observation in check["observations"]:
                            span = OcrSpan.model_validate(observation)
                            key = digest(span.model_dump(mode="json"))
                            spans[key] = span
                            occurrences[key] += 1
                        # Repeated references across fields are the same evidence;
                        # duplicate OCR detections inside a field remain ambiguous.
                        multiplicity |= occurrences
                ocr = OcrPage(page=page["page"], width=page["width"], height=page["height"],
                              spans=[span for key, span in spans.items() for _ in range(multiplicity[key])])
                for fact in facts:
                    recomputed = crosscheck_fact(expected[fact["fact_id"]], ocr, min_confidence=profile["min_confidence"])
                    if fact != recomputed:
                        raise VisualArtifactError("Fact references or visual checks do not reproduce")
            if any(f["page"] != expected[f["fact_id"]].pdf_location.page for f in doc["facts"]):
                raise VisualArtifactError("Fact is assigned to the wrong page")
            counts = Counter(f["status"] for f in doc["facts"])
            if doc["summary"] != {s: counts[s] for s in ("consistent", "conflict", "insufficient")}:
                raise VisualArtifactError("Document counts do not match its checked facts")
            totals.update(counts)
        if report["summary"] != {s: totals[s] for s in ("consistent", "conflict", "insufficient")}:
            raise VisualArtifactError("Overall counts do not match document results")
        return report

    def view(self):
        report = copy.deepcopy(self.report())
        for doc in report["documents"]:
            for page in doc["pages"]:
                page["preview_available"] = self._path(doc["id"], page["page"]).is_file()
        report["preview_policy"] = "Local registered page renders only; raw pages are not in the public repository."
        return report

    def _path(self, doc_id: str, page: int):
        if doc_id not in REGISTERED_PACKS or type(page) is not int or not 1 <= page <= 20000:
            raise FileNotFoundError("Unknown document page")
        path = (self.local_dir/doc_id/f"page-{page}.png").resolve()
        if not path.is_relative_to(self.local_dir.resolve()):
            raise VisualArtifactError("Preview path left the registered directory")
        return path

    def image(self, doc_id: str, page_number: int) -> bytes:
        if doc_id not in REGISTERED_PACKS:
            raise FileNotFoundError("Unknown document")
        document = next(d for d in self.report()["documents"] if d["id"] == doc_id)
        page = next((p for p in document["pages"] if p["page"] == page_number), None)
        if page is None:
            raise FileNotFoundError("Unknown document page")
        path = self._path(doc_id, page_number)
        if not path.is_file():
            raise FileNotFoundError("Local preview missing; run scripts/evaluate_document_ai.py")
        if path.stat().st_size > 25_000_000:
            raise VisualArtifactError("Preview exceeds size limit")
        raw = path.read_bytes()
        if not raw.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(raw).hexdigest() != page["image_sha256"]:
            raise VisualArtifactError("Preview hash mismatch; rebuild before using")
        return raw
