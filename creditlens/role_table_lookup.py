"""Role-specific anchors and a bounded whole-row accounting-dollar grammar.

The query contains no reference value, box or section. Frozen line and word
methods are returned unchanged. New role decisions remain derived candidates.
"""
from __future__ import annotations

from copy import deepcopy
import re

from .diligence import digest
from .table_lookup import MIN_CONFIDENCE, TableLookupError, lookup_layout_column, parse_lookup_amount
from .visual import OcrPage
from .word_evidence import WordPage
from .word_table_lookup import (lookup_word_table, MAX_SYMBOL_GAP, MESSAGES as WORD_MESSAGES,
    _compact, _same_row, _contained, _references, _parent_record, _original_map, _issue, _validate)

ROLE_TABLE_LOOKUP_VERSION = "role-table-lookup-1"
CONFIDENCE_BASIS = "parent_line_for_anchors; parent_and_native_words_for_amounts; scores_not_calibrated"
GEOMETRY_BASIS = "numeric_token_for_column_and_scoring; complete_group_union_for_display"
SCOPE = [
    "The complete WordPage, exact normalized row label and year are the only inputs. No reference amount, location or section is used to choose evidence.",
    "Frozen line and protected-word results are returned unchanged. The role method is a new diagnostic on already observed development material.",
    "Row labels, units and year anchors use unchanged parent lines and their 0.90 score threshold; their unused word defects are explicitly recorded and do not rewrite anchors.",
    "All relevant monetary parents and every word must conserve text, remain strictly contained and score at least 0.90; no numeric, symbol or neighboring year is discarded.",
    "Cross-parent dollar association is allowed only for a complete row of one standalone leading dollar and one complete numeric token per registered year column, ordered without overlap or extra tokens.",
    "The accounting-row grammar uses the preceding number or label as a left barrier, not a tuned maximum dollar gap. The existing 8-point limit remains for ordinary within-parent word groups only.",
    "For derived_accounting_cell, span.bbox is the original numeric-token box used for column assignment and scoring. prefix_associations separately preserve the full dollar-plus-number display union.",
    "A dollar symbol does not establish USD. Currency remains unknown; selection is not fact adoption, document authentication, calibrated confidence, model improvement or causal evidence.",
    "Referenced mappings and complete relevant parents support local relationship review; full-page extraction replay still requires the retained complete WordPage.",
]
MESSAGES = {**WORD_MESSAGES,
    "anchor_line_low_confidence": "行名、表头或单位的整行分数不足，不能由字词分数替代。",
    "anchor_amount_role_overlap": "同一整行同时被当作表头锚与金额行，角色关系不明确。",
    "accounting_row_order": "会计式货币行的数字未按年度列唯一排列，不能跨年配对。",
    "accounting_prefix_barrier": "前置货币符号跨越行名、前一数值或当前数值，不能归属。",
    "accounting_pair_row_mismatch": "货币符号与金额不在同一行，不能配成一组。",
}


def _word_issues(page, index, *, include_parent_score):
    entry = page.lines[index]
    refs = [(index, j) for j in range(len(entry.words))]
    issues = []
    if not entry.words:
        return [_issue("missing_word_evidence", [index])]
    if _compact(entry.line.text) != _compact("".join(w.text for w in entry.words)):
        issues.append(_issue("parent_word_text_mismatch", [index], refs))
    outside = [(index, j) for j, w in enumerate(entry.words) if not _contained(entry.line, w)]
    if outside:
        issues.append(_issue("word_outside_parent", [index], outside))
    if (include_parent_score and entry.line.confidence < MIN_CONFIDENCE) or any(w.confidence < MIN_CONFIDENCE for w in entry.words):
        issues.append(_issue("word_low_confidence", [index], refs))
    return issues


def _finish(base, page, anchors, monetary, issues, nonblocking, *, derived=None, mapping=None, associations=()):
    result = deepcopy(base)
    result["method"] = "role_layout"
    if issues:
        reason = issues[0]["code"]
        result.update(status="insufficient", reason=reason, message=MESSAGES[reason],
                      raw_value=None, normalized_value=None, selected_span_index=None)
    mapping = mapping if mapping is not None else {i: _original_map(i) for i in range(len(page.lines))}
    referenced = _references(result)
    relevant = set(anchors) | monetary
    for index in referenced:
        relevant.update(mapping[index]["parent_indices"])
    result.update(
        selected_span_origin=mapping[result["selected_span_index"]]["origin"] if result["selected_span_index"] is not None else None,
        parent_evidence=[_parent_record(page, i) for i in sorted(relevant)],
        guard_issues=issues, nonblocking_anchor_issues=nonblocking,
        evidence_roles={"line_anchors": [{"line_index": i, "roles": sorted(roles)} for i, roles in sorted(anchors.items())],
                        "amount_parents": sorted(monetary)},
        prefix_associations=list(associations),
        adaptation={"status": "applied" if derived is not None else "not_applied",
                    "line_input_sha256": digest(page.line_page().model_dump(mode="json")),
                    "derived_page_sha256": digest(derived.model_dump(mode="json")) if derived is not None else None,
                    "mapping_scope": "referenced_spans_only", "source_map": [mapping[i] for i in referenced]},
        confidence_basis=CONFIDENCE_BASIS,
    )
    return result


