"""Explicit, input-bound row selection without changing the full OCR page."""
from __future__ import annotations

from copy import deepcopy
import re

from . import table_lookup as table
from .diligence import digest
from .visual import OcrPage

ROW_DISAMBIGUATION_VERSION = "row-disambiguation-1"
CONTEXT_LIMIT = 4
CONTEXT_BASIS = "nearest_vertical_neighbors_max4_each_side; not_semantic_section"
SCOPE = [
    "Only the complete line-level OcrPage, exact normalized row label and year are inputs. No reference amount, location or section is supplied.",
    "Every same-name row is retained with its original zero-based span index. No row is renamed, deleted, reordered or selected by default, including a single candidate.",
    "Candidate extraction is a preview, not an explicit choice. An explicit choice binds the complete page, original query and rule version through input_sha256.",
    "After choice, the frozen line-layout unit, header, year, confidence and complete amount-column rules apply to the original full page. Other same-name rows and all interfering spans remain present.",
    "Nearby context is geometric original text, not a resolved financial section or an inferred meaning of the selected row.",
    "This line-only operation does not check native words or certify OCR correctness. Currency remains unknown; a scale or dollar symbol does not prove USD.",
    "Digests establish consistency, not authenticity. Binding to a PDF, image, tenant or actor is the caller's responsibility; this pure function performs no authorization, adoption, approval, network request or persistence.",
    "Selection is a candidate for review, not proof that the user chose the intended accounting scope. Existing development pages do not become an independent benchmark.",
]


class RowDisambiguationError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Row selection input is invalid"):
        self.code, self.path = code, path
        super().__init__(message)


def _validate(page, row_label, year):
    try:
        return table._validate(page, row_label, year)
    except table.TableLookupError as exc:
        raise RowDisambiguationError(exc.code, exc.path) from None


def _lookup_at_index(page, row_label, year, label):
    """Frozen layout body, with explicit label index replacing only _label.

    The page is never transformed. All original spans participate in unit,
    header and amount checks, including other same-name rows.
    """
    result = table._result("explicit_row_layout")
    table._evidence(result, "row_label", page, [label])
    row = page.spans[label]
    unit, reason = table._unit(page, [i for i, s in enumerate(page.spans) if s.bbox.bottom <= row.bbox.top], row_label, result)
    if reason:
        return table._finish(result, reason)
    unit_span = page.spans[unit]
    pure_years = [i for i, s in enumerate(page.spans) if table._year(s.text) is not None and
                  s.bbox.bottom <= row.bbox.top and table._cy(s) > table._cy(unit_span) and s.bbox.x0 >= row.bbox.x1]
    groups = []
    for i in sorted(pure_years, key=lambda k: (table._cy(page.spans[k]), table._cx(page.spans[k]))):
        if not groups or table._cy(page.spans[i]) - table._cy(page.spans[groups[-1][0]]) > table.HEADER_CENTER_TOLERANCE:
            groups.append([i])
        else:
            groups[-1].append(i)
    groups = [g for g in groups if 2 <= len(g) <= table.MAX_YEAR_COLUMNS]
    if len(groups) != 1:
        table._evidence(result, "year_headers", page, [i for group in groups for i in group])
        return table._finish(result, "missing_year_group" if not groups else "ambiguous_year_group")
    headers = sorted(groups[0], key=lambda i: table._cx(page.spans[i]))
    if set(pure_years) != set(headers):
        table._evidence(result, "year_headers", page, pure_years)
        return table._finish(result, "ambiguous_year_group")
    header_bottom = max(page.spans[i].bbox.bottom for i in headers)
    context = [i for i, s in enumerate(page.spans) if i != unit and table._cy(unit_span) < table._cy(s) <= header_bottom]
    table._evidence(result, "year_headers", page, context)
    if any(not table._header_text(page.spans[i].text) for i in context):
        return table._finish(result, "unanchored_header")
    years = [table._year(page.spans[i].text) for i in headers]
    if len(set(years)) != len(years):
        return table._finish(result, "duplicate_header_year")
    if year not in years:
        return table._finish(result, "year_not_found")
    centers = [table._cx(page.spans[i]) for i in headers]
    bounds = [max(0.0, centers[0] - (centers[1]-centers[0])/2),
              *[(a+b)/2 for a, b in zip(centers, centers[1:])],
              min(page.width, centers[-1] + (centers[-1]-centers[-2])/2)]
    if row.bbox.x1 >= bounds[0] or any(page.spans[a].bbox.x1 >= page.spans[b].bbox.x0 for a, b in zip(headers, headers[1:])):
        return table._finish(result, "label_column_overlap")
    selected_column = years.index(year)
    result["column"] = {"year": year, "left": bounds[selected_column], "right": bounds[selected_column+1], "header_span_index": headers[selected_column]}
    amounts = [i for i, span in enumerate(page.spans) if i != label and span.bbox.x1 > row.bbox.x1 and table._same_row(row, span)]
    amounts.sort(key=lambda i: page.spans[i].bbox.x0)
    table._evidence(result, "amounts", page, amounts)
    if any(table._number(page.spans[i].text) is None for i in amounts):
        table._evidence(result, "guards", page, [i for i in amounts if table._number(page.spans[i].text) is None])
        return table._finish(result, "invalid_or_merged_amount")
    if len(amounts) != len(headers):
        return table._finish(result, "row_amount_count_mismatch")
    assignment = {}
    for index in amounts:
        box = page.spans[index].bbox
        columns = [j for j in range(len(headers)) if bounds[j] <= box.x0 and box.x1 <= bounds[j+1]]
        if len(columns) != 1:
            return table._finish(result, "cell_crosses_column")
        if columns[0] in assignment:
            return table._finish(result, "duplicate_amount_column")
        assignment[columns[0]] = index
    if set(assignment) != set(range(len(headers))):
        return table._finish(result, "duplicate_amount_column")
    if not table._confidence(page, [label, unit, *context, *amounts]):
        return table._finish(result, "low_confidence")
    return table._finish(result, "selected", page, assignment[selected_column])


