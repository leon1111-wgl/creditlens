"""Typed financial facts and deterministic review signals, never credit decisions."""
from __future__ import annotations
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
import hashlib
import json
import re


RULE_VERSION = "cash-debt-review-3"


def displayed_amount(raw: str) -> Decimal:
    """Parse the limited amount notation supported by the fact-pack contract.

    This checks internal consistency, not the authenticity of the source document.
    Ambiguous OCR, exponents, percent signs and broken comma grouping fail closed.
    """
    text = raw.strip()
    if text.startswith(("$", "¥", "￥")):
        text = text[1:].strip()
    parenthesized = text.startswith("(") and text.endswith(")")
    if parenthesized:
        text = text[1:-1].strip()
        if text.startswith(("-", "+")):
            raise ValueError("A parenthesized amount must not also contain a sign")
    if not re.fullmatch(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise ValueError("Ambiguous displayed amount; verify the original cell")
    value = Decimal(text.replace(",", ""))
    return -value if parenthesized else value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PdfBox(StrictModel):
    x0: float = Field(ge=0, allow_inf_nan=False)
    top: float = Field(ge=0, allow_inf_nan=False)
    x1: float = Field(gt=0, allow_inf_nan=False)
    bottom: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.x0 >= self.x1 or self.top >= self.bottom:
            raise ValueError("Empty or reversed PDF box")
        return self


class PdfSection(StrictModel):
    start_label: str = Field(min_length=1, max_length=100)
    end_label: str = Field(min_length=1, max_length=100)
    start_bbox: PdfBox
    end_bbox: PdfBox


class PdfLocation(StrictModel):
    page: int = Field(ge=1, le=20000)
    page_width: float = Field(gt=0, le=20000, allow_inf_nan=False)
    page_height: float = Field(gt=0, le=20000, allow_inf_nan=False)
    coordinate_system: Literal["pdf_points_top_left"]
    amount_bbox: PdfBox
    row_label: str = Field(min_length=1, max_length=300)
    row_label_bbox: PdfBox
    year: int = Field(ge=1900, le=2200)
    year_bbox: PdfBox
    period_header: str = Field(min_length=1, max_length=100)
    period_bbox: PdfBox
    unit_header: str = Field(min_length=1, max_length=100)
    unit_bbox: PdfBox
    parser_version: str = Field(min_length=1, max_length=100)
    section: PdfSection | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def within_page(self):
        regions = [self.amount_bbox, self.row_label_bbox, self.year_bbox, self.period_bbox, self.unit_bbox]
        if self.section:
            regions.extend([self.section.start_bbox, self.section.end_bbox])
            if not self.section.start_bbox.bottom < self.row_label_bbox.top < self.row_label_bbox.bottom < self.section.end_bbox.top:
                raise ValueError("PDF row is outside its declared section")
        for region in regions:
            if region.x1 > self.page_width or region.bottom > self.page_height:
                raise ValueError("PDF evidence box is outside the page")
        return self


class Source(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    published_at: date
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Fact(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    metric: Literal["liquid_assets", "cash_equivalents", "short_investments", "short_debt", "current_debt", "long_debt", "current_assets",
                    "current_liabilities", "cfo", "capex", "net_income", "revenue"]
    label: str = Field(min_length=1, max_length=100)
    value: Decimal = Field(max_digits=24, decimal_places=6, allow_inf_nan=False)
    unit: Literal["USD_million", "USD", "CNY_million"]
    period_start: date | None = None
    period_end: date
    source_id: str = Field(max_length=100)
    locator: str = Field(min_length=1, max_length=300)
    raw_value: str = Field(min_length=1, max_length=100)
    transformation: Literal["displayed_magnitude", "absolute_cash_outflow"] = "displayed_magnitude"
    pdf_location: PdfLocation | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def valid_period(self):
        if self.pdf_location is not None and self.pdf_location.year != self.period_end.year:
            raise ValueError("PDF year column disagrees with the fact period")
        original = displayed_amount(self.raw_value)
        if self.transformation == "absolute_cash_outflow":
            if self.metric != "capex":
                raise ValueError("Absolute cash-outflow normalization is only supported for capex")
            original = abs(original)
        if original != self.value:
            raise ValueError("Normalized value disagrees with the supplied displayed amount")
        flow = self.metric in {"cfo", "capex", "net_income", "revenue"}
        if flow != (self.period_start is not None):
            raise ValueError("Flow facts need a start date; balance-sheet facts are point-in-time")
        if self.period_start is not None and self.period_start > self.period_end:
            raise ValueError("Invalid financial period")
        if self.metric in {"capex", "liquid_assets", "cash_equivalents", "short_investments", "short_debt", "current_debt", "long_debt",
                           "current_assets", "current_liabilities"} and self.value < 0:
            raise ValueError("This metric must use a non-negative displayed magnitude")
        return self


class CasePack(StrictModel):
    schema_version: Literal[1] = 1
    entity_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    sources: list[Source] = Field(min_length=1, max_length=30)
    facts: list[Fact] = Field(min_length=1, max_length=300)
    fiscal_calendar: Literal["week_52_53"] | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def references(self):
        source_map = {s.id: s for s in self.sources}
        if len(source_map) != len(self.sources) or len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError("Duplicate source or fact identifier")
        for fact in self.facts:
            if fact.source_id not in source_map:
                raise ValueError("Fact references an unknown source")
            if fact.period_end > source_map[fact.source_id].published_at:
                raise ValueError("A report cannot disclose a period that has not ended")
        return self


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def analyze_pack(pack: CasePack, as_of: date, stress_pct: int = 0) -> dict:
    """Fixed, disclosed rules; no learned risk score and no lender calibration."""
    if type(stress_pct) is not int or not 0 <= stress_pct <= 80:
        raise ValueError("stress_pct must be an integer between 0 and 80")
    sources = {s.id: s for s in pack.sources}
    available = [f for f in pack.facts if sources[f.source_id].published_at <= as_of]
    latest = max((f.period_end for f in available), default=None)
    metrics, issues = [], []

    def issue(code, title, detail, evidence=(), blocking=False):
        issues.append({"id": code, "title": title, "detail": detail,
                       "fact_ids": [f.id for f in evidence],
                       "severity": "blocking" if blocking else "review"})

    if latest is None:
        issue("no_evidence", "截止日期前没有可用证据", "源文件的发布日期晚于截止日期，未进行计算。", blocking=True)
    current = [f for f in available if f.period_end == latest]
    groups = {}
    for fact in current:
        groups.setdefault(fact.metric, []).append(fact)
    required = {"short_debt", "current_debt", "cfo", "capex", "net_income"}
    usable = {}
    for metric, values in groups.items():
        if len(values) != 1:
            issue("conflict_" + metric, "同口径存在重复或冲突数值", metric + " 必须确认唯一有效版本。", values, True)
        else:
            usable[metric] = values[0]
    for missing in sorted(required - set(usable)):
        issue("missing_" + missing, "缺少必要财务事实", missing + " 未通过可用性检查。", blocking=True)
    components = ["cash_equivalents", "short_investments"]
    liquidity_inputs = []
    if "liquid_assets" in groups and any(n in groups for n in components):
        issue("ambiguous_liquidity_basis", "流动资金合计与分项同时存在",
              "请确认使用合计或完整分项，避免重复计入现金或短期投资。", current, True)
    elif "liquid_assets" in usable:
        liquidity_inputs = ["liquid_assets"]
    elif "liquid_assets" not in groups and all(n in usable for n in components):
        liquidity_inputs = components
    else:
        issue("missing_liquid_assets", "缺少必要财务事实",
              "需要唯一流动资金合计，或现金及现金等价物与流动投资两个分项；缺失不得填零。", blocking=True)

    def add_metric(key, title, names, formula, calculate, kind="ratio"):
        if any(name not in usable for name in names):
            return
        facts = [usable[name] for name in names]
        flow_periods = {(f.period_start, f.period_end) for f in facts if f.period_start is not None}
        if len({f.unit for f in facts}) != 1 or len(flow_periods) > 1:
            issue("basis_" + key, "单位或期间不一致", title + " 已被阻断，不能直接混算。", facts, True)
            return
        values = [f.value for f in facts]
        try:
            with localcontext() as ctx:
                ctx.prec = 28
                value = calculate(*values)
                if value is None or not value.is_finite():
                    raise ArithmeticError()
        except ArithmeticError:
            issue("undefined_" + key, "指标无法按当前公式解释", title + " 的分母或适用条件不满足。", facts, True)
            return
        metrics.append({"id": key, "title": title, "value": str(value), "kind": kind,
                        "unit": facts[0].unit if kind == "amount" else "ratio",
                        "formula": formula, "fact_ids": [f.id for f in facts],
                        "operands": [{"id": f.id, "value": str(f.value), "unit": f.unit} for f in facts]})

    if liquidity_inputs == ["liquid_assets"]:
        add_metric("liquidity", "流动资金 / 短期债务", liquidity_inputs + ["short_debt", "current_debt"],
                   "liquid_assets / (short_debt + current_debt)", lambda c, s, d: c / (s + d) if s + d > 0 else None)
    elif liquidity_inputs == components:
        add_metric("liquidity", "流动资金 / 短期债务", components + ["short_debt", "current_debt"],
                   "(cash_equivalents + short_investments) / (short_debt + current_debt)",
                   lambda c, i, s, d: (c + i) / (s + d) if s + d > 0 else None)
    add_metric("cash_conversion", "经营现金流 / 净利润", ["cfo", "net_income"],
               "cfo / net_income", lambda c, n: c / n if n > 0 else None)
    add_metric("fcf", "自由现金流（简化口径）", ["cfo", "capex"], "cfo - capex", lambda c, x: c - x, "amount")
    add_metric("capex_intensity", "资本开支 / 经营现金流", ["capex", "cfo"],
               "capex / cfo", lambda x, c: x / c if c > 0 else None)
    add_metric("current_ratio", "流动比率", ["current_assets", "current_liabilities"],
               "current_assets / current_liabilities", lambda a, l: a / l if l > 0 else None)
    if stress_pct:
        add_metric("stress_fcf", "经营现金流压力情景", ["cfo", "capex"],
                   f"cfo * (1 - {stress_pct}/100) - capex",
                   lambda c, x: c * (1 - Decimal(stress_pct) / 100) - x if c > 0 else None, "amount")
    computed = {m["id"]: Decimal(m["value"]) for m in metrics}
    if computed.get("capex_intensity", Decimal(0)) > Decimal("0.4"):
        issue("capex_pressure", "资本开支占用较多经营现金流",
              "资本开支 / 经营现金流超过演示规则 40%。需核查项目承诺、后续开支和融资安排；这不是违约判断。",
              [usable["capex"], usable["cfo"]])
    if "cash_conversion" in computed and computed["cash_conversion"] < 1:
        issue("cash_conversion_review", "利润现金转化需解释", "经营现金流低于净利润，需结合营运资本与一次性项目解释。",
              [usable["cfo"], usable["net_income"]])
    if "liquidity" in computed and computed["liquidity"] < 1:
        issue("liquidity_review", "所列流动资金不足以覆盖所列短债", "需核查现金可用性、其他授信及债务展期安排。",
              [usable[n] for n in liquidity_inputs + ["short_debt", "current_debt"]])
    issue("scope_review", "债务与现金可用性仍需人工确认",
          "本口径含现金、现金等价物和短期投资；债务覆盖不含全部租赁、担保、资本承诺和受限资金调整。公开披露不足以直接作授信决策。",
          [f for f in current if f.metric in {"liquid_assets", "cash_equivalents", "short_investments", "short_debt", "current_debt"}])
    comparison = []
    for f in usable.values():
        prior = [p for p in available if p.metric == f.metric and p.period_end < f.period_end]
        if not prior:
            continue
        prior_end = max(p.period_end for p in prior)
        candidates = [p for p in prior if p.period_end == prior_end]
        if len(candidates) != 1:
            continue
        p = candidates[0]
        comparable = p.unit == f.unit and (f.period_end - p.period_end).days in (365, 366)
        if pack.fiscal_calendar == "week_52_53":
            comparable = p.unit == f.unit and (f.period_end-p.period_end).days in (364, 371)
        if f.period_start and p.period_start:
            comparable &= abs((f.period_end-f.period_start).days - (p.period_end-p.period_start).days) <= 1
            if pack.fiscal_calendar == "week_52_53":
                comparable &= (f.period_end-f.period_start).days+1 in (364, 371)
                comparable &= (p.period_end-p.period_start).days+1 in (364, 371)
        if comparable:
            comparison.append({"metric": f.metric, "label": f.label, "current": str(f.value),
                               "prior": str(p.value), "unit": f.unit,
                               "growth": str((f.value-p.value)/abs(p.value)) if p.value != 0 else None,
                               "fact_ids": [p.id, f.id]})
    return {"rule_version": RULE_VERSION, "as_of": as_of.isoformat(),
            "period_end": latest.isoformat() if latest else None, "metrics": metrics,
            "issues": issues, "comparison": comparison, "excluded_future_facts": len(pack.facts)-len(available),
            "stress_pct": stress_pct, "blocking_count": sum(i["severity"] == "blocking" for i in issues),
            "limitations": ["Rules are illustrative and not fitted to lender outcomes.",
                            "Percentage cash-flow downside scenarios require a positive starting operating cash flow.",
                            "A source hash identifies a capture; it does not certify the source or its availability at a historical time.",
                            "No model inference, credit rating, PD or lending decision is produced by these calculations."]}