def _columns(page, base):
    headers = [e["span_index"] for e in base["evidence"]["year_headers"]
               if re.fullmatch(r"(?:19|20|21)[0-9]{2}", page.lines[e["span_index"]].line.text.strip())]
    headers.sort(key=lambda i: (page.lines[i].line.bbox.x0+page.lines[i].line.bbox.x1)/2)
    centers = [(page.lines[i].line.bbox.x0+page.lines[i].line.bbox.x1)/2 for i in headers]
    bounds = [max(0.0, centers[0]-(centers[1]-centers[0])/2),
              *[(a+b)/2 for a, b in zip(centers, centers[1:])],
              min(page.width, centers[-1]+(centers[-1]-centers[-2])/2)]
    return headers, bounds


def _union(words):
    return {"x0": min(w.bbox.x0 for w in words), "top": min(w.bbox.top for w in words),
            "x1": max(w.bbox.x1 for w in words), "bottom": max(w.bbox.bottom for w in words)}


def _complete_number(text):
    if any(marker in text for marker in ("$", "¥", "￥")):
        return False
    try:
        parse_lookup_amount(text)
        return True
    except TableLookupError:
        return False


def _whole_accounting_row(flat, count):
    return (len(flat) == count*2 and all(flat[j][2].text.strip() == "$" and _complete_number(flat[j+1][2].text)
                                        for j in range(0, len(flat), 2)))


def _group_span(page, group, origin, *, numeric=None):
    parents = sorted({i for i, _, _ in group})
    return ({"text": "".join(w.text.strip() for _, _, w in group),
             "confidence": min([page.lines[i].line.confidence for i in parents]+[w.confidence for _, _, w in group]),
             "bbox": numeric.bbox.model_dump(mode="json") if numeric is not None else _union([w for _, _, w in group])},
            {"origin": origin, "parent_indices": parents,
             "word_refs": [{"line_index": i, "word_index": j} for i, j, _ in group]})


