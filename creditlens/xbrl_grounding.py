"""Score image-only extraction against an issuer's own Inline XBRL tags.

The answer key comes from the issuer's iXBRL 10-K: every ``ix:nonFraction`` on a
rendered financial-statement page, with its box, printed text, concept, context
(period and dimensions), unit, decimals, scale and sign. The extraction side sees
only the rendered page image: full-page OCR (the pinned RapidOCR profile), then a
reference-free page reading that finds unit headers and year columns. Facts are
matched to OCR only after recognition, with the repository's existing rules:

* spatial selection, confidence floor and 8-point misalignment guard from
  ``visual.compare_region`` (visual-evidence-1);
* the detached sign/digit guard from ``word_evidence`` (word-evidence-1);
* the strict amount grammar and header/column geometry from ``table_lookup``
  (table-lookup-1), generalised from one queried row to every numeric cell.

Nothing here downloads, renders or runs a model. Results describe clean
Chromium renders of born-digital filings, not scans.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import re
import unicodedata

from .table_lookup import parse_lookup_amount, TableLookupError, HEADER_CENTER_TOLERANCE, MAX_YEAR_COLUMNS
from .visual import MIN_CONFIDENCE

SCORE_RULE_1 = "xbrl-grounding-score-1"   # v0.34.0, held-out run 42712945764
SCORE_RULE_2 = "xbrl-grounding-score-2"   # v0.35.0: heading scope limited by indentation
SCORE_RULES = (SCORE_RULE_1, SCORE_RULE_2)
SCORE_RULE_VERSION = SCORE_RULE_2
MISALIGNMENT_POINTS = 8.0      # visual.compare_region
SIGN_GAP_POINTS = 8.0          # word_evidence.SIGN_GAP_POINTS
ROW_CENTER_TOLERANCE = 4.0     # table_lookup.ROW_CENTER_TOLERANCE
MIN_VERTICAL_OVERLAP = 0.50    # table_lookup.MIN_VERTICAL_OVERLAP
PENALTIES = (0, 10)
HEADING_ROWS = 4               # amount rows a heading may sit above (Basic, Diluted, ...)
INDENT_POINTS = 3.0            # score-2: rows below the first must be indented this much under a heading

# Formats whose printed text has a defined numeric value here. Others are kept
# in the key but not scored for value (counted as unsupported_format).
_NUMERIC_FORMATS = {"num-dot-decimal", "numdotdecimal", "numcommadot", "num-dot-decimal-apos"}
_ZERO_FORMATS = {"fixed-zero", "zerodash", "fixedzero"}

_UNIT = re.compile(r"^\(?\s*(?:(?:u\.?s\.?\s+)?dollars?\s+|amounts?\s+|\$\s*)?in\s+(thousands|millions|billions)\b")
_UNIT_SCALE = {"thousands": 3, "millions": 6, "billions": 9}
_YEAR = re.compile(r"^(?:(?:fiscal\s+)?(?:years?|periods?)\s+ended\s+)?(?:as\s+of\s+)?"
                   r"(?:[a-z]{3,9}\.?\s+[0-9]{1,2},?\s+|fiscal\s+(?:year\s+)?)?((?:19|20)[0-9]{2})$")
_SHARE_ROW = re.compile(r"per[\s-]*share|\bshares?\b|par\s+value")
_PER_SHARE_SUBLABEL = re.compile(r"^(?:basic|diluted|basic\s+and\s+diluted)\b")
_FRAGMENT = re.compile(r"[0-9()+\-−.,%]")

OUTCOME_LABELS = {
    "match": "value, sign, scale and period column all agree with the key",
    "conflict": "the extraction asserted a reading that disagrees with the key",
    "abstain": "the extraction gave no complete reading",
}
ABSTAIN_REASONS = {
    "no_spatial_evidence": "no OCR span selected at the fact's box",
    "low_ocr_confidence": "a selected OCR span scored below 0.90",
    "merged_or_misaligned_region": "a selected OCR span reaches more than 8 pt outside the fact's box",
    "ambiguous_cell": "more than one OCR span selected for one cell",
    "detached_numeric_fragment_near_cell": "a separate sign or digit fragment sits next to the cell",
    "unreadable_numeric_cell": "the cell text is not a complete amount (e.g. a dash, footnote mark or merged text)",
    "unit_missing": "no supported unit header (e.g. '(In millions)') above the cell",
    "share_or_per_share_row": "row or heading names shares / per share / par value; the printed scale does not state it",
    "no_year_header": "no year column header above the cell",
    "duplicate_header_year": "the year header repeats a year, so the column is not unique",
    "outside_header_columns": "the cell lies outside the year columns",
    "cell_crosses_column": "the cell crosses a year-column boundary",
}


class GroundingError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------

def _local(name: str | None) -> str:
    return (name or "").split(":")[-1].strip().casefold()


def key_magnitude(text: str, fmt: str | None) -> Decimal | None:
    """Printed magnitude by the fact's ixt format; None when the format is not supported here."""
    raw = unicodedata.normalize("NFKC", text or "").strip()
    kind = _local(fmt)
    if kind in _ZERO_FORMATS:
        return Decimal(0)
    if kind and kind not in _NUMERIC_FORMATS:
        return None
    cleaned = raw.replace(",", "").replace(" ", "")
    if kind == "num-dot-decimal-apos":
        cleaned = cleaned.replace("'", "")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cleaned):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def page_period_starts(facts: list[dict], contexts: dict) -> dict[str, int]:
    """Start date -> end year of every duration context used on one page."""
    starts = {}
    for fact in facts:
        period = (contexts.get(fact.get("context_ref") or "", {}) or {}).get("period") or {}
        if period.get("start") and period.get("end") and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", period["end"]):
            starts[period["start"]] = int(period["end"][:4])
    return starts


