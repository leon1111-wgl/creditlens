"""Reference-free row/year lookup from the same full-page line-level OCR.

No expected amount, reference box, fact, or section can be supplied. The text
baseline never consults coordinates to select a cell; both methods emit original
spans for inspection and share numeric syntax, unit and confidence policies.
"""
from __future__ import annotations

from decimal import Decimal, localcontext
import re
import unicodedata

from .diligence import displayed_amount
from .visual import OcrPage

TABLE_LOOKUP_VERSION = "table-lookup-1"
MIN_CONFIDENCE = 0.90
HEADER_CENTER_TOLERANCE = 4.0
ROW_CENTER_TOLERANCE = 4.0
MIN_VERTICAL_OVERLAP = 0.50
MAX_YEAR_COLUMNS = 6
SCOPE = [
    "Queries contain only an exact normalized row label and year; no expected value, reference box or section is supplied.",
    "Both methods use the same saved line-level OcrPage. No new OCR or word-level inference is performed.",
    "A selected candidate is not an approved financial fact, document authentication, independent benchmark result or proof of OCR quality.",
    "The unit text and its exceptions are retained. A scale such as millions and a currency symbol do not establish USD; currency remains unknown.",
    "Only a bounded single-table header/row pattern is supported. Repeated labels, split labels, merged cells and unresolved symbols can require refusal.",
]
MESSAGES = {
    "selected": "已按该方法找到唯一候选；仍需核对原文、口径和识别质量。",
    "row_not_found": "没有找到规范化后完全一致的行名。",
    "duplicate_row_label": "同名行出现多次，查询没有分区信息，无法确定所指行。",
    "unit_missing": "行名前没有找到支持的单位说明。",
    "unit_ambiguous": "存在多处单位说明，无法确定适用表格。",
    "unit_exception_row": "单位说明含例外，而该行涉及每股、股数或面值，不能套用金额量级。",
    "missing_year_group": "没有找到至少两列、可定位为表头的独立年份。",
    "ambiguous_year_group": "存在多组可能的年份表头，不能任选一组。",
    "duplicate_header_year": "同组表头出现重复年份，不能唯一定位该列。",
    "unanchored_header": "单位说明到年份之间混有其他内容，不能确认该年份组是适用表头。",
    "year_not_found": "适用表头中没有查询年份。",
    "low_confidence": "行名、单位、表头或金额证据的识别分数低于统一门槛。",
    "invalid_or_merged_amount": "金额含多余符号、多个数值或无法完整解析，不能截取其中一段。",
    "row_amount_count_mismatch": "金额数量与年份列数不一致，无法确认列对应关系。",
    "label_column_overlap": "行名与年份列范围重叠，无法安全分隔标签和金额。",
    "ambiguous_row_content": "同一行存在额外文字或未归属符号，不能忽略。",
    "cell_crosses_column": "金额框跨越年份列边界，无法唯一归属。",
    "duplicate_amount_column": "同一列出现多个候选或缺少对应金额，无法唯一选择。",
}
_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_AMOUNT = re.compile(r"(?:[$¥￥]\s*)?(?:[+-]?" + _NUMBER + r"|\(" + _NUMBER + r"\))")
_MONTH = r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
_DATE_CUE = re.compile(r"(?:(?:fiscal )?years? ended(?:\s+" + _MONTH + r"\s+[0-9]{1,2},?)?|as of(?:\s+" + _MONTH + r"\s+[0-9]{1,2},?)?|" + _MONTH + r"\s+[0-9]{1,2},?)")
_UNIT = re.compile(r"^\(?in\s+(millions|thousands|billions)\b")


class TableLookupError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Table lookup input is invalid"):
        self.code, self.path = code, path
        super().__init__(message)


def normalize_row_label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split()).rstrip(":").rstrip()


