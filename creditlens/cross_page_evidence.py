"""A deterministic three-card CFO/revenue task, not general document reasoning."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

RULE_VERSION = "cross-page-evidence-1"
ROLES = ("cfo", "revenue", "consolidation_scope")
DECIMAL_PRECISION = 40
DECIMAL_PLACES = 6
EXPECTED_SCOPE = {"consolidation": "consolidated", "statement_basis": "as_reported_statement_total", "operations_partition": "unknown"}
LIMITATIONS = [
    "This fixed task links registered cards across different statement pages; it is not continuation-table reconstruction or general question understanding.",
    "Operating cash flow divided by revenue is an observed statement-line ratio, not a collection rate, cash-receipt rate, profit margin, causal explanation or prediction.",
    "Consolidated statement totals are used as reported. Continuing/discontinued-operation components remain unknown; this does not establish economic homogeneity beyond the registered scope.",
    "Card registration and source hashes do not establish source authenticity, OCR/model quality or independent human verification.",
    "Current material registration is supported; historical-as-of use is refused. Registration date is not the original publication date.",
    "Not disclosed, not applicable, unreadable and numeric zero are separate states. None of the first three is replaced with zero.",
    "The caller must supply a trusted frozen task and card catalog. This pure function checks their structure and relationships, not external authenticity or tenant authorization.",
]
Identifier = Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_.:-]+$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Short = Annotated[str, Field(min_length=1, max_length=500)]
DateString = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Role = Literal["cfo", "revenue", "consolidation_scope"]


class CrossPageEvidenceError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Cross-page evidence contract is invalid"):
        self.code, self.path = code, path
        super().__init__(message)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Period(_Strict):
    start: DateString
    end: DateString

    @model_validator(mode="after")
    def valid_dates(self):
        if date.fromisoformat(self.start) > date.fromisoformat(self.end):
            raise ValueError("Reversed period")
        return self


class _Scope(_Strict):
    consolidation: Short
    statement_basis: Short
    operations_partition: Short


class _Required(_Strict):
    cfo: Identifier
    revenue: Identifier
    consolidation_scope: Identifier


class _TaskAvailability(_Strict):
    mode: Literal["current_verified_materials", "historical_as_of"]
    as_of: DateString

    @field_validator("as_of")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value


class _Task(_Strict):
    schema_version: int
    id: Identifier
    title: Short
    issuer_id: Identifier
    report_sha256: Sha
    year: int = Field(ge=1900, le=2200)
    period: _Period
    unit: Literal["USD_million"]
    financial_scope: _Scope
    required_role_ids: _Required
    availability: _TaskAvailability


class _Source(_Strict):
    id: Identifier
    title: Short
    url: str = Field(min_length=8, max_length=1000, pattern=r"^https://[^\s]+$")


class _Box(_Strict):
    x0: int | float
    top: int | float
    x1: int | float
    bottom: int | float

    @model_validator(mode="after")
    def nondegenerate(self):
        if not (0 <= self.x0 < self.x1 <= 20000 and 0 <= self.top < self.bottom <= 20000):
            raise ValueError("Invalid registered rectangle")
        return self


class _Location(_Strict):
    page: int = Field(ge=1, le=20000)
    printed_page: str | None = Field(max_length=40)
    locator: Short
    page_width: int | float | None
    page_height: int | float | None
    amount_bbox: _Box | None
    year_bbox: _Box | None
    unit_bbox: _Box | None

    @model_validator(mode="after")
    def bounded_geometry(self):
        if (self.page_width is None) != (self.page_height is None):
            raise ValueError("Incomplete page dimensions")
        if self.page_width is not None and not (0 < self.page_width <= 20000 and 0 < self.page_height <= 20000):
            raise ValueError("Invalid page dimensions")
        for box in (self.amount_bbox, self.year_bbox, self.unit_bbox):
            if box is not None and (self.page_width is None or box.x1 > self.page_width or box.bottom > self.page_height):
                raise ValueError("Box is outside declared page")
        return self


class _Availability(_Strict):
    registered_at: DateString
    source_published_at: DateString | None
    source_date_basis: Literal["registered_source_date", "unknown"]
    historical_verified: bool

    @model_validator(mode="after")
    def dates_and_basis(self):
        date.fromisoformat(self.registered_at)
        if self.source_published_at is not None:
            date.fromisoformat(self.source_published_at)
        if (self.source_date_basis == "unknown") != (self.source_published_at is None):
            raise ValueError("Source-date basis disagrees with registered date")
        if self.historical_verified is not False:
            raise ValueError("This catalog cannot assert historical verification")
        return self


class _Provenance(_Strict):
    kind: Literal["registered_official_disclosure", "synthetic_control"]
    reference_method: Short
    independent_human_verified: bool
    model_output_verified: bool

    @model_validator(mode="after")
    def bounded_claims(self):
        if self.independent_human_verified is not False or self.model_output_verified is not False:
            raise ValueError("Unverified reference/model claims cannot be promoted")
        return self


class _Card(_Strict):
    schema_version: int
    id: Identifier
    label: Short
    role: Role
    issuer_id: Identifier
    report_sha256: Sha
    year: int = Field(ge=1900, le=2200)
    period: _Period
    unit: str | None = Field(max_length=50)
    financial_scope: _Scope
    source: _Source
    value_status: Literal["disclosed", "not_disclosed", "not_applicable", "unreadable", "text_evidence"]
    raw_value: str | None = Field(max_length=100)
    normalized_value: str | None = Field(max_length=40)
    location: _Location
    availability: _Availability
    provenance: _Provenance


def _is_json(value, ancestors=None, depth=0):
    if depth > 30:
        return False
    if value is None or type(value) in (str, int, bool):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in (list, dict):
        return False
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        return False
    ancestors.add(id(value))
    try:
        return (type(value) is not dict or all(type(k) is str for k in value)) and all(
            _is_json(v, ancestors, depth+1) for v in (value.values() if type(value) is dict else value))
    finally:
        ancestors.remove(id(value))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _validate(model, value, path):
    try:
        parsed = model.model_validate(value)
        if getattr(parsed, "schema_version", 1) != 1:
            raise ValueError("Unknown schema")
        return parsed
    except (ValueError, TypeError, ValidationError):
        raise CrossPageEvidenceError("invalid_contract", path) from None


def _amount(card):
    """Parse a complete bounded decimal; never discard currency/sign characters."""
    raw, normalized = card.raw_value, card.normalized_value
    if raw is None or normalized is None:
        return None, "missing_numeric_value"
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,6})?", normalized):
        return None, "invalid_decimal_value"
    text = raw.strip()
    if text.startswith("USD "):
        text = text[4:]
    elif text.startswith("$"):
        text = text[1:]
    parenthesized = text.startswith("(") and text.endswith(")")
    if parenthesized:
        text = text[1:-1]
    elif text.startswith("-"):
        text = text[1:]
    # A single leading minus and parentheses are distinct complete grammars.
    negative = parenthesized or raw.strip().removeprefix("USD ").removeprefix("$").startswith("-")
    numeric = r"(?:0|[1-9][0-9]{0,23}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,7})(?:\.[0-9]{1,6})?"
    if not re.fullmatch(numeric, text):
        return None, "invalid_raw_amount"
    parsed = Decimal(("-" if negative else "") + text.replace(",", ""))
    value = Decimal(normalized)
    if parsed != value or parsed.is_signed() != value.is_signed():
        return None, "raw_normalized_mismatch"
    return value, "amount_consistent"


def evaluate_evidence_selection(task: dict, cards: list[dict], selected_ids: list[str]) -> dict:
    """Evaluate only the registered CFO/revenue card task, without side effects."""
    if type(task) is not dict or type(cards) is not list or len(cards) > 64 or type(selected_ids) is not list or len(selected_ids) > 16:
        raise CrossPageEvidenceError("invalid_contract", "$")
    if not _is_json([task, cards, selected_ids]):
        raise CrossPageEvidenceError("invalid_json_value", "$")
    if any(type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", value) for value in selected_ids):
        raise CrossPageEvidenceError("invalid_selection", "selected_ids")
    parsed_task = _validate(_Task, task, "task")
    parsed_cards = [_validate(_Card, value, f"cards[{i}]") for i, value in enumerate(cards)]
    if len({c.id for c in parsed_cards}) != len(parsed_cards):
        raise CrossPageEvidenceError("duplicate_catalog_id", "cards")
    required = parsed_task.required_role_ids.model_dump()
    if len(set(required.values())) != 3:
        raise CrossPageEvidenceError("invalid_required_roles", "task.required_role_ids")
    try:
        hashes = {"task_sha256": _hash(task), "cards_sha256": _hash(cards), "selection_sha256": _hash(selected_ids)}
    except (ValueError, TypeError, UnicodeError, OverflowError):
        raise CrossPageEvidenceError("invalid_json_value", "$") from None
    by_id = {c.id: c for c in parsed_cards}
    chosen = [by_id[cid] for cid in selected_ids if cid in by_id]
    counts = Counter(c.role for c in chosen)
    missing_roles = [role for role in ROLES if counts[role] == 0]
    checks = []

    def check(identifier, passed, code, message, ids=None):
        checks.append({"id": identifier, "passed": bool(passed), "code": "passed" if passed else code,
                       "message": message, "card_ids": list(ids if ids is not None else selected_ids)})

    check("unique_selection", len(set(selected_ids)) == len(selected_ids), "duplicate_selection", "每张证据卡只能选择一次。")
    check("known_selection", len(chosen) == len(selected_ids), "unknown_selected_card", "只能选择当前目录中已登记的卡片。")
    check("required_roles", len(chosen) == 3 and all(counts[role] == 1 for role in ROLES), "missing_or_extra_role", "经营现金流、营业收入、合并范围三个角色必须各一张。")
    check("registered_selection", len(chosen) == 3 and all(c.id == required[c.role] for c in chosen), "wrong_registered_card", "每个角色必须使用任务绑定的期望卡片。")
    check("catalog_requirements", all(cid in by_id and by_id[cid].role == role for role, cid in required.items()), "required_card_unavailable", "任务绑定的三项参考必须存在且角色正确。", list(required.values()))
    check("current_mode", parsed_task.availability.mode == "current_verified_materials", "historical_mode_unsupported", "当前仅支持登记版本，不支持历史时点回看。", [])
    as_of = date.fromisoformat(parsed_task.availability.as_of)
    check("ended_period", date.fromisoformat(parsed_task.period.end) <= as_of, "future_period", "报告期间必须已经结束。", [])
    check("supported_scope", parsed_task.financial_scope.model_dump() == EXPECTED_SCOPE, "unsupported_task_scope", "任务仅采用合并报表原行总额，持续及终止经营分解仍未知。", [])
    values = {}
    for card in chosen:
        prefix = card.id
        check(prefix + ":issuer", card.issuer_id == parsed_task.issuer_id, "issuer_mismatch", "证据须属于同一主体。", [card.id])
        check(prefix + ":report", card.report_sha256 == parsed_task.report_sha256, "report_version_mismatch", "证据须属于同一报告字节版本。", [card.id])
        check(prefix + ":period", card.year == parsed_task.year and card.period == parsed_task.period, "period_mismatch", "财年标签及实际起止日期须一致。", [card.id])
        check(prefix + ":scope", card.financial_scope == parsed_task.financial_scope, "financial_scope_mismatch", "证据的合并范围与报表原行口径须一致。", [card.id])
        known_date = card.availability.source_published_at
        available = date.fromisoformat(card.availability.registered_at) <= as_of and (known_date is None or date.fromisoformat(known_date) <= as_of)
        check(prefix + ":availability", available, "future_material", "材料登记日及已知披露日期不能晚于任务截止日。", [card.id])
        chronology = known_date is None or date.fromisoformat(known_date) >= date.fromisoformat(card.period.end)
        check(prefix + ":publication_period", chronology, "publication_precedes_period", "已知披露日期不能早于其年度金额覆盖的期末。", [card.id])
        if card.role == "consolidation_scope":
            text_ok = (card.value_status == "text_evidence" and card.raw_value is None and card.normalized_value is None and card.unit is None
                       and all(getattr(card.location, name) is None for name in ("amount_bbox", "year_bbox", "unit_bbox")))
            check(prefix + ":scope_reference", text_ok, "invalid_scope_evidence", "范围卡须为有定位的文字证据，不得充当数值或自行补金额。", [card.id])
        else:
            check(prefix + ":unit", card.unit == parsed_task.unit, "unit_mismatch", "金额须使用同一币种和计量单位。", [card.id])
            disclosed = card.value_status == "disclosed"
            check(prefix + ":value_state", disclosed, "value_" + card.value_status, "未披露、不适用、无法识别与零分别记录，只有明确披露值参与计算。", [card.id])
            complete = all(getattr(card.location, name) is not None for name in ("page_width", "page_height", "amount_bbox", "year_bbox", "unit_bbox"))
            check(prefix + ":location", complete, "incomplete_numeric_location", "金额、年度、单位框及页面尺寸都须有登记定位。", [card.id])
            value, reason = _amount(card) if disclosed else (None, "numeric_state_unavailable")
            check(prefix + ":amount", value is not None, reason, "原始金额的完整符号与归一十进制值须一致。", [card.id])
            if value is not None:
                values[card.role] = value
    cfo_cards, revenue_cards = [c for c in chosen if c.role == "cfo"], [c for c in chosen if c.role == "revenue"]
    different_pages = len(cfo_cards) == len(revenue_cards) == 1 and cfo_cards[0].location.page != revenue_cards[0].location.page
    check("different_statement_pages", different_pages, "not_cross_page", "两张金额卡须来自不同报表页；本任务不声称理解跨页续表。")
    positive_denominator = "revenue" in values and values["revenue"] > 0
    check("positive_revenue", positive_denominator, "nonpositive_or_unavailable_revenue", "营业收入必须为已披露的正数，零或负数不作为本任务分母。", [c.id for c in revenue_cards])
    calculation = None
    if all(item["passed"] for item in checks):
        with localcontext() as ctx:
            ctx.prec = DECIMAL_PRECISION
            percentage = values["cfo"] / values["revenue"] * Decimal(100)
            rounded = percentage.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        inputs = {role: {"card_id": card.id, "raw_value": card.raw_value, "normalized_value": card.normalized_value, "unit": card.unit}
                  for role, card in (("cfo", cfo_cards[0]), ("revenue", revenue_cards[0]))}
        calculation = {"formula": "cfo / revenue * 100", "inputs": inputs, "decimal_precision": DECIMAL_PRECISION,
                       "decimal_places": DECIMAL_PLACES, "rounding": "ROUND_HALF_UP", "unrounded_percentage": format(percentage, "f"),
                       "result": {"value": format(rounded, ".6f"), "unit": "percent"},
                       "interpretation": "经营现金流净额占营业收入比例；仅观察同期间合并报表原行。"}
    reference_fields = ("id", "label", "role", "issuer_id", "report_sha256", "year", "period", "unit", "financial_scope", "source",
                        "value_status", "raw_value", "normalized_value", "location", "availability", "provenance")
    originals = {card["id"]: card for card in cards}
    return {"schema_version": 1, "rule_version": RULE_VERSION, "task_id": parsed_task.id,
            "status": "computed" if calculation is not None else "refused", **hashes,
            "selected_ids": list(selected_ids), "required_role_ids": required, "missing_roles": missing_roles, "checks": checks,
            "errors": [{"check_id": c["id"], "code": c["code"], "card_ids": list(c["card_ids"])} for c in checks if not c["passed"]],
            "references": [{key: deepcopy(originals[c.id][key]) for key in reference_fields} for c in chosen],
            "calculation": calculation, "read_only": True, "automatic_approval": False, "independently_verified": False,
            "model_quality_verified": False, "limitations": list(LIMITATIONS)}