def _span(page, index):
    return {"span_index": index, "span": page.spans[index].model_dump(mode="json")}


def _context(page, index):
    row = page.spans[index]
    above = [i for i, s in enumerate(page.spans) if i != index and s.bbox.bottom <= row.bbox.top]
    below = [i for i, s in enumerate(page.spans) if i != index and s.bbox.top >= row.bbox.bottom]
    above.sort(key=lambda i: (row.bbox.top-page.spans[i].bbox.bottom, page.spans[i].bbox.x0, i))
    below.sort(key=lambda i: (page.spans[i].bbox.top-row.bbox.bottom, page.spans[i].bbox.x0, i))
    return {"above": [_span(page, i) for i in above[:CONTEXT_LIMIT]],
            "below": [_span(page, i) for i in below[:CONTEXT_LIMIT]], "basis": CONTEXT_BASIS}


def _enumerate(page, row_label, year):
    query = {"page": page.page, "row_label": row_label, "normalized_row_label": table.normalize_row_label(row_label), "year": year}
    page_sha = digest(page.model_dump(mode="json"))
    binding = {"page_sha256": page_sha,
               "input_sha256": digest({"rule_version": ROW_DISAMBIGUATION_VERSION, "page_sha256": page_sha, "query": query})}
    hits = [i for i, span in enumerate(page.spans) if table.normalize_row_label(span.text) == query["normalized_row_label"]]
    return {"schema_version": 1, "rule_version": ROW_DISAMBIGUATION_VERSION, "query": query,
            "input_binding": binding, "status": "selection_required" if hits else "not_found", "candidate_count": len(hits),
            "candidates": [{"row_index": i, "row": _span(page, i), "context": _context(page, i),
                            "extraction": _lookup_at_index(page, row_label, year, i)} for i in hits],
            "legacy_line_result": table.lookup_layout_column(page, row_label=row_label, year=year),
            "scope": list(SCOPE), "automatic_approval": False}


def enumerate_row_candidates(page: OcrPage, *, row_label: str, year: int) -> dict:
    """Expose all candidates without silently choosing even a sole match."""
    page = _validate(page, row_label, year)
    return _enumerate(page, row_label, year)


def lookup_selected_row(page: OcrPage, *, row_label: str, year: int, selection: dict | None = None) -> dict:
    """An explicit, digest-bound choice replaces only the label ambiguity gate."""
    page = _validate(page, row_label, year)
    if selection is not None:
        if type(selection) is not dict or set(selection) != {"input_sha256", "row_index"}:
            raise RowDisambiguationError("invalid_selection", "selection")
        if type(selection["input_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", selection["input_sha256"]):
            raise RowDisambiguationError("invalid_selection_digest", "selection.input_sha256")
        if type(selection["row_index"]) is not int:
            raise RowDisambiguationError("invalid_row_index", "selection.row_index")
        if not 0 <= selection["row_index"] < len(page.spans):
            raise RowDisambiguationError("row_index_out_of_range", "selection.row_index")
    result = _enumerate(page, row_label, year)
    result.update(selection=None, extraction=None)
    if selection is None:
        return result
    if selection["input_sha256"] != result["input_binding"]["input_sha256"]:
        raise RowDisambiguationError("stale_selection", "selection.input_sha256")
    candidate = next((c for c in result["candidates"] if c["row_index"] == selection["row_index"]), None)
    if candidate is None:
        raise RowDisambiguationError("selection_not_candidate", "selection.row_index")
    result.update(selection={**selection, "explicit": True}, extraction=deepcopy(candidate["extraction"]), status=candidate["extraction"]["status"])
    return result
