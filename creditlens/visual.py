"""Compare full-page OCR with declared financial evidence, without approving facts.

Reference regions are used only after image recognition. This is a spatial
cross-check of known facts, not blind table extraction or document authentication.
"""
from __future__ import annotations

import re
import math
from typing import Literal

from pydantic import Field, model_validator

from .diligence import Fact, PdfBox, StrictModel, displayed_amount

VISUAL_RULE_VERSION = "visual-evidence-1"
MIN_CONFIDENCE = 0.90


class OcrSpan(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    bbox: PdfBox


class OcrPage(StrictModel):
    page: int = Field(ge=1, le=20000)
    width: float = Field(gt=0, le=20000, allow_inf_nan=False)
    height: float = Field(gt=0, le=20000, allow_inf_nan=False)
    coordinate_system: Literal["pdf_points_top_left"] = "pdf_points_top_left"
    spans: list[OcrSpan] = Field(max_length=5000)

    @model_validator(mode="after")
    def inside_page(self):
        for span in self.spans:
            if span.bbox.x1 > self.width or span.bbox.bottom > self.height:
                raise ValueError("OCR box outside declared page")
        return self


def coverage(target: PdfBox, other: PdfBox) -> float:
    area = max(0, min(target.x1, other.x1)-max(target.x0, other.x0)) * max(
        0, min(target.bottom, other.bottom)-max(target.top, other.top))
    return area / ((target.x1-target.x0)*(target.bottom-target.top))


def canonical(text: str) -> str:
    # Ignore punctuation/spacing for labels only; never repair OCR digits/signs.
    return "".join(re.findall(r"[a-z0-9]+", text.casefold()))


def compare_region(page: OcrPage, *, field: str, target: PdfBox, expected: str,
                   min_confidence: float = MIN_CONFIDENCE) -> dict:
    if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
        raise ValueError("Invalid OCR confidence threshold")
    hits = [s for s in page.spans if coverage(target, s.bbox) >= 0.35 or
            (target.x0 <= (s.bbox.x0+s.bbox.x1)/2 <= target.x1 and
             target.top <= (s.bbox.top+s.bbox.bottom)/2 <= target.bottom)]
    hits.sort(key=lambda s: (round(s.bbox.top/3), s.bbox.x0))
    result = {"field": field, "expected": expected, "region": target.model_dump(),
              "observations": [s.model_dump() for s in hits], "status": "insufficient",
              "reason": "no_spatial_evidence"}
    if not hits:
        return result
    if any(s.confidence < min_confidence for s in hits):
        return dict(result, reason="low_ocr_confidence")
    # Reject merged cells and content outside the declared region. Never pick a
    # substring because it happens to equal the expected financial amount.
    if any(s.bbox.x0 < target.x0-8 or s.bbox.x1 > target.x1+8 or
           s.bbox.top < target.top-8 or s.bbox.bottom > target.bottom+8 for s in hits):
        return dict(result, reason="merged_or_misaligned_region")
    text = " ".join(s.text for s in hits)
    if field in {"amount", "year"}:
        if len(hits) != 1:
            return dict(result, reason="ambiguous_cell")
        try:
            actual, wanted = displayed_amount(text), displayed_amount(expected)
        except ValueError:
            return dict(result, reason="unreadable_numeric_cell")
        if field == "year" and not re.fullmatch(r"[0-9]{4}", text.strip()):
            return dict(result, reason="unreadable_year")
        match = actual == wanted
    else:
        match = canonical(text) == canonical(expected)
    return dict(result, status="consistent" if match else "conflict",
                reason="same_displayed_content" if match else "different_displayed_content")


def crosscheck_fact(fact: Fact, page: OcrPage, *, min_confidence: float = MIN_CONFIDENCE) -> dict:
    loc = fact.pdf_location
    if loc is None or (page.page, page.width, page.height) != (loc.page, loc.page_width, loc.page_height):
        raise ValueError("Fact and OCR page coordinates do not match")
    regions = [("amount", loc.amount_bbox, fact.raw_value), ("year", loc.year_bbox, str(loc.year)),
               ("period", loc.period_bbox, loc.period_header), ("unit", loc.unit_bbox, loc.unit_header),
               ("row_label", loc.row_label_bbox, loc.row_label)]
    if loc.section:
        regions += [("section_start", loc.section.start_bbox, loc.section.start_label),
                    ("section_end", loc.section.end_bbox, loc.section.end_label)]
    checks = [compare_region(page, field=key, target=region, expected=text, min_confidence=min_confidence)
              for key, region, text in regions]
    states = {c["status"] for c in checks}
    state = "conflict" if "conflict" in states else "insufficient" if "insufficient" in states else "consistent"
    return {"fact_id": fact.id, "label": fact.label, "page": loc.page,
            "raw_value": fact.raw_value, "value": str(fact.value), "unit": fact.unit,
            "period_start": fact.period_start.isoformat() if fact.period_start else None,
            "period_end": fact.period_end.isoformat(), "status": state,
            "automatic_approval": False, "checks": checks}