def parse_lookup_amount(text: str) -> str:
    """Parse the whole supported span, with no answer-dependent substring choice."""
    if type(text) is not str or not 0 < len(text) <= 100 or not _AMOUNT.fullmatch(text.strip()):
        raise TableLookupError("unreadable_amount", "text")
    try:
        # displayed_amount's parenthesized unary minus uses the active context.
        # The input is at most 100 characters, so 100 significant digits preserve
        # every supported numeral independently of a caller's Decimal settings.
        with localcontext() as context:
            context.prec = 100
            value = displayed_amount(text)
    except ValueError:
        raise TableLookupError("unreadable_amount", "text") from None
    # Preserve a parenthesized zero's sign in addition to the untouched raw span.
    without_marker = re.sub(r"^[$¥￥]\s*", "", text.strip())
    if value == 0 and without_marker.startswith("("):
        value = Decimal("-0")
    return format(value, "f")


def _number(text):
    try:
        return parse_lookup_amount(text)
    except TableLookupError:
        return None


def _year(text):
    return int(text.strip()) if re.fullmatch(r"(?:19|20|21)[0-9]{2}", text.strip()) else None


def _header_text(text):
    value = " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    return _year(value) is not None or _DATE_CUE.fullmatch(value) is not None


def _validate(page, row_label, year):
    if not isinstance(page, OcrPage):
        raise TableLookupError("invalid_page", "page")
    # Revalidation protects against Pydantic model_copy(update=...) bypasses.
    try:
        page = OcrPage.model_validate(page.model_dump(mode="json"))
    except ValueError:
        raise TableLookupError("invalid_page", "page") from None
    if type(row_label) is not str or not 0 < len(row_label) <= 500 or not normalize_row_label(row_label):
        raise TableLookupError("invalid_row_label", "row_label")
    if type(year) is not int or not 1900 <= year <= 2199:
        raise TableLookupError("invalid_year", "year")
    return page


def _result(method):
    return {"method": method, "status": "insufficient", "reason": "row_not_found", "message": MESSAGES["row_not_found"],
            "raw_value": None, "normalized_value": None, "selected_span_index": None,
            "unit": {"raw_text": None, "scale": None, "currency": None, "qualifier_present": False},
            "evidence": {key: [] for key in ("row_label", "year_headers", "unit", "amounts", "guards")},
            "column": None, "automatic_approval": False}


def _evidence(result, key, page, indices):
    result["evidence"][key] = [{"span_index": i, "span": page.spans[i].model_dump(mode="json")} for i in indices]


def _finish(result, reason, page=None, index=None):
    result.update(reason=reason, message=MESSAGES[reason])
    if reason == "selected":
        result.update(status="selected", selected_span_index=index, raw_value=page.spans[index].text,
                      normalized_value=parse_lookup_amount(page.spans[index].text))
    return result


def _label(page, row_label, result):
    hits = [i for i, span in enumerate(page.spans) if normalize_row_label(span.text) == normalize_row_label(row_label)]
    _evidence(result, "row_label", page, hits)
    if len(hits) != 1:
        return None, "row_not_found" if not hits else "duplicate_row_label"
    return hits[0], None


def _unit(page, indices, row_label, result):
    hits = [i for i in indices if _UNIT.match(" ".join(page.spans[i].text.casefold().split()))]
    _evidence(result, "unit", page, hits)
    if len(hits) != 1:
        return None, "unit_missing" if not hits else "unit_ambiguous"
    text = page.spans[hits[0]].text
    match = _UNIT.match(" ".join(text.casefold().split()))
    result["unit"] = {"raw_text": text, "scale": match.group(1)[:-1], "currency": None,
                      "qualifier_present": "except" in text.casefold()}
    if result["unit"]["qualifier_present"] and re.search(r"\b(?:per[ -]+share|shares?|par value)\b", normalize_row_label(row_label)):
        return None, "unit_exception_row"
    return hits[0], None