def _role(page, base, row_label, year):
    anchors = {}
    for key, role in (("row_label", "row_label"), ("unit", "unit"), ("year_headers", "year_header")):
        for item in base["evidence"][key]:
            anchors.setdefault(item["span_index"], set()).add(role)
    monetary = set(_references(base))-set(anchors)
    issues, nonblocking = [], []
    for index in sorted(anchors):
        if page.lines[index].line.confidence < MIN_CONFIDENCE:
            issues.append(_issue("anchor_line_low_confidence", [index]))
        nonblocking.extend(_word_issues(page, index, include_parent_score=False))
    labels = base["evidence"]["row_label"]
    if len(labels) == 1:
        label_index = labels[0]["span_index"]
        row = page.lines[label_index].line
        for i, entry in enumerate(page.lines):
            if i == label_index:
                continue
            if i in anchors:
                # Unused anchor-word positions cannot nominate monetary inputs.
                if entry.line.bbox.x1 > row.bbox.x1 and _same_row(row, entry.line):
                    issues.append(_issue("anchor_amount_role_overlap", [i]))
                continue
            if any(s.bbox.x1 > row.bbox.x1 and _same_row(row, s) for s in [entry.line, *entry.words]):
                monetary.add(i)
        for i in sorted(monetary):
            entry = page.lines[i]
            refs = [(i, j) for j in range(len(entry.words))]
            issues.extend(_word_issues(page, i, include_parent_score=True))
            if any(not _same_row(row, w) for w in entry.words):
                issues.append(_issue("word_row_mismatch", [i], refs))
            if any(a.bbox.x1 > b.bbox.x0 for a, b in zip(entry.words, entry.words[1:])):
                issues.append(_issue("word_order_or_overlap", [i], refs))
    if issues or base["column"] is None:
        return _finish(base, page, anchors, monetary, issues, nonblocking)

    headers, bounds = _columns(page, base)
    flat = sorted([(i, j, word) for i in sorted(monetary) for j, word in enumerate(page.lines[i].words)], key=lambda item: item[2].bbox.x0)
    spans, mappings, associations = [], [], []
    if _whole_accounting_row(flat, len(headers)):
        barrier = row.bbox.x1
        for k in range(len(headers)):
            prefix, number = flat[k*2:k*2+2]
            group = [prefix, number]
            parents = [prefix[0], number[0]]
            refs = [(i, j) for i, j, _ in group]
            if not (bounds[k] <= number[2].bbox.x0 and number[2].bbox.x1 <= bounds[k+1]):
                issues.append(_issue("accounting_row_order", parents, refs))
            if not (barrier <= prefix[2].bbox.x0 < prefix[2].bbox.x1 <= number[2].bbox.x0):
                issues.append(_issue("accounting_prefix_barrier", parents, refs))
            if not _same_row(prefix[2], number[2]):
                issues.append(_issue("accounting_pair_row_mismatch", parents, refs))
            span, mapping = _group_span(page, group, "derived_accounting_cell", numeric=number[2])
            spans.append(span); mappings.append(mapping)
            associations.append({"column_year": int(page.lines[headers[k]].line.text.strip()), "column_left": bounds[k], "column_right": bounds[k+1],
                "prefix_ref": {"line_index": prefix[0], "word_index": prefix[1]}, "amount_ref": {"line_index": number[0], "word_index": number[1]},
                "left_barrier": barrier, "gap": number[2].bbox.x0-prefix[2].bbox.x1, "raw_value": span["text"],
                "numeric_bbox": deepcopy(span["bbox"]), "display_bbox": _union([prefix[2], number[2]]), "geometry_basis": GEOMETRY_BASIS})
            barrier = number[2].bbox.x1
    else:
        groups = [[] for _ in headers]
        for i, j, word in flat:
            columns = [k for k in range(len(headers)) if bounds[k] <= word.bbox.x0 and word.bbox.x1 <= bounds[k+1]]
            if len(columns) != 1:
                issues.append(_issue("word_crosses_column", [i], [(i, j)]))
            else:
                groups[columns[0]].append((i, j, word))
        for group in groups:
            parents = sorted({i for i, _, _ in group}); refs = [(i, j) for i, j, _ in group]
            if not group:
                issues.append(_issue("word_column_coverage", monetary))
                continue
            numbers, unsupported = 0, []
            for i, j, word in group:
                try:
                    parse_lookup_amount(word.text); numbers += 1
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
            span, mapping = _group_span(page, group, "derived_word_group")
            try:
                parse_lookup_amount(span["text"])
            except TableLookupError:
                issues.append(_issue("incomplete_word_amount", parents, refs))
            spans.append(span); mappings.append(mapping)
    if issues:
        return _finish(base, page, anchors, monetary, issues, nonblocking)

    derived_spans, source_map, association_map = [], {}, []
    first = min(monetary)
    for i, entry in enumerate(page.lines):
        if i == first:
            for k, (span, mapping) in enumerate(zip(spans, mappings)):
                index = len(derived_spans)
                source_map[index] = {"span_index": index, **mapping}
                derived_spans.append(span)
                if associations:
                    association_map.append({"span_index": index, **associations[k]})
        if i not in monetary:
            index = len(derived_spans)
            source_map[index] = {**_original_map(i), "span_index": index}
            derived_spans.append(entry.line.model_dump(mode="json"))
    derived = OcrPage.model_validate({"page": page.page, "width": page.width, "height": page.height,
                                    "coordinate_system": page.coordinate_system, "spans": derived_spans})
    result = lookup_layout_column(derived, row_label=row_label, year=year)
    return _finish(result, page, anchors, monetary, [], nonblocking, derived=derived, mapping=source_map, associations=association_map)


def lookup_role_table(page: WordPage, *, row_label: str, year: int) -> dict:
    page = _validate(page)
    old = lookup_word_table(page, row_label=row_label, year=year)
    return {"schema_version": 1, "rule_version": ROLE_TABLE_LOOKUP_VERSION, "query": deepcopy(old["query"]),
            "methods": {"line": old["methods"]["line"], "word": old["methods"]["word"],
                        "role": _role(page, old["methods"]["line"], row_label, year)},
            "scope": list(SCOPE), "automatic_approval": False}


def lookup_role_layout(page: WordPage, *, row_label: str, year: int) -> dict:
    return lookup_role_table(page, row_label=row_label, year=year)["methods"]["role"]
