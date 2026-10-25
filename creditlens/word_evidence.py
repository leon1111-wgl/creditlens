"""Conservative token-location ablation over a full-page OCR result.

The OCR model never sees the reference facts. These rules inspect its complete
line/token output only after inference and never choose by the expected value.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from .diligence import Fact, PdfBox, StrictModel
from .visual import MIN_CONFIDENCE, OcrPage, OcrSpan, coverage, crosscheck_fact

WORD_RULE_VERSION = "word-evidence-1"
SIGN_GAP_POINTS = 8.0


class WordLine(StrictModel):
    line: OcrSpan
    words: list[OcrSpan] = Field(max_length=2000)


class WordPage(StrictModel):
    page: int = Field(ge=1, le=20000)
    width: float = Field(gt=0, le=20000, allow_inf_nan=False)
    height: float = Field(gt=0, le=20000, allow_inf_nan=False)
    coordinate_system: Literal["pdf_points_top_left"] = "pdf_points_top_left"
    lines: list[WordLine] = Field(max_length=5000)

    @model_validator(mode="after")
    def validate_geometry(self):
        self.line_page()
        self.token_page()
        return self

    def line_page(self) -> OcrPage:
        return OcrPage(page=self.page, width=self.width, height=self.height,
                       spans=[entry.line for entry in self.lines])

    def token_page(self) -> OcrPage:
        # RapidOCR 3.10's native token score is retained in WordPage, but the
        # decision uses the parent line score. Its score-list-to-word mapping
        # does not establish a calibrated confidence for each grouped word.
        return OcrPage(page=self.page, width=self.width, height=self.height,
                       spans=[word.model_copy(update={"confidence": entry.line.confidence})
                              for entry in self.lines for word in entry.words])


def _without_space(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _selected(box: PdfBox, target: PdfBox) -> bool:
    return coverage(target, box) >= 0.35 or (
        target.x0 <= (box.x0+box.x1)/2 <= target.x1 and
        target.top <= (box.top+box.bottom)/2 <= target.bottom)


def _near_numeric_cell(box: PdfBox, target: PdfBox) -> bool:
    overlap = max(0, min(box.bottom, target.bottom)-max(box.top, target.top))
    vertical = overlap / min(box.bottom-box.top, target.bottom-target.top)
    gap = max(0, box.x0-target.x1, target.x0-box.x1)
    return vertical >= 0.5 and gap <= SIGN_GAP_POINTS


def crosscheck_word_fact(fact: Fact, page: WordPage, *, min_confidence: float = MIN_CONFIDENCE) -> dict:
    result = crosscheck_fact(fact, page.token_page(), min_confidence=min_confidence)
    for check in result["checks"]:
        target = PdfBox.model_validate(check["region"])
        parents = []
        reasons = []
        guard_observations = []
        for line_id, entry in enumerate(page.lines):
            indices = [i for i, word in enumerate(entry.words) if _selected(word.bbox, target)]
            parent_selected = _selected(entry.line.bbox, target)
            if not indices and not parent_selected:
                continue
            parents.append({"line_index": line_id, "line": entry.line.model_dump(mode="json"),
                            "selected_word_indices": indices})
            # A second overlapping line may have no word boxes. Discarding it
            # could turn an ambiguous cell into a false agreement.
            if parent_selected and not indices:
                reasons.append("parent_line_without_spatial_words")
            # Do not let the token adapter silently lose a sign or decimal.
            if _without_space(entry.line.text) != "".join(_without_space(w.text) for w in entry.words):
                reasons.append("token_text_not_conserved")
        if check["field"] in {"amount", "year"}:
            # A detached sign, decimal or continuation digit just outside a
            # reference box must not disappear because its centre is out.
            for entry in page.lines:
                for word in entry.words:
                    if (not _selected(word.bbox, target) and
                        re.search(r"[0-9()+\-−.,%]", word.text) and
                        _near_numeric_cell(word.bbox, target)):
                        reason = "detached_numeric_fragment_near_cell"
                        reasons.append(reason)
                        guard_observations.append({"reason": reason,
                                                   "observation": word.model_dump(mode="json")})
        check["parent_lines"] = parents
        check["confidence_basis"] = "parent_line_score"
        check["word_guards"] = sorted(set(reasons))
        check["guard_observations"] = guard_observations
        if reasons:
            check["status"] = "insufficient"
            check["reason"] = sorted(set(reasons))[0]
    states = {check["status"] for check in result["checks"]}
    result["status"] = ("conflict" if "conflict" in states else
                        "insufficient" if "insufficient" in states else "consistent")
    result["evidence_granularity"] = "native_ocr_word_boxes"
    result["automatic_approval"] = False
    return result
