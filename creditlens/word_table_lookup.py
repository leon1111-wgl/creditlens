"""Conservative word-granularity table lookup without reference values or boxes.

The saved line projection supplies the unchanged baseline and table anchors.
Only conserved, wholly attributable monetary word groups are substituted in a
transient page. The original OCR and every relevant parent remain inspectable.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .diligence import digest
from .table_lookup import (MIN_CONFIDENCE, MIN_VERTICAL_OVERLAP, ROW_CENTER_TOLERANCE,
                           TableLookupError, lookup_layout_column, normalize_row_label,
                           parse_lookup_amount)
from .visual import OcrPage
from .word_evidence import WordPage

WORD_TABLE_LOOKUP_VERSION = "word-table-lookup-1"
MAX_SYMBOL_GAP = 8.0
CONFIDENCE_BASIS = "both_parent_and_native_word_scores_not_calibrated"
SCOPE = [
    "The only query inputs are the complete saved WordPage, exact normalized row label and year; no fact, expected amount, reference box or section is accepted.",
    "Line lookup uses the unchanged layout method on this WordPage's line projection. Word lookup combines finer granularity with additional integrity and confidence guards, not an isolated layout-only intervention.",
    "Only relevant parents are guarded. All their words are preserved; text conservation removes whitespace only and parent containment has zero tolerance.",
    "A monetary group can join symbols and one numeric token only within one parent and one year column. Cross-parent symbols, unassigned words and unresolved columns require refusal.",
    "Native word and parent scores are separately thresholded at 0.90; neither score is a calibrated probability of correctness.",
    "Selected spans are derived evidence, not new OCR or approved financial facts. Unit text does not establish currency, and currency remains unknown.",
    "The derived page is transient. Public mappings cover referenced spans only and do not permit full-page extraction replay without the retained complete WordPage.",
]
MESSAGES = {
    "missing_word_evidence": "相关父行缺少字词证据，不能将它静默丢弃。",
    "parent_word_text_mismatch": "父行与全部字词的文字或符号不守恒，不能选择性取数。",
    "word_outside_parent": "相关字词框超出其父行，父词位置关系不可靠。",
    "word_low_confidence": "相关父行或原生字词分数低于统一门槛。",
    "word_row_mismatch": "同一金额父行的字词不全在查询行，不能丢掉其他行内容。",
    "word_order_or_overlap": "金额字词次序与位置矛盾或存在重叠，不能安全拼接。",
    "word_crosses_column": "字词跨越或落在年份列范围之外，无法唯一归属。",
    "word_column_coverage": "部分年份列缺少金额字词，不能将其他列前移补位。",
    "multiple_numeric_words": "同列有多个独立数值，不能拼成新数字或任选其中一个。",
    "unsupported_word_content": "金额区域存在无法完整解释的文字或符号。",
    "cross_parent_symbol_association": "同列符号与数值来自不同父行，本规则不猜测其归属。",
    "word_symbol_gap": "同列符号与数值相隔过远，不能视为同一完整金额。",
    "incomplete_word_amount": "保留全部符号后的金额语法不完整，不能修补或截取。",
}


def _compact(text):
    return "".join(text.split())


def _same_row(first, second):
    a, b = first.bbox, second.bbox
    overlap = max(0.0, min(a.bottom, b.bottom) - max(a.top, b.top))
    ratio = overlap / min(a.bottom - a.top, b.bottom - b.top)
    return abs((a.top+a.bottom-b.top-b.bottom)/2) <= ROW_CENTER_TOLERANCE or ratio >= MIN_VERTICAL_OVERLAP


def _contained(parent, word):
    a, b = parent.bbox, word.bbox
    return a.x0 <= b.x0 < b.x1 <= a.x1 and a.top <= b.top < b.bottom <= a.bottom


def _references(result):
    refs = {e["span_index"] for values in result["evidence"].values() for e in values}
    if result["selected_span_index"] is not None:
        refs.add(result["selected_span_index"])
    return sorted(refs)


def _parent_record(page, index):
    entry = page.lines[index]
    return {
        "line_index": index, "line": entry.line.model_dump(mode="json"),
        "words": [{"word_index": j, "span": w.model_dump(mode="json")} for j, w in enumerate(entry.words)],
        "text_conserved": bool(entry.words) and _compact(entry.line.text) == _compact("".join(w.text for w in entry.words)),
        "geometry_valid": bool(entry.words) and all(_contained(entry.line, w) for w in entry.words),
    }


def _issue(code, parents, words=()):
    return {"code": code, "line_indices": sorted(set(parents)),
            "word_refs": [{"line_index": i, "word_index": j} for i, j in words]}


def _original_map(index):
    return {"span_index": index, "origin": "original_parent", "parent_indices": [index], "word_refs": []}


def _extended(result, page, relevant, issues, *, derived=None, mapping=None):
    result = deepcopy(result)
    result["method"] = "word_layout"
    if issues:
        reason = issues[0]["code"]
        result.update(status="insufficient", reason=reason, message=MESSAGES[reason],
                      raw_value=None, normalized_value=None, selected_span_index=None)
    mapping = mapping if mapping is not None else {i: _original_map(i) for i in range(len(page.lines))}
    # Include full original parents behind every emitted span, not only the
    # selected value. The full-page mapping itself is deliberately not returned.
    referenced = _references(result)
    relevant = set(relevant)
    for index in referenced:
        relevant.update(mapping[index]["parent_indices"])
    result.update(
        selected_span_origin=(mapping[result["selected_span_index"]]["origin"]
                              if result["selected_span_index"] is not None else None),
        parent_evidence=[_parent_record(page, i) for i in sorted(relevant)],
        guard_issues=issues,
        adaptation={"status": "applied" if derived is not None else "not_applied",
                    "line_input_sha256": digest(page.line_page().model_dump(mode="json")),
                    "derived_page_sha256": digest(derived.model_dump(mode="json")) if derived is not None else None,
                    "mapping_scope": "referenced_spans_only",
                    "source_map": [mapping[i] for i in referenced]},
        confidence_basis=CONFIDENCE_BASIS,
    )
    return result


def _validate(page):
    if not isinstance(page, WordPage):
        raise TableLookupError("invalid_word_page", "page")
    try:
        return WordPage.model_validate(page.model_dump(mode="json"))
    except ValueError:
        raise TableLookupError("invalid_word_page", "page") from None


def _lookup(page, baseline, row_label, year):
    relevant = set(_references(baseline))
    labels = baseline["evidence"]["row_label"]
    monetary = set()
    if len(labels) == 1:
        label_index = labels[0]["span_index"]
        row = page.lines[label_index].line
        # Inspect both levels: a shifted parent must not hide in-row words, and
        # a parent without words must not disappear from the guarded area.
        for i, entry in enumerate(page.lines):
            if i != label_index and any(s.bbox.x1 > row.bbox.x1 and _same_row(row, s)
                                        for s in [entry.line, *entry.words]):
                monetary.add(i)
        relevant.update(monetary)
    issues = []
    for i in sorted(relevant):
        entry = page.lines[i]
        refs = [(i, j) for j in range(len(entry.words))]
        if not entry.words:
            issues.append(_issue("missing_word_evidence", [i]))
            continue
        if _compact(entry.line.text) != _compact("".join(w.text for w in entry.words)):
            issues.append(_issue("parent_word_text_mismatch", [i], refs))
        outside = [(i, j) for j, w in enumerate(entry.words) if not _contained(entry.line, w)]
        if outside:
            issues.append(_issue("word_outside_parent", [i], outside))
        if entry.line.confidence < MIN_CONFIDENCE or any(w.confidence < MIN_CONFIDENCE for w in entry.words):
            issues.append(_issue("word_low_confidence", [i], refs))
        if i in monetary:
            if any(not _same_row(row, w) for w in entry.words):
                issues.append(_issue("word_row_mismatch", [i], refs))
            if any(a.bbox.x1 > b.bbox.x0 for a, b in zip(entry.words, entry.words[1:])):
                issues.append(_issue("word_order_or_overlap", [i], refs))
    if issues or baseline["column"] is None:
        return _extended(baseline, page, relevant, issues)

    # Table anchors and their uniqueness were established by the unchanged line
    # method. The query year never selects a numeric token or repairs a parent.
    headers = [e["span_index"] for e in baseline["evidence"]["year_headers"]
               if re.fullmatch(r"(?:19|20|21)[0-9]{2}", page.lines[e["span_index"]].line.text.strip())]
    headers.sort(key=lambda i: (page.lines[i].line.bbox.x0 + page.lines[i].line.bbox.x1)/2)
    centers = [(page.lines[i].line.bbox.x0 + page.lines[i].line.bbox.x1)/2 for i in headers]
    bounds = [max(0.0, centers[0]-(centers[1]-centers[0])/2),
              *[(a+b)/2 for a, b in zip(centers, centers[1:])],
              min(page.width, centers[-1]+(centers[-1]-centers[-2])/2)]
    groups = [[] for _ in headers]
    for i in sorted(monetary):
        for j, word in enumerate(page.lines[i].words):
            columns = [k for k in range(len(headers)) if bounds[k] <= word.bbox.x0 and word.bbox.x1 <= bounds[k+1]]
            if len(columns) != 1:
                issues.append(_issue("word_crosses_column", [i], [(i, j)]))
            else:
                groups[columns[0]].append((i, j, word))
    if issues:
        return _extended(baseline, page, relevant, issues)
    derived_spans, group_maps = [], []
    for group in groups:
        group.sort(key=lambda item: item[2].bbox.x0)
        parents = sorted({i for i, _, _ in group})
        refs = [(i, j) for i, j, _ in group]
        if not group:
            issues.append(_issue("word_column_coverage", monetary))
            continue
        numbers, unsupported = 0, []
        for i, j, word in group:
            try:
                parse_lookup_amount(word.text)
                numbers += 1
            except TableLookupError:
                if word.text.strip() not in {"$", "¥", "￥", "+", "-", "(", ")"}:
                    unsupported.append((i, j))
        if unsupported:
            issues.append(_issue("unsupported_word_content", parents, unsupported))
        if numbers != 1:
            issues.append(_issue("multiple_numeric_words", parents, refs))
        if len(parents) != 1:
            issues.append(_issue("cross_parent_symbol_association", parents, refs))
        if any(a[2].bbox.x1 > b[2].bbox.x0 for a, b in zip(group, group[1:])):
            issues.append(_issue("word_order_or_overlap", parents, refs))
        if any(b[2].bbox.x0-a[2].bbox.x1 > MAX_SYMBOL_GAP for a, b in zip(group, group[1:])):
            issues.append(_issue("word_symbol_gap", parents, refs))
        text = "".join(w.text.strip() for _, _, w in group)
        try:
            parse_lookup_amount(text)
        except TableLookupError:
            issues.append(_issue("incomplete_word_amount", parents, refs))
        if issues:
            continue
        derived_spans.append({
            "text": text,
            "confidence": min([page.lines[i].line.confidence for i in parents] + [w.confidence for _, _, w in group]),
            "bbox": {"x0": min(w.bbox.x0 for _, _, w in group), "top": min(w.bbox.top for _, _, w in group),
                     "x1": max(w.bbox.x1 for _, _, w in group), "bottom": max(w.bbox.bottom for _, _, w in group)},
        })
        group_maps.append({"origin": "derived_word_group", "parent_indices": parents,
                           "word_refs": [{"line_index": i, "word_index": j} for i, j in refs]})
    if issues:
        return _extended(baseline, page, relevant, issues)

    spans, mapping = [], {}
    first = min(monetary)
    for i, entry in enumerate(page.lines):
        if i == first:
            for span, info in zip(derived_spans, group_maps):
                index = len(spans)
                mapping[index] = {"span_index": index, **info}
                spans.append(span)
        if i not in monetary:
            index = len(spans)
            mapping[index] = {**_original_map(i), "span_index": index}
            spans.append(entry.line.model_dump(mode="json"))
    derived = OcrPage.model_validate({"page": page.page, "width": page.width, "height": page.height,
                                    "coordinate_system": page.coordinate_system, "spans": spans})
    result = lookup_layout_column(derived, row_label=row_label, year=year)
    return _extended(result, page, relevant, [], derived=derived, mapping=mapping)


def lookup_word_layout(page: WordPage, *, row_label: str, year: int) -> dict:
    page = _validate(page)
    baseline = lookup_layout_column(page.line_page(), row_label=row_label, year=year)
    return _lookup(page, baseline, row_label, year)


def lookup_word_table(page: WordPage, *, row_label: str, year: int) -> dict:
    page = _validate(page)
    baseline = lookup_layout_column(page.line_page(), row_label=row_label, year=year)
    return {"schema_version": 1, "rule_version": WORD_TABLE_LOOKUP_VERSION,
            "query": {"page": page.page, "row_label": row_label, "normalized_row_label": normalize_row_label(row_label), "year": year},
            "methods": {"line": baseline, "word": _lookup(page, baseline, row_label, year)},
            "scope": list(SCOPE), "automatic_approval": False}
