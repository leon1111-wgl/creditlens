"""Check registered company margin statements against a saved accounting case.

Only structured numeric statements and direction are checked. Prose, drivers,
causality and source authenticity are not inferred from arithmetic agreement.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Context, Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .margin_bridge import evaluate_margin_bridge, MarginBridgeError

MARGIN_CLAIMS_VERSION = "margin-claims-1"
DECIMAL_PRECISION = 80
STATUSES = ("matched_numeric", "matched_direction", "scope_unavailable", "contradiction", "invalid")
LIMITATIONS = [
    "This checks pre-registered structured numbers and directions; it does not interpret arbitrary text, perform NLI or extract claims from documents.",
    "A matched number means compatibility at the disclosed precision, not exact equality of a rounded statement and an unrounded amount.",
    "Amounts, relative percentage growth and percentage-point rate changes are different quantities and are never substituted for each other.",
    "Company drivers and qualifiers are preserved as company statements, not independently verified causes or allocations to accounting decomposition components.",
    "Microsoft Cloud is outside the supplied total/products/services classifications. A nearby total margin cannot substitute for that scope.",
    "The source hash identifies the registered capture; it does not establish raw HTTP bytes, source authenticity, historic availability or independence from the financial report.",
    "Exact fields mean unrounded arithmetic at the declared 80-digit Decimal precision, not an infinitely precise rational representation.",
    "The caller must provide registered claims and a verified saved case. This function rechecks internal arithmetic and bindings, not tenant permissions or the source's natural-language content.",
]
Text = Annotated[str, Field(min_length=1, max_length=2000)]
Id = Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_.:-]+$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Number = Annotated[str, Field(pattern=r"^[+-]?[0-9]{1,36}(?:\.[0-9]{1,12})?$")]
DateText = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class MarginClaimsError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Margin statement contract is invalid"):
        self.code, self.path = code, path
        super().__init__(message)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def no_blank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Blank strings are unsupported")
        return value


class _Period(_Strict):
    start: DateText
    end: DateText


class _Periods(_Strict):
    base: _Period
    current: _Period


class _ClaimSource(_Strict):
    id: Id
    url: Text
    sha256: Sha
    locator: Text


class _Qualifiers(_Strict):
    primary: bool
    partial_offset: bool


class _Driver(_Strict):
    role: Literal["driver", "partial_offset"]
    text_zh: Text


class _Disclosure(_Strict):
    kind: Literal["delta_amount", "growth_percent", "current_percent", "base_percent", "delta_pp"]
    value: Number
    unit: Literal["USD_billion", "USD_million", "percent", "percentage_point"]
    decimal_places: int = Field(ge=0, le=1)


class _Claim(_Strict):
    id: Id
    issuer_id: Id
    report_sha256: Sha
    base_year: int = Field(ge=1900, le=2199)
    current_year: int = Field(ge=1900, le=2199)
    periods: _Periods
    scope: Literal["consolidated_total", "products", "services", "microsoft_cloud"]
    metric: Literal["gross_profit_amount", "gross_margin_rate"]
    claim_type: Literal["company_explanation", "numeric_only"]
    source: _ClaimSource
    paraphrase_zh: Text
    qualifiers: _Qualifiers
    drivers: list[_Driver] = Field(max_length=12)
    direction: Literal["increase", "decrease", "unchanged"] | None
    disclosures: list[_Disclosure] = Field(max_length=5)


class _Case(_Strict):
    input: dict
    result: dict


class _Input(_Strict):
    schema_version: int
    margin_case: _Case
    claims: list[_Claim] = Field(min_length=1, max_length=32)


def _digest(value):
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise MarginClaimsError("invalid_input", "input") from None


def _text(number):
    return "0" if number == 0 else format(number, "f")


def _url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and parsed.password is None
    except ValueError:
        return False


def _direction(number):
    return "increase" if number > 0 else "decrease" if number < 0 else "unchanged"


def _actual(result, scope):
    periods = []
    ids = []
    for summary, statement in zip(result["period_results"], result["inputs"]["statements"]):
        if scope == "consolidated_total":
            amount, revenue, rate = summary["gross_profit_reported"], summary["total_revenue"], summary["gross_margin"]
            refs = [statement[k]["id"] for k in ("total_revenue", "total_cost", "gross_profit")]
        else:
            category = next(c for c in summary["categories"] if c["id"] == scope)
            amount, revenue, rate = category["gross_profit_derived"], category["revenue"], category["margin"]
            refs = list(category["input_ids"])
        periods.append({"year": summary["year"], "period": deepcopy(summary["period"]),
                        "gross_profit_amount": amount, "revenue": revenue, "gross_margin_rate": rate,
                        "gross_margin_percent": _text(Decimal(rate)*100), "input_ids": refs})
        ids.extend(refs)
    before, after = (Decimal(p["gross_profit_amount"]) for p in periods)
    delta_amount = after-before
    return {"scope": scope, "amount_unit": "USD_million", "base": periods[0], "current": periods[1],
            "delta_amount": _text(delta_amount),
            "growth_percent": _text(delta_amount/before*100) if before > 0 else None,
            "delta_rate_pp": _text((Decimal(periods[1]["gross_margin_rate"])-Decimal(periods[0]["gross_margin_rate"]))*100),
            "input_ids": list(dict.fromkeys(ids)), "decimal_precision": DECIMAL_PRECISION}


def _numeric(actual, disclosure):
    kind, unit, places = disclosure["kind"], disclosure["unit"], disclosure["decimal_places"]
    values = {"delta_amount": actual["delta_amount"], "growth_percent": actual["growth_percent"],
              "current_percent": actual["current"]["gross_margin_percent"],
              "base_percent": actual["base"]["gross_margin_percent"], "delta_pp": actual["delta_rate_pp"]}
    formulas = {"delta_amount": "current_gross_profit - base_gross_profit",
                "growth_percent": "(current_gross_profit - base_gross_profit) / base_gross_profit * 100; base > 0",
                "current_percent": "current_gross_profit / current_revenue * 100",
                "base_percent": "base_gross_profit / base_revenue * 100",
                "delta_pp": "(current_gross_margin_rate - base_gross_margin_rate) * 100"}
    check = {"kind": kind, "status": "invalid", "unit": unit, "decimal_places": places,
             "stated": disclosure["value"], "exact": None, "rounded": None, "matched": False,
             "formula": formulas[kind], "input_ids": actual["input_ids"], "rounding": "ROUND_HALF_UP",
             "reason": "nonpositive_growth_base"}
    if values[kind] is None:
        return check
    number = Decimal(values[kind])
    if kind == "delta_amount" and unit == "USD_billion":
        number /= 1000
        check["formula"] += "; USD_million / 1000 -> USD_billion"
    rounded = number.quantize(Decimal("1") if places == 0 else Decimal("0.1"), rounding=ROUND_HALF_UP)
    same = rounded == Decimal(disclosure["value"])
    check.update(status="matched" if same else "contradiction", exact=_text(number), rounded=format(rounded, "f"),
                 matched=same, reason="matched_at_disclosed_precision" if same else "rounded_value_mismatch")
    return check


def evaluate_margin_claims(payload: dict) -> dict:
    if type(payload) is not dict:
        raise MarginClaimsError("invalid_input", "input")
    input_sha = _digest(payload)
    try:
        data = _Input.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        raise MarginClaimsError("invalid_input", "input") from None
    if data["schema_version"] != 1:
        raise MarginClaimsError("unsupported_version", "schema_version")
    if len({c["id"] for c in data["claims"]}) != len(data["claims"]):
        raise MarginClaimsError("duplicate_claim_id", "claims")
    try:
        expected = evaluate_margin_bridge(data["margin_case"]["input"])
    except MarginBridgeError:
        raise MarginClaimsError("invalid_margin_case", "margin_case") from None
    if _digest(expected) != _digest(data["margin_case"]["result"]):
        raise MarginClaimsError("margin_case_mismatch", "margin_case")
    if expected["status"] != "computed":
        raise MarginClaimsError("margin_case_unavailable", "margin_case")
    base, current = expected["inputs"]["statements"]
    if (base["year"], current["year"]) != (2024, 2025):
        raise MarginClaimsError("unsupported_periods", "margin_case")
    results = []
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        for claim in data["claims"]:
            result = {"id": claim["id"], "status": "invalid", "claim": deepcopy(claim), "binding_checks": [],
                      "reason": None, "actual": None, "direction_check": None, "numeric_checks": [], "rate_display_comparison": None,
                      "support_scope": "company_statement_only", "independently_verified": False, "causal_explanation": False,
                      "linked_to_accounting_effects": False, "natural_language_understood": False, "automatic_approval": False}
            def check(code, passed, message):
                result["binding_checks"].append({"code": code, "passed": bool(passed), "message": message})
            check("issuer_mismatch", claim["issuer_id"] == expected["issuer_id"], "说明与财务数值必须属于同一家公司。")
            check("report_mismatch", claim["report_sha256"] == expected["source"]["sha256"], "说明所关联的报告版本必须一致。")
            check("year_mismatch", claim["base_year"] == base["year"] and claim["current_year"] == current["year"], "比较年度必须对应FY2024和FY2025。")
            check("period_mismatch", claim["periods"] == {"base": {k: base["period"][k] for k in ("start", "end")},
                                                          "current": {k: current["period"][k] for k in ("start", "end")}},
                  "完整财务期间必须一致，不能只凭年份标签配对。")
            check("invalid_source_url", _url(claim["source"]["url"]), "公司说明需要登记的HTTPS来源和原文定位。")
            driver = any(d["role"] == "driver" for d in claim["drivers"])
            offset = any(d["role"] == "partial_offset" for d in claim["drivers"])
            qualifier_valid = (claim["qualifiers"]["partial_offset"] == offset and (not claim["qualifiers"]["primary"] or driver))
            check("qualifier_driver_mismatch", qualifier_valid, "主要因素、抵消项及对应角色必须结构一致；这不验证转述语义。")
            check("claim_type_mismatch", (claim["claim_type"] == "company_explanation" and driver)
                  or (claim["claim_type"] == "numeric_only" and not claim["drivers"] and not any(claim["qualifiers"].values())),
                  "纯数值披露不能带公司驱动归因，解释必须保留结构化因素。")
            check("empty_claim", claim["direction"] is not None or bool(claim["disclosures"]), "本任务至少需要一个登记方向或数值。")
            check("duplicate_disclosure_kind", len({d["kind"] for d in claim["disclosures"]}) == len(claim["disclosures"]), "同一说明不任选多个同类披露值之一。")
            allowed = {"delta_amount", "growth_percent"} if claim["metric"] == "gross_profit_amount" else {"current_percent", "base_percent", "delta_pp"}
            for disclosure in claim["disclosures"]:
                check("disclosure_metric_mismatch", disclosure["kind"] in allowed, "毛利金额增长、毛利率和百分点变化不能互换。")
                valid_unit = ((disclosure["kind"] == "delta_amount" and disclosure["unit"] in {"USD_million", "USD_billion"})
                              or (disclosure["kind"] in {"growth_percent", "current_percent", "base_percent"} and disclosure["unit"] == "percent")
                              or (disclosure["kind"] == "delta_pp" and disclosure["unit"] == "percentage_point"))
                check("disclosure_unit_mismatch", valid_unit, "数值的金额、百分比或百分点单位必须与检查种类一致。")
                check("disclosure_precision_mismatch", Decimal(disclosure["value"]).as_tuple().exponent == -disclosure["decimal_places"],
                      "登记的小数位数须与披露数值字面精度一致，不自行增加精度。")
            failed = next((c for c in result["binding_checks"] if not c["passed"]), None)
            if failed:
                result["reason"] = {k: failed[k] for k in ("code", "message")}
                results.append(result)
                continue
            if claim["scope"] == "microsoft_cloud":
                result.update(status="scope_unavailable", reason={"code": "scope_not_registered", "message": "现有分类没有Microsoft Cloud对应数据，不能借全公司或服务毛利率配对。"})
                results.append(result)
                continue
            actual = _actual(expected, claim["scope"])
            result["actual"] = actual
            if claim["direction"] is not None:
                delta = actual["delta_amount"] if claim["metric"] == "gross_profit_amount" else actual["delta_rate_pp"]
                actual_direction = _direction(Decimal(delta))
                result["direction_check"] = {"expected": claim["direction"], "actual": actual_direction,
                                             "matched": claim["direction"] == actual_direction, "delta_exact": delta,
                                             "delta_unit": "USD_million" if claim["metric"] == "gross_profit_amount" else "percentage_point",
                                             "metric": claim["metric"]}
            result["numeric_checks"] = [_numeric(actual, d) for d in claim["disclosures"]]
            rate_values = {d["kind"]: d["value"] for d in claim["disclosures"] if d["kind"] in {"base_percent", "current_percent"}}
            if set(rate_values) == {"base_percent", "current_percent"}:
                result["rate_display_comparison"] = {"base_stated_percent": rate_values["base_percent"], "current_stated_percent": rate_values["current_percent"],
                    "stated_delta_pp": _text(Decimal(rate_values["current_percent"])-Decimal(rate_values["base_percent"])),
                    "exact_delta_pp": actual["delta_rate_pp"], "scope": "Difference between separately rounded disclosures is not the unrounded rate change."}
            if any(n["status"] == "invalid" for n in result["numeric_checks"]):
                result["reason"] = {"code": "nonpositive_growth_base", "message": "基期毛利金额不为正，不能用常规同比增长率核对。"}
            elif ((result["direction_check"] is not None and not result["direction_check"]["matched"])
                  or any(not n["matched"] for n in result["numeric_checks"])):
                result.update(status="contradiction", reason={"code": "arithmetic_disagrees", "message": "登记方向或披露精度下的数值与本次算术不一致；需回查范围、原文和登记。"})
            else:
                result.update(status="matched_numeric" if result["numeric_checks"] else "matched_direction",
                              reason={"code": "numeric_compatibility_only", "message": "结构化数值或方向相容；未证明公司解释、原因或会计分解归属。"})
            results.append(result)
    return {"schema_version": 1, "rule_version": MARGIN_CLAIMS_VERSION, "input_sha256": input_sha,
            "issuer_id": expected["issuer_id"], "report_sha256": expected["source"]["sha256"],
            "margin_case_sha256": _digest(data["margin_case"]), "results": results,
            "summary": {status: sum(r["status"] == status for r in results) for status in STATUSES},
            "limitations": list(LIMITATIONS), "automatic_approval": False}
