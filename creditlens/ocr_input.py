"""Strict, reference-free input adapter for the RapidOCR 3.10 word-box shape.

This new opt-in adapter does not replace the frozen experimental adapters. It
validates every raw corner before any bounding-box reduction, without clipping,
sorting vertices, repairing text, discarding rows, loading models or downloading.
"""
from __future__ import annotations

import math
from typing import Any

from .word_evidence import WordLine, WordPage
from .visual import OcrSpan


OCR_INPUT_VERSION = "ocr-input-1"
MAX_LINES = 5000
MAX_WORDS_PER_LINE = 2000
MAX_TOTAL_WORDS = 5000  # Existing WordPage.token_page / OcrPage bound.
MAX_IMAGE_SIDE = 1_000_000
MAX_ARRAY_ELEMENTS = 100_000
RESULT_FIELDS = ("boxes", "txts", "scores", "word_results")
SCOPE = ("Checks supported raw OCR arrays, finite values and simple convex quadrilaterals only; "
         "not OCR correctness, parent-word ownership, financial consistency, source authenticity or automatic approval. "
         "Previously normalized WordPage data cannot establish validity of discarded raw corners.")
EMPTY_CONTRACT = ("Four null fields; four empty arrays; or boxes/txts/scores null with the exact RapidOCR 3.10 "
                  "default word_results sentinel ((empty string, 1.0, null),). Source: rapidocr/utils/output.py, "
                  "RapidOCROutput defaults (installed version 3.10). Nonempty lines require explicit word arrays; "
                  "an empty word array preserves the parent, while null word mappings are rejected.")


class OcrInputError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.code, self.path, self.message = code, path, message
        super().__init__(f"{code} at {path}: {message}")


def _fail(code, path, message):
    raise OcrInputError(code, path, message)


def _numpy(value):
    """Import optionally only for genuine numpy types, never duck-call tolist."""
    if not type(value).__module__.startswith("numpy"):
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    return np


def _sequence(value, path, maximum):
    if type(value) in (list, tuple):
        if len(value) > maximum:
            _fail("too_many_items", path, "Sequence exceeds the supported bound")
        return list(value)
    np = _numpy(value)
    if np is not None and type(value) is np.ndarray:
        if value.dtype.kind not in "biufUS" or value.ndim == 0:
            _fail("unsupported_array", path, "Only non-object numeric or text numpy arrays are supported")
        if len(value) > maximum or value.size > MAX_ARRAY_ELEMENTS:
            _fail("too_many_items", path, "Numpy array exceeds the supported bound")
        # Exact ndarray type excludes overridden tolist on a subclass. Returned
        # scalar elements are still checked individually for type and finiteness.
        return np.ndarray.tolist(value)
    _fail("invalid_type", path, "Expected a list, tuple or supported numpy ndarray")


def _number(value, path):
    if type(value) not in (int, float):
        np = _numpy(value)
        if np is None or not isinstance(value, (np.integer, np.floating)) or isinstance(value, np.bool_):
            _fail("invalid_type", path, "Expected a real numeric value, not bool or numeric text")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise OcrInputError("invalid_number", path, "Numeric value cannot be represented as a finite float") from exc
    if not math.isfinite(number):
        _fail("nonfinite_number", path, "NaN and infinity are not permitted")
    return number


def _integer(value, path, maximum):
    np = _numpy(value)
    if not (type(value) is int or np is not None and isinstance(value, np.integer) and not isinstance(value, np.bool_)):
        _fail("invalid_type", path, "Expected an integer, not bool, float or text")
    number = int(value)
    if not 1 <= number <= maximum:
        _fail("out_of_range", path, "Integer is outside the supported positive range")
    return number


def _dimension(value, path):
    number = _number(value, path)
    if not 0 < number <= 20000:
        _fail("out_of_range", path, "PDF dimensions must be positive and at most 20000 points")
    return number


def _text(value, path):
    if type(value) is not str:
        _fail("invalid_type", path, "OCR text must be a string")
    if not value.strip() or len(value) > 3000:
        _fail("invalid_text", path, "OCR text must be nonblank and at most 3000 characters")
    return value


