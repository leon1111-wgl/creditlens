"""Pinned company statements checked against an existing accounting snapshot."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Context, Decimal, localcontext, ROUND_HALF_UP, ROUND_HALF_EVEN
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .diligence import digest
from .margin_experiment import load_margin_bridge_snapshot, IMPLEMENTATION_FILES as BRIDGE_FILES

VERSION = "margin-claims-development-1"
CATALOG = "data/sources/margin-company-claims.json"
REPORT = "artifacts/margin-claims-report.json"
CATALOG_RAW_SHA256 = "8b7f5f390e7afca1191e3aadf58b630fa2710969c0bc2df78f4a37da4d929501"
CATALOG_SHA256 = "77adcb4a73c10843735e499531030d95431334c052fc425e46a56e7b90927160"
MARGIN_REPORT_SHA256 = "c19c7e2bbd80e62ae027f4d8a6eacffd87a93475ad2deb69585ad4b72a471938"
IMPLEMENTATION_FILES = (*BRIDGE_FILES, "creditlens/margin_claims.py", "creditlens/margin_claim_experiment.py",
                        "scripts/run_margin_claims.py")
SCOPE = {"registered_company_claims": 8, "companies": 2, "independent_human_gold": 0,
         "live_html_fetched": False, "raw_html_bytes_rechecked": False,
         "full_ocr_replayed": False, "new_ocr_calls": 0, "model_calls": 0,
         "automatic_nli": False, "causal_explanation_verified": False,
         "company_statement_attribution_only": True, "historical_snapshot_verified": False}
LIMITATIONS = [
    "数值或方向相容只说明登记陈述与已有金额之间的关系，不证明公司解释成立。",
    "总公司、产品、服务与Microsoft Cloud范围分别处理；数值舍入相近不能弥补范围缺失。",
    "毛利金额增长、毛利率变化、相对增长百分比及百分点分别计算，不能互相替代。",
    "舍入策略为本工具的HALF_UP核对规则，不声称公司公开声明采用该算法。",
    "中文转述、主要原因和部分抵消限定保留为公司陈述，未执行自动语义蕴含或因果验证。",
    "微软来源登记包含曾抓取HTML的响应摘要；苹果使用官方页面工具返回的文本，未核验原始HTTP字节。本次均不重新抓取。",
    "来源登记由工具辅助整理，独立人工确认pending；八条陈述不是八项模型或人工金标准成绩。",
]


class MarginClaimExperimentError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def _require(condition, code, path):
    if not condition:
        raise MarginClaimExperimentError(code, path)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key")
        value[key] = item
    return value


def _read(path, limit=3_000_000):
    try:
        path = Path(path)
        _require(path.stat().st_size <= limit, "input_too_large", path.name)
        raw = path.read_bytes()
        _require(len(raw) <= limit, "input_too_large", path.name)
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        _require(type(data) is dict, "invalid_json", path.name)
        return raw, data
    except MarginClaimExperimentError:
        raise
    except (ValueError, OSError, TypeError, UnicodeError) as exc:
        raise MarginClaimExperimentError("input_unavailable", Path(path).name) from exc


def _hashes(root):
    try:
        return {name: hashlib.sha256((root/name).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()
                for name in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc:
        raise MarginClaimExperimentError("implementation_unavailable", "implementation") from exc


def _load(root):
    root = Path(root).resolve()
    _require((root/CATALOG).resolve().is_relative_to(root), "catalog_path_escape", CATALOG)
    raw, catalog = _read(root/CATALOG, 200_000)
    _require(hashlib.sha256(raw).hexdigest() == CATALOG_RAW_SHA256 and digest(catalog) == CATALOG_SHA256,
             "claim_catalog_changed", CATALOG)
    snapshot = load_margin_bridge_snapshot(root)
    _require(snapshot["report_sha256"] == catalog["margin_report_sha256"] == MARGIN_REPORT_SHA256,
             "margin_snapshot_changed", "margin_report_sha256")
    cases = {case["input"]["issuer_id"]: case for case in snapshot["report"]["cases"]}
    records = {record["id"]: record for record in catalog["source_records"]}
    _require(len(records) == len(catalog["source_records"]) == len(catalog["claims"]) == 8
             and len({claim["id"] for claim in catalog["claims"]}) == 8
             and set(records) == {claim["source"]["id"] for claim in catalog["claims"]},
             "claim_inventory_changed", "claims")
    for claim in catalog["claims"]:
        _require(claim["issuer_id"] in cases, "unknown_claim_issuer", claim["id"])
        case = cases[claim["issuer_id"]]
        base, current = case["input"]["statements"]
        record = records[claim["source"]["id"]]
        _require(claim["report_sha256"] == case["input"]["source"]["sha256"]
                 and claim["base_year"] == base["year"] and claim["current_year"] == current["year"]
                 and claim["periods"] == {"base": {k: base["period"][k] for k in ("start", "end")},
                                         "current": {k: current["period"][k] for k in ("start", "end")}},
                 "claim_margin_binding_mismatch", claim["id"])
        _require(record["issuer_id"] == claim["issuer_id"] and record["url"] == claim["source"]["url"]
                 and record["capture_sha256"] == claim["source"]["sha256"] and record["locator"] == claim["source"]["locator"]
                 and record["historical_snapshot_verified"] is False and record["independent_source"] is False,
                 "claim_capture_binding_mismatch", claim["id"])
    _require(_read(root/CATALOG, 200_000)[0] == raw, "catalog_changed_during_read", CATALOG)
    return catalog, cases, records, snapshot


def _decimal(fraction, places=None):
    with localcontext(Context(prec=100, rounding=ROUND_HALF_EVEN)):
        number = Decimal(fraction.numerator)/Decimal(fraction.denominator)
        if places is not None:
            number = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        if number.is_zero():
            number = abs(number)
        return format(number, "f")


def numeric_oracle(margin_case, claim):
    """Exact rational arithmetic; no use of candidate result or prose meaning."""
    scope = claim["scope"]
    if scope == "microsoft_cloud":
        return {"available": False, "reason": "scope_not_in_registered_accounting", "numeric_match_cannot_repair_scope": True}
    states = []
    for statement in margin_case["input"]["statements"]:
        if scope == "consolidated_total":
            revenue = Fraction(statement["total_revenue"]["value"])
            cost = Fraction(statement["total_cost"]["value"])
        else:
            category = next(c for c in statement["categories"] if c["id"] == scope)
            revenue, cost = Fraction(category["revenue"]["value"]), Fraction(category["cost"]["value"])
        states.append({"revenue": revenue, "gross_profit": revenue-cost, "rate": (revenue-cost)/revenue*100})
    base, current = states
    amount_delta, rate_delta = current["gross_profit"]-base["gross_profit"], current["rate"]-base["rate"]
    metric_delta = amount_delta if claim["metric"] == "gross_profit_amount" else rate_delta
    direction = "increase" if metric_delta > 0 else "decrease" if metric_delta < 0 else "unchanged"
    checks = []
    for disclosure in claim["disclosures"]:
        kind, unit = disclosure["kind"], disclosure["unit"]
        valid, value = True, None
        if kind == "delta_amount":
            valid = claim["metric"] == "gross_profit_amount" and unit in {"USD_million", "USD_billion"}
            value = amount_delta/(1000 if unit == "USD_billion" else 1)
        elif kind == "growth_percent":
            valid = claim["metric"] == "gross_profit_amount" and unit == "percent" and base["gross_profit"] > 0
            value = amount_delta/base["gross_profit"]*100 if base["gross_profit"] > 0 else None
        elif kind in {"base_percent", "current_percent"}:
            valid = claim["metric"] == "gross_margin_rate" and unit == "percent"
            value = base["rate"] if kind == "base_percent" else current["rate"]
        elif kind == "delta_pp":
            valid = claim["metric"] == "gross_margin_rate" and unit == "percentage_point"
            value = rate_delta
        else:
            valid = False
        rounded = _decimal(value, disclosure["decimal_places"]) if valid and value is not None else None
        checks.append({"kind": kind, "unit": unit, "valid": valid,
                       "exact_fraction": f"{value.numerator}/{value.denominator}" if value is not None else None,
                       "rounded": rounded, "stated": disclosure["value"],
                       "matched": valid and rounded is not None and Fraction(rounded) == Fraction(disclosure["value"])})
    rate_levels = {d["kind"]: d for d in claim["disclosures"] if d["kind"] in {"base_percent", "current_percent"} and d["unit"] == "percent"}
    displayed_delta = None
    if set(rate_levels) == {"base_percent", "current_percent"}:
        displayed_delta = Fraction(rate_levels["current_percent"]["value"])-Fraction(rate_levels["base_percent"]["value"])
    return {"available": True, "method": "independent_exact_fractions", "direction": direction,
            "direction_required": claim["direction"] is not None,
            "direction_matches": claim["direction"] is None or claim["direction"] == direction,
            "base_gross_profit_million": _decimal(base["gross_profit"]), "current_gross_profit_million": _decimal(current["gross_profit"]),
            "amount_delta_million": _decimal(amount_delta), "base_rate_percent": _decimal(base["rate"]),
            "current_rate_percent": _decimal(current["rate"]), "exact_rate_delta_pp": _decimal(rate_delta),
            "disclosed_rate_level_difference_pp": _decimal(displayed_delta) if displayed_delta is not None else None,
            "displayed_rate_delta_equals_exact": displayed_delta == rate_delta if displayed_delta is not None else None,
            "checks": checks, "semantic_support_verified": False}


def _claim(case, metric="gross_profit_amount", scope="consolidated_total", direction="increase", disclosures=None):
    base, current = case["input"]["statements"]
    return {"id": "synthetic-margin-claim", "issuer_id": case["input"]["issuer_id"],
            "report_sha256": case["input"]["source"]["sha256"], "base_year": base["year"], "current_year": current["year"],
            "periods": {"base": {k: base["period"][k] for k in ("start", "end")},
                        "current": {k: current["period"][k] for k in ("start", "end")}},
            "scope": scope, "metric": metric, "claim_type": "numeric_only",
            "source": {"id": "synthetic-claim-source", "url": "https://example.invalid/synthetic-claim", "sha256": "a"*64,
                       "locator": "Synthetic control; not a company disclosure"},
            "paraphrase_zh": "合成核对条件，不是公司披露。", "qualifiers": {"primary": False, "partial_offset": False},
            "drivers": [], "direction": direction,
            "disclosures": disclosures if disclosures is not None else [{"kind": "delta_amount", "value": "22.9", "unit": "USD_billion", "decimal_places": 1},
                                                                          {"kind": "growth_percent", "value": "13", "unit": "percent", "decimal_places": 0}]}


def _threshold_case(mode):
    from .margin_experiment import _synthetic_payload
    from .margin_bridge import evaluate_margin_bridge
    payload = _synthetic_payload()
    for index, statement in enumerate(payload["statements"]):
        cost = "50" if index == 0 else "30.551" if mode == "below" else "30.55"
        if mode == "zero_base" and index == 0:
            cost = "100"
        for category in statement["categories"]:
            category["revenue"].update(raw_value="100", value="100")
            category["cost"].update(raw_value=cost, value=cost)
        total_cost = Fraction(cost)*2
        statement["total_revenue"].update(raw_value="200", value="200")
        statement["total_cost"].update(raw_value=_decimal(total_cost), value=_decimal(total_cost))
        statement["gross_profit"].update(raw_value=_decimal(200-total_cost), value=_decimal(200-total_cost))
    return {"input": payload, "result": evaluate_margin_bridge(payload)}


def synthetic_controls(cases):
    msft, aapl = ({k: cases[issuer][k] for k in ("input", "result")} for issuer in ("MSFT", "AAPL"))
    contexts = {"msft": deepcopy(msft), "aapl": deepcopy(aapl), "threshold": _threshold_case("at"),
                "below": _threshold_case("below"), "zero_base": _threshold_case("zero_base")}
    controls = []
    def add(identifier, title, context_id, claim, expected):
        controls.append({"id": identifier, "title": title, "provenance": "synthetic_control", "context_id": context_id,
                         "claim": claim, "expected_status": expected})
    add("rounded-amount", "总毛利金额与增长率分别舍入", "msft", _claim(msft), "matched_numeric")
    add("amount-up-rate-down", "金额增加时总率仍可下降", "msft", _claim(msft, "gross_margin_rate", direction="decrease", disclosures=[]), "matched_direction")
    add("cloud-not-company", "Cloud69%不能链接全公司约69%", "msft", _claim(msft, "gross_margin_rate", "microsoft_cloud", None,
        [{"kind": "current_percent", "value": "69", "unit": "percent", "decimal_places": 0}]), "scope_unavailable")
    add("company-rounded-69", "合并总率单独舍入为69%", "msft", _claim(msft, "gross_margin_rate", direction=None,
        disclosures=[{"kind": "current_percent", "value": "69", "unit": "percent", "decimal_places": 0}]), "matched_numeric")
    for identifier, field, value in (("wrong-issuer", "issuer_id", "OTHER"), ("wrong-year", "current_year", 2024),
                                      ("wrong-report", "report_sha256", "b"*64)):
        claim = _claim(msft); claim[field] = value
        add(identifier, "公司、年份或来源错配", "msft", claim, "invalid")
    claim = _claim(msft); claim["periods"]["current"]["end"] = "2025-06-29"
    add("wrong-period", "比较期间错配", "msft", claim, "invalid")
    claim = _claim(msft); claim["disclosures"][0]["value"] = "22.8"
    add("wrong-amount-rounding", "22.885十亿不能舍入成22.8", "msft", claim, "contradiction")
    claim = _claim(msft); claim["direction"] = "decrease"
    add("wrong-amount-direction", "毛利金额增加不能标为减少", "msft", claim, "contradiction")
    claim = _claim(msft); claim["disclosures"][1]["unit"] = "percentage_point"
    add("growth-percent-not-pp", "增长百分比不能标百分点", "msft", claim, "invalid")
    add("rate-pp-not-percent", "毛利率差额不能标百分比增长", "aapl", _claim(aapl, "gross_margin_rate", direction="increase",
        disclosures=[{"kind": "delta_pp", "value": "0.7", "unit": "percent", "decimal_places": 1}]), "invalid")
    add("product-amount-up", "苹果产品毛利金额增加", "aapl", _claim(aapl, "gross_profit_amount", "products", "increase", []), "matched_direction")
    add("product-rate-down", "苹果产品金额增而毛利率降", "aapl", _claim(aapl, "gross_margin_rate", "products", "decrease",
        [{"kind": "base_percent", "value": "37.2", "unit": "percent", "decimal_places": 1},
         {"kind": "current_percent", "value": "36.8", "unit": "percent", "decimal_places": 1}]), "matched_numeric")
    for identifier, context_id, stated, expected in (("half-up-threshold", "threshold", "69.5", "matched_numeric"),
                                                    ("below-threshold", "below", "69.4", "matched_numeric"),
                                                    ("below-wrong-round", "below", "69.5", "contradiction")):
        add(identifier, "独立固定舍入边界", context_id, _claim(contexts[context_id], "gross_margin_rate", direction=None,
            disclosures=[{"kind": "current_percent", "value": stated, "unit": "percent", "decimal_places": 1}]), expected)
    add("zero-base-growth", "零基期不生成增长百分比", "zero_base", _claim(contexts["zero_base"], disclosures=[
        {"kind": "growth_percent", "value": "13", "unit": "percent", "decimal_places": 0}]), "invalid")
    claim = _claim(msft); claim["claim_type"] = "company_explanation"
    claim["paraphrase_zh"] = "合成夸大文本：全部变化均已被独立证实为这个原因。"
    claim["qualifiers"] = {"primary": True, "partial_offset": False}
    claim["drivers"] = [{"role": "driver", "text_zh": "未证实的合成原因"}]
    add("prose-is-not-causal-proof", "自由文字不能升级因果证实", "msft", claim, "matched_numeric")
    return contexts, controls


def _oracle_comparison(oracle, result, *, require_actual=False):
    checks = []
    def check(code, passed):
        checks.append({"code": code, "passed": bool(passed)})
    check("company_statement_only", result["support_scope"] == "company_statement_only")
    for flag in ("independently_verified", "causal_explanation", "linked_to_accounting_effects",
                 "natural_language_understood", "automatic_approval"):
        check(flag + "_remains_false", result[flag] is False)
    actual = result["actual"]
    if not oracle["available"]:
        check("unregistered_scope_has_no_amounts", actual is None and not result["numeric_checks"]
              and result["status"] == "scope_unavailable")
    elif require_actual:
        check("required_arithmetic_present", actual is not None)
        check("required_direction_present", (result["direction_check"] is not None) == oracle["direction_required"])
        check("required_rate_display_present", (result.get("rate_display_comparison") is not None) ==
              (oracle["disclosed_rate_level_difference_pp"] is not None))
        if any(not item["valid"] for item in oracle["checks"]):
            status = "invalid"
        elif not oracle["direction_matches"] or any(not item["matched"] for item in oracle["checks"]):
            status = "contradiction"
        else:
            status = "matched_numeric" if oracle["checks"] else "matched_direction"
        check("independent_numeric_outcome", result["status"] == status)
    if oracle["available"] and actual is not None:
        expected_numbers = {"delta_amount": oracle["amount_delta_million"], "delta_rate_pp": oracle["exact_rate_delta_pp"]}
        for field, expected in expected_numbers.items():
            check("rational_" + field, abs(Fraction(actual[field])-Fraction(expected)) <= Fraction(1, 10**60))
        for field, oracle_key in (("base", "base_rate_percent"), ("current", "current_rate_percent")):
            check("rational_" + field + "_rate", abs(Fraction(actual[field]["gross_margin_percent"])-Fraction(oracle[oracle_key])) <= Fraction(1, 10**60))
        if result["direction_check"] is not None:
            check("independent_direction", result["direction_check"]["actual"] == oracle["direction"]
                  and result["direction_check"]["matched"] == oracle["direction_matches"])
        check("numeric_check_inventory", len(result["numeric_checks"]) == len(oracle["checks"]))
        for index, (observed, expected) in enumerate(zip(result["numeric_checks"], oracle["checks"])):
            check(f"numeric_{index}_identity", (observed["kind"], observed["unit"], observed["stated"]) ==
                  (expected["kind"], expected["unit"], expected["stated"]))
            check(f"numeric_{index}_rounding", observed["rounded"] == expected["rounded"] and observed["matched"] == expected["matched"])
            if not expected["valid"]:
                check(f"numeric_{index}_invalid", observed["status"] == "invalid")
        if result.get("rate_display_comparison") is not None:
            check("disclosed_levels_not_exact_delta", Fraction(result["rate_display_comparison"]["stated_delta_pp"]) ==
                  Fraction(oracle["disclosed_rate_level_difference_pp"]))
    return {"method": "independent_exact_fraction_calculations", "checks": checks,
            "passed": all(item["passed"] for item in checks), "checks_prose_meaning": False,
            "checks_source_authenticity": False, "arithmetic_compared": oracle["available"] and actual is not None,
            "structural_refusal_only": oracle["available"] and actual is None}


def build_margin_claim_report(root: Path) -> dict:
    from .margin_claims import evaluate_margin_claims, MARGIN_CLAIMS_VERSION, STATUSES
    root = Path(root).resolve()
    catalog, margin_cases, records, snapshot = _load(root)
    cases = []
    bindings = []
    for issuer, case in margin_cases.items():
        selected = [claim for claim in catalog["claims"] if claim["issuer_id"] == issuer]
        context = {key: case[key] for key in ("input", "result")}
        payload = {"schema_version": 1, "margin_case": context, "claims": selected}
        output = evaluate_margin_claims(payload)
        _require(len(output["results"]) == len(selected), "incomplete_claim_results", issuer)
        bindings.append({"issuer_id": issuer, "margin_case_id": case["id"], "margin_case_sha256": digest(context),
                         "report_sha256": case["input"]["source"]["sha256"], "input_sha256": output["input_sha256"],
                         "claim_ids": [claim["id"] for claim in selected]})
        for claim, result in zip(selected, output["results"]):
            _require(result["claim"] == claim and result["id"] == claim["id"], "claim_result_binding_mismatch", issuer)
            oracle = numeric_oracle(context, claim)
            cases.append({"id": claim["id"], "issuer_id": issuer, "margin_case_id": case["id"],
                          "provenance": "registered_company_disclosure", "claim": deepcopy(claim), "result": result,
                          "source_record": deepcopy(records[claim["source"]["id"]]), "independent_numeric_oracle": oracle,
                          "oracle_comparison": _oracle_comparison(oracle, result, require_actual=oracle["available"]),
                          "independent_human_confirmation": "pending"})
    contexts, specs = synthetic_controls(margin_cases)
    controls = []
    for specification in specs:
        context = contexts[specification["context_id"]]
        payload = {"schema_version": 1, "margin_case": context, "claims": [specification["claim"]]}
        result = evaluate_margin_claims(payload)["results"][0]
        oracle = numeric_oracle(context, specification["claim"])
        comparison = _oracle_comparison(oracle, result, require_actual=specification["expected_status"] in
                                        {"matched_numeric", "matched_direction", "contradiction"})
        controls.append({**deepcopy(specification), "input_sha256": digest(payload), "result": result,
                         "independent_numeric_oracle": oracle, "oracle_comparison": comparison,
                         "passed": result["status"] == specification["expected_status"] and comparison["passed"]})
    observed = Counter(case["result"]["status"] for case in cases)
    report = {"schema_version": 1, "experiment_version": VERSION, "rule_version": MARGIN_CLAIMS_VERSION,
              "catalog": {"path": CATALOG, "raw_sha256": CATALOG_RAW_SHA256, "canonical_sha256": CATALOG_SHA256,
                          "raw_hash_basis": "exact_utf8_lf_bytes", "registration_provenance": deepcopy(catalog["provenance"])},
              "margin_report_sha256": snapshot["report_sha256"], "source_bindings": bindings,
              "implementation_hash_basis": "python_utf8_lf", "implementation_sha256": _hashes(root),
              "cases": cases, "summary": {"registered_claims": len(cases), "companies": len(margin_cases),
                  "status_counts": {status: observed[status] for status in STATUSES},
                  "oracle_checks_passed": sum(c["oracle_comparison"]["passed"] for c in cases),
                  "independent_human_gold": 0},
              "controls": {"provenance": "synthetic_development_controls", "contexts": contexts, "cases": controls,
                           "summary": {"cases": len(controls), "passed": sum(c["passed"] for c in controls),
                                       "failed": sum(not c["passed"] for c in controls)},
                           "scope": "Fixed development counterexamples; not model accuracy or independently annotated data."},
              "scope": deepcopy(SCOPE), "limitations": list(LIMITATIONS), "read_only": True,
              "independently_verified": False, "automatic_approval": False}
    return report


def validate_margin_claim_report(root: Path, report: dict) -> dict:
    _require(type(report) is dict, "invalid_report", "report")
    try:
        supplied = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise MarginClaimExperimentError("invalid_report", "report") from exc
    expected = build_margin_claim_report(root)
    serialized = json.dumps(expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    _require(supplied == serialized, "report_replay_mismatch", "report")
    _require(all(c["oracle_comparison"]["passed"] for c in expected["cases"])
             and expected["controls"]["summary"]["failed"] == 0,
             "acceptance_checks_failed", "report")
    return deepcopy(expected)


def load_margin_claim_snapshot(root: Path) -> dict:
    root = Path(root).resolve()
    _require((root/REPORT).resolve().is_relative_to(root), "report_path_escape", REPORT)
    raw, report = _read(root/REPORT)
    normalized = validate_margin_claim_report(root, report)
    _require(_read(root/REPORT)[0] == raw, "report_changed_during_read", REPORT)
    return {"schema_version": 1, "report": normalized, "report_sha256": hashlib.sha256(raw).hexdigest(),
            "margin_report_sha256": normalized["margin_report_sha256"], "read_only": True,
            "validation_scope": {"registered_claims_and_arithmetic_replayed": True,
                                 "live_html_fetched": False, "raw_html_bytes_rechecked": False,
                                 "semantic_entailment_checked": False, "independently_verified": False,
                                 "full_ocr_replayed": False, "model_quality_verified": False}}
