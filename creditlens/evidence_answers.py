"""Structured evidence answers over registered annual-report figures.

The caller supplies an explicit plan (issuer, metric, fiscal year, kind, requested
unit and scope). This layer never reads question text. It projects registered
observations, reuses the research comparability rule for duration figures
(``research_materials._annual_change``), applies the matching instant rule for
balance-sheet figures, keeps Microsoft/Apple side by side only and converts USD
scale exactly. Anything not registered is reported as missing, never filled.

The browser demo runs a TypeScript port of ``answer_plan`` over the pack built by
``build_answer_pack``; ``scripts/export_answer_parity.py`` checks both agree.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import json
from pathlib import Path

from .diligence import digest, displayed_amount
from .research_materials import _REGISTRY, _annual_change

ANSWER_RULE_VERSION = "evidence-answer-1"
ROOT = Path(__file__).resolve().parents[1]
YEARS = (2024, 2025)
CURRENCY = "USD"
# Requested USD scale -> power of ten relative to one US dollar.
SCALES = {"one": 0, "thousand": 3, "ten_thousand": 4, "million": 6, "hundred_million": 8, "billion": 9}
REGISTERED_SCALE = "million"
KINDS = ("lookup", "change", "comparable_check", "cross_company")
SCOPES = ("consolidated", "parent_only")

ISSUERS = {
    "MSFT": {"company": "Microsoft Corporation", "name_zh": "微软", "document": "Microsoft FY2025 Form 10-K",
             "fiscal_calendar": "calendar_year", "fiscal_year_end": "6 月 30 日"},
    "AAPL": {"company": "Apple Inc.", "name_zh": "苹果", "document": "Apple FY2025 Form 10-K",
             "fiscal_calendar": "week_52_53", "fiscal_year_end": "9 月最后一个周六"},
}
# metric id -> (Chinese label, kind, statement)
METRICS = {
    "revenue": ("营业收入", "duration", "income"),
    "product_revenue": ("产品收入", "duration", "income"),
    "service_revenue": ("服务收入", "duration", "income"),
    "cost_of_revenue": ("营业成本", "duration", "income"),
    "product_cost": ("产品成本", "duration", "income"),
    "service_cost": ("服务成本", "duration", "income"),
    "gross_profit": ("毛利", "duration", "income"),
    "gross_margin_pct": ("毛利率", "percent", "income"),
    "product_gross_margin_pct": ("产品毛利率", "percent", "income"),
    "service_gross_margin_pct": ("服务毛利率", "percent", "income"),
    "net_income": ("净利润", "duration", "cash_flow"),
    "cfo": ("经营活动现金流净额", "duration", "cash_flow"),
    "capex": ("固定资产购建现金流出", "duration", "cash_flow"),
    "short_debt": ("短期债务", "instant", "balance"),
    "current_debt": ("一年内到期的长期债务", "instant", "balance"),
    "long_debt": ("长期债务（非流动）", "instant", "balance"),
    "current_assets": ("流动资产合计", "instant", "balance"),
    "current_liabilities": ("流动负债合计", "instant", "balance"),
    "liquid_assets": ("现金、现金等价物与短期投资合计", "instant", "balance"),
    "cash_equivalents": ("现金及现金等价物", "instant", "balance"),
    "short_investments": ("流动有价证券", "instant", "balance"),
}
METRIC_NOTES = {
    "capex": "仅为现金流量表中购建固定资产的现金支出行，不代表全部资本投入（不含无形资产、并购或租赁）；原文为括号负值，按登记规则转为流出绝对额。",
    "short_debt": "微软为 Short-term debt；苹果为 Commercial paper（商业票据）。两家行名不同，只能分别查看。",
    "current_debt": "微软为 Current portion of long-term debt；苹果为流动负债中的 Term debt。",
    "long_debt": "非流动部分：微软 Long-term debt；苹果为非流动负债中的 Term debt。不含一年内到期部分。",
    "liquid_assets": "微软资产负债表的合计行，包含短期投资；未单列现金及现金等价物。",
    "product_revenue": "产品与服务是利润表中的收入分类，不是经营分部。",
    "service_revenue": "产品与服务是利润表中的收入分类，不是经营分部。微软行名为 Service and other。",
    "product_cost": "产品与服务是利润表中的成本分类，不是经营分部。",
    "service_cost": "产品与服务是利润表中的成本分类，不是经营分部。",
    "gross_margin_pct": "登记计算：毛利 ÷ 营业收入，来自毛利率分解（margin-bridge-1）。",
    "product_gross_margin_pct": "登记计算：(产品收入 − 产品成本) ÷ 产品收入；分类毛利由收入减成本推导。",
    "service_gross_margin_pct": "登记计算：(服务收入 − 服务成本) ÷ 服务收入；分类毛利由收入减成本推导。",
}
_PDF_METRICS = ("revenue", "net_income", "cfo", "capex", "short_debt", "current_debt", "long_debt",
                "current_assets", "current_liabilities", "liquid_assets", "cash_equivalents", "short_investments")
_MARGIN_METRICS = {("revenue", "products"): "product_revenue", ("revenue", "services"): "service_revenue",
                   ("cost", "products"): "product_cost", ("cost", "services"): "service_cost",
                   ("cost", "total"): "cost_of_revenue", ("gross_profit", "total"): "gross_profit"}
_PERCENT = {"gross_margin_pct": ("total", ("gross_profit", "revenue")),
            "product_gross_margin_pct": ("products", ("product_revenue", "product_cost")),
            "service_gross_margin_pct": ("services", ("service_revenue", "service_cost"))}
LIMITATIONS = [
    "只回答两份 FY2025 年报中登记的 FY2024、FY2025 年度合并报表行；未登记的公司、年度、季度、分部、母公司报表和指标不补值。",
    "数值、页码与定位框来自仓库保存的记录；原页预览是按保存识别片段绘制的重建示意，不是原页图像。",
    "两期变化先通过可比性检查再计算；这是描述性计算，未经分析员采用，不写入研究底稿，也不是原因或预测。",
    "微软与苹果不是登记的严格同行，财年起止日和日历不同，只并列展示，不排名、不相减。",
    "只在美元内精确换算量级；未登记汇率，不换算为人民币或其他币种。",
]


class AnswerError(ValueError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def plain(value: Decimal) -> str:
    """Canonical decimal text shared with the TypeScript port: no exponent, no trailing zeros."""
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "0") else text


def _gap(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def _load(root: Path, rel: str) -> dict:
    return json.loads((root/rel).read_text(encoding="utf-8"))


def _registry(issuer_id: str) -> dict:
    return next(entry for entry in _REGISTRY if entry["issuer_id"] == issuer_id)


def _scope_evidence(entry: dict, page: int, row_label: str, metric: str) -> list[dict]:
    common = {"source_sha256": entry["sha256"], "verification": "manual_source_review_2026-10-25"}
    return [
        {**common, "field": "consolidation_scope", "value": "consolidated", "page": entry["scope_page"],
         "quote": entry["scope_quote"], "note": entry["scope_note"]},
        {**common, "field": "operations_scope", "value": "reported_consolidated_statement_total", "page": page,
         "quote": row_label, "note": "合并报表中该行的列示总额；不推断持续/终止经营分解。"},
        {**common, "field": "capex_components", "value": "ppe_cash_payments" if metric == "capex" else "not_applicable",
         "page": page, "quote": row_label,
         "note": "仅固定资产现金流出行。" if metric == "capex" else "该行不是资本开支。"},
    ]


def _period(fact_start, fact_end, calendar: str, kind: str) -> dict:
    if kind == "instant":
        return {"start": None, "end": fact_end, "days": None, "fiscal_calendar": calendar, "kind": "instant"}
    days = (date.fromisoformat(fact_end) - date.fromisoformat(fact_start)).days + 1
    return {"start": fact_start, "end": fact_end, "days": days, "fiscal_calendar": calendar, "kind": "annual"}


def _box(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {k: round(float(value[k]), 2) for k in ("x0", "top", "x1", "bottom")}


def _observation(issuer: str, metric: str, year: int, *, raw: str, normalized: str, transformation: str,
                 period: dict, page: int, printed_page, row_label: str, label_en: str, locator: str,
                 amount_bbox, unit_header: str | None, unit_bbox, evidence: dict, ocr: dict) -> dict:
    entry = _registry(issuer)
    sha = entry["sha256"]
    return {
        "id": f"{issuer}:{metric}:{year}", "fact_id": f"{metric}-{year}", "issuer_id": issuer,
        "company": ISSUERS[issuer]["company"], "metric": metric, "label": METRICS[metric][0], "row_label": label_en,
        "year": year, "kind": METRICS[metric][1], "raw_value": raw,
        "raw_signed_value": plain(displayed_amount(raw)), "normalized_value": normalized,
        "unit": "USD_million", "transformation": transformation, "source_version_id": f"{issuer}:{sha}",
        "period": period,
        "basis": {"consolidation_scope": "consolidated", "operations_scope": "reported_consolidated_statement_total",
                  "operations_partition": "unknown",
                  "capex_components": "ppe_cash_payments" if metric == "capex" else "not_applicable",
                  "measurement_basis": "as_presented_in_FY2025_report", "definition": row_label,
                  "scope_evidence": _scope_evidence(entry, page, row_label, metric)},
        "source": {"document": ISSUERS[issuer]["document"], "url": entry["url"], "sha256": sha, "page": page,
                   "printed_page": printed_page, "locator": locator, "amount_bbox": _box(amount_bbox),
                   "unit_header": unit_header, "unit_bbox": _box(unit_bbox)},
        "evidence": evidence, "ocr": ocr,
    }


def _role_cases(root: Path) -> dict:
    report = _load(root, "artifacts/role-table-lookup-report.json")
    units = {c["id"]: c["methods"]["role"]["unit_check"] for c in _load(root, "artifacts/unit-applicability-report.json")["cases"]}
    cases = {}
    for document in report["documents"]:
        for case in document["cases"]:
            role = case["methods"]["role"]
            extraction, score = role["extraction"], role["score"]
            span = None
            if extraction["status"] == "selected":
                amounts = extraction["evidence"]["amounts"]
                chosen = next(a for a in amounts if a["span_index"] == extraction["selected_span_index"]) \
                    if any(a["span_index"] == extraction["selected_span_index"] for a in amounts) else None
                if chosen is not None:
                    span = {"text": chosen["span"]["text"], "bbox": _box(chosen["span"]["bbox"]),
                            "confidence": chosen["span"]["confidence"]}
            unit_check = units.get(case["id"], {})
            cases[case["id"]] = {
                "method": "role_layout", "outcome": score["outcome"], "reason": extraction["reason"],
                "raw_value": extraction.get("raw_value"), "span": span,
                "unit_text": (extraction.get("unit") or {}).get("raw_text"),
                "unit_check": unit_check.get("status"),
            }
    return cases


def _printed_page(issuer: str, page: int, root: Path):
    if issuer != "AAPL":
        return None
    provenance = _load(root, "data/examples/aapl-pdf-provenance.json")
    pages = dict(zip(provenance["selected_physical_pages"], provenance["selected_printed_pages"]))
    return pages.get(page)


def build_answer_catalog(root: Path = ROOT) -> dict:
    """Every registered annual observation the answer layer may state, with its evidence."""
    root = Path(root)
    role = _role_cases(root)
    observations = []
    for issuer, prefix in (("MSFT", "msft"), ("AAPL", "aapl")):
        entry = _registry(issuer)
        pack = _load(root, entry["path"])
        if digest(pack) != entry["pack_sha256"]:
            raise AnswerError("material_hash_mismatch", "登记事实包已改变。")
        calendar = pack.get("fiscal_calendar") or "calendar_year"
        for fact in pack["facts"]:
            metric, year = fact["metric"], int(fact["id"].rsplit("-", 1)[1])
            if metric not in _PDF_METRICS or year not in YEARS:
                continue
            loc = fact["pdf_location"]
            kind = METRICS[metric][1]
            ocr = role[f"{prefix}:{fact['id']}"]
            observations.append(_observation(
                issuer, metric, year, raw=fact["raw_value"], normalized=fact["value"],
                transformation=fact["transformation"],
                period=_period(fact["period_start"], fact["period_end"], calendar, kind),
                page=loc["page"], printed_page=_printed_page(issuer, loc["page"], root), row_label=loc["row_label"],
                label_en=loc["row_label"], locator=fact["locator"], amount_bbox=loc["amount_bbox"],
                unit_header=loc["unit_header"], unit_bbox=loc["unit_bbox"],
                evidence={"method": "pdf_text_layer", "parser_version": loc["parser_version"],
                          "period_header": loc["period_header"], "row_label_bbox": _box(loc["row_label_bbox"]),
                          "year_bbox": _box(loc["year_bbox"]), "reference": "registered_fact_pack"},
                ocr=ocr))
    margin = _load(root, "data/examples/margin-source-observations.json")
    for document in margin["documents"]:
        issuer = document["issuer_id"]
        periods = {p["year"]: p for p in document["periods"]}
        for fact in document["facts"]:
            metric = _MARGIN_METRICS.get((fact["role"], fact["category"]))
            if metric is None or fact["year"] not in YEARS:
                continue
            p = periods[fact["year"]]
            parent = fact["amount_parent"]
            observations.append(_observation(
                issuer, metric, fact["year"], raw=fact["raw_value"], normalized=fact["normalized_value"],
                transformation="displayed_magnitude",
                period=_period(p["start"], p["end"], p["fiscal_calendar"], "duration"),
                page=fact["page"], printed_page=_printed_page(issuer, fact["page"], root),
                row_label=f"{fact['label']} ({fact['role']})", label_en=fact["label"], locator=fact["locator"],
                amount_bbox=fact["amount_bbox"], unit_header=fact["unit_locator"]["text"],
                unit_bbox=fact["unit_locator"]["bbox"],
                evidence={"method": "ocr_parent_span_ai_assisted_transcription", "bbox_role": fact["bbox_role"],
                          "period_header": fact["date_locator"]["text"], "row_label_bbox": _box(fact["row_label"]["bbox"]),
                          "year_bbox": _box(fact["year_locator"]["bbox"]), "reference": "margin_source_observations"},
                ocr={"method": "ocr_parent_span", "outcome": "registered_span", "reason": "ai_assisted_transcription",
                     "raw_value": fact["raw_value"],
                     "span": {"text": parent["text"], "bbox": _box(parent["bbox"]), "confidence": parent["confidence"]},
                     "unit_text": fact["unit_locator"]["text"], "unit_check": None}))
    report = _load(root, "artifacts/margin-bridge-report.json")
    by_id = {o["id"]: o for o in observations}
    percents = []
    for case in report["cases"]:
        result = case["result"]
        issuer = result["issuer_id"]
        for period in result["period_results"]:
            year = period["year"]
            for metric, (category, inputs) in _PERCENT.items():
                if category == "total":
                    exact = period["gross_margin_percent"]
                else:
                    exact = next(c["margin_percent"] for c in period["categories"] if c["id"] == category)
                refs = [by_id[f"{issuer}:{m}:{year}"]["id"] for m in inputs]
                percents.append({
                    "id": f"{issuer}:{metric}:{year}", "issuer_id": issuer, "company": ISSUERS[issuer]["company"],
                    "metric": metric, "label": METRICS[metric][0], "year": year, "kind": "percent",
                    "unit": "percent", "exact_percent": exact,
                    "percent_6dp": format(Decimal(exact).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f"),
                    "percent_2dp": format(Decimal(exact).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), "f"),
                    "period": {"start": period["period"]["start"], "end": period["period"]["end"],
                               "days": period["period"]["days"], "fiscal_calendar": period["period"]["fiscal_calendar"],
                               "kind": "annual"},
                    "input_ids": refs, "rule_version": result["rule_version"], "report_case": case["id"],
                })
    observations.sort(key=lambda o: (o["issuer_id"], o["metric"], o["year"]))
    percents.sort(key=lambda o: (o["issuer_id"], o["metric"], o["year"]))
    return {"rule_version": ANSWER_RULE_VERSION, "observations": observations, "percents": percents}


def _scaled(value: str, scale: str) -> str:
    return plain(Decimal(value).scaleb(SCALES[REGISTERED_SCALE] - SCALES[scale]))


def _instant_change(previous: dict, current: dict) -> dict:
    """Balance-sheet counterpart of research_materials._annual_change (same row shape)."""
    candidates = [previous["id"], current["id"]]
    reasons, warnings = [], []
    row = {
        "issuer_id": current["issuer_id"], "company": current["company"], "metric": current["metric"],
        "from_year": previous["year"], "to_year": current["year"], "input_ids": list(candidates),
        "candidate_observation_ids": candidates, "status": "blocked", "reasons": reasons, "warnings": warnings,
        "absolute_change": None, "growth_rate": None, "growth_rate_status": "blocked",
        "formula": {"absolute_change": "current.normalized_value - previous.normalized_value",
                    "growth_rate": "absolute_change / previous.normalized_value"}, "unit": current["unit"],
    }
    for field in ("issuer_id", "metric", "unit", "source_version_id", "transformation"):
        if previous[field] != current[field]:
            reasons.append({"code": f"{field}_mismatch", "message": f"两期的 {field} 不一致。"})
    for field in ("consolidation_scope", "operations_scope", "capex_components", "measurement_basis", "definition"):
        values = [x["basis"].get(field) for x in (previous, current)]
        if any(v in (None, "", "unknown") for v in values):
            reasons.append({"code": "unknown_basis", "message": f"{field} 没有可核验口径。"})
        elif values[0] != values[1]:
            reasons.append({"code": "basis_mismatch", "message": f"两期的 {field} 不一致。"})
    p, c = previous["period"], current["period"]
    if p["kind"] != "instant" or c["kind"] != "instant":
        reasons.append({"code": "period_kind_mismatch", "message": "时点余额只能与时点余额比较。"})
    else:
        pe, ce = date.fromisoformat(p["end"]), date.fromisoformat(c["end"])
        if current["year"] != previous["year"] + 1 or ce <= pe:
            reasons.append({"code": "annual_order_mismatch", "message": "两期必须为相邻财年末，按时间先后排列。"})
        elif p["fiscal_calendar"] != c["fiscal_calendar"]:
            reasons.append({"code": "calendar_mismatch", "message": "两期必须使用相同年度日历。"})
        elif p["fiscal_calendar"] == "calendar_year":
            if (pe.month, pe.day) != (ce.month, ce.day):
                reasons.append({"code": "year_end_mismatch", "message": "两期财年末的月日不同。"})
        elif p["fiscal_calendar"] == "week_52_53":
            if (ce - pe).days not in (364, 371):
                reasons.append({"code": "year_end_mismatch", "message": "周历财年末之间须相隔 52 或 53 周。"})
        else:
            reasons.append({"code": "unknown_calendar", "message": "年度日历未经支持。"})
    if reasons:
        return row
    with localcontext() as ctx:
        ctx.prec = 40
        base, value = Decimal(previous["normalized_value"]), Decimal(current["normalized_value"])
        change = value - base
        row.update(status="computed", absolute_change=format(change, "f"))
        if base > 0:
            row.update(growth_rate=format((change / base).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f"),
                       growth_rate_status="computed")
        else:
            row["growth_rate_status"] = "not_computed_nonpositive_base"
            warnings.append({"code": "nonpositive_base", "message": "基期为零或负值，仅展示绝对变化，不给常规增长率。"})
    return row


def _cross_reasons(first: dict, second: dict) -> list[dict]:
    reasons = [_gap("not_strict_peer_benchmark", "微软/苹果为跨公司流程样例，未建立严格同行比较契约，不排名、不相减。")]
    if first["metric"] != second["metric"]:
        reasons.append(_gap("metric_mismatch", "两项不是同一指标。"))
    p, q = first["period"], second["period"]
    if (p["start"], p["end"]) != (q["start"], q["end"]):
        reasons.append(_gap("actual_period_mismatch", "相同 FY 标签不代表相同实际期间；两项的起止日或时点不同。"))
    if (p["fiscal_calendar"], p["days"]) != (q["fiscal_calendar"], q["days"]):
        reasons.append(_gap("annual_calendar_mismatch", "日历年度与周历年度长度不同，不自动年化。"))
    if first.get("row_label") != second.get("row_label") and first["kind"] != "percent":
        reasons.append(_gap("line_definition_differs", "两家报表行名不同，定义未经逐项对齐。"))
    return reasons


def _normalize_plan(plan) -> dict:
    if type(plan) is not dict or set(plan) - {"kind", "items", "unit", "scope"}:
        raise AnswerError("invalid_plan", "计划只接受 kind、items、unit、scope。")
    kind, items = plan.get("kind"), plan.get("items")
    if kind not in KINDS or type(items) is not list or len(items) != (1 if kind == "lookup" else 2):
        raise AnswerError("invalid_plan", "查询类型或所选项数量不符合固定契约。")
    clean = []
    for item in items:
        if (type(item) is not dict or set(item) != {"issuer_id", "metric", "year"} or type(item["issuer_id"]) is not str
                or type(item["metric"]) is not str or type(item["year"]) is not int):
            raise AnswerError("invalid_plan", "每项须为公司、指标和整数年度。")
        clean.append(dict(item))
    unit = plan.get("unit")
    if unit is not None and (type(unit) is not dict or set(unit) != {"currency", "scale"}
                             or type(unit["currency"]) is not str or unit["scale"] not in (*SCALES, None)):
        raise AnswerError("invalid_plan", "单位须为币种与量级。")
    scope = plan.get("scope", "consolidated")
    if scope not in SCOPES:
        raise AnswerError("invalid_plan", "口径只支持合并或母公司。")
    return {"kind": kind, "items": clean, "unit": deepcopy(unit), "scope": scope}


def _project(observation: dict | None, percent: dict | None, unit: dict | None) -> dict:
    if percent is not None:
        return {"observation_id": percent["id"], "kind": "percent", "percent_2dp": percent["percent_2dp"],
                "percent_6dp": percent["percent_6dp"], "requested": None}
    requested = None
    if unit is not None and unit["scale"] is not None and unit["scale"] != REGISTERED_SCALE:
        requested = {"scale": unit["scale"], "value": _scaled(observation["normalized_value"], unit["scale"])}
    return {"observation_id": observation["id"], "kind": observation["kind"], "raw_value": observation["raw_value"],
            "normalized_value": observation["normalized_value"], "unit": observation["unit"], "requested": requested}


def answer_plan(catalog: dict, plan) -> dict:
    """Answer one explicit plan from the catalog; deterministic and read-only."""
    plan = _normalize_plan(plan)
    observations = {o["id"]: o for o in catalog["observations"]}
    percents = {o["id"]: o for o in catalog["percents"]}
    gaps, selected, found = [], [], []
    unit = plan["unit"]
    for item in plan["items"]:
        key = f"{item['issuer_id']}:{item['metric']}:{item['year']}"
        hit_o, hit_p = observations.get(key), percents.get(key)
        if item["issuer_id"] not in ISSUERS:
            gaps.append(_gap("issuer_not_registered", f"演示没有登记 {item['issuer_id']} 的年报。"))
        elif item["metric"] not in METRICS:
            gaps.append(_gap("metric_not_registered", f"演示没有登记指标 {item['metric']}。"))
        elif item["year"] not in YEARS:
            gaps.append(_gap("year_not_registered", f"FY{item['year']} 未登记；演示只有 FY2024、FY2025 年度列。"))
        elif hit_o is None and hit_p is None:
            gaps.append(_gap("observation_not_registered",
                             f"{ISSUERS[item['issuer_id']]['name_zh']}没有登记“{METRICS[item['metric']][0]}”这一行；不补值，也不代表公司未披露。"))
        selected.append({"selector": dict(item), "status": "registered" if (hit_o or hit_p) else "not_registered"})
        found.append((hit_o, hit_p))
    if plan["scope"] == "parent_only":
        gaps.append(_gap("scope_not_registered", "只登记了合并报表；未登记母公司（单体）报表，不能用合并数代替。"))
    if unit is not None and unit["currency"] != CURRENCY:
        gaps.append(_gap("currency_not_registered", f"报表以美元列示，未登记汇率，不能换算为 {unit['currency']}。"))
    if unit is not None and any(p is not None for _, p in found):
        gaps.append(_gap("unit_not_applicable", "毛利率是百分比，不能用金额单位表示。"))
    result = {"schema_version": 1, "rule_version": ANSWER_RULE_VERSION, "plan": plan, "selected": selected,
              "values": [], "comparison": None, "cross_company": None, "status": None, "gaps": gaps,
              "read_only": True, "adopted_by_analyst": False, "ranking": None}
    if gaps:
        codes = {g["code"] for g in gaps}
        result["status"] = ("scope_unavailable" if "scope_not_registered" in codes else
                            "unit_unavailable" if codes & {"currency_not_registered", "unit_not_applicable"} and
                            all(s["status"] == "registered" for s in selected) else "not_registered")
        return result
    result["values"] = [_project(o, p, unit) for o, p in found]
    if plan["kind"] == "lookup":
        result["status"] = "answered"
        return result
    (first_o, first_p), (second_o, second_p) = found
    first, second = first_o or first_p, second_o or second_p
    if first["id"] == second["id"]:
        result["status"] = "blocked"
        result["gaps"] = [_gap("duplicate_selection", "两期不能重复选择同一项。")]
        return result
    if first["issuer_id"] != second["issuer_id"] or plan["kind"] == "cross_company":
        if first["issuer_id"] == second["issuer_id"]:
            result["status"] = "blocked"
            result["gaps"] = [_gap("same_issuer", "跨公司并列需要两家不同公司。")]
            return result
        result["status"] = "side_by_side_only"
        result["cross_company"] = {"reasons": _cross_reasons(first, second), "ranking": None, "difference": None}
        return result
    if first["metric"] != second["metric"]:
        result["status"] = "blocked"
        result["gaps"] = [_gap("metric_mismatch", "不同指标不能作为同一指标的基期和本期。")]
        return result
    if first["kind"] == "percent":
        if [first["year"], second["year"]] != [2024, 2025]:
            result["status"] = "blocked"
            result["gaps"] = [_gap("annual_order_mismatch", "两期必须为相邻财年，按时间先后排列。")]
            return result
        with localcontext() as ctx:
            ctx.prec = 90
            delta = Decimal(second["exact_percent"]) - Decimal(first["exact_percent"])
        result["comparison"] = {"status": "computed", "metric": first["metric"], "from_year": first["year"],
                                "to_year": second["year"], "reasons": [], "warnings": [],
                                "change_pp_6dp": format(delta.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN), "f"),
                                "change_pp_2dp": format(delta.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), "f"),
                                "formula": "current.percent - previous.percent"}
    elif first["kind"] == "instant":
        result["comparison"] = _instant_change(first, second)
    else:
        result["comparison"] = _annual_change(first, second, {first["id"], second["id"]})
    row = result["comparison"]
    if row["status"] != "computed":
        result["status"] = "blocked"
        result["gaps"] = [_gap(r["code"], r["message"]) for r in row["reasons"]]
        return result
    if first["kind"] != "percent":
        row["growth_rate_percent"] = None if row["growth_rate"] is None else plain(Decimal(row["growth_rate"]).scaleb(2))
        row["requested_change"] = None
        if unit is not None and unit["scale"] not in (None, REGISTERED_SCALE):
            row["requested_change"] = {"scale": unit["scale"], "value": _scaled(row["absolute_change"], unit["scale"])}
    result["status"] = "computed"
    return result


def build_answer_pack(root: Path = ROOT, page_spans: dict | None = None) -> dict:
    """Everything the browser engine needs; built and checked by scripts/build_browser_pack.py."""
    catalog = build_answer_catalog(root)
    pack = {
        "schema_version": 1, "rule_version": ANSWER_RULE_VERSION, "years": list(YEARS), "currency": CURRENCY,
        "registered_scale": REGISTERED_SCALE, "scales": dict(SCALES),
        "issuers": deepcopy(ISSUERS),
        "metrics": {k: {"label": v[0], "kind": v[1], "statement": v[2], "note": METRIC_NOTES.get(k)} for k, v in METRICS.items()},
        "observations": catalog["observations"], "percents": catalog["percents"],
        "limitations": list(LIMITATIONS), "pages": page_spans or {},
    }
    pack["catalog_sha256"] = digest({"observations": pack["observations"], "percents": pack["percents"]})
    return pack