def column_years(context: dict, starts: dict[str, int] | None = None) -> tuple[list[int], str | None]:
    """Years of the printed columns a fact may legitimately sit in.

    A duration sits in the column of its end year; an instant in the column of its own year.
    An instant dated the day before a duration on the same page starts is also that duration's
    opening balance (cash "beginning of period" on a cash-flow statement), so the same fact is
    printed in two columns: its own year as an ending balance and the next as an opening one.
    """
    year = period_year(context)
    if year is None:
        return [], None
    period = context.get("period") or {}
    instant = period.get("instant")
    if instant and starts:
        following = (date.fromisoformat(instant) + timedelta(days=1)).isoformat()
        if following in starts and starts[following] != year:
            return [year, starts[following]], "opening_or_closing_balance"
    return [year], None


def period_year(context: dict) -> int | None:
    period = context.get("period") or {}
    end = period.get("instant") or period.get("end")
    if not end or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", end):
        return None
    return int(end[:4])


def key_entry(fact: dict, contexts: dict, units: dict, starts: dict[str, int] | None = None) -> dict:
    """Derive the comparison key for one fact; never consults OCR."""
    magnitude = key_magnitude(fact.get("text", ""), fact.get("format"))
    try:
        scale = int(fact.get("scale") or 0)
    except ValueError:
        scale = None
    context = contexts.get(fact.get("context_ref") or "", {})
    printed_negative = bool(fact.get("printed_open") and fact.get("printed_close"))
    xbrl_negative = fact.get("sign") == "-"
    value = None
    if magnitude is not None and scale is not None:
        value = magnitude.scaleb(scale)
    years, period_role = column_years(context, starts)
    year = years[0] if years else None
    return {
        "magnitude": None if magnitude is None else format(magnitude, "f"),
        "scale": scale, "year": year, "years": years, "period_role": period_role,
        "printed_negative": printed_negative, "xbrl_negative": xbrl_negative,
        "printed_value": None if value is None else format(-value if printed_negative else value, "f"),
        "xbrl_value": None if value is None else format(-value if xbrl_negative else value, "f"),
        "unit": units.get(fact.get("unit_ref") or "", fact.get("unit_ref")),
        "period": context.get("period"), "dimensions": context.get("dims", []),
        "scorable": magnitude is not None and scale is not None and year is not None,
        "not_scored_reason": None if magnitude is not None else "unsupported_format",
    }


