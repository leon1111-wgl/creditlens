"""Unconditional amount-evidence guards layered over the frozen currency rule.

Spatial candidates are selected before parsing or consulting the expected value.
This known-location diagnostic can only retain or downgrade the legacy result;
it is neither OCR, currency identification, source authentication nor approval.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import re
import unicodedata

from .currency_evidence import crosscheck_currency_fact, _parse_marked_amount
from .diligence import Fact, PdfBox
from .visual import MIN_CONFIDENCE
from .word_evidence import WordPage, _near_numeric_cell, _selected, _without_space


EVIDENCE_GUARD_RULE_VERSION = "evidence-guard-1"
GEOMETRY_EPSILON = 1e-6  # Float representation tolerance, not layout tolerance.
REASON_LABELS = {
    "amount_evidence_guards_passed": "本层金额证据守卫未发现新增阻断项",
    "no_unique_spatial_amount_word": "金额空间候选不是唯一完整词",
    "word_outside_its_parent_geometry": "字词框不属于登记父行框",
    "parent_text_not_conserved": "完整父行文字与全部字词不守恒",
    "nearby_parent_without_words": "金额邻域有非空父行却没有字词映射",
    "nearby_unassigned_parent": "金额邻域存在其他未归属父行",
    "nearby_unassigned_word": "金额邻域存在未归属字词或符号",
    "unassigned_currency_word_in_amount_parent": "金额父行内的货币字词没有建立完整归属关系",
    "amount_parent_confidence_low": "相关金额父行识别分数不足",
    "unsupported_complete_amount_syntax": "完整金额包含不受支持的文字或符号",
    "currency_marker_conflicts_with_registered_usd": "显式货币标记与已登记美元口径冲突",
    "registered_raw_currency_conflict": "参考显示值的货币标记与登记美元单位冲突",
    "unsupported_registered_amount_unit": "本层未定义此登记单位的货币标记规则",
    "complete_signed_amount_mismatch": "完整金额的数值或正负号与参考不符",
}
_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"


def _contains(parent: PdfBox, word: PdfBox) -> bool:
    return (parent.x0 - GEOMETRY_EPSILON <= word.x0 and word.x1 <= parent.x1 + GEOMETRY_EPSILON
            and parent.top - GEOMETRY_EPSILON <= word.top and word.bottom <= parent.bottom + GEOMETRY_EPSILON)


def _markers(text: str) -> list[str]:
    return sorted({char for char in text if unicodedata.category(char) == "Sc"})


def _parse_complete(text: str) -> Decimal | None:
    """Preserve every sign and bracket; never repair or choose a substring."""
    text = text.strip()
    marked = _parse_marked_amount(text)
    if marked is not None:
        return marked[1]
    if re.fullmatch(rf"[+-]?{_NUMBER}", text):
        return Decimal(text.replace(",", ""))
    if re.fullmatch(rf"\({_NUMBER}\)", text):
        return Decimal(text[1:-1].replace(",", "")).copy_negate()
    return None


def crosscheck_guarded_fact(fact: Fact, page: WordPage, *, min_confidence: float = MIN_CONFIDENCE) -> dict:
    """Retain the full legacy result and apply guards even if it was consistent.

    All non-amount checks are unchanged. The fixed neighborhood is the registered
    amount region plus each geometrically selected whole word, with the existing
    eight-point horizontal / half-height overlap neighborhood. Parents are
    checked even when their words are missing. Unassigned nonempty text is not
    discarded because it fails a numeric regular expression.
    """
    legacy = crosscheck_currency_fact(fact, page, min_confidence=min_confidence)
    result = deepcopy(legacy)
    result["legacy_currency_result"] = deepcopy(legacy)
    result["evidence_guard_rule_version"] = EVIDENCE_GUARD_RULE_VERSION
    result["automatic_approval"] = False
    target = fact.pdf_location.amount_bbox
    selected = [(line_index, word_index) for line_index, entry in enumerate(page.lines)
                for word_index, word in enumerate(entry.words) if _selected(word.bbox, target)]
    windows = [target] + [page.lines[i].words[j].bbox for i, j in selected]
    prefix_association = {"applied": False, "reason": "no_complete_same_parent_prefix_association",
                          "line_index": None, "prefix_word_index": None, "amount_word_index": None,
                          "prefix": None, "amount": None, "parent_line": None, "complete_text": None}
    paired_prefix = None
    if len(selected) == 1:
        line_index, word_index = selected[0]
        entry = page.lines[line_index]
        # This is an explicit two-word ownership relation, not permission to
        # drop any symbol near a number. No expected amount participates.
        if len(entry.words) == 2 and word_index == 1:
            prefix, amount_word = entry.words
            marker = prefix.text.strip()
            if (len(marker) == 1 and _markers(marker) == [marker]
                    and _contains(entry.line.bbox, prefix.bbox) and _contains(entry.line.bbox, amount_word.bbox)
                    and prefix.bbox.x1 <= amount_word.bbox.x0 and _near_numeric_cell(prefix.bbox, amount_word.bbox)
                    and _without_space(entry.line.text) == "".join(_without_space(w.text) for w in entry.words)
                    and entry.line.confidence >= min_confidence and _parse_marked_amount(entry.line.text) is not None):
                paired_prefix = (line_index, 0)
                windows.append(prefix.bbox)
                prefix_association = {"applied": True, "reason": "same_parent_complete_currency_amount",
                                      "line_index": line_index, "prefix_word_index": 0, "amount_word_index": 1,
                                      "prefix": prefix.model_dump(mode="json"), "amount": amount_word.model_dump(mode="json"),
                                      "parent_line": entry.line.model_dump(mode="json"), "complete_text": entry.line.text}

    def nearby(box):
        return any(_selected(box, window) or _near_numeric_cell(box, window) for window in windows)

    guard = {
        "status": "passed", "reason": "amount_evidence_guards_passed", "issues": [],
        "parent_evidence": [], "selected_word_refs": [{"line_index": i, "word_index": j} for i, j in selected],
        "currency_markers": [], "registered_currency": "USD" if fact.unit in {"USD", "USD_million"} else None,
        "scope": "Amount integrity in a fixed spatial neighborhood; marker compatibility with registered USD only. No currency identification, source authenticity or automatic approval.",
        "geometry_epsilon_points": GEOMETRY_EPSILON, "neighborhood_horizontal_gap_points": 8.0,
        "confidence_basis": "parent_line_score", "parsed_signed_amount": None,
        "prefix_association": prefix_association,
    }
    result["evidence_guard"] = guard

    def issue(code, line_index=None, word_index=None, observation=None, severity="insufficient"):
        guard["issues"].append({"code": code, "severity": severity, "line_index": line_index,
                                "word_index": word_index,
                                "observation": observation.model_dump(mode="json") if observation is not None else None,
                                "parent_line": page.lines[line_index].line.model_dump(mode="json") if line_index is not None else None})

    if len(selected) != 1:
        issue("no_unique_spatial_amount_word")
    candidate_parent = selected[0][0] if len(selected) == 1 else None
    for line_index, entry in enumerate(page.lines):
        indices = [j for i, j in selected if i == line_index]
        relevant = bool(indices) or nearby(entry.line.bbox) or any(nearby(w.bbox) for w in entry.words)
        if not relevant:
            continue
        guard["parent_evidence"].append({"line_index": line_index, "line": entry.line.model_dump(mode="json"),
                                         "words": [w.model_dump(mode="json") for w in entry.words], "selected_word_indices": indices})
        if not entry.line.text.strip() and not any(w.text.strip() for w in entry.words):
            continue
        if not entry.words:
            issue("nearby_parent_without_words", line_index, observation=entry.line)
        if _without_space(entry.line.text) != "".join(_without_space(w.text) for w in entry.words):
            issue("parent_text_not_conserved", line_index, observation=entry.line)
        if entry.line.confidence < min_confidence:
            issue("amount_parent_confidence_low", line_index, observation=entry.line)
        for word_index, word in enumerate(entry.words):
            if not _contains(entry.line.bbox, word.bbox):
                issue("word_outside_its_parent_geometry", line_index, word_index, word)
        if line_index != candidate_parent:
            if entry.line.text.strip() and nearby(entry.line.bbox):
                issue("nearby_unassigned_parent", line_index, observation=entry.line)
            for word_index, word in enumerate(entry.words):
                if word.text.strip() and nearby(word.bbox):
                    issue("nearby_unassigned_word", line_index, word_index, word)
        else:
            for word_index, word in enumerate(entry.words):
                if (line_index, word_index) not in selected and (line_index, word_index) != paired_prefix and word.text.strip():
                    if _markers(word.text):
                        issue("unassigned_currency_word_in_amount_parent", line_index, word_index, word)
                    elif nearby(word.bbox):
                        issue("nearby_unassigned_word", line_index, word_index, word)
    # Check complete spatially selected tokens, not expected-value substrings.
    # A symbol such as yen only contradicts USD here; it does not prove CNY/JPY.
    for line_index, word_index in selected + ([paired_prefix] if paired_prefix is not None else []):
        word = page.lines[line_index].words[word_index]
        markers = _markers(word.text)
        guard["currency_markers"].append({"line_index": line_index, "word_index": word_index,
                                          "markers": markers, "text": word.text})
        if guard["registered_currency"] == "USD" and any(marker != "$" for marker in markers):
            issue("currency_marker_conflicts_with_registered_usd", line_index, word_index, word, "conflict")
    if guard["registered_currency"] is None:
        issue("unsupported_registered_amount_unit")
    elif any(marker != "$" for marker in _markers(fact.raw_value)):
        issue("registered_raw_currency_conflict", severity="conflict")
    # Only after geometry/ownership has been established consult the reference.
    if len(selected) == 1:
        line_index, word_index = selected[0]
        word = page.lines[line_index].words[word_index]
        parsed = _parse_complete(page.lines[line_index].line.text if paired_prefix is not None else word.text)
        if parsed is None:
            issue("unsupported_complete_amount_syntax", line_index, word_index, word)
        elif not guard["issues"]:
            guard["parsed_signed_amount"] = format(parsed, "f")
            expected = _parse_complete(fact.raw_value)
            if expected is None:
                issue("unsupported_complete_amount_syntax")
            elif parsed != expected or parsed.is_signed() != expected.is_signed():
                issue("complete_signed_amount_mismatch", line_index, word_index, word, "conflict")
    if guard["issues"]:
        guard["status"] = "blocked"
        strongest = next((i for i in guard["issues"] if i["severity"] == "conflict"), guard["issues"][0])
        guard["reason"] = strongest["code"]
        amount = next(c for c in result["checks"] if c["field"] == "amount")
        # A legacy conflict must not be weakened by a new uncertainty.
        amount["status"] = "conflict" if amount["status"] == "conflict" or strongest["severity"] == "conflict" else "insufficient"
        amount["reason"] = "evidence_guard:" + strongest["code"]
        states = {check["status"] for check in result["checks"]}
        result["status"] = "conflict" if "conflict" in states else "insufficient" if "insufficient" in states else "consistent"
    return result
