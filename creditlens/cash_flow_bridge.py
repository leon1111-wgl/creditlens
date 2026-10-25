"""Exact, source-bound cash-flow reconciliations, not causal explanations."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re

from .diligence import displayed_amount


BRIDGE_RULE_VERSION = "cash-flow-accounting-bridge-1"
CONTRIBUTION_ROLES = ("net_income", "reconciliation_adjustments", "operating_assets_and_liabilities")
ALL_ROLES = (*CONTRIBUTION_ROLES, "cfo_total")
LIMITATIONS = [
    "会计桥仅核对登记表行的加总及年际变化；残差为零不证明材料真实性、完整提取能力或因果解释。",
    "一般调节项和经营资产负债区段沿用原报表分区，不把所有调节项称为非现金，也不把全部资产负债变化称为营运资本。",
    "FY2024 使用 FY2025 报告的比较列，不代表 FY2024 当时可得版本；没有历史时点或预测结论。",
    "公司之间不自动配对同名调节项，不排名，不计算贡献百分比，不进行现金流预测。",
    "附件使用服务端登记的原表行；会计核对不等于研究员逐项采用或独立复核批准。",
]


class CashFlowBridgeError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _digest(value) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise CashFlowBridgeError("invalid_source_json", "Cash-flow sources must be finite JSON data") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _number(value) -> Decimal:
    # Bound magnitude and decimal scale so the fixed local precision is exact.
    if type(value) is not str or not re.fullmatch(r"[+-]?\d{1,36}(?:\.\d{1,12})?", value):
        raise ValueError("Expected a bounded signed decimal string")
    number = Decimal(value)
    if not number.is_finite():
        raise ValueError("Amount must be finite")
    return number


def _text(value: Decimal) -> str:
    return "0" if value.is_zero() else format(value, "f")


def _reason(code, message, fact_id=None):
    result = {"code": code, "message": message}
    if fact_id is not None:
        result["fact_id"] = fact_id
    return result


def _period(period):
    if type(period) is not dict or set(period) != {"start", "end", "days", "fiscal_calendar", "kind"}:
        raise ValueError("Incomplete annual period")
    start, end = date.fromisoformat(period["start"]), date.fromisoformat(period["end"])
    if type(period["days"]) is not int or period["days"] != (end - start).days + 1 or end < start:
        raise ValueError("Annual day count does not match dates")
    if period["kind"] != "annual" or period["fiscal_calendar"] not in {"calendar_year", "week_52_53"}:
        raise ValueError("Unsupported annual calendar")
    lengths = (365, 366) if period["fiscal_calendar"] == "calendar_year" else (364, 371)
    if period["days"] not in lengths:
        raise ValueError("Period is not a full supported fiscal year")
    return start, end


def _statement(statement: dict, source: dict) -> dict:
    """Check one registered inventory without repairing, imputing or selecting rows."""
    fields = ("id", "issuer_id", "company", "year", "source_id", "source_sha256", "reported_cfo_observation_id",
              "unit", "period", "scope", "total_fact_id", "expected_row_keys")
    result = {key: deepcopy(statement.get(key)) for key in fields}
    result.update(status="blocked", rows=[], groups=[], computed_cfo=None, reported_cfo=None, residual=None,
                  input_ids=[], contribution_input_ids=[], reasons=[], warnings=[], automatic_approval=False,
                  formula={"computed_cfo": "sum(signed_value of every registered contribution row)",
                           "identity": "net_income + reconciliation_adjustments + operating_assets_and_liabilities = reported_cfo",
                           "residual": "computed_cfo - reported_cfo"})
    reasons = result["reasons"]
    if any(statement.get(key) is None for key in fields):
        reasons.append(_reason("missing_statement_field", "年度表登记字段不完整。"))
    if (statement.get("issuer_id") != source.get("issuer_id")
            or statement.get("source_id") != source.get("id")
            or statement.get("source_sha256") != source.get("sha256")):
        reasons.append(_reason("source_mismatch", "年度表的发行人、来源版本与登记文件不一致。"))
    if statement.get("unit") != "USD_million":
        reasons.append(_reason("unit_mismatch", "本轮仅处理披露的百万美元单位，不隐式换算。"))
    if statement.get("scope") != "reported_consolidated_statement_total":
        reasons.append(_reason("scope_mismatch", "本轮只核对同一合并现金流量表的列示范围。"))
    try:
        _period(statement.get("period"))
    except (ValueError, TypeError, KeyError):
        reasons.append(_reason("invalid_period", "日期、年度日历或期间天数不一致。"))
    year = statement.get("year")
    if type(year) is not int or year not in (2024, 2025):
        reasons.append(_reason("invalid_year", "本次登记仅包含 FY2024 和 FY2025。"))
    expected = statement.get("expected_row_keys")
    if (type(expected) is not list or not expected or len(expected) > 100
            or any(type(key) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", key) for key in expected)
            or len(expected) != len(set(expected)) or "cfo" in expected):
        reasons.append(_reason("invalid_row_inventory", "完整贡献行清单缺失、重复或混入 CFO 总计。"))
        expected = []
    facts = statement.get("facts")
    if type(facts) is not list or not facts or len(facts) > 101 or any(type(fact) is not dict for fact in facts):
        reasons.append(_reason("missing_facts", "缺少完整原表事实，不能补零。"))
        return result
    ids, keys = [f.get("id") for f in facts], [f.get("row_key") for f in facts]
    if (any(type(value) is not str or not value for value in ids + keys)
            or len(set(str(value) for value in ids)) != len(ids)
            or len(set(str(value) for value in keys)) != len(keys)):
        reasons.append(_reason("duplicate_or_invalid_row", "原表事实 ID 和行键须完整且唯一。"))
        if any(type(value) is not str or not value for value in ids + keys):
            return result
    result["input_ids"] = [value for value in ids if type(value) is str]
    total = [f for f in facts if f.get("role") == "cfo_total"]
    contributing = [f for f in facts if f.get("role") != "cfo_total"]
    if len(total) != 1 or total[0].get("id") != statement.get("total_fact_id") or total[0].get("row_key") != "cfo":
        reasons.append(_reason("invalid_total", "需要且只允许一个独立登记的 CFO 汇总行。"))
    if set(str(f.get("row_key")) for f in contributing) != set(expected) or len(contributing) != len(expected):
        reasons.append(_reason("row_inventory_mismatch", "贡献行必须与完整登记清单恰好一致，零值行也不能遗漏。"))
    for role in CONTRIBUTION_ROLES:
        matching = [f for f in contributing if f.get("role") == role]
        if not matching or (role == "net_income" and len(matching) != 1):
            reasons.append(_reason("group_inventory_mismatch", "需要单个净利润行及两个完整原表调节区段。"))
    amounts = {}
    for fact in facts:
        fid = fact.get("id")
        if fact.get("role") not in ALL_ROLES:
            reasons.append(_reason("unknown_role", "未知原表分区不能自动归类。", fid))
        expected_id = f"{statement.get('issuer_id')}:{statement.get('source_sha256')}:cf-bridge-{fact.get('row_key')}-{year}"
        if fid != expected_id or fact.get("source_sha256") != statement.get("source_sha256"):
            reasons.append(_reason("fact_source_mismatch", "事实身份或来源文件与年度表不一致。", fid))
        for name in ("unit", "period", "source_id"):
            if name in fact and fact[name] != statement.get(name):
                reasons.append(_reason(f"row_{name}_mismatch", f"原表行的 {name} 不同，不能混合加总。", fid))
        if (not fact.get("label") or not fact.get("label_en") or type(fact.get("page")) is not int
                or fact["page"] < 1 or type(fact.get("location")) is not dict
                or fact["location"].get("page") != fact["page"] or type(fact.get("bbox")) is not dict):
            reasons.append(_reason("incomplete_evidence", "原表行缺少完整标签与页面证据。", fid))
        try:
            amount = _number(fact.get("signed_value"))
            raw = fact.get("raw_value")
            with localcontext() as context:
                context.prec = 80
                displayed = displayed_amount(raw) if type(raw) is str and len(raw) <= 100 else None
            if displayed != amount:
                raise ValueError("Original sign or amount differs from signed value")
            amounts[fid] = amount
        except (InvalidOperation, ValueError, TypeError):
            reasons.append(_reason("invalid_signed_amount", "金额缺失、非有限、符号错误或与原始显示值不一致；不取绝对值。", fid))
    if total:
        if any(f.get("page") != total[0].get("page") for f in facts):
            reasons.append(_reason("page_mismatch", "本次登记为单页现金流表，不混入其他页面来源行。"))
        if total[0].get("id") in amounts:
            result["reported_cfo"] = _text(amounts[total[0]["id"]])
    expected_observation = f"{statement.get('issuer_id')}:{statement.get('source_sha256')}:cfo-{year}"
    if statement.get("reported_cfo_observation_id") != expected_observation:
        reasons.append(_reason("cfo_observation_mismatch", "CFO 必须引用旧研究材料中同公司、同文件、同年度的全局观察值。"))
    order = {key: index for index, key in enumerate(expected)}
    contributing.sort(key=lambda f: (order.get(f.get("row_key"), 101), str(f.get("id"))))
    result["rows"] = [{**deepcopy(fact), "group": fact.get("role")} for fact in contributing]
    result["contribution_input_ids"] = [f.get("id") for f in contributing]
    result["groups"] = [{"group": role, "input_ids": [f.get("id") for f in contributing if f.get("role") == role], "value": None}
                        for role in CONTRIBUTION_ROLES]
    if reasons:
        return result
    with localcontext() as context:
        context.prec = 80
        for group in result["groups"]:
            group["value"] = _text(sum((amounts[fid] for fid in group["input_ids"]), Decimal(0)))
        computed = sum((amounts[f["id"]] for f in contributing), Decimal(0))
        residual = computed - amounts[total[0]["id"]]
        result.update(computed_cfo=_text(computed), residual=_text(residual))
    if residual:
        reasons.append(_reason("nonzero_reconciliation_residual", "原表有符号贡献行加总不等于披露 CFO，不能宣称桥接成功。"))
    else:
        result["status"] = "reconciled"
    return result


def _change(previous: dict, current: dict) -> dict:
    result = {"issuer_id": current["issuer_id"], "company": current["company"], "from_year": 2024, "to_year": 2025,
              "source_sha256": current["source_sha256"], "unit": current["unit"],
              "previous_period": deepcopy(previous["period"]), "current_period": deepcopy(current["period"]),
              "status": "blocked", "rows": [], "groups": [], "reported_cfo_change": None,
              "contribution_total": None, "residual": None,
              "input_ids": previous["input_ids"] + current["input_ids"],
              "reported_cfo_observation_ids": [previous["reported_cfo_observation_id"], current["reported_cfo_observation_id"]],
              "formula": {"row_contribution": "current.signed_value - previous.signed_value",
                          "contribution_total": "sum(all registered row contributions)",
                          "reported_cfo_change": "current.reported_cfo - previous.reported_cfo",
                          "residual": "contribution_total - reported_cfo_change"},
              "scope": "Accounting contribution in the same report's comparative columns; not causation",
              "reasons": [], "warnings": [], "automatic_approval": False}
    reasons = result["reasons"]
    if any(s["status"] != "reconciled" for s in (previous, current)):
        reasons.append(_reason("unreconciled_statement", "两期原表必须先分别完成会计核对。"))
        return result
    for field in ("issuer_id", "source_id", "source_sha256", "unit", "scope"):
        if previous[field] != current[field]:
            reasons.append(_reason(f"{field}_mismatch", f"两期 {field} 不同，不自动拼接。"))
    p, c = previous["period"], current["period"]
    ps, pe, cs, ce = [date.fromisoformat(value) for value in (p["start"], p["end"], c["start"], c["end"])]
    if cs != pe + timedelta(days=1) or ce <= pe:
        reasons.append(_reason("nonconsecutive_periods", "两期必须是相邻、不重叠的完整年度。"))
    if p["fiscal_calendar"] != c["fiscal_calendar"]:
        reasons.append(_reason("calendar_mismatch", "两期财年日历不同，不自动折算。"))
    elif p["fiscal_calendar"] == "week_52_53" and p["days"] != c["days"]:
        reasons.append(_reason("week_year_length_mismatch", "52 周与 53 周的年度变化不在本轮直接配对。"))
    elif p["fiscal_calendar"] == "calendar_year":
        if (ps.month, ps.day) != (cs.month, cs.day) or (pe.month, pe.day) != (ce.month, ce.day):
            reasons.append(_reason("calendar_boundaries_mismatch", "日历财年的起止边界不同。"))
        elif p["days"] != c["days"]:
            result["warnings"].append("完整年度包含闰日差异；使用报表原值，不日均化或年化。")
    before = {row["row_key"]: row for row in previous["rows"]}
    after = {row["row_key"]: row for row in current["rows"]}
    if set(before) != set(after):
        reasons.append(_reason("row_set_changed", "两期完整贡献行集合不同，需人工建立重分类依据，不自动配对。"))
    for key in sorted(set(before) & set(after)):
        if any(before[key][field] != after[key][field] for field in ("role", "label", "label_en")):
            reasons.append(_reason("row_classification_changed", f"{key} 的原行标签或分区改变，不自动视作同一行。"))
    if reasons:
        return result
    with localcontext() as context:
        context.prec = 80
        for previous_row in previous["rows"]:
            key = previous_row["row_key"]
            current_row = after[key]
            amount = _number(current_row["signed_value"]) - _number(previous_row["signed_value"])
            result["rows"].append({"row_key": key, "label": current_row["label"], "label_en": current_row["label_en"],
                                   "group": current_row["role"], "previous_id": previous_row["id"], "current_id": current_row["id"],
                                   "previous_value": previous_row["signed_value"], "current_value": current_row["signed_value"],
                                   "contribution": _text(amount)})
        for role in CONTRIBUTION_ROLES:
            rows = [row for row in result["rows"] if row["group"] == role]
            result["groups"].append({"group": role, "input_ids": [fid for row in rows for fid in (row["previous_id"], row["current_id"])],
                                     "contribution": _text(sum((Decimal(row["contribution"]) for row in rows), Decimal(0)))})
        total = sum((Decimal(row["contribution"]) for row in result["rows"]), Decimal(0))
        reported_change = _number(current["reported_cfo"]) - _number(previous["reported_cfo"])
        residual = total - reported_change
        result.update(reported_cfo_change=_text(reported_change), contribution_total=_text(total), residual=_text(residual))
    if residual:
        reasons.append(_reason("nonzero_change_residual", "逐行变化之和不等于披露 CFO 变化。"))
    else:
        result["status"] = "reconciled"
    return result


def _build_from_bundle(bundle: dict) -> dict:
    """Internal deterministic arithmetic; public entry first verifies registration."""
    source_hash = _digest(bundle)
    if (type(bundle) is not dict or bundle.get("schema_version") != 1
            or type(bundle.get("source_versions")) is not list or type(bundle.get("statements")) is not list):
        raise CashFlowBridgeError("invalid_bundle", "Missing registered source and statement inventory")
    sources = bundle["source_versions"]
    if (len(sources) != 2 or any(type(s) is not dict or type(s.get("id")) is not str
                               or type(s.get("issuer_id")) is not str or type(s.get("sha256")) is not str
                               or not re.fullmatch(r"[0-9a-f]{64}", s["sha256"])
                               or s["id"] != f"{s['issuer_id']}:{s['sha256']}" for s in sources)
            or len({s["id"] for s in sources}) != 2):
        raise CashFlowBridgeError("invalid_source_inventory", "Exactly two unique source versions are required")
    by_source = {s["id"]: s for s in sources}
    statements = bundle["statements"]
    if len(statements) != 4 or any(type(s) is not dict for s in statements):
        raise CashFlowBridgeError("invalid_statement_inventory", "Exactly four registered annual statements are required")
    identities = [(s.get("issuer_id"), s.get("year")) for s in statements]
    if (any(type(issuer) is not str or type(year) is not int for issuer, year in identities)
            or any(type(s.get("id")) is not str or type(s.get("source_id")) is not str for s in statements)):
        raise CashFlowBridgeError("invalid_statement_identity", "Issuer and year must be explicit")
    issuer_ids = {s.get("issuer_id") for s in sources}
    if (len(issuer_ids) != 2 or set(identities) != {(issuer, year) for issuer in issuer_ids for year in (2024, 2025)}
            or len({s.get("id") for s in statements}) != 4):
        raise CashFlowBridgeError("invalid_statement_inventory", "Duplicate or missing issuer-year statement")
    results = [_statement(s, by_source.get(s.get("source_id"), {})) for s in statements]
    results.sort(key=lambda statement: (statement["issuer_id"], statement["year"]))
    changes = []
    for issuer in sorted(issuer_ids):
        pair = [statement for statement in results if statement["issuer_id"] == issuer]
        changes.append(_change(pair[0], pair[1]))
    return {"schema_version": 1, "id": "msft-aapl-cash-flow-bridge-1", "rule_version": BRIDGE_RULE_VERSION,
            "source_bundle": deepcopy(bundle), "source_bundle_sha256": source_hash,
            "statements": results, "changes": changes, "limitations": list(LIMITATIONS),
            "scope": "Registered accounting rows and their annual differences; no causal or model inference",
            "prediction": False, "automatic_approval": False}


def build_cash_flow_bridge(root: Path) -> dict:
    """Load pinned sources, cross-check existing CFO observations and calculate."""
    from .cash_flow_sources import load_cash_flow_sources
    from .research_materials import build_material_bundle

    bundle = load_cash_flow_sources(Path(root))
    result = _build_from_bundle(bundle)
    material = build_material_bundle(Path(root))
    observations = {observation["id"]: observation for observation in material["observations"]}
    for statement in result["statements"]:
        observation = observations.get(statement["reported_cfo_observation_id"])
        if (observation is None or observation["metric"] != "cfo"
                or observation["issuer_id"] != statement["issuer_id"] or observation["year"] != statement["year"]
                or observation["source"]["sha256"] != statement["source_sha256"]
                or observation["unit"] != statement["unit"] or observation["period"] != statement["period"]
                or observation["normalized_value"] != statement["reported_cfo"]):
            raise CashFlowBridgeError("existing_cfo_mismatch", "Cash-flow total does not match the registered research observation")
    return result