# ---------------------------------------------------------------------------
# Reference-free page reading (OCR only)
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _amount(text: str) -> Decimal | None:
    try:
        return Decimal(parse_lookup_amount(text))
    except (TableLookupError, InvalidOperation):
        return None


def _cx(box):
    return (box["x0"] + box["x1"]) / 2


def _cy(box):
    return (box["top"] + box["bottom"]) / 2


def _same_row(a, b):
    overlap = max(0.0, min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]))
    ratio = overlap / max(min(a["bottom"] - a["top"], b["bottom"] - b["top"]), 1e-9)
    return abs(_cy(a) - _cy(b)) <= ROW_CENTER_TOLERANCE or ratio >= MIN_VERTICAL_OVERLAP


def header_year(text: str) -> int | None:
    match = _YEAR.fullmatch(_norm(text).rstrip(",:").strip())
    return int(match.group(1)) if match else None


def unit_header(text: str) -> dict | None:
    value = _norm(text)
    match = _UNIT.match(value)
    if not match:
        return None
    return {"scale": _UNIT_SCALE[match.group(1)], "qualified": "except" in value}


class PageReading:
    """Everything the extraction knows about one page, read from OCR spans only."""

    def __init__(self, spans: list[dict], rule: str = SCORE_RULE_1):
        if rule not in SCORE_RULES:
            raise GroundingError("unknown score rule: " + str(rule))
        self.rule = rule
        self.spans = spans
        self.amounts = {i: _amount(s["text"]) for i, s in enumerate(spans)}
        self.units = [(i, unit_header(s["text"])) for i, s in enumerate(spans)]
        self.units = [(i, u) for i, u in self.units if u]
        years = sorted(((i, header_year(s["text"])) for i, s in enumerate(spans)), key=lambda p: (_cy(spans[p[0]]["bbox"]), _cx(spans[p[0]]["bbox"])))
        years = [(i, y) for i, y in years if y is not None]
        groups: list[list[tuple[int, int]]] = []
        for item in years:
            if groups and _cy(spans[item[0]]["bbox"]) - _cy(spans[groups[-1][0][0]]["bbox"]) <= HEADER_CENTER_TOLERANCE:
                groups[-1].append(item)
            else:
                groups.append([item])
        self.groups = []
        for group in groups:
            if not 2 <= len(group) <= MAX_YEAR_COLUMNS:
                continue
            ordered = sorted(group, key=lambda p: _cx(spans[p[0]]["bbox"]))
            centers = [_cx(spans[i]["bbox"]) for i, _ in ordered]
            bounds = [centers[0] - (centers[1] - centers[0]) / 2,
                      *[(a + b) / 2 for a, b in zip(centers, centers[1:])],
                      centers[-1] + (centers[-1] - centers[-2]) / 2]
            self.groups.append({"spans": [i for i, _ in ordered], "years": [y for _, y in ordered],
                                "cy": sum(_cy(spans[i]["bbox"]) for i, _ in ordered) / len(ordered),
                                "bounds": bounds, "duplicate": len({y for _, y in ordered}) != len(ordered)})

    def _above(self, items, top):
        best = None
        for item in items:
            cy = item["cy"] if isinstance(item, dict) else _cy(self.spans[item[0]]["bbox"])
            if cy < top and (best is None or cy > (best["cy"] if isinstance(best, dict) else _cy(self.spans[best[0]]["bbox"]))):
                best = item
        return best

    def _row_text(self, index, left_edge):
        box = self.spans[index]["bbox"]
        words = [s for j, s in enumerate(self.spans) if j != index and self.amounts[j] is None
                 and s["bbox"]["x1"] <= left_edge and _same_row(s["bbox"], box)]
        return " ".join(s["text"] for s in sorted(words, key=lambda s: s["bbox"]["x0"]))

    def _label_x0(self, row):
        """Left edge of a row's label: its leftmost span that is not an amount (None if it has none)."""
        xs = [self.spans[j]["bbox"]["x0"] for j in row if self.amounts[j] is None]
        return min(xs) if xs else None

    def _heading(self, index):
        """Nearest label-only line above (e.g. 'Earnings per share:' above 'Basic' and 'Diluted').

        Rows that carry amounts are skipped (at most HEADING_ROWS of them), so 'Diluted' finds the
        heading above 'Basic'. A year-header row ends the search. 'Above' compares centres, since
        OCR boxes of adjacent lines can overlap by a fraction of a point.

        score-2: the first amount row under a label-only line keeps it (a real heading, or that row's
        own wrapped label). A row further down keeps it only if it and every amount row between are
        indented at least INDENT_POINTS under the line; a row back at the line's indent is a new item
        (e.g. 'Additional paid-in capital' under the wrapped 'Common stock, $0.001 par value; ... shares'
        label). Rows with no label of their own do not end the scope.
        """
        box = self.spans[index]["bbox"]
        above = [j for j, s in enumerate(self.spans) if _cy(s["bbox"]) < box["top"]]
        rows: list[list[int]] = []
        for j in sorted(above, key=lambda k: -_cy(self.spans[k]["bbox"])):
            if rows and _same_row(self.spans[rows[-1][0]]["bbox"], self.spans[j]["bbox"]):
                rows[-1].append(j)
            else:
                rows.append([j])
        skipped: list[list[int]] = []
        for row in rows:
            if any(header_year(self.spans[j]["text"]) is not None for j in row):
                return ""
            if all(self.amounts[j] is None for j in row):
                if self.rule == SCORE_RULE_2 and skipped:
                    heading_x0 = min(self.spans[j]["bbox"]["x0"] for j in row)
                    own = [j for j, s in enumerate(self.spans) if j != index and s["bbox"]["x1"] <= box["x0"]
                           and _same_row(s["bbox"], box)]
                    for member in skipped + [own]:
                        x0 = self._label_x0(member)
                        if x0 is not None and x0 < heading_x0 + INDENT_POINTS:
                            return ""
                return " ".join(self.spans[j]["text"] for j in sorted(row, key=lambda k: self.spans[k]["bbox"]["x0"]))
            skipped.append(row)
            if len(skipped) > HEADING_ROWS:
                return ""
        return ""

    def read(self, index: int) -> dict:
        """Scale and column year for one numeric span, or the reason none can be given."""
        span = self.spans[index]
        box = span["bbox"]
        result = {"unit_span": None, "scale": None, "header_spans": [], "year": None, "reason": None,
                  "row_text": None, "heading": None}
        group = self._above(self.groups, box["top"])
        left_edge = group["bounds"][0] if group else box["x0"]
        result["row_text"] = self._row_text(index, left_edge)
        result["heading"] = self._heading(index)
        unit = self._above(self.units, box["top"])
        if unit is None:
            result["reason"] = "unit_missing"
        elif _SHARE_ROW.search(_norm((result["row_text"] or "") + " " + (result["heading"] or ""))) or (
                self.rule == SCORE_RULE_2 and unit[1]["qualified"] and _PER_SHARE_SUBLABEL.match(_norm(result["row_text"] or ""))):
            result["unit_span"] = unit[0]
            result["reason"] = "share_or_per_share_row"
        else:
            result["unit_span"], result["scale"] = unit[0], unit[1]["scale"]
        if group is None:
            result["reason"] = result["reason"] or "no_year_header"
            return result
        result["header_spans"] = group["spans"]
        if group["duplicate"]:
            result["reason"] = result["reason"] or "duplicate_header_year"
            return result
        bounds = group["bounds"]
        if box["x1"] <= bounds[0] or box["x0"] >= bounds[-1]:
            result["reason"] = result["reason"] or "outside_header_columns"
            return result
        columns = [j for j in range(len(group["years"])) if bounds[j] <= box["x0"] and box["x1"] <= bounds[j + 1]]
        if len(columns) != 1:
            result["reason"] = result["reason"] or "cell_crosses_column"
            return result
        result["year"] = group["years"][columns[0]]
        result["column_span"] = group["spans"][columns[0]]
        return result


