"""Contracts for synthetic, coordinate-preserving document stress experiments.

No OCR or imaging dependency is imported until the offline runner requests it.
These development experiments measure known-reference consistency, not accuracy
on real scans, model calibration, document authenticity or unseen layouts.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .diligence import StrictModel

STATES = ("consistent", "conflict", "insufficient")
ROBUSTNESS_VERSION = "visual-robustness-1"
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class PageInput(StrictModel):
    document_id: Literal["msft", "aapl"]
    page: int = Field(strict=True, ge=1, le=20000)
    image_sha256: Hash
    pack_sha256: Hash


class Variant(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,40}$")
    operation: Literal["identity", "gaussian_blur", "contrast", "jpeg"]
    radius: float | None = Field(default=None, gt=0, le=5, allow_inf_nan=False)
    factor: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    quality: int | None = Field(default=None, strict=True, ge=1, le=95)

    @model_validator(mode="after")
    def operation_parameters(self):
        expected = {"identity": set(), "gaussian_blur": {"radius"},
                    "contrast": {"factor"}, "jpeg": {"quality"}}[self.operation]
        actual = {key for key in ("radius", "factor", "quality") if getattr(self, key) is not None}
        if actual != expected:
            raise ValueError("Each transform must declare exactly its applicable parameter")
        return self


class RobustnessConfig(StrictModel):
    schema_version: Literal[1] = 1
    suite_version: Literal["visual-robustness-1"] = ROBUSTNESS_VERSION
    frozen_before_inference: Literal[True]
    selection_reason: str = Field(min_length=10)
    source_report_sha256: Hash
    source_profile_sha256: Hash
    pages: list[PageInput] = Field(min_length=1, max_length=10)
    variants: list[Variant] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def unique_and_complete(self):
        if len({(p.document_id, p.page) for p in self.pages}) != len(self.pages):
            raise ValueError("Duplicate page input")
        if len({v.id for v in self.variants}) != len(self.variants):
            raise ValueError("Duplicate variant identifier")
        if self.variants[0].operation != "identity" or sum(v.operation == "identity" for v in self.variants) != 1:
            raise ValueError("Exactly one clean identity baseline must be first")
        return self


def verified_image(path: Path, expected_sha256: str, expected_size: tuple[int, int]) -> bytes:
    """Require the already registered complete page image; never retrieve a URL."""
    from PIL import Image
    if not path.is_file():
        raise FileNotFoundError("Registered source render missing; run evaluate_document_ai.py first")
    if path.stat().st_size > 25_000_000:
        raise ValueError("Source image exceeds the experiment size limit")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Source image does not match its frozen hash")
    with Image.open(BytesIO(raw)) as image:
        if image.format != "PNG" or image.size != expected_size:
            raise ValueError("Source page format or dimensions changed")
        image.load()
    return raw


def transform_image(raw: bytes, variant: Variant) -> bytes:
    """Generate a complete page, retaining width/height and all coordinate origins.

JPEG is encoded with fixed options, decoded, then saved losslessly as PNG. This
preserves JPEG pixel degradation while using one local image format for OCR.
There is no reference fact/label/box argument and no crop or answer repair.
"""
    from PIL import Image, ImageEnhance, ImageFilter
    with Image.open(BytesIO(raw)) as source:
        original_size = source.size
        source.load()
        if variant.operation == "identity":
            return raw
        image = source.convert("RGB")
    if variant.operation == "gaussian_blur":
        image = image.filter(ImageFilter.GaussianBlur(radius=variant.radius))
    elif variant.operation == "contrast":
        image = ImageEnhance.Contrast(image).enhance(variant.factor)
    elif variant.operation == "jpeg":
        compressed = BytesIO()
        image.save(compressed, format="JPEG", quality=variant.quality, subsampling=0,
                   optimize=False, progressive=False)
        with Image.open(BytesIO(compressed.getvalue())) as decoded:
            image = decoded.convert("RGB")
    else:
        raise ValueError("Unsupported transformation")
    if image.size != original_size:
        raise ValueError("A transform changed the page coordinate system")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def summarize(facts: list[dict]) -> dict:
    ids = [f["fact_id"] for f in facts]
    if len(set(ids)) != len(ids) or any(f["status"] not in STATES for f in facts):
        raise ValueError("Duplicate fact or unrecognized state")
    counts = Counter(f["status"] for f in facts)
    return {state: counts[state] for state in STATES}


def compare_conditions(clean: list[dict], variant: list[dict]) -> dict:
    summarize(clean)
    summarize(variant)
    reference = {f["fact_id"]: f["status"] for f in clean}
    observed = {f["fact_id"]: f["status"] for f in variant}
    if reference.keys() != observed.keys():
        raise ValueError("A condition omitted or added facts")
    transitions = {f"{a}_to_{b}": 0 for a in STATES for b in STATES}
    for key, state in reference.items():
        transitions[f"{state}_to_{observed[key]}"] += 1
    return {"fact_count": len(clean), "transitions": transitions,
            "status_changed": sum(reference[key] != value for key, value in observed.items()),
            "consistent_to_nonconsistent": sum(reference[key] == "consistent" and value != "consistent"
                                                for key, value in observed.items()),
            "nonconsistent_to_consistent": sum(reference[key] != "consistent" and value == "consistent"
                                                for key, value in observed.items())}


def compact_fact(result: dict) -> dict:
    """Publish every field outcome; retain original evidence snippets for failures."""
    return {key: result[key] for key in ("fact_id", "label", "page", "status", "automatic_approval")} | {
        "checks": [{key: c[key] for key in ("field", "status", "reason")} |
                   ({"expected": c["expected"], "region": c["region"], "observations": c["observations"]}
                    if c["status"] != "consistent" else {}) for c in result["checks"]]}