def _confidence(page, indices):
    return all(page.spans[i].confidence >= MIN_CONFIDENCE for i in indices)


def lookup_text_sequence(page: OcrPage, *, row_label: str, year: int) -> dict:
    """Read OCR sequence only; coordinates are emitted but never select a cell."""
    page = _validate(page, row_label, year)
    result = _result("text_sequence")
    label, reason = _label(page, row_label, result)
    if reason:
        return _finish(result, reason)
    unit, reason = _unit(page, range(label), row_label, result)
    if reason:
        return _finish(result, reason)
    context, headers = [], []
    for i in range(unit+1, label):
        if not _header_text(page.spans[i].text):
            break
        context.append(i)
        if _year(page.spans[i].text) is not None:
            headers.append(i)
    _evidence(result, "year_headers", page, context)
    if not 2 <= len(headers) <= MAX_YEAR_COLUMNS:
        return _finish(result, "missing_year_group")
    years = [_year(page.spans[i].text) for i in headers]
    if len(set(years)) != len(years):
        return _finish(result, "duplicate_header_year")
    # Another year run before the requested row is unresolved, not ignored.
    later = [i for i in range(context[-1]+1, label) if _year(page.spans[i].text) is not None]
    if later:
        _evidence(result, "guards", page, later)
        return _finish(result, "ambiguous_year_group")
    if year not in years:
        return _finish(result, "year_not_found")
    amounts = []
    for i in range(label+1, len(page.spans)):
        text = page.spans[i].text.strip()
        if _number(text) is not None:
            amounts.append(i)
            continue
        if ((re.search(r"[0-9$¥￥€£()−+\-]", text) and not re.match(r"^[A-Za-z]", text))
                or text.upper() in {"C", "CA", "CAD", "HK", "HKD", "US", "USD", "EUR", "JPY", "CNY", "RMB"}):
            _evidence(result, "amounts", page, amounts)
            _evidence(result, "guards", page, [i])
            return _finish(result, "invalid_or_merged_amount")
        break
    _evidence(result, "amounts", page, amounts)
    if len(amounts) != len(headers):
        return _finish(result, "row_amount_count_mismatch")
    if not _confidence(page, [label, unit, *context, *amounts]):
        return _finish(result, "low_confidence")
    return _finish(result, "selected", page, amounts[years.index(year)])


def _cx(span):
    return (span.bbox.x0 + span.bbox.x1) / 2


def _cy(span):
    return (span.bbox.top + span.bbox.bottom) / 2


def _same_row(first, second):
    overlap = max(0, min(first.bbox.bottom, second.bbox.bottom)-max(first.bbox.top, second.bbox.top))
    ratio = overlap / min(first.bbox.bottom-first.bbox.top, second.bbox.bottom-second.bbox.top)
    return abs(_cy(first)-_cy(second)) <= ROW_CENTER_TOLERANCE or ratio >= MIN_VERTICAL_OVERLAP


