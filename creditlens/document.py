"""Bounded, positioned-text table extraction; no OCR or document authentication."""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite
from .diligence import displayed_amount

PARSER_VERSION = "positioned-financial-table-1"
STACKED_PARSER_VERSION = "stacked-period-financial-table-1"


class EvidenceError(ValueError):
    """An anchor, column or cell could not be resolved without guessing."""


@dataclass(frozen=True)
class Token:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def cy(self):
        return (self.top + self.bottom) / 2


def box(tokens):
    if not tokens:
        raise EvidenceError("Empty evidence region")
    return {"x0": round(min(t.x0 for t in tokens), 3), "top": round(min(t.top for t in tokens), 3),
            "x1": round(max(t.x1 for t in tokens), 3), "bottom": round(max(t.bottom for t in tokens), 3)}


def lines(tokens):
    """Only horizontal text is supported. This is a pinned-layout adapter."""
    groups = []
    for token in sorted(tokens, key=lambda t: (t.cy, t.x0)):
        if not all(isfinite(v) for v in (token.x0, token.top, token.x1, token.bottom)):
            raise EvidenceError("Non-finite coordinates")
        if token.x0 >= token.x1 or token.top >= token.bottom:
            raise EvidenceError("Invalid token bounds")
        group = next((g for g in groups if abs(g[0].cy-token.cy) <= 2.5), None)
        if group is None:
            groups.append([token])
        else:
            group.append(token)
    return [sorted(g, key=lambda t: t.x0) for g in groups]


def phrase(rows, text, *, prefix=False):
    wanted = text.split()
    matches = []
    for row in rows:
        words = [t.text for t in row]
        if words == wanted or (prefix and words[:len(wanted)] == wanted):
            matches.append((row, row[:len(wanted)]))
    if len(matches) != 1:
        raise EvidenceError("Missing or ambiguous anchor: " + text)
    return matches[0]


def extract_table(tokens, *, page, width, height, title, unit_header, period_header, years, metrics):
    """Read by row label and year position; expected financial values are not inputs."""
    rows = lines(tokens)
    title_row, _ = phrase(rows, title)
    unit_row, _ = phrase(rows, unit_header)
    header, period_tokens = phrase(rows, period_header, prefix=True)
    if not (title_row[0].cy < unit_row[0].cy < header[0].cy):
        raise EvidenceError("Title, unit and period anchors are out of order")
    year_tokens = header[len(period_tokens):]
    if [t.text for t in year_tokens] != [str(y) for y in years]:
        raise EvidenceError("Unexpected or repeated year columns")
    result = []
    for metric, label in metrics.items():
        row, label_tokens = phrase(rows, label, prefix=True)
        if row[0].cy <= header[0].cy:
            raise EvidenceError("Financial row appears before the year header")
        cells = [t for t in row[len(label_tokens):] if t.text != "$"]
        if len(cells) != len(years):
            raise EvidenceError("Missing or ambiguous amount cells: " + metric)
        for year, column, cell in zip(years, year_tokens, cells):
            # Right-aligned amounts must end below their declared year column.
            if abs(column.x1-cell.x1) > 4 or cell.x0 <= label_tokens[-1].x1:
                raise EvidenceError("Amount is not aligned with its year: " + metric)
            try:
                amount = displayed_amount(cell.text)
            except ValueError as exc:
                raise EvidenceError("Unreadable amount: " + metric) from exc
            result.append({"metric": metric, "year": year, "raw_value": cell.text,
                           "value": str(abs(amount) if metric == "capex" else amount),
                           "pdf_location": {"page": page, "page_width": width, "page_height": height,
                            "coordinate_system": "pdf_points_top_left", "amount_bbox": box([cell]),
                            "row_label": label, "row_label_bbox": box(label_tokens),
                            "year": year, "year_bbox": box([column]), "period_header": period_header,
                            "period_bbox": box(period_tokens), "unit_header": unit_header,
                            "unit_bbox": box(unit_row), "parser_version": PARSER_VERSION}})
    return result


