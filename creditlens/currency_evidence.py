"""Conservative post-OCR association of one complete currency-marked amount.

This diagnostic only explains a leftward currency/amount merge rejected by the
old geometry rule. It never crops strings or selects a matching numeric value.
A dollar marker is checked against registered USD metadata; it does not establish
currency, source authenticity, or approval of the financial fact.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re

from .date_evidence import _contains, crosscheck_date_fact
from .diligence import Fact, PdfBox, displayed_amount
from .visual import MIN_CONFIDENCE, crosscheck_fact
from .word_evidence import WordPage, _near_numeric_cell, _selected, _without_space


CURRENCY_RULE_VERSION = "currency-evidence-1"
CURRENCY_REASON_LABELS = {
    "not_attempted": "尚未尝试符号关联",
    "legacy_amount_does_not_need_currency_association": "旧金额检查不属于本规则处理范围",
    "legacy_amount_has_unresolved_guards": "旧金额检查仍有未解决的符号或数字守卫",
    "amount_parent_or_word_is_not_unique": "金额父行或候选词不唯一",
    "amount_is_not_a_complete_single_word": "金额并非一个完整独立词",
    "parent_and_word_do_not_conserve_complete_text": "父行与词的完整文本或位置不一致",
    "amount_parent_confidence_is_low": "金额父行识别分数不足",
    "existing_line_amount_conflict": "已有父行金额冲突不能覆盖",
    "other_financial_fields_are_not_verified": "年份、期间、单位或行名仍未通过检查",
    "amount_has_unexplained_nonleft_extension": "金额在右侧或上下仍有无法解释的越界",
    "amount_failure_is_not_a_left_currency_extension": "不足并非左侧货币符号合并造成",
    "registered_header_and_amount_column_are_ambiguous": "登记表头与金额列关系不明确",
    "year_headers_are_not_complete_single_words": "年份表头不是守恒的完整四位单词",
    "year_header_confidence_is_low": "相邻年份表头识别分数不足",
    "neighboring_year_boundary_unavailable": "缺少足够的相邻年份列界依据",
    "year_headers_overlap_or_repeat": "年份表头重叠或重复",
    "year_header_has_unassigned_neighbor": "年份表头邻域存在未归属文字或符号",
    "current_year_column_is_not_unique": "当前年份列的空间候选不唯一",
    "year_column_boundary_outside_page": "推得的年份列界超出页面",
    "complete_amount_crosses_its_registered_column": "完整金额或父行跨出登记列界",
    "nearby_unassigned_text_or_symbol_evidence": "金额邻域存在未归属文字、数字或符号",
    "complete_currency_amount_syntax_is_unsupported": "完整货币金额格式不受支持",
    "currency_marker_differs_from_registered_usd": "符号与已登记美元口径不符",
    "signed_amount_differs_from_registered_fact": "完整有符号金额与登记事实不一致",
    "complete_currency_amount_associated_with_column": "完整货币金额与唯一列位置对应",
}
_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
_MARKER = r"[$\u20ac\u00a3\u00a5\uffe5]"


def _signed_display(text: str) -> Decimal:
    value = displayed_amount(text)
    raw = text.strip()
    if raw.startswith(("$", "\u00a5", "\uffe5")):
        raw = raw[1:].strip()
    # Decimal unary minus can normalize zero's sign; retaining a displayed
    # parenthesis is still required even when its magnitude happens to be zero.
    return value.copy_abs().copy_negate() if raw.startswith("(") else value


def _parse_marked_amount(text: str) -> tuple[str, Decimal] | None:
    """Parse the whole token, including its sign; no character repair or slice."""
    # Only literal currency markers are handled. C$, US$, currency names,
    # Unicode minus repair, percentages, extra numbers and suffixes are refused.
    value = text.strip()
    match = re.fullmatch(rf"(?P<marker>{_MARKER})\s*(?P<amount>[+-]?{_NUMBER}|\({_NUMBER}\))", value)
    if match is not None:
        return match["marker"], _signed_display(match["amount"])
    match = re.fullmatch(rf"\((?P<marker>{_MARKER})\s*(?P<amount>{_NUMBER})\)", value)
    if match is not None:
        return match["marker"], displayed_amount(match["amount"]).copy_negate()
    return None


def _vertical_overlap(first: PdfBox, second: PdfBox) -> float:
    return max(0, min(first.bottom, second.bottom)-max(first.top, second.top)) / min(
        first.bottom-first.top, second.bottom-second.top)


def _column_evidence(page: WordPage, target: PdfBox, min_confidence: float) -> tuple[dict | None, str]:
    """Infer a local column from complete sibling year headers, by position.

    All numeric parents on the year row are considered before a current header
    is selected. No expected year or amount is an input to this helper.
    """
    headers = []
    for line_index, entry in enumerate(page.lines):
        if _vertical_overlap(entry.line.bbox, target) < .5 or not re.search(r"[0-9]", entry.line.text):
            continue
        if (not re.fullmatch(r"[0-9]{4}", entry.line.text.strip()) or len(entry.words) != 1 or
                not re.fullmatch(r"[0-9]{4}", entry.words[0].text.strip()) or
                _without_space(entry.line.text) != _without_space(entry.words[0].text) or
                not _contains(entry.line.bbox, entry.words[0].bbox)):
            return None, "year_headers_are_not_complete_single_words"
        if entry.line.confidence < min_confidence:
            return None, "year_header_confidence_is_low"
        headers.append((line_index, entry))
    if len(headers) < 2:
        return None, "neighboring_year_boundary_unavailable"
    headers.sort(key=lambda item: item[1].line.bbox.x0)
    if (len({entry.line.text.strip() for _, entry in headers}) != len(headers) or
            any(a.line.bbox.x1 >= b.line.bbox.x0 for (_, a), (_, b) in zip(headers, headers[1:]))):
        return None, "year_headers_overlap_or_repeat"
    for header_id, header in headers:
        for line_id, other in enumerate(page.lines):
            if line_id == header_id:
                continue
            if any(span.text.strip() and (_selected(span.bbox, header.words[0].bbox) or
                                         _near_numeric_cell(span.bbox, header.words[0].bbox))
                   for span in (other.line, *other.words)):
                return None, "year_header_has_unassigned_neighbor"
    selected = [i for i, (_, entry) in enumerate(headers) if
                _selected(entry.line.bbox, target) and _selected(entry.words[0].bbox, target)]
    if len(selected) != 1:
        return None, "current_year_column_is_not_unique"
    index = selected[0]
    centers = [(entry.line.bbox.x0+entry.line.bbox.x1)/2 for _, entry in headers]
    center = centers[index]
    left = (centers[index-1]+center)/2 if index else center-(centers[1]-center)/2
    right = (center+centers[index+1])/2 if index+1 < len(centers) else center+(center-centers[index-1])/2
    if not 0 <= left < center < right <= page.width:
        return None, "year_column_boundary_outside_page"
    return {"left": left, "right": right, "current_header_index": headers[index][0],
            "headers": [{"line_index": i, "line": e.line.model_dump(mode="json"),
                         "word": e.words[0].model_dump(mode="json")} for i, e in headers]}, "column_resolved"


def crosscheck_currency_fact(fact: Fact, page: WordPage, *,
                             min_confidence: float = MIN_CONFIDENCE) -> dict:
    """Keep the date baseline and replace only a fully established amount check.

    Already-consistent amounts are left untouched, so they are not represented
    as having passed this currency diagnostic. A rejected attempt retains the
    original amount evidence/status; all other checks always remain unchanged.
    """
    result = crosscheck_date_fact(fact, page, min_confidence=min_confidence)
    amount = next(check for check in result["checks"] if check["field"] == "amount")
    loc = fact.pdf_location
    association = {
        "applied": False, "reason": "not_attempted", "legacy_amount": deepcopy(amount),
        "candidate_parent_indices": [p["line_index"] for p in amount["parent_lines"]],
        "candidate_word_index": None, "observed_text": None, "parsed_signed_amount": None,
        "registered_currency": "USD" if fact.unit in {"USD", "USD_million"} else None,
        "marker_scope": "Marker consistency with registered USD only; $ alone does not establish currency",
        "column_bounds": None, "column_headers": [], "guard_observations": [],
    }
    result["currency_rule_version"] = CURRENCY_RULE_VERSION
    result["currency_association"] = association
    result["automatic_approval"] = False

    def reject(reason):
        association["reason"] = reason
        return result

    if amount["status"] != "insufficient" or amount["reason"] != "merged_or_misaligned_region":
        return reject("legacy_amount_does_not_need_currency_association")
    if amount["word_guards"]:
        return reject("legacy_amount_has_unresolved_guards")
    # Candidate ownership is established only by the existing spatial hits.
    parents = amount["parent_lines"]
    if len(parents) != 1 or parents[0]["selected_word_indices"] != [0]:
        return reject("amount_parent_or_word_is_not_unique")
    parent_index = parents[0]["line_index"]
    entry = page.lines[parent_index]
    if len(entry.words) != 1:
        return reject("amount_is_not_a_complete_single_word")
    word = entry.words[0]
    association.update(candidate_word_index=0, observed_text=word.text)
    if (_without_space(entry.line.text) != _without_space(word.text) or
            not _contains(entry.line.bbox, word.bbox)):
        return reject("parent_and_word_do_not_conserve_complete_text")
    if entry.line.confidence < min_confidence:
        return reject("amount_parent_confidence_is_low")
    line_amount = next(c for c in crosscheck_fact(fact, page.line_page(), min_confidence=min_confidence)["checks"] if c["field"] == "amount")
    if line_amount["status"] == "conflict":
        return reject("existing_line_amount_conflict")
    if any(c["status"] != "consistent" for c in result["checks"] if c["field"] != "amount"):
        return reject("other_financial_fields_are_not_verified")
    # Retain the original right/top/bottom 8-point tolerance. Only a leftward
    # extension may be explained, and it needs independent header boundaries.
    target = loc.amount_bbox
    for bounds in (entry.line.bbox, word.bbox):
        if (bounds.x1 > target.x1+8 or bounds.top < target.top-8 or bounds.bottom > target.bottom+8
                or _vertical_overlap(bounds, target) < .5):
            return reject("amount_has_unexplained_nonleft_extension")
    if word.bbox.x0 >= target.x0-8:
        return reject("amount_failure_is_not_a_left_currency_extension")
    center = (target.x0+target.x1)/2
    if (not _contains(loc.period_bbox, loc.year_bbox) or not loc.year_bbox.x0 <= center <= loc.year_bbox.x1
            or loc.amount_bbox.top <= loc.period_bbox.bottom or
            not loc.period_bbox.x0 <= target.x0 < target.x1 <= loc.period_bbox.x1 or
            loc.row_label_bbox.x1 >= entry.line.bbox.x0):
        return reject("registered_header_and_amount_column_are_ambiguous")
    column, reason = _column_evidence(page, loc.year_bbox, min_confidence)
    if column is None:
        return reject(reason)
    association["column_headers"] = column["headers"]
    left, right = max(column["left"], loc.period_bbox.x0), min(column["right"], loc.period_bbox.x1)
    association["column_bounds"] = {"left": left, "right": right,
                                    "current_header_index": column["current_header_index"],
                                    "basis": "spatial_sibling_year_midpoints_intersected_with_registered_period_band"}
    if any(not left <= b.x0 < b.x1 <= right for b in (entry.line.bbox, word.bbox, target)):
        return reject("complete_amount_crosses_its_registered_column")
    # A missing words=[] projection must not discard an adjacent sign/number.
    # The guarded neighborhood includes the expanded candidate, not just the
    # smaller reference amount box whose left edge caused the old refusal.
    for line_index, other in enumerate(page.lines):
        if line_index == parent_index:
            continue
        observations = [(None, other.line)] + list(enumerate(other.words))
        for word_index, observation in observations:
            if (observation.text.strip() and
                    (_selected(observation.bbox, word.bbox) or _near_numeric_cell(observation.bbox, word.bbox))):
                association["guard_observations"].append({"line_index": line_index, "word_index": word_index,
                                                           "observation": observation.model_dump(mode="json")})
    if association["guard_observations"]:
        return reject("nearby_unassigned_text_or_symbol_evidence")
    parsed = _parse_marked_amount(word.text)
    parent_parsed = _parse_marked_amount(entry.line.text)
    if parsed is None or parsed != parent_parsed:
        return reject("complete_currency_amount_syntax_is_unsupported")
    marker, actual = parsed
    association["parsed_signed_amount"] = format(actual, "f")
    if marker != "$" or association["registered_currency"] != "USD":
        return reject("currency_marker_differs_from_registered_usd")
    # The expected amount is consulted only after candidate, syntax and spatial
    # ownership are fixed. Zero also retains an explicit negative sign.
    expected = _signed_display(fact.raw_value)
    if actual != expected or actual.is_signed() != expected.is_signed():
        return reject("signed_amount_differs_from_registered_fact")
    association.update(applied=True, reason="complete_currency_amount_associated_with_column")
    amount.update(status="consistent", reason="complete_currency_amount_matches_registered_fact")
    states = {check["status"] for check in result["checks"]}
    result["status"] = "conflict" if "conflict" in states else "insufficient" if "insufficient" in states else "consistent"
    return result
