"""Bounded association of a complete English date with its existing year token.

This post-OCR diagnostic preserves every non-year word check. It neither fills
missing characters nor extracts a matching substring. The v0.8 rules and their
reports remain unchanged; an association is never an automatic approval.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from .diligence import Fact, PdfBox
from .visual import MIN_CONFIDENCE, crosscheck_fact
from .word_evidence import (WordPage, _near_numeric_cell, _selected,
                            _without_space, crosscheck_word_fact)

DATE_RULE_VERSION = "date-evidence-1"
_MONTHS = {name: index for index, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"),
    ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
    ("aug", "august"), ("sep", "september"), ("oct", "october"),
    ("nov", "november"), ("dec", "december")), 1) for name in names}


def _complete_date(text: str) -> date | None:
    # Require the whole string, a named month, an explicit comma and four year
    # digits. Locale-ambiguous numeric formats and OCR repairs are unsupported.
    match = re.fullmatch(r"([A-Za-z]+)\s+([0-9]{1,2}),\s*([0-9]{4})", text.strip())
    if match is None or match[1].casefold() not in _MONTHS:
        return None
    try:
        return date(int(match[3]), _MONTHS[match[1].casefold()], int(match[2]))
    except ValueError:
        return None


def _contains(outer: PdfBox, inner: PdfBox) -> bool:
    return (outer.x0 <= inner.x0 < inner.x1 <= outer.x1 and
            outer.top <= inner.top < inner.bottom <= outer.bottom)


def crosscheck_date_fact(fact: Fact, page: WordPage, *,
                         min_confidence: float = MIN_CONFIDENCE) -> dict:
    """Explain only a day-fragment guard using a unique, fully verified date.

    Candidate identity comes from existing spatial selection, before parsing or
    comparison with period_end. A standalone year must already be correct under
    the original numeric rule. Complete line and token dates, their ownership,
    and all nearby numeric fragments must also agree. Unhandled cases retain the
    original word result, including every failure and evidence item.
    """
    result = crosscheck_word_fact(fact, page, min_confidence=min_confidence)
    token = crosscheck_fact(fact, page.token_page(), min_confidence=min_confidence)
    line = crosscheck_fact(fact, page.line_page(), min_confidence=min_confidence)
    checks = {item["field"]: item for item in result["checks"]}
    token_checks = {item["field"]: item for item in token["checks"]}
    line_checks = {item["field"]: item for item in line["checks"]}
    year, period = checks["year"], checks["period"]
    association = {
        "applied": False, "reason": "not_attempted", "legacy_year": deepcopy(year),
        "token_year_status": token_checks["year"]["status"],
        "line_year_status": line_checks["year"]["status"],
        "candidate_parent_indices": [p["line_index"] for p in period["parent_lines"]],
        "date_text": None, "parsed_date": None, "year_word_index": None,
        "consumed_guard_observations": [],
    }
    result["date_rule_version"] = DATE_RULE_VERSION
    result["date_association"] = association
    result["automatic_approval"] = False

    def reject(reason: str) -> dict:
        association["reason"] = reason
        return result

    if token_checks["year"]["status"] == "conflict" or line_checks["year"]["status"] == "conflict":
        return reject("existing_year_conflict")
    if year["status"] != "insufficient":
        return reject("legacy_year_does_not_need_association")
    if (token_checks["year"]["status"] != "consistent" or
            year["word_guards"] != ["detached_numeric_fragment_near_cell"]):
        return reject("year_has_unresolved_evidence")
    if period["status"] != "consistent" or line_checks["period"]["status"] != "consistent":
        return reject("complete_date_not_verified")
    parents = period["parent_lines"]
    if (len(parents) != 1 or len(year["parent_lines"]) != 1 or
            parents[0]["line_index"] != year["parent_lines"][0]["line_index"]):
        return reject("date_parent_not_unique")
    parent_index = parents[0]["line_index"]
    entry = page.lines[parent_index]
    association["date_text"] = entry.line.text
    if (not entry.words or parents[0]["selected_word_indices"] != list(range(len(entry.words))) or
            _without_space(entry.line.text) != "".join(_without_space(w.text) for w in entry.words)):
        return reject("date_words_not_complete")
    if any(not _contains(entry.line.bbox, word.bbox) for word in entry.words):
        return reject("date_word_outside_parent")
    centers = [(word.bbox.x0 + word.bbox.x1)/2 for word in entry.words]
    if any(a >= b for a, b in zip(centers, centers[1:])):
        return reject("date_word_order_ambiguous")
    parsed = _complete_date(entry.line.text)
    word_date = _complete_date(" ".join(word.text for word in entry.words))
    if parsed is None or parsed != word_date:
        return reject("unsupported_or_invalid_complete_date")
    association["parsed_date"] = parsed.isoformat()
    loc = fact.pdf_location
    if parsed != fact.period_end or parsed.year != loc.year:
        return reject("complete_date_differs_from_period_end")
    year_index = len(entry.words)-1
    association["year_word_index"] = year_index
    if (not re.fullmatch(r"[0-9]{4}", entry.words[year_index].text) or
            year["parent_lines"][0]["selected_word_indices"] != [year_index]):
        return reject("year_is_not_the_unique_date_year_token")
    # A year associated with this date must remain inside its registered header
    # and above the amount in the same column. No widening of the old threshold.
    amount_center = (loc.amount_bbox.x0 + loc.amount_bbox.x1)/2
    if (not _contains(loc.period_bbox, loc.year_bbox) or
            not loc.year_bbox.x0 <= amount_center <= loc.year_bbox.x1 or
            loc.amount_bbox.top < loc.period_bbox.bottom):
        return reject("date_and_amount_are_not_in_the_same_column")
    # Re-identify every guarded fragment by its real parent and token indices.
    # Equal text elsewhere is not enough to explain an observation away.
    guarded = []
    for line_index, candidate in enumerate(page.lines):
        if (line_index != parent_index and
                re.search(r"[0-9()+\-−.,%]", candidate.line.text) and
                _near_numeric_cell(candidate.line.bbox, loc.year_bbox)):
            # A nearby line with a missing word projection is still evidence;
            # it cannot disappear just because token_page() has no words for it.
            return reject("nearby_numeric_parent_outside_complete_date")
        for word_index, word in enumerate(candidate.words):
            if (not _selected(word.bbox, loc.year_bbox) and
                    re.search(r"[0-9()+\-−.,%]", word.text) and
                    _near_numeric_cell(word.bbox, loc.year_bbox)):
                if line_index != parent_index or word_index >= year_index:
                    return reject("nearby_numeric_fragment_outside_complete_date")
                guarded.append(word.model_dump(mode="json"))
    if (not guarded or guarded != [g["observation"] for g in year["guard_observations"]]):
        return reject("numeric_guard_ownership_not_verified")
    association.update(applied=True, reason="complete_date_year_association",
                       consumed_guard_observations=deepcopy(year["guard_observations"]))
    year.update(status="consistent", reason="year_associated_with_complete_date",
                word_guards=[], guard_observations=[])
    states = {check["status"] for check in result["checks"]}
    result["status"] = ("conflict" if "conflict" in states else
                        "insufficient" if "insufficient" in states else "consistent")
    return result