# ---------------------------------------------------------------------------
# Matching one fact (only after OCR has run on the whole page)
# ---------------------------------------------------------------------------

def coverage(target, other):
    area = max(0.0, min(target["x1"], other["x1"]) - max(target["x0"], other["x0"])) * max(
        0.0, min(target["bottom"], other["bottom"]) - max(target["top"], other["top"]))
    return area / max((target["x1"] - target["x0"]) * (target["bottom"] - target["top"]), 1e-9)


def selected(box, target):
    """visual.compare_region selection: 35% of the target covered, or the span centre inside it."""
    return coverage(target, box) >= 0.35 or (
        target["x0"] <= _cx(box) <= target["x1"] and target["top"] <= _cy(box) <= target["bottom"])


def _misaligned(box, target):
    return (box["x0"] < target["x0"] - MISALIGNMENT_POINTS or box["x1"] > target["x1"] + MISALIGNMENT_POINTS
            or box["top"] < target["top"] - MISALIGNMENT_POINTS or box["bottom"] > target["bottom"] + MISALIGNMENT_POINTS)


def _near_cell(box, target):
    overlap = max(0.0, min(box["bottom"], target["bottom"]) - max(box["top"], target["top"]))
    vertical = overlap / max(min(box["bottom"] - box["top"], target["bottom"] - target["top"]), 1e-9)
    gap = max(0.0, box["x0"] - target["x1"], target["x0"] - box["x1"])
    return vertical >= 0.5 and gap <= SIGN_GAP_POINTS