def _score(value, path):
    number = _number(value, path)
    if not 0 <= number <= 1:
        _fail("out_of_range", path, "OCR score must be between zero and one")
    return number


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p):
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def _intersects(a, b, c, d):
    ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if (ab_c > 0 > ab_d or ab_d > 0 > ab_c) and (cd_a > 0 > cd_b or cd_b > 0 > cd_a):
        return True
    return (ab_c == 0 and _on_segment(a, b, c) or ab_d == 0 and _on_segment(a, b, d)
            or cd_a == 0 and _on_segment(c, d, a) or cd_b == 0 and _on_segment(c, d, b))


def _quad(value, path, image_width, image_height):
    raw = _sequence(value, path, 4)
    if len(raw) != 4:
        _fail("invalid_shape", path, "Expected four vertices in boundary order")
    points = []
    for index, point in enumerate(raw):
        point_path = f"{path}[{index}]"
        pair = _sequence(point, point_path, 2)
        if len(pair) != 2:
            _fail("invalid_shape", point_path, "Expected exactly x and y coordinates")
        x, y = (_number(pair[axis], f"{point_path}[{axis}]") for axis in (0, 1))
        for axis, number, bound in ((0, x, image_width), (1, y, image_height)):
            if not 0 <= number <= bound:
                _fail("coordinate_out_of_bounds", f"{point_path}[{axis}]", "Raw corner lies outside the declared image")
        points.append((x, y))
    # Only after all raw coordinates have been validated may geometry be reduced.
    if len(set(points)) != 4:
        _fail("degenerate_quad", path, "Quadrilateral has repeated vertices")
    if _intersects(points[0], points[1], points[2], points[3]) or _intersects(points[1], points[2], points[3], points[0]):
        _fail("self_intersecting_quad", path, "Nonadjacent quadrilateral edges intersect")
    turns = [_cross(points[i], points[(i + 1) % 4], points[(i + 2) % 4]) for i in range(4)]
    if any(turn == 0 for turn in turns):
        _fail("degenerate_quad", path, "Quadrilateral has a collinear corner")
    if not (all(turn > 0 for turn in turns) or all(turn < 0 for turn in turns)):
        _fail("nonconvex_quad", path, "Only simple strictly convex quadrilaterals are supported")
    return points


def _fields(result):
    if type(result) is dict:
        if set(result) != set(RESULT_FIELDS):
            _fail("invalid_fields", "result", "Expected exactly boxes, txts, scores and word_results")
        return result
    # Native RapidOCROutput has additional image/timing fields. They are not
    # inspected or serialized. Only the four named data attributes are needed.
    try:
        return {key: getattr(result, key) for key in RESULT_FIELDS}
    except (AttributeError, TypeError) as exc:
        raise OcrInputError("invalid_fields", "result", "Missing native OCR output attributes") from exc


def _empty_sentinel(value):
    # RapidOCR 3.10 utils/output.py: RapidOCROutput.word_results default. Do not
    # use permissive equality: True == 1.0 must not make a valid score sentinel.
    if type(value) not in (list, tuple) or len(value) != 1:
        return False
    record = value[0]
    return (type(record) in (list, tuple) and len(record) == 3 and type(record[0]) is str and record[0] == ""
            and type(record[1]) in (int, float) and record[1] == 1.0 and record[2] is None)


