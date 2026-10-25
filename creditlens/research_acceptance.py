"""Read-only evidence checks for three fixed, registered research tasks.

This module does not interpret a question, execute document instructions, verify
an identity, or change an existing review/snapshot. Callers authenticate and load
the current task through ResearchTaskStore before calling this layer.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .cash_flow_bridge import CashFlowBridgeError, build_cash_flow_bridge
from .research_materials import (
    BUNDLE_ID, MATERIAL_MODE, MaterialsError, build_comparison, build_material_bundle,
)


ACCEPTANCE_RULE_VERSION = "research-task-acceptance-1"
ROOT = Path(__file__).resolve().parents[1]
_SCOPE = {
    "kind": "fixed_task_templates", "bundle_id": BUNDLE_ID,
    "issuer_ids": ["MSFT", "AAPL"], "metrics": ["cfo", "capex"],
    "years": [2024, 2025], "material_mode": MATERIAL_MODE,
    "natural_language_question_understood": False, "historical_as_of_supported": False,
    "prediction": False, "causality_verified": False,
}
_LIMITATIONS = [
    "只检查三项预设任务及已登记材料，不理解自由问题，也不判定任意自然语言答案是否正确。",
    "使用当前登记的 FY2025 报告版本，不证明这些字节在历史某日已经可得，也不自动获取最新资料。",
    "来源登记与证据定位不等于本次重新读取 PDF、真实性鉴定或模型识别验证。",
    "采用决定、复核流程状态、会计计算与经营解释证据分别展示；本检查不会自动批准任何事实或底稿。",
    "本次结果只绑定当前任务版本，不修改或补写过去提交的快照与复核意见。",
]


class ResearchAcceptanceError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _canonical(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ResearchAcceptanceError("invalid_task", "任务必须是有限数值构成的 JSON 数据。") from exc


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _same(left, right) -> bool:
    return _canonical(left) == _canonical(right)


def _gap(code: str, message: str, ids=()) -> dict:
    return {"code": code, "message": message, "observation_ids": list(ids)}


def _requirement(key: str, label: str, met: bool, detail: str) -> dict:
    return {"id": key, "label": label, "status": "met" if met else "unmet", "detail": detail}


def task_contract_catalog() -> dict:
    """Return fresh JSON data documenting the fixed task contracts."""
    return {
        "schema_version": 1, "rule_version": ACCEPTANCE_RULE_VERSION,
        "scope": deepcopy(_SCOPE), "automatic_approval": False,
        "contracts": [
            {"id": "metric_lookup", "title": "查指标",
             "input": "读取已登记微软与苹果两期 CFO 和固定资产现金支出，共八项观察。",
             "correctness": "每张卡同时保留原始符号、归一方式、实际期间、单位、范围与原页定位。",
             "common_failures": "缺来源或字段、错版本，以及把未采用误认为数值为零。",
             "recovery": "补齐并重新登记材料；采用与否由分析员单独写明理由。",
             "evidence_requirements": ["原值及显式归一方式", "期间、单位币种和指标定义", "来源版本、页码和位置", "独立的采用决定"],
             "acceptance_rule": "仅说明登记证据可以定位；不以采用或流程状态替代客观来源检查。",
             "limitations": ["不读取任意 URL 或问题中的公司名称。", "PPE 支出不代表全部资本投入。"]},
            {"id": "comparable_change", "title": "比同口径变化",
             "input": "使用同公司、同指标、相邻两年度且均明确采用的观察。",
             "correctness": "沿用固定比较规则，给出输入、公式、差额和允许计算的增长率。",
             "common_failures": "未采用、实际日期不同、口径改变，或把跨公司并列表当成排名。",
             "recovery": "先补采用决定与口径依据；期间无法对齐时保留并列和阻断原因。",
             "evidence_requirements": ["双方均明确采用", "同来源定义与范围", "实际年度连续及长度检查", "输入和公式可重算"],
             "acceptance_rule": "只复用登记比较规则；四项跨公司结果始终禁止排名。",
             "limitations": ["不是预测，也不作因果解释。", "不同 FY 标签边界不自动年化。"]},
            {"id": "operating_explanation", "title": "整理经营解释证据",
             "input": "分别整理会计变化、公司明示解释和分析员待查假设。",
             "correctness": "会计桥仅验证逐行加总；经营解释须另有附注、原话和核实依据。",
             "common_failures": "将零残差、流程通过或用户写的“已证明”当成因果证据。",
             "recovery": "登记经营附注和公司解释原文，再记录独立核实；暂缺时保留待查。",
             "evidence_requirements": ["可重算的会计变化", "登记的经营附注", "公司明示解释原文", "独立核实记录"],
             "acceptance_rule": "当前未登记公司解释，结果始终为证据不足；假设始终未验证。",
             "limitations": ["会计分解不是经营因果识别。", "未登记不表示企业没有披露。"]},
        ],
    }


def _check_task(task: dict) -> tuple[dict, dict, dict | None]:
    if type(task) is not dict:
        raise ResearchAcceptanceError("invalid_task", "需要服务端读取的当前任务对象。")
    _canonical(task)
    if (type(task.get("id")) is not str or not task["id"].strip() or len(task["id"]) > 200
            or type(task.get("revision")) is not int or task["revision"] < 1):
        raise ResearchAcceptanceError("invalid_task", "任务标识和正整数版本不可缺少。")
    version = task.get("store_version")
    if version not in ("research-handoff-1", "research-handoff-2"):
        raise ResearchAcceptanceError("unsupported_store_version", "不支持此研究任务版本。")
    if task.get("material_mode") != MATERIAL_MODE or task.get("as_of") is not None:
        raise ResearchAcceptanceError("historical_version_unverified", "本检查不支持历史可得时点。")
    bundle = task.get("bundle")
    if (type(bundle) is not dict or task.get("bundle_id") != BUNDLE_ID or bundle.get("id") != BUNDLE_ID
            or task.get("bundle_sha256") != _digest(bundle)):
        raise ResearchAcceptanceError("bundle_binding_mismatch", "任务、材料标识和材料摘要未绑定。")
    try:
        registered = build_material_bundle(ROOT)
    except (MaterialsError, OSError, ValueError) as exc:
        raise ResearchAcceptanceError("material_integrity_mismatch", "无法校验当前登记材料，需恢复已登记版本。") from exc
    if not _same(bundle, registered):
        raise ResearchAcceptanceError("material_integrity_mismatch", "材料与服务端登记版本不同，不能补值或继续计算。")
    try:
        comparison = build_comparison(bundle, task.get("decisions"))
    except MaterialsError as exc:
        raise ResearchAcceptanceError("comparison_integrity_mismatch", f"比较输入无效：{exc.code}。") from exc
    if not _same(comparison, task.get("comparison")):
        raise ResearchAcceptanceError("comparison_integrity_mismatch", "保存的比较与本次按采用决定重算的结果不同。")
    bridge_fields = ("cash_flow_bridge", "cash_flow_bridge_sha256", "bridge_review_note")
    bridge = None
    if version == "research-handoff-1":
        if ("include_cash_flow_bridge" in task and task["include_cash_flow_bridge"] is not False
                or any(k in task for k in bridge_fields)):
            raise ResearchAcceptanceError("bridge_integrity_mismatch", "旧任务版本不能夹带未经绑定的会计桥。")
    else:
        if task.get("include_cash_flow_bridge") is not True or type(task.get("cash_flow_bridge")) is not dict:
            raise ResearchAcceptanceError("bridge_integrity_mismatch", "新版任务缺少已绑定会计桥。")
        try:
            bridge = build_cash_flow_bridge(ROOT)
        except (CashFlowBridgeError, MaterialsError, OSError, ValueError) as exc:
            raise ResearchAcceptanceError("bridge_integrity_mismatch", "无法从登记资料重算会计桥。") from exc
        if (not _same(bridge, task["cash_flow_bridge"])
                or task.get("cash_flow_bridge_sha256") != _digest(bridge)):
            raise ResearchAcceptanceError("bridge_integrity_mismatch", "会计桥内容、来源或摘要与重算不同。")
        observations = {o["id"]: o for o in bundle["observations"]}
        for statement in bridge["statements"]:
            observation = observations.get(statement["reported_cfo_observation_id"])
            if (observation is None or observation["metric"] != "cfo"
                    or observation["issuer_id"] != statement["issuer_id"] or observation["year"] != statement["year"]
                    or observation["source"]["sha256"] != statement["source_sha256"]
                    or observation["unit"] != statement["unit"] or observation["period"] != statement["period"]
                    or observation["normalized_value"] != statement["reported_cfo"]):
                raise ResearchAcceptanceError("bridge_integrity_mismatch", "会计桥合计与当前任务 CFO 观察未绑定。")
    snapshot_id, snapshot_sha = task.get("snapshot_id"), task.get("snapshot_sha256")
    if (snapshot_id is not None or snapshot_sha is not None) and (
            type(snapshot_id) is not str or not snapshot_id
            or type(snapshot_sha) is not str or not re.fullmatch(r"[0-9a-f]{64}", snapshot_sha)):
        raise ResearchAcceptanceError("invalid_task", "关联快照标识和摘要必须成对；本层不评估其历史内容。")
    return bundle, comparison, bridge


def _hypotheses(task: dict, ids: set[str]) -> list[dict]:
    records = task.get("hypotheses")
    if type(records) is not list or len(records) > 3:
        raise ResearchAcceptanceError("invalid_hypotheses", "候选假设须为最多三项的列表。")
    result, seen = [], set()
    for item in records:
        if type(item) is not dict or set(item) != {"id", "text", "evidence_ids", "status", "note"}:
            raise ResearchAcceptanceError("invalid_hypotheses", "假设字段不符合既有任务契约。")
        key, evidence_ids = item["id"], item["evidence_ids"]
        if (type(key) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}", key) or key in seen
                or type(item["text"]) is not str or not item["text"].strip() or len(item["text"]) > 4000
                or type(item["note"]) is not str or len(item["note"]) > 4000
                or item["status"] not in ("pending", "not_assessed")
                or type(evidence_ids) is not list or len(evidence_ids) > 8
                or any(type(i) is not str or i not in ids for i in evidence_ids)
                or len(evidence_ids) != len(set(evidence_ids))):
            raise ResearchAcceptanceError("invalid_hypotheses", "假设标识、状态或引用无效；文本不能升级证据等级。")
        seen.add(key)
        result.append({**deepcopy(item), "submitted_status": item["status"], "status": "unverified_candidate"})
    return result


def build_task_acceptance(task: dict) -> dict:
    """Recompute a current task's fixed checks without mutating it or its store."""
    bundle, comparison, bridge = _check_task(task)
    hypotheses = _hypotheses(task, {o["id"] for o in bundle["observations"]})
    decisions = {d["observation_id"]: d for d in comparison["decisions"]}
    sources = {s["id"]: s for s in bundle["source_versions"]}
    cards, adoption_gaps = [], []
    for observation in bundle["observations"]:
        oid = observation["id"]
        decision = decisions.get(oid)
        adopted = decision is not None and decision["decision"] == "adopted"
        gaps = [] if adopted else [_gap("observation_not_adopted", "此观察未明确采用，保留原披露值但不参与计算。", [oid])]
        card = {**deepcopy(observation), "observation_id": oid, "currency": "USD",
                "currency_basis": "registered_unit_USD_million",
                "source_version": deepcopy(sources[observation["source_version_id"]]),
                "locatability": "registered_source_locatable",
                "adoption": {"status": decision["decision"] if decision else "unassessed", "note": decision["note"] if decision else ""},
                "usable_for_calculation": adopted, "gaps": gaps}
        del card["id"]
        cards.append(card)
        adoption_gaps.extend(gaps)
    by_id = {card["observation_id"]: card for card in cards}
    annual = deepcopy(comparison["annual_changes"])
    for change in annual:
        change["inputs"] = [{field: deepcopy(by_id[oid][field]) for field in (
            "observation_id", "issuer_id", "year", "metric", "raw_value", "raw_signed_value", "normalized_value",
            "transformation", "unit", "period", "adoption", "source")}
            for oid in change["candidate_observation_ids"]]
    computed = sum(change["status"] == "computed" for change in annual)
    comparison_gaps = [_gap(reason["code"], reason["message"], change["candidate_observation_ids"])
                       for change in annual for reason in change["reasons"]]
    comparison_gaps.extend(_gap(reason["code"], reason["message"], row["candidate_observation_ids"])
                           for row in comparison["cross_company"] for reason in row["reasons"])
    explanation_gaps = [
        _gap("operating_notes_not_registered", "本包未登记经营附注；不能据此判断企业是否已在别处披露。"),
        _gap("company_explanation_not_registered", "本包未登记公司对经营变化的明示解释原文。"),
        _gap("independent_corroboration_missing", "本包没有对经营解释的独立核实记录。"),
    ]
    accounting = []
    if bridge is None:
        explanation_gaps.append(_gap("accounting_bridge_not_attached", "此任务未附登记的逐行会计桥。"))
    else:
        for change in bridge["changes"]:
            ids = change["reported_cfo_observation_ids"]
            complete = all(by_id[oid]["usable_for_calculation"] for oid in ids)
            gaps = [] if complete else [_gap("accounting_inputs_not_adopted", "会计恒等式可以复算，但此任务未采用两期 CFO，不能将其作为本任务结论。", ids)]
            accounting.append({"issuer_id": change["issuer_id"], "company": change["company"],
                               "status": "available" if complete and change["status"] == "reconciled" else "blocked",
                               "input_adoption_complete": complete, "calculation": deepcopy(change), "gaps": gaps})
            explanation_gaps.extend(gaps)
    contracts = {c["id"]: c for c in task_contract_catalog()["contracts"]}
    tasks = [
        {"contract_id": "metric_lookup", "title": contracts["metric_lookup"]["title"], "status": "evidence_available",
         "requirements": [
             _requirement("registered_values", "原值、定义、期间与来源版本", True, "八项观察均与固定登记材料一致；零值不会由缺项补出。"),
             _requirement("source_location", "原页证据定位", True, "登记来源页码与坐标齐全；本次未重新读取 PDF。"),
             _requirement("adoption", "计算用途的明确采用", not adoption_gaps, "采用决定另列，不改变原披露值或客观可定位性。")],
         "results": {"cards": cards}, "gaps": adoption_gaps,
         "summary": {"registered_count": len(cards), "locatable_count": len(cards),
                     "adopted_count": len(comparison["adopted_observation_ids"]),
                     "rejected_count": len(comparison["rejected_observation_ids"]),
                     "unassessed_count": len(comparison["unassessed_observation_ids"])}},
        {"contract_id": "comparable_change", "title": contracts["comparable_change"]["title"],
         "status": "computed" if computed == len(annual) else "partial" if computed else "blocked",
         "requirements": [
             _requirement("adopted_comparable_pairs", "同公司同口径、连续年度且双方采用", computed == len(annual), "沿用登记比较规则；输入、公式及阻断原因逐项保留。"),
             _requirement("cross_company_ranking", "跨公司排名的可比依据", False, "财年边界不同且非严格同行，本组只能并列。")],
         "results": {"annual_changes": annual, "cross_company": deepcopy(comparison["cross_company"])},
         "gaps": comparison_gaps, "summary": {"annual_computed": computed, "annual_blocked": len(annual) - computed,
                                                  "cross_company_rankings": 0}},
        {"contract_id": "operating_explanation", "title": contracts["operating_explanation"]["title"], "status": "insufficient",
         "requirements": [
             _requirement("accounting_decomposition", "可采用的会计分解", bool(accounting) and all(a["status"] == "available" for a in accounting), "仅检查会计项目加总与年度差额；不能解释经济原因。"),
             _requirement("operating_notes", "经营附注", False, "当前任务材料未登记。"),
             _requirement("company_explanation", "公司明示解释", False, "当前任务材料未登记。"),
             _requirement("corroboration", "独立核实", False, "当前任务材料未登记。")],
         "results": {"accounting_changes": accounting, "company_explanations": [],
                     "company_explanation_status": "not_registered", "hypotheses": hypotheses},
         "gaps": explanation_gaps,
         "summary": {"accounting_available": sum(a["status"] == "available" for a in accounting),
                     "company_explanations_registered": 0, "unverified_candidates": len(hypotheses), "causal_conclusions": 0}},
    ]
    return {
        "schema_version": 1, "rule_version": ACCEPTANCE_RULE_VERSION, "task_id": task["id"],
        "revision": task["revision"], "bundle_id": bundle["id"], "bundle_sha256": task["bundle_sha256"],
        "store_version": task["store_version"], "evaluation_scope": "current_read_only_assessment",
        "input_binding": {"kind": "current_task_revision", "task_id": task["id"], "revision": task["revision"],
                          "bundle_sha256": task["bundle_sha256"],
                          "associated_snapshot_id": task.get("snapshot_id"), "associated_snapshot_sha256": task.get("snapshot_sha256"),
                          "historical_snapshot_evaluated": False},
        "scope": deepcopy(_SCOPE), "question": deepcopy(task.get("question")),
        "workflow_status": deepcopy(task.get("status")), "historical_snapshot_rewritten": False,
        "automatic_approval": False, "tasks": tasks, "limitations": list(_LIMITATIONS),
    }