def extract_stacked_table(tokens, *, page, width, height, title, unit_header, years,
                          date_headers, metrics, duration_header=None):
    """Pinned centered, two-line date columns with explicit section boundaries.

    Column regions come from header centers, never from reference financial values.
    Repeated row labels require both an opening and closing section anchor.
    """
    rows = lines(tokens)
    title_row, _ = phrase(rows, title)
    unit_row, _ = phrase(rows, unit_header)
    year_row, _ = phrase(rows, " ".join(map(str, years)))
    date_row, _ = phrase(rows, " ".join(date_headers))
    if len(years) < 2 or len(set(years)) != len(years) or len(date_headers) != len(years):
        raise EvidenceError("Invalid declared date columns")
    if not (title_row[0].cy < unit_row[0].cy < date_row[0].cy < year_row[0].cy
            and year_row[0].cy-date_row[0].cy <= 18):
        raise EvidenceError("Stacked date anchors are out of order")
    if duration_header:
        duration_row, _ = phrase(rows, duration_header)
        if not unit_row[0].cy < duration_row[0].cy < date_row[0].cy:
            raise EvidenceError("Duration header is misplaced")
    centers = [(t.x0+t.x1)/2 for t in year_row]
    edges = [centers[0]-(centers[1]-centers[0])/2]
    edges += [(a+b)/2 for a, b in zip(centers, centers[1:])]
    edges += [centers[-1]+(centers[-1]-centers[-2])/2]
    if any(b-a < 20 for a, b in zip(edges, edges[1:])) or edges[0] < 0 or edges[-1] > width:
        raise EvidenceError("Invalid date column regions")
    dates = []
    for i, label in enumerate(date_headers):
        group = [t for t in date_row if edges[i] <= t.x0 and t.x1 <= edges[i+1]]
        if [t.text for t in group] != label.split():
            raise EvidenceError("Date text does not match its year column")
        dates.append(group)
    result = []
    for metric, spec in metrics.items():
        spec = {"label": spec} if isinstance(spec, str) else spec
        candidate_rows, section = rows, None
        if "section" in spec:
            start, end = spec["section"]
            first, _ = phrase(rows, start)
            last, _ = phrase(rows, end, prefix=True)
            if not year_row[0].cy < first[0].cy < last[0].cy:
                raise EvidenceError("Section boundaries are out of order")
            candidate_rows = [r for r in rows if first[0].cy < r[0].cy < last[0].cy]
            section = {"start_label": start, "end_label": end,
                       "start_bbox": box(first), "end_bbox": box(last[:len(end.split())])}
        row, label_tokens = phrase(candidate_rows, spec["label"], prefix=True)
        if row[0].cy <= year_row[0].cy or label_tokens[-1].x1 >= edges[0]:
            raise EvidenceError("Financial row is outside the declared table")
        cells = [t for t in row[len(label_tokens):] if t.text != "$"]
        if len(cells) != len(years):
            raise EvidenceError("Missing or ambiguous amount cells: " + metric)
        for i, (year, column, cell) in enumerate(zip(years, year_row, cells)):
            if not edges[i] <= cell.x0 < cell.x1 <= edges[i+1]:
                raise EvidenceError("Amount crosses a date column: " + metric)
            try:
                amount = displayed_amount(cell.text)
            except ValueError as exc:
                raise EvidenceError("Unreadable amount: " + metric) from exc
            loc = {"page": page, "page_width": width, "page_height": height,
                   "coordinate_system": "pdf_points_top_left", "amount_bbox": box([cell]),
                   "row_label": spec["label"], "row_label_bbox": box(label_tokens),
                   "year": year, "year_bbox": box([column]),
                   "period_header": f"{date_headers[i]} {year}", "period_bbox": box(dates[i]+[column]),
                   "unit_header": unit_header, "unit_bbox": box(unit_row),
                   "parser_version": STACKED_PARSER_VERSION}
            if section:
                loc["section"] = section
            result.append({"metric": metric, "year": year, "raw_value": cell.text,
                           "value": str(abs(amount) if metric == "capex" else amount), "pdf_location": loc})
    return result


def pdfium_tokens(page):
    """Preserve word text and glyph locations from a digital PDF text layer."""
    textpage = page.get_textpage()
    tokens, word, glyphs = [], [], []
    def flush():
        if word:
            b = box(glyphs)
            tokens.append(Token("".join(word), **b))
            word.clear()
            glyphs.clear()
    try:
        for i in range(textpage.count_chars()):
            ch = textpage.get_text_range(i, 1)
            if not ch or ch.isspace():
                flush()
                continue
            left, bottom, right, top = textpage.get_charbox(i)
            glyph = Token(ch, left, page.get_height()-top, right, page.get_height()-bottom)
            if glyphs and (glyph.top > max(g.bottom for g in glyphs)+1
                           or glyph.bottom < min(g.top for g in glyphs)-1
                           or glyph.x0-glyphs[-1].x1 > 3):
                flush()
            word.append(ch)
            glyphs.append(glyph)
        flush()
        return tokens
    finally:
        textpage.close()