def score_fact(fact: dict, key: dict, reading: PageReading, *, min_confidence: float = MIN_CONFIDENCE) -> dict:
    target = fact["box"]
    spans = reading.spans
    hits = [i for i, s in enumerate(spans) if selected(s["bbox"], target)]
    record = {"box_hit": bool(hits), "hits": hits, "outcome": "abstain", "reason": None,
              "read": None, "checks": None, "diagnostic": None}
    if not key["scorable"]:
        record.update(outcome="not_scored", reason=key["not_scored_reason"] or "no_period")
        return record

    def abstain(reason, **extra):
        record.update(reason=reason, **extra)
        return record

    if not hits:
        return abstain("no_spatial_evidence")
    if any(spans[i]["confidence"] < min_confidence for i in hits):
        return abstain("low_ocr_confidence")
    if any(_misaligned(spans[i]["bbox"], target) for i in hits):
        merged = " ".join(spans[i]["text"] for i in sorted(hits, key=lambda k: spans[k]["bbox"]["x0"]))
        value = _amount(merged)
        diagnostic = None if value is None else {"merged_text": merged, "digits_equal": format(abs(value), "f") == key["magnitude"]}
        return abstain("merged_or_misaligned_region", diagnostic=diagnostic)
    if len(hits) != 1:
        return abstain("ambiguous_cell")
    hit = hits[0]
    near = [j for j, s in enumerate(spans) if j != hit and not selected(s["bbox"], target)
            and _FRAGMENT.search(s["text"]) and _near_cell(s["bbox"], target)]
    if near:
        return abstain("detached_numeric_fragment_near_cell", diagnostic={"fragments": near})
    displayed = reading.amounts[hit]
    if displayed is None:
        return abstain("unreadable_numeric_cell")
    view = reading.read(hit)
    negative = displayed < 0 or (displayed == 0 and spans[hit]["text"].strip().lstrip("$¥￥ ").startswith("("))
    record["read"] = {"span": hit, "text": spans[hit]["text"], "confidence": spans[hit]["confidence"],
                      "magnitude": format(abs(displayed), "f"), "negative": negative,
                      "scale": view["scale"], "year": view["year"], "unit_span": view["unit_span"],
                      "column_span": view.get("column_span"), "row_text": view["row_text"], "heading": view["heading"]}
    partial = {"digits": format(abs(displayed), "f") == key["magnitude"], "sign": negative == key["printed_negative"]}
    if view["scale"] is not None:
        partial["scale"] = view["scale"] == key["scale"]
    if view["year"] is not None:
        partial["period"] = view["year"] in key["years"]
    record["checks"] = partial
    if view["reason"]:
        return abstain(view["reason"])
    record["outcome"] = "match" if all(partial.values()) else "conflict"
    record["reason"] = None if record["outcome"] == "match" else "+".join(k for k, ok in partial.items() if not ok)
    return record


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _rate(numerator, denominator):
    return None if not denominator else round(numerator / denominator, 4)