def adapt_raw_ocr(result: Any, *, page, width, height, image_width, image_height) -> WordPage:
    """Validate native or four-field OCR data before reference-free conversion."""
    page = _integer(page, "page", 20000)
    width, height = _dimension(width, "width"), _dimension(height, "height")
    image_width = _integer(image_width, "image_width", MAX_IMAGE_SIDE)
    image_height = _integer(image_height, "image_height", MAX_IMAGE_SIDE)
    data = _fields(result)
    if all(data[key] is None for key in RESULT_FIELDS):
        return WordPage(page=page, width=width, height=height, lines=[])
    if all(data[key] is None for key in RESULT_FIELDS[:3]) and _empty_sentinel(data["word_results"]):
        return WordPage(page=page, width=width, height=height, lines=[])
    if any(data[key] is None for key in RESULT_FIELDS):
        _fail("invalid_empty_output", "result", "Null output fields cannot hide orphaned OCR records")
    arrays = {key: _sequence(data[key], f"result.{key}", MAX_LINES) for key in RESULT_FIELDS}
    for key in RESULT_FIELDS:
        value, np = data[key], _numpy(data[key])
        if np is not None and type(value) is np.ndarray:
            allowed_shape = value.ndim == 1 if key != "boxes" else value.ndim == 3 and value.shape[1:] == (4, 2)
            # Plain [] may also be represented by an empty one-dimensional array.
            if not allowed_shape and not (value.ndim == 1 and value.size == 0):
                _fail("invalid_shape", f"result.{key}", "Native array dimensions do not match the output contract")
    sizes = [len(arrays[key]) for key in RESULT_FIELDS]
    if len(set(sizes)) != 1:
        _fail("line_length_mismatch", "result", "Line and word arrays have different lengths")

    def span(text, score, quad, text_path, score_path, quad_path):
        text, score = _text(text, text_path), _score(score, score_path)
        points = _quad(quad, quad_path, image_width, image_height)
        # Divide pixels first to avoid multiplication overflow. No min/max clip.
        xs = [x / image_width * width for x, _ in points]
        ys = [y / image_height * height for _, y in points]
        if not (min(xs) < max(xs) and min(ys) < max(ys)):
            _fail("degenerate_converted_box", quad_path, "Valid raw geometry collapses at the declared output scale")
        return OcrSpan(text=text, confidence=score, bbox={"x0": min(xs), "x1": max(xs), "top": min(ys), "bottom": max(ys)})

    lines, total_words = [], 0
    for index in range(sizes[0]):
        line = span(arrays["txts"][index], arrays["scores"][index], arrays["boxes"][index],
                    f"result.txts[{index}]", f"result.scores[{index}]", f"result.boxes[{index}]")
        words_path = f"result.word_results[{index}]"
        if arrays["word_results"][index] is None:
            _fail("missing_word_mapping", words_path, "A detected line needs an explicit word array, which may be empty")
        words_raw = _sequence(arrays["word_results"][index], words_path, MAX_WORDS_PER_LINE)
        total_words += len(words_raw)
        if total_words > MAX_TOTAL_WORDS:
            _fail("too_many_items", words_path, "Total word count exceeds the WordPage contract")
        words = []
        for word_index, word in enumerate(words_raw):
            word_path = f"{words_path}[{word_index}]"
            record = _sequence(word, word_path, 3)
            if len(record) != 3:
                _fail("invalid_shape", word_path, "Expected word text, score and quadrilateral")
            words.append(span(*record, f"{word_path}[0]", f"{word_path}[1]", f"{word_path}[2]"))
        lines.append(WordLine(line=line, words=words))
    return WordPage(page=page, width=width, height=height, lines=lines)


def inspect_raw_ocr(raw_envelope: dict) -> dict:
    """Inspect one explicit raw-envelope object; errors carry a bounded path."""
    fields = {"schema_version", "page", "width", "height", "image_width", "image_height", "result"}
    if type(raw_envelope) is not dict or set(raw_envelope) != fields:
        _fail("invalid_fields", "$", "Unexpected or missing raw OCR envelope fields")
    if type(raw_envelope["schema_version"]) is not int or raw_envelope["schema_version"] != 1:
        _fail("unsupported_schema", "schema_version", "Only raw OCR envelope schema 1 is supported")
    if type(raw_envelope["result"]) is not dict:
        _fail("invalid_type", "result", "Envelope result must be a four-field object")
    page = adapt_raw_ocr(raw_envelope["result"], **{key: raw_envelope[key] for key in fields - {"schema_version", "result"}})
    return {"schema_version": 1, "adapter_version": OCR_INPUT_VERSION, "ok": True,
            "page": page.model_dump(mode="json"),
            "summary": {"lines": len(page.lines), "words": sum(len(line.words) for line in page.lines), "empty": not page.lines},
            "scope": SCOPE, "empty_contract": EMPTY_CONTRACT, "automatic_approval": False}
