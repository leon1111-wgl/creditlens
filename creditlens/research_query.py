"""Explicit selectors over registered research evidence, without language parsing."""
from __future__ import annotations

from copy import deepcopy
from decimal import Context, localcontext
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .company_attribution import CompanyAttributionError, build_company_attribution
from .research_acceptance import ResearchAcceptanceError, build_task_acceptance


QUERY_RULE_VERSION = "research-query-1"


class ResearchQueryError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.code, self.path, self.message = code, path, message
        super().__init__(f"{code}: {message}")


class ObservationSelector(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    issuer_id: str = Field(min_length=1, max_length=32)
    metric: str = Field(min_length=1, max_length=64)
    year: int = Field(ge=1, le=9999)

    @field_validator("issuer_id", "metric")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A nonblank selector is required")
        # Identity is exact; no aliases, case folding or natural-language mapping.
        return value


class ResearchEvidenceQuery(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    revision: int = Field(ge=1)
    task_kind: Literal["metric_lookup", "comparable_change", "operating_evidence"]
    observations: list[ObservationSelector] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def cardinality(self):
        expected = 1 if self.task_kind == "metric_lookup" else 2
        if len(self.observations) != expected:
            raise ValueError("Wrong number of selectors for this task kind")
        return self


def _digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(raw.encode("utf8")).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ResearchQueryError("invalid_query", "request", "查询须为可编码的有限 JSON 数据。") from exc


def _gap(code, message, ids=()):
    return {"code": code, "message": message, "observation_ids": list(ids)}


def _key(value):
    return value["issuer_id"], value["metric"], value["year"]


def _request(request):
    try:
        raw = request.model_dump() if isinstance(request, ResearchEvidenceQuery) else request
        if type(raw) is not dict:
            raise ValueError("Object required")
        query = ResearchEvidenceQuery.model_validate(raw).model_dump()
        _digest(query)
        return query
    except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError) as exc:
        # Do not echo Pydantic input values or unknown field names into API errors.
        raise ResearchQueryError("invalid_query", "request", "查询字段、类型或所选项数量不符合固定契约。") from exc


def _pair_gaps(cards, comparison):
    first, second = cards
    ids = [c["observation_id"] for c in cards]
    gaps = []
    if ids[0] == ids[1]:
        gaps.append(_gap("duplicate_selection", "两期不能重复选择同一项观察。", ids))
    if first["issuer_id"] != second["issuer_id"]:
        gaps.append(_gap("issuer_mismatch", "当前只计算同一公司的年度变化，跨公司只能保留并列证据。", ids))
        existing = [row for row in comparison["cross_company"]
                    if set(row["candidate_observation_ids"]) == set(ids)]
        for row in existing:
            gaps.extend(_gap(r["code"], r["message"], ids) for r in row["reasons"])
        if not existing:
            gaps.append(_gap("not_strict_peer_benchmark", "该样例没有建立跨公司排名的可比契约。", ids))
            if first["period"] != second["period"]:
                gaps.append(_gap("actual_period_mismatch", "所选事实的实际起止日期或年度日历不同，不能按 FY 标签视为同一期。", ids))
    if first["metric"] != second["metric"]:
        gaps.append(_gap("metric_mismatch", "不同指标不能作为同一指标的基期和本期。", ids))
    if [c["year"] for c in cards] != [2024, 2025]:
        gaps.append(_gap("annual_order_mismatch", "当前仅支持按 FY2024、FY2025 顺序选取两期，不自动重排。", ids))
    return gaps


def _operating(task, acceptance, cards, bindings):
    issuer = cards[0]["issuer_id"]
    ids = [c["observation_id"] for c in cards]
    adopted = all(c["usable_for_calculation"] for c in cards)
    prior = acceptance["tasks"][2]["results"]
    rows = [row for row in prior["accounting_changes"] if row["issuer_id"] == issuer]
    accounting = deepcopy(rows[0]) if rows else None
    gaps = [_gap("independent_corroboration_missing", "没有登记独立核实；会计分解、公司说明和待查假设均不能证明原因。", ids),
            _gap("operating_notes_not_registered", "本查询未登记额外经营附注，不表示企业没有披露。", ids)]
    if accounting is None:
        gaps.append(_gap("accounting_bridge_not_attached", "此任务没有附会计桥，不能用其他任务的会计分解代替。", ids))
    else:
        gaps.extend(deepcopy(accounting["gaps"]))
        if accounting["status"] != "available":
            accounting["calculation"] = None
        bridge = task["cash_flow_bridge"]
        for source in bridge["source_bundle"]["source_versions"]:
            if source["issuer_id"] == issuer:
                bindings.append({"kind": "accounting_source", "issuer_id": issuer,
                                 "source_version_id": source["id"], "source": deepcopy(source),
                                 "observation_ids": ids, "source_bundle_sha256": bridge["source_bundle_sha256"]})
    if not adopted:
        gaps.append(_gap("observation_not_adopted", "必须采用两期 CFO 才能把相关数值关联为本任务的计算输入。", ids))
    try:
        attribution = build_company_attribution(task)
    except (CompanyAttributionError, ResearchAcceptanceError) as exc:
        raise ResearchQueryError(exc.code, "company_attribution", exc.message) from exc
    entries = [deepcopy(entry) for entry in attribution["entries"] if entry["issuer_id"] == issuer]
    coverage = [deepcopy(item) for item in attribution["coverage"] if item["issuer_id"] == issuer]
    for entry in entries:
        bindings.append({"kind": "company_statement", "issuer_id": issuer, "entry_id": entry["id"],
                         "source": deepcopy(entry["source"]), "observation_ids": deepcopy(entry["evidence_ids"]),
                         "catalog_sha256": attribution["catalog_sha256"]})
        gaps.extend(deepcopy(entry["gaps"]))
    if not entries:
        gaps.append(_gap("company_explanation_not_registered", "当前目录未登记该公司的解释；不能写成公司没有披露。", ids))
    hypotheses = []
    for hypothesis in prior["hypotheses"]:
        overlap = [oid for oid in hypothesis["evidence_ids"] if oid in ids]
        if overlap:
            outside = [oid for oid in hypothesis["evidence_ids"] if oid not in ids]
            hypotheses.append({**deepcopy(hypothesis), "selected_evidence_ids": overlap,
                               "out_of_query_evidence_ids": outside, "all_references_selected": not outside})
            if outside:
                gaps.append(_gap("hypothesis_references_outside_query", "某待查假设还引用了本次选择以外的观察，本查询不检查那些证据。", overlap))
    result = {"accounting_decomposition": accounting, "company_statements": entries,
              "company_coverage": coverage, "candidate_hypotheses": hypotheses,
              "independent_verification": {"status": "missing", "independently_verified": False, "causality_verified": False},
              "attribution_catalog_sha256": attribution["catalog_sha256"],
              "accounting_bridge_sha256": task.get("cash_flow_bridge_sha256"), "causal_conclusion": None}
    return result, "insufficient" if adopted else "blocked", gaps


def build_research_query(task: dict, request: dict | ResearchEvidenceQuery) -> dict:
    """Inspect the current task first, then project only explicit evidence scope.

    Authentication and loading a tenant-scoped current task belong to the caller.
    No query or result is appended to an existing task, approval or snapshot.
    """
    query = _request(request)
    with localcontext(Context(prec=40)):
        try:
            acceptance = build_task_acceptance(task)
        except ResearchAcceptanceError as exc:
            raise ResearchQueryError(exc.code, "task", exc.message) from exc
        if query["revision"] != acceptance["revision"]:
            raise ResearchQueryError("stale_revision", "request.revision", "任务版本已变化，请重新读取并选择查询范围。")
        available = {_key(card): card for card in acceptance["tasks"][0]["results"]["cards"]}
        selected, cards, gaps = [], [], []
        for selector in query["observations"]:
            card = available.get(_key(selector))
            selected.append({"selector": deepcopy(selector), "status": "registered" if card else "not_registered",
                             "card": deepcopy(card) if card else None})
            if card:
                cards.append(card)
            else:
                gaps.append(_gap("observation_not_registered", "本任务没有登记所选公司、指标与年度的组合；不代表未披露，也不补零。"))
        bindings, seen = [], set()
        for card in cards:
            key = card["source_version_id"]
            if key not in seen:
                seen.add(key)
                bindings.append({"kind": "financial_disclosure", "issuer_id": card["issuer_id"], "source_version_id": key,
                                 "source": deepcopy(card["source_version"]),
                                 "observation_ids": list(dict.fromkeys(c["observation_id"] for c in cards if c["source_version_id"] == key))})
        calculation, operating = None, None
        comparison = acceptance["tasks"][1]["results"]
        if gaps:
            status = "not_registered"
        elif len(cards) == 2 and cards[0]["observation_id"] == cards[1]["observation_id"]:
            status = "blocked"
            gaps = [_gap("duplicate_selection", "两期不能重复选择同一项观察。", [cards[0]["observation_id"]])]
        elif query["task_kind"] == "metric_lookup":
            status = "evidence_available"
            gaps = deepcopy(cards[0]["gaps"])
        else:
            pair_gaps = _pair_gaps(cards, comparison)
            if query["task_kind"] == "comparable_change":
                if pair_gaps:
                    status, gaps = "blocked", pair_gaps
                else:
                    ids = [c["observation_id"] for c in cards]
                    matches = [r for r in comparison["annual_changes"] if r["candidate_observation_ids"] == ids]
                    if len(matches) != 1:
                        raise ResearchQueryError("comparison_binding_mismatch", "task.comparison", "选定输入未唯一绑定登记比较。")
                    calculation = deepcopy(matches[0])
                    status = calculation["status"]
                    gaps = [_gap(r["code"], r["message"], ids) for r in calculation["reasons"]]
            elif pair_gaps or cards[0]["metric"] != "cfo":
                status = "unsupported_scope"
                gaps = pair_gaps + [_gap("operating_scope_unsupported", "当前经营证据查询仅支持同一公司 FY2024 至 FY2025 的两期 CFO。")]
            else:
                operating, status, gaps = _operating(task, acceptance, cards, bindings)
    query_sha = _digest(query)
    binding_sha = _digest(bindings)
    result = {
        "schema_version": 1, "rule_version": QUERY_RULE_VERSION, "query": query, "query_sha256": query_sha,
        "task_id": acceptance["task_id"], "revision": acceptance["revision"],
        "bundle_id": acceptance["bundle_id"], "bundle_sha256": acceptance["bundle_sha256"],
        "input_binding": {**deepcopy(acceptance["input_binding"]), "query_sha256": query_sha,
                          "selected_observation_ids": list(dict.fromkeys(c["observation_id"] for c in cards)),
                          "source_bindings_sha256": binding_sha, "acceptance_rule_version": acceptance["rule_version"]},
        "source_bindings": bindings, "source_bindings_sha256": binding_sha,
        "selected_evidence": selected, "status": status, "gaps": gaps,
        "calculation": calculation, "operating_evidence": operating,
        "read_only": True, "automatic_approval": False, "included_in_frozen_snapshot": False,
        "historical_snapshot_rewritten": False, "natural_language_question_understood": False,
        "independently_verified": False, "causality_verified": False, "ranking": None,
        "scope": {"kind": "explicit_registered_observation_query", "material_mode": "current_verified_materials",
                  "historical_as_of_supported": False, "prediction": False, "question_text_used": False},
        "limitations": [
            "仅处理显式公司、指标、年度选择；不理解或回答自由填写的研究标题。",
            "来源为固定 FY2025 报告比较列及登记说明，非实时查询或历史可得版本。",
            "PPE 现金支出不代表全部资本投入，原始负号与流出额归一过程分别保留。",
            "会计分解不是原因；公司说明尚未独立核实；假设文字不能改变证据支持等级。",
            "只读结果不继承旧审批，也不修改已提交快照；来源摘要不是签名或真实性证明。",
        ],
    }
    result["result_sha256"] = _digest(result)
    return result