def summarize(records: list[dict]) -> dict:
    """records: dicts with outcome, reason, box_hit, checks, plus slice fields."""
    scored = [r for r in records if r["outcome"] != "not_scored"]
    n = len(scored)
    outcomes = Counter(r["outcome"] for r in scored)
    reasons = Counter(r["reason"] for r in scored if r["outcome"] == "abstain")
    conflicts = Counter(r["reason"] for r in scored if r["outcome"] == "conflict")
    value_read = [r for r in scored if r["checks"] and "scale" in r["checks"]]
    value_ok = [r for r in value_read if r["checks"]["digits"] and r["checks"]["sign"] and r["checks"]["scale"]]
    period_read = [r for r in scored if r["checks"] and "period" in r["checks"]]
    period_ok = [r for r in period_read if r["checks"]["period"]]
    box_hits = sum(1 for r in scored if r["box_hit"])
    summary = {
        "facts_scored": n,
        "not_scored": dict(Counter(r["reason"] for r in records if r["outcome"] == "not_scored")),
        "outcomes": {k: outcomes.get(k, 0) for k in ("match", "conflict", "abstain")},
        "abstain_reasons": dict(sorted(reasons.items())),
        "conflict_components": dict(sorted(conflicts.items())),
        "box_recall": {"hit": box_hits, "of": n, "rate": _rate(box_hits, n)},
        "exact_value": {"correct": len(value_ok), "read": len(value_read), "of": n,
                        "rate_of_read": _rate(len(value_ok), len(value_read)), "rate_of_all": _rate(len(value_ok), n)},
        "period_column": {"correct": len(period_ok), "read": len(period_read), "of": n,
                          "rate_of_read": _rate(len(period_ok), len(period_read)), "rate_of_all": _rate(len(period_ok), n)},
    }
    for c in PENALTIES:
        summary[f"rs_{c}"] = None if not n else round((outcomes.get("match", 0) - c * outcomes.get("conflict", 0)) / n, 4)
    return summary


def slice_summaries(records: list[dict], field: str) -> dict:
    groups = defaultdict(list)
    for r in records:
        groups[str(r.get(field))].append(r)
    return {k: summarize(v) for k, v in sorted(groups.items())}


# ---------------------------------------------------------------------------
# Issuer rule helpers (pure; the network calls live in scripts/xbrl_grounding.py)
# ---------------------------------------------------------------------------

def candidate_order(ciks, salt: str) -> list[int]:
    """Deterministic pseudo-random order fixed by the committed salt."""
    return sorted({int(c) for c in ciks}, key=lambda c: hashlib.sha256((salt + f"{c:010d}").encode()).hexdigest())


def statement_pages(facts: list[dict], contexts: dict, anchors: dict, *, min_facts: int, max_pages: int) -> dict:
    """First page (document order) holding each statement's anchor concept, non-dimensional."""
    counts = Counter(f["page"] for f in facts if f.get("displayed"))
    chosen = {}
    for statement, concepts in anchors.items():
        wanted = [c.casefold() for c in concepts]
        best = None
        for concept in wanted:  # anchors are tried in priority order
            pages = sorted({f["page"] for f in facts if f.get("displayed") and f["concept"].casefold() == concept
                            and not contexts.get(f["context_ref"], {}).get("dims") and counts[f["page"]] >= min_facts})
            if pages:
                best = pages[0]
                break
        if best is not None:
            chosen[statement] = best
    ordered = sorted(set(chosen.values()))[:max_pages]
    return {"by_statement": chosen, "pages": ordered}


def canonical_digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)