def lookup_layout_column(page: OcrPage, *, row_label: str, year: int) -> dict:
    """Use an unambiguous unit-anchored header and full-cell column containment."""
    page = _validate(page, row_label, year)
    result = _result("layout_column")
    label, reason = _label(page, row_label, result)
    if reason:
        return _finish(result, reason)
    row = page.spans[label]
    unit, reason = _unit(page, [i for i, s in enumerate(page.spans) if s.bbox.bottom <= row.bbox.top], row_label, result)
    if reason:
        return _finish(result, reason)
    unit_span = page.spans[unit]
    pure_years = [i for i, s in enumerate(page.spans) if _year(s.text) is not None and
                  s.bbox.bottom <= row.bbox.top and _cy(s) > _cy(unit_span) and s.bbox.x0 >= row.bbox.x1]
    groups = []
    for i in sorted(pure_years, key=lambda k: (_cy(page.spans[k]), _cx(page.spans[k]))):
        if not groups or _cy(page.spans[i]) - _cy(page.spans[groups[-1][0]]) > HEADER_CENTER_TOLERANCE:
            groups.append([i])
        else:
            groups[-1].append(i)
    groups = [g for g in groups if 2 <= len(g) <= MAX_YEAR_COLUMNS]
    if len(groups) != 1:
        _evidence(result, "year_headers", page, [i for group in groups for i in group])
        return _finish(result, "missing_year_group" if not groups else "ambiguous_year_group")
    headers = sorted(groups[0], key=lambda i: _cx(page.spans[i]))
    if set(pure_years) != set(headers):
        _evidence(result, "year_headers", page, pure_years)
        return _finish(result, "ambiguous_year_group")
    header_bottom = max(page.spans[i].bbox.bottom for i in headers)
    context = [i for i, s in enumerate(page.spans) if i != unit and _cy(unit_span) < _cy(s) <= header_bottom]
    _evidence(result, "year_headers", page, context)
    if any(not _header_text(page.spans[i].text) for i in context):
        return _finish(result, "unanchored_header")
    years = [_year(page.spans[i].text) for i in headers]
    if len(set(years)) != len(years):
        return _finish(result, "duplicate_header_year")
    if year not in years:
        return _finish(result, "year_not_found")
    centers = [_cx(page.spans[i]) for i in headers]
    bounds = [max(0.0, centers[0] - (centers[1]-centers[0])/2),
              *[(a+b)/2 for a, b in zip(centers, centers[1:])],
              min(page.width, centers[-1] + (centers[-1]-centers[-2])/2)]
    if row.bbox.x1 >= bounds[0] or any(page.spans[a].bbox.x1 >= page.spans[b].bbox.x0 for a, b in zip(headers, headers[1:])):
        return _finish(result, "label_column_overlap")
    selected_column = years.index(year)
    result["column"] = {"year": year, "left": bounds[selected_column], "right": bounds[selected_column+1], "header_span_index": headers[selected_column]}
    # A wide OCR parent spanning the label and a column is relevant too. Do not
    # discard it solely because its left edge begins before the label's right.
    amounts = [i for i, span in enumerate(page.spans) if i != label and span.bbox.x1 > row.bbox.x1 and _same_row(row, span)]
    amounts.sort(key=lambda i: page.spans[i].bbox.x0)
    _evidence(result, "amounts", page, amounts)
    if any(_number(page.spans[i].text) is None for i in amounts):
        _evidence(result, "guards", page, [i for i in amounts if _number(page.spans[i].text) is None])
        return _finish(result, "invalid_or_merged_amount")
    if len(amounts) != len(headers):
        return _finish(result, "row_amount_count_mismatch")
    assignment = {}
    for index in amounts:
        box = page.spans[index].bbox
        columns = [j for j in range(len(headers)) if bounds[j] <= box.x0 and box.x1 <= bounds[j+1]]
        if len(columns) != 1:
            return _finish(result, "cell_crosses_column")
        if columns[0] in assignment:
            return _finish(result, "duplicate_amount_column")
        assignment[columns[0]] = index
    if set(assignment) != set(range(len(headers))):
        return _finish(result, "duplicate_amount_column")
    if not _confidence(page, [label, unit, *context, *amounts]):
        return _finish(result, "low_confidence")
    return _finish(result, "selected", page, assignment[selected_column])


def lookup_table(page: OcrPage, *, row_label: str, year: int) -> dict:
    page = _validate(page, row_label, year)
    return {"schema_version": 1, "rule_version": TABLE_LOOKUP_VERSION,
            "query": {"page": page.page, "row_label": row_label, "normalized_row_label": normalize_row_label(row_label), "year": year},
            "methods": {"text_sequence": lookup_text_sequence(page, row_label=row_label, year=year),
                        "layout_column": lookup_layout_column(page, row_label=row_label, year=year)},
            "scope": list(SCOPE), "automatic_approval": False}
