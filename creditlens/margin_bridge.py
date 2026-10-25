"""Source-bound symmetric gross-margin decomposition, not a causal model."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Context, Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .table_lookup import parse_lookup_amount, TableLookupError

MARGIN_BRIDGE_VERSION = "margin-bridge-1"
DECIMAL_PRECISION = 80
DISPLAY_PLACES = 6
RESIDUAL_TOLERANCE_PP = Decimal("0.000000000000000000000001")
EXPECTED_SCOPE = {"consolidation": "consolidated", "statement_basis": "as_reported_statement_total", "operations_partition": "unknown"}
LIMITATIONS = [
    "This is a symmetric accounting decomposition of two reported product/service categories, not a causal explanation, forecast or cross-company ranking.",
    "Products and services are revenue classifications, not operating segments. Original category labels and definitions remain issuer-specific.",
    "Category gross profit is derived as revenue minus cost; only the supplied total gross profit is treated as a disclosed amount.",
    "Classification and source consistency are checked against caller-supplied registered metadata. The pure function does not authenticate sources, read PDFs, evaluate OCR or authorize users.",
    "Earlier-year amounts are the comparative column in the same supplied report; no historical-as-of availability or revision-free history is established.",
    "Missing or unavailable amounts are never zero-filled. Currency/unit conversion, negative-cost reinterpretation and 52/53-week annualization are not performed.",
    "Displayed effects are percentage points, not growth percentages. Full-precision and display-rounding residuals are separate.",
    "Software agreement and arithmetic reconciliation do not establish independent human gold or automatically approve any fact.",
]
Id = Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[A-Za-z0-9_.:-]+$")]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=1000)]
Amount = Annotated[str, Field(pattern=r"^[+-]?[0-9]{1,36}(?:\.[0-9]{1,12})?$")]
DateText = Annotated[str, Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]


class MarginBridgeError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Gross-margin bridge input is invalid"):
        self.code, self.path = code, path
        super().__init__(message)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def nonblank_strings(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("Blank metadata is unsupported")
        return value


class _Source(_Strict):
    id: Id
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    sha256: Sha


class _PeriodDates(_Strict):
    start: DateText
    end: DateText


class _Period(_PeriodDates):
    days: int = Field(ge=1, le=1000)
    kind: Text
    fiscal_calendar: Text


class _Box(_Strict):
    x0: float = Field(ge=0, le=20000, allow_inf_nan=False)
    top: float = Field(ge=0, le=20000, allow_inf_nan=False)
    x1: float = Field(gt=0, le=20000, allow_inf_nan=False)
    bottom: float = Field(gt=0, le=20000, allow_inf_nan=False)


class _Location(_Strict):
    page: int = Field(ge=1, le=20000)
    locator: Text
    amount_bbox: _Box | None


class _Fact(_Strict):
    id: Id
    label: Text
    raw_value: Annotated[str, Field(min_length=1, max_length=100)] | None
    value: Amount | None
    value_status: Literal["reported", "not_disclosed", "not_applicable", "unreadable"]
    source_id: Id
    source_sha256: Sha
    unit: Text
    period: _PeriodDates
    location: _Location


class _Category(_Strict):
    id: Id
    label: Text
    definition: Text
    revenue: _Fact
    cost: _Fact


class _Scope(_Strict):
    consolidation: Text
    statement_basis: Text
    operations_partition: Text


class _Classification(_Strict):
    basis: Text
    definition: Text


class _Statement(_Strict):
    year: int = Field(ge=1900, le=2199)
    issuer_id: Id
    source_id: Id
    source_sha256: Sha
    unit: Text
    period: _Period
    scope: _Scope
    classification: _Classification
    categories: list[_Category] = Field(max_length=8)
    total_revenue: _Fact
    total_cost: _Fact
    gross_profit: _Fact


class _Input(_Strict):
    schema_version: int
    id: Id
    issuer_id: Id
    company: Text
    source: _Source
    statements: list[_Statement] = Field(min_length=2, max_length=2)


def _digest(value):
    try:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise MarginBridgeError("invalid_input", "input") from None


def _decimal_text(value):
    return "0" if value == 0 else format(value, "f")


def _display(value):
    rounded = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return "0.000000" if rounded == 0 else format(rounded, "f")


def _facts(statement):
    for category in statement["categories"]:
        for role in ("revenue", "cost"):
            yield category[role], category["id"] + "_" + role
    for role in ("total_revenue", "total_cost", "gross_profit"):
        yield statement[role], role


def _annual(period):
    try:
        start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    except ValueError:
        return False, None, None
    days = (end-start).days + 1
    expected = {"calendar_year": {365, 366}, "week_52_53": {364, 371}}
    valid = (period["kind"] == "annual" and period["days"] == days
             and days in expected.get(period["fiscal_calendar"], set()))
    return valid, start, end


def evaluate_margin_bridge(payload: dict) -> dict:
    """Validate one issuer's pair, then decompose with isolated Decimal precision."""
    if type(payload) is not dict:
        raise MarginBridgeError("invalid_input", "input")
    input_sha = _digest(payload)
    try:
        data = _Input.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        raise MarginBridgeError("invalid_input", "input") from None
    if data["schema_version"] != 1:
        raise MarginBridgeError("unsupported_version", "schema_version")
    try:
        url = urlsplit(data["source"]["url"])
        source_valid = url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None
    except ValueError:
        source_valid = False
    if not source_valid:
        raise MarginBridgeError("invalid_source_url", "source.url")
    result = {"schema_version": 1, "rule_version": MARGIN_BRIDGE_VERSION, "id": data["id"],
              "issuer_id": data["issuer_id"], "company": data["company"], "source": deepcopy(data["source"]),
              "input_sha256": input_sha, "status": "blocked", "checks": [], "reasons": [], "warnings": [],
              "period_results": [], "components": [], "calculation": None, "inputs": deepcopy(data), "input_refs": [],
              "formula": {"category_margin": "m_i,t = (revenue_i,t - cost_i,t) / revenue_i,t",
                          "category_weight": "w_i,t = revenue_i,t / total_revenue_t",
                          "mix_effect_pp": "100 * sum((w_i,1-w_i,0) * (m_i,1+m_i,0) / 2)",
                          "within_effect_pp": "100 * sum((m_i,1-m_i,0) * (w_i,1+w_i,0) / 2)",
                          "residual_pp": "100 * (gross_margin_1-gross_margin_0) - mix_effect_pp - within_effect_pp"},
              "human_gold": False, "causal_explanation": False, "cross_company_ranking": False,
              "automatic_approval": False, "limitations": list(LIMITATIONS)}

    def check(code, passed, message, ids=()):
        item = {"code": code, "passed": bool(passed), "message": message, "input_ids": list(ids)}
        result["checks"].append(item)
        if not passed:
            result["reasons"].append(deepcopy(item))

    numbers, all_ids, annuals = {}, [], []
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        for statement in data["statements"]:
            facts = list(_facts(statement)); ids = [f["id"] for f, _ in facts]
            all_ids.extend(ids)
            check("issuer_mismatch", statement["issuer_id"] == data["issuer_id"], "两期必须属于同一家公司。", ids)
            check("source_mismatch", statement["source_id"] == data["source"]["id"] and statement["source_sha256"] == data["source"]["sha256"],
                  "年度列必须属于同一版登记报告。", ids)
            check("unit_mismatch", statement["unit"] == "USD_million", "只处理登记的百万美元，不自动换算币种或量级。", ids)
            check("scope_mismatch", statement["scope"] == EXPECTED_SCOPE, "仅支持同一合并报表列示总额，经营分解保持未知。", ids)
            valid, start, end = _annual(statement["period"]); annuals.append((valid, start, end))
            check("invalid_annual_period", valid, "起止日期、天数与全年日历必须一致。", ids)
            check("year_period_mismatch", end is not None and end.year == statement["year"], "年度标签必须对应期间结束所在年。", ids)
            category_ids = [c["id"] for c in statement["categories"]]
            check("category_inventory_mismatch", len(category_ids) == 2 and set(category_ids) == {"products", "services"},
                  "必须完整且唯一地登记产品与服务两类，不能补类或合并重复项。", ids)
            check("classification_changed", statement["classification"]["basis"] == "product_service_revenue_classification",
                  "这里只分解产品和服务收入分类，不将其当作经营分部。", ids)
            for category in statement["categories"]:
                check("category_label_mismatch", all(category[role]["label"] == category["label"] for role in ("revenue", "cost")),
                      "分类标签必须与收入和成本原行名一致，不能把两类事实互换。", [category["revenue"]["id"], category["cost"]["id"]])
            for fact, role in facts:
                fid = fact["id"]
                result["input_refs"].append({"fact_id": fid, "year": statement["year"], "role": role,
                                              "source_sha256": fact["source_sha256"], "location": deepcopy(fact["location"])})
                check("fact_source_mismatch", fact["source_id"] == data["source"]["id"] and fact["source_sha256"] == data["source"]["sha256"],
                      "每个金额都必须绑定同一报告来源。", [fid])
                check("fact_unit_mismatch", fact["unit"] == statement["unit"], "金额单位必须与年度列一致。", [fid])
                check("fact_period_mismatch", fact["period"] == {k: statement["period"][k] for k in ("start", "end")},
                      "金额起止日期必须与年度列一致。", [fid])
                box = fact["location"]["amount_bbox"]
                available = (fact["value_status"] == "reported" and fact["raw_value"] is not None and fact["value"] is not None
                             and box is not None and box["x0"] < box["x1"] and box["top"] < box["bottom"])
                check("fact_unavailable", available, "只有带原文金额和定位的已披露值可计算；缺失不能填零。", [fid])
                if not available:
                    continue
                number = Decimal(fact["value"])
                check("raw_currency_mismatch", not any(marker in fact["raw_value"] for marker in ("¥", "￥")),
                      "显式货币标记不得与登记美元单位冲突；美元符号本身也不独立证明币种。", [fid])
                try:
                    raw = Decimal(parse_lookup_amount(fact["raw_value"]))
                    same = raw == number and (raw != 0 or raw.is_signed() == number.is_signed())
                except TableLookupError:
                    same = False
                check("raw_value_mismatch", same, "完整金额原文与带符号数值必须一致，不取绝对值或修补符号。", [fid])
                numbers[fid] = number
            if all(fid in numbers for fid in ids):
                revenue = sum((numbers[c["revenue"]["id"]] for c in statement["categories"]), Decimal(0))
                cost = sum((numbers[c["cost"]["id"]] for c in statement["categories"]), Decimal(0))
                reported_r = numbers[statement["total_revenue"]["id"]]
                reported_c = numbers[statement["total_cost"]["id"]]
                reported_g = numbers[statement["gross_profit"]["id"]]
                check("revenue_total_mismatch", revenue == reported_r, "两类收入之和必须精确等于披露总收入，不用舍入容差遮盖金额差。", ids)
                check("cost_total_mismatch", cost == reported_c, "两类成本之和必须精确等于披露总成本。", ids)
                check("gross_profit_mismatch", reported_r-reported_c == reported_g, "披露总收入减总成本必须精确等于披露毛利。", ids)
                check("nonpositive_total_revenue", reported_r > 0, "总收入必须大于零。", [statement["total_revenue"]["id"]])
                for category in statement["categories"]:
                    check("nonpositive_category_revenue", numbers[category["revenue"]["id"]] > 0,
                          "每类收入必须大于零，零收入不能定义该类毛利率。", [category["revenue"]["id"]])
                    check("negative_cost_unsupported", numbers[category["cost"]["id"]] >= 0,
                          "本契约不解释负成本，不会自动取绝对值。", [category["cost"]["id"]])
                check("negative_cost_unsupported", reported_c >= 0, "披露总成本不能在本契约中被自动改符号。", [statement["total_cost"]["id"]])

        check("duplicate_fact_id", len(all_ids) == len(set(all_ids)), "两个年度全部事实 ID 必须唯一，不能复用同一事实代替另一期。", all_ids)
        base, current = data["statements"]
        check("nonconsecutive_years", current["year"] == base["year"]+1, "输入必须按基期、本期排序，且年度连续。", all_ids)
        check("noncontiguous_periods", all(a[0] for a in annuals) and (annuals[1][1]-annuals[0][2]).days == 1,
              "两期必须为相邻完整年度，不能重叠或遗漏期间。", all_ids)
        same_calendar = base["period"]["fiscal_calendar"] == current["period"]["fiscal_calendar"]
        check("calendar_mismatch", same_calendar, "不能混用日历年与周历年。", all_ids)
        length_ok = same_calendar and (base["period"]["fiscal_calendar"] == "calendar_year" or base["period"]["days"] == current["period"]["days"])
        check("annual_length_mismatch", length_ok, "周历年长度必须相同；本规则不将53周换算成52周。", all_ids)
        check("classification_changed", base["classification"] == current["classification"], "两年分类定义必须完全一致。", all_ids)
        base_categories = {c["id"]: c for c in base["categories"]}
        current_categories = {c["id"]: c for c in current["categories"]}
        for cid in ("products", "services"):
            old, new = base_categories.get(cid), current_categories.get(cid)
            check("category_definition_changed", old is not None and new is not None and old["definition"] == new["definition"] and old["label"] == new["label"],
                  "同类的原名与定义必须跨年一致；不能用总额相同掩盖分类变更。", all_ids)
        if result["reasons"]:
            return result
        if base["period"]["days"] != current["period"]["days"]:
            result["warnings"].append({"code": "calendar_day_difference", "message": "两期日历年度相差一天；使用披露全年金额，不作按日年化。", "input_ids": all_ids})

        computed = []
        for statement in data["statements"]:
            total_r = numbers[statement["total_revenue"]["id"]]
            total_c = numbers[statement["total_cost"]["id"]]
            total_g = numbers[statement["gross_profit"]["id"]]
            categories, internal = [], {}
            for category in sorted(statement["categories"], key=lambda c: c["id"]):
                revenue, cost = numbers[category["revenue"]["id"]], numbers[category["cost"]["id"]]
                gross, margin, weight = revenue-cost, (revenue-cost)/revenue, revenue/total_r
                ids = [category["revenue"]["id"], category["cost"]["id"], statement["total_revenue"]["id"]]
                internal[category["id"]] = {"margin": margin, "weight": weight, "input_ids": ids, "label": category["label"]}
                categories.append({"id": category["id"], "label": category["label"], "revenue": _decimal_text(revenue), "cost": _decimal_text(cost),
                                   "gross_profit_derived": _decimal_text(gross), "margin": _decimal_text(margin), "margin_percent": _decimal_text(margin*100),
                                   "weight": _decimal_text(weight), "weight_percent": _decimal_text(weight*100), "input_ids": ids})
                if gross < 0:
                    result["warnings"].append({"code": "negative_category_margin", "message": "该分类成本高于收入，保留负毛利率参与会计分解。", "input_ids": ids})
            computed.append({"margin": total_g/total_r, "categories": internal})
            result["period_results"].append({"year": statement["year"], "period": deepcopy(statement["period"]), "unit": statement["unit"],
                "total_revenue": _decimal_text(total_r), "total_cost": _decimal_text(total_c), "gross_profit_reported": _decimal_text(total_g),
                "gross_profit_computed": _decimal_text(total_r-total_c), "gross_margin": _decimal_text(total_g/total_r),
                "gross_margin_percent": _decimal_text(total_g/total_r*100), "categories": categories,
                "display": {"gross_margin_percent": _display(total_g/total_r*100),
                            "categories": [{"id": c["id"], "label": c["label"], "margin_percent": _display(Decimal(c["margin_percent"])),
                                            "weight_percent": _display(Decimal(c["weight_percent"]))} for c in categories]},
                "reconciliation_residuals": {"revenue": "0", "cost": "0", "gross_profit": "0"}})
        mix, within = Decimal(0), Decimal(0)
        display_components = []
        for cid in ("products", "services"):
            old, new = computed[0]["categories"][cid], computed[1]["categories"][cid]
            dw, dm = new["weight"]-old["weight"], new["margin"]-old["margin"]
            am, aw = (new["margin"]+old["margin"])/2, (new["weight"]+old["weight"])/2
            contribution_mix, contribution_within = dw*am*100, dm*aw*100
            mix += contribution_mix; within += contribution_within
            result["components"].append({"category_id": cid, "label": old["label"],
                "base_weight": _decimal_text(old["weight"]), "current_weight": _decimal_text(new["weight"]),
                "base_margin": _decimal_text(old["margin"]), "current_margin": _decimal_text(new["margin"]),
                "delta_weight": _decimal_text(dw), "average_margin": _decimal_text(am), "delta_margin": _decimal_text(dm), "average_weight": _decimal_text(aw),
                "mix_contribution_pp": _decimal_text(contribution_mix), "within_contribution_pp": _decimal_text(contribution_within),
                "total_contribution_pp": _decimal_text(contribution_mix+contribution_within), "input_ids": old["input_ids"]+new["input_ids"]})
            display_components.append({"category_id": cid, "mix_contribution_pp": _display(contribution_mix),
                                       "within_contribution_pp": _display(contribution_within), "total_contribution_pp": _display(contribution_mix+contribution_within)})
        change = (computed[1]["margin"]-computed[0]["margin"])*100
        residual = change-mix-within
        check("rounding_residual_exceeded", abs(residual) <= RESIDUAL_TOLERANCE_PP, "全精度恒等式残差只能处于声明的Decimal舍入界内。", all_ids)
        if result["reasons"]:
            result["period_results"], result["components"] = [], []
            return result
        if computed[0]["margin"] < 0 or computed[1]["margin"] < 0:
            result["warnings"].append({"code": "negative_total_margin", "message": "披露总毛利为负，保持其原符号；分解不代表经营原因。", "input_ids": all_ids})
        result["calculation"] = {"base_year": base["year"], "current_year": current["year"],
            "base_margin_percent": _decimal_text(computed[0]["margin"]*100), "current_margin_percent": _decimal_text(computed[1]["margin"]*100),
            "total_change_pp": _decimal_text(change), "mix_effect_pp": _decimal_text(mix), "within_effect_pp": _decimal_text(within),
            "explained_change_pp": _decimal_text(mix+within), "residual_pp": _decimal_text(residual),
            "residual_tolerance_pp": _decimal_text(RESIDUAL_TOLERANCE_PP), "decimal_precision": DECIMAL_PRECISION,
            "display": {"decimal_places": DISPLAY_PLACES, "rounding": "ROUND_HALF_UP",
                "base_margin_percent": _display(computed[0]["margin"]*100), "current_margin_percent": _display(computed[1]["margin"]*100),
                "total_change_pp": _display(change), "mix_effect_pp": _display(mix), "within_effect_pp": _display(within),
                "explained_change_pp": _display(mix+within), "residual_pp": _display(residual),
                "displayed_residual_pp": _display(Decimal(_display(change))-Decimal(_display(mix))-Decimal(_display(within))),
                "components": display_components}}
        result["status"] = "computed"
        return result
