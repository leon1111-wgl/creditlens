"""Source-bound accounting margin comparison, not a causal or model study."""
from __future__ import annotations

from copy import deepcopy
from decimal import Context, Decimal, localcontext, ROUND_HALF_EVEN, ROUND_HALF_UP
from fractions import Fraction
import hashlib
import json
from pathlib import Path

from .diligence import CasePack, digest
from .research_materials import build_material_bundle
from .visual_store import VisualStore

EXPERIMENT_VERSION = "margin-bridge-development-1"
SOURCE = "data/examples/margin-source-observations.json"
REPORT = "artifacts/margin-bridge-report.json"
SOURCE_RAW_SHA256 = "b61ec2a0c7cf74a40c9fbbbe98442b6cd95e361b13df7b166318e6f6436d25dd"
SOURCE_CANONICAL_SHA256 = "c4fbb371bf7f11afd04d2efb877f7417f13f4f9df88480932b2a88e3dce65776"
IMPLEMENTATION_FILES = ("creditlens/margin_bridge.py", "creditlens/margin_experiment.py",
                        "creditlens/diligence.py", "creditlens/visual.py", "creditlens/visual_store.py",
                        "creditlens/table_lookup.py", "creditlens/research_materials.py",
                        "creditlens/experiment_paths.py", "scripts/evaluate_margin_bridge.py")
SUPPORT_FILES = ("artifacts/document-ai-report.json", "configs/document-ai.json",
                 "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json")
EXPECTED_DISPLAYS = {
    "msft": {"base_margin_percent": "69.764444", "current_margin_percent": "68.823742",
             "mix_effect_pp": "-0.411185", "within_effect_pp": "-0.529517", "total_change_pp": "-0.940701"},
    "aapl": {"base_margin_percent": "46.206350", "current_margin_percent": "46.905164",
             "mix_effect_pp": "0.616372", "within_effect_pp": "0.082442", "total_change_pp": "0.698814"},
}
SCOPE = {"previously_observed_development_material": True, "registered_company_cases": 2,
         "registered_amount_observations": 28, "independent_human_gold": 0,
         "new_ocr_calls": 0, "model_calls": 0, "raw_pdf_bytes_checked": False,
         "full_ocr_replayed": False, "causal_explanation": False, "segment_analysis": False,
         "cross_company_ranking": False, "historical_as_of_verified": False,
         "amount_bboxes_are_approximate_locators_not_evaluation_gold": True}
LIMITATIONS = [
    "两家公司分别比较FY2024与FY2025，不把四个期间或28项重复参与计算的金额当作独立模型题。",
    "金额来自同份FY2025报告中的原页工具辅助整理，独立人工确认pending；未执行新OCR或模型。",
    "产品与服务是本次收入和成本分类，不等于公司经营分部，两家公司分类定义不可直接排名。",
    "构成项与分类自身毛利率项是对称会计分解，不证明价格、销量、成本驱动或任何经营原因。",
    "金额框沿用OCR父行近似定位，可能包含相邻货币符号；用于回查，不作为精准位置金标准。",
    "FY2024是FY2025报告比较列，不是当时可得的历史版本；单位与合并范围沿已登记来源而非仅凭货币符号推断。",
]


class MarginExperimentError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def _require(condition, code, path):
    if not condition:
        raise MarginExperimentError(code, path)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _read(path, limit=2_000_000):
    try:
        path = Path(path)
        _require(path.stat().st_size <= limit, "input_too_large", path.name)
        raw = path.read_bytes()
        _require(len(raw) <= limit, "input_too_large", path.name)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        _require(type(value) is dict, "invalid_json", path.name)
        return raw, value
    except MarginExperimentError:
        raise
    except (ValueError, OSError, TypeError, UnicodeError) as exc:
        raise MarginExperimentError("input_unavailable", Path(path).name) from exc


def _file_hashes(root):
    try:
        return {name: hashlib.sha256((root/name).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()
                for name in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc:
        raise MarginExperimentError("implementation_unavailable", "implementation") from exc


def _display(value: Fraction):
    with localcontext(Context(prec=100, rounding=ROUND_HALF_EVEN)):
        number = Decimal(value.numerator)/Decimal(value.denominator)
        rounded = number.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        return format(abs(rounded) if rounded.is_zero() else rounded, ".6f")


def fraction_oracle(payload):
    """Independent exact rational check; never use core output as expectation."""
    states = []
    for statement in sorted(payload["statements"], key=lambda s: s["year"]):
        rows = {c["id"]: {key: Fraction(c[key]["value"]) for key in ("revenue", "cost")}
                for c in statement["categories"]}
        total_revenue = sum(c["revenue"] for c in rows.values())
        total_cost = sum(c["cost"] for c in rows.values())
        states.append({"rows": rows, "revenue": total_revenue, "cost": total_cost,
                       "margin": (total_revenue-total_cost)/total_revenue})
    before, after = states
    mix, within = Fraction(0), Fraction(0)
    components = []
    for category in before["rows"]:
        old, new = before["rows"][category], after["rows"][category]
        old_weight, new_weight = old["revenue"]/before["revenue"], new["revenue"]/after["revenue"]
        old_margin, new_margin = (old["revenue"]-old["cost"])/old["revenue"], (new["revenue"]-new["cost"])/new["revenue"]
        component_mix = (new_weight-old_weight)*(old_margin+new_margin)/2*100
        component_within = (new_margin-old_margin)*(old_weight+new_weight)/2*100
        mix += component_mix
        within += component_within
        components.append({"category_id": category, "mix_contribution_pp": _display(component_mix),
                           "within_contribution_pp": _display(component_within),
                           "total_contribution_pp": _display(component_mix+component_within)})
    delta = (after["margin"]-before["margin"])*100
    values = {"previous_margin_percent": before["margin"]*100, "current_margin_percent": after["margin"]*100,
              "mix_effect_pp": mix, "within_effect_pp": within, "total_change_pp": delta,
              "explained_change_pp": mix+within, "residual_pp": delta-mix-within}
    return {"method": "independent_exact_fractions", "display": {k: _display(v) for k, v in values.items()},
            "exact": {k: f"{v.numerator}/{v.denominator}" for k, v in values.items()}, "components": components}


def _synthetic_payload():
    source = {"id": "synthetic-margin-source", "url": "https://example.invalid/synthetic-margin", "sha256": "a"*64}
    statements = []
    for year, product_revenue, product_cost, service_revenue, service_cost in ((2024, 100, 60, 100, 20), (2025, 150, 90, 50, 10)):
        period = {"start": f"{year}-01-01", "end": f"{year}-12-31", "days": 366 if year == 2024 else 365,
                  "kind": "annual", "fiscal_calendar": "calendar_year"}
        def fact(name, amount):
            return {"id": f"synthetic:{year}:{name}", "label": name, "raw_value": str(amount), "value": str(amount),
                    "value_status": "reported", "source_id": source["id"], "source_sha256": source["sha256"],
                    "unit": "USD_million", "period": {"start": period["start"], "end": period["end"]},
                    "location": {"page": 1, "locator": "Synthetic row, not an official statement", "amount_bbox": {"x0": 1, "top": 1, "x1": 10, "bottom": 10}}}
        categories = [{"id": name, "label": name, "definition": name+" synthetic revenue classification",
                       "revenue": fact(name+"-revenue", revenue), "cost": fact(name+"-cost", cost)}
                      for name, revenue, cost in (("products", product_revenue, product_cost), ("services", service_revenue, service_cost))]
        for category in categories:
            for role in ("revenue", "cost"):
                category[role]["label"] = category["label"]
        statements.append({"year": year, "issuer_id": "SYNTHETIC", "source_id": source["id"], "source_sha256": source["sha256"],
                           "unit": "USD_million", "period": period,
                           "scope": {"consolidation": "consolidated", "statement_basis": "as_reported_statement_total", "operations_partition": "unknown"},
                           "classification": {"basis": "product_service_revenue_classification", "definition": "Synthetic product and service classification"},
                           "categories": categories, "total_revenue": fact("total-revenue", product_revenue+service_revenue),
                           "total_cost": fact("total-cost", product_cost+service_cost),
                           "gross_profit": fact("gross-profit", product_revenue+service_revenue-product_cost-service_cost)})
    return {"schema_version": 1, "id": "synthetic-margin-pair", "issuer_id": "SYNTHETIC", "company": "Synthetic Company",
            "source": source, "statements": statements}


def synthetic_controls():
    cases = []
    def add(identifier, title, mutate=None, expected="blocked", display=None):
        payload = _synthetic_payload()
        if mutate:
            mutate(payload)
        cases.append({"id": identifier, "title": title, "provenance": "synthetic_control",
                      "input": payload, "expected": {"status": expected, "display": display}})
    def set_value(fact, value):
        fact.update(value=str(value), raw_value=str(value))
    add("mix-only", "构成变化正控制", expected="computed",
        display={"mix_effect_pp": "-10.000000", "within_effect_pp": "0.000000", "total_change_pp": "-10.000000"})
    def within_only(p):
        s = p["statements"][1]
        for c, revenue, cost in ((s["categories"][0], 100, 50), (s["categories"][1], 100, 10)):
            set_value(c["revenue"], revenue); set_value(c["cost"], cost)
        set_value(s["total_cost"], 60); set_value(s["gross_profit"], 140)
    add("within-only", "分类自身毛利率变化正控制", within_only, "computed",
        {"mix_effect_pp": "0.000000", "within_effect_pp": "10.000000", "total_change_pp": "10.000000"})
    def negative_margin(p):
        for s in p["statements"]:
            for c in s["categories"]:
                set_value(c["revenue"], 100); set_value(c["cost"], 120)
            set_value(s["total_revenue"], 200); set_value(s["total_cost"], 240); set_value(s["gross_profit"], -40)
    add("negative-margin", "负毛利仍保持符号的正控制", negative_margin, "computed",
        {"mix_effect_pp": "0.000000", "within_effect_pp": "0.000000", "total_change_pp": "0.000000"})
    add("revenue-sum", "分类收入不能加总到总收入", lambda p: set_value(p["statements"][1]["total_revenue"], 201))
    add("cost-sum", "分类成本不能加总到总成本", lambda p: set_value(p["statements"][1]["total_cost"], 101))
    add("gross-identity", "总收入减成本不等于毛利", lambda p: set_value(p["statements"][1]["gross_profit"], 101))
    add("missing-category", "缺少一个分类不能补零", lambda p: p["statements"][1]["categories"].pop())
    add("duplicate-category", "重复分类不能重复加总", lambda p: p["statements"][1]["categories"].append(deepcopy(p["statements"][1]["categories"][0])))
    add("classification-change", "不同年度分类定义改变", lambda p: p["statements"][1]["classification"].update(definition="Changed classification"))
    add("wrong-issuer", "混入另一公司年度", lambda p: p["statements"][1].update(issuer_id="OTHER"))
    add("wrong-source", "金额来自另一来源版本", lambda p: p["statements"][1]["categories"][0]["cost"].update(source_sha256="b"*64))
    add("wrong-unit", "同一金额误标为其他币种单位", lambda p: p["statements"][1]["categories"][0]["cost"].update(unit="EUR_million"))
    add("wrong-period", "金额混入另一期间", lambda p: p["statements"][1]["categories"][0]["cost"]["period"].update(end="2024-12-31"))
    def missing_value(p):
        p["statements"][1]["categories"][0]["cost"].update(value_status="not_disclosed", value=None, raw_value=None)
    add("not-disclosed", "未披露金额不当作零", missing_value)
    add("zero-category-revenue", "分类收入为零不能计算该分类率", lambda p: set_value(p["statements"][1]["categories"][0]["revenue"], 0))
    add("negative-cost", "负成本不作普通成本口径", lambda p: set_value(p["statements"][1]["categories"][0]["cost"], -90))
    add("wrong-sign", "显示值括号与数值符号不符", lambda p: p["statements"][1]["categories"][0]["cost"].update(raw_value="(90)"))
    add("wrong-currency-marker", "人民币符号不能被当作美元数值", lambda p: p["statements"][1]["categories"][0]["cost"].update(raw_value="¥90"))
    add("segment-scope", "经营分部范围冒充合并收入分类", lambda p: p["statements"][1]["scope"].update(statement_basis="segment_statement"))
    return cases


def _source_inputs(root):
    """Pin source bytes and bind each registered row to prior material identity."""
    root = Path(root).resolve()
    try:
        tracked = {}
        for name in (SOURCE, *SUPPORT_FILES):
            path = root/name
            _require(path.resolve().is_relative_to(root), "source_path_escape", name)
            raw, value = _read(path)
            tracked[name] = (raw, value)
        raw, source = tracked[SOURCE]
        _require(hashlib.sha256(raw).hexdigest() == SOURCE_RAW_SHA256 and digest(source) == SOURCE_CANONICAL_SHA256,
                 "source_registry_changed", SOURCE)
        _require(type(source["schema_version"]) is int and source["schema_version"] == 1
                 and source["provenance"]["human_gold"] is False
                 and source["provenance"]["independent_human_confirmation"] == "pending",
                 "source_scope_changed", SOURCE)
        with localcontext(Context(prec=100, rounding=ROUND_HALF_EVEN)):
            visual = VisualStore(root)._validate(deepcopy(tracked["artifacts/document-ai-report.json"][1]))
            material_bundle = build_material_bundle(root)
            materials = {s["issuer_id"]: s for s in material_bundle["source_versions"]}
            docs = {d["id"]: d for d in visual["documents"]}
            result = []
            _require(len(source["documents"]) == 2 and {d["id"] for d in source["documents"]} == {"msft", "aapl"},
                     "source_inventory_changed", "documents")
            for doc in source["documents"]:
                key = doc["id"]
                _require(doc["pack_path"] == f"data/examples/{key}-fy2025-pdf.json", "pack_path_mismatch", key)
                pack_json = tracked[doc["pack_path"]][1]
                pack = CasePack.model_validate(pack_json)
                expected_source = pack.sources[0].model_dump(mode="json")
                _require(digest(pack_json) == digest(pack.model_dump(mode="json")) == doc["pack_canonical_sha256"]
                         == docs[key]["pack_sha256"] == materials[doc["issuer_id"]]["pack_sha256"],
                         "pack_binding_mismatch", key)
                _require(doc["issuer_id"] == pack.entity_id and doc["company"] == pack.company
                         and all(doc["source"][field] == value for field, value in expected_source.items())
                         and doc["source"]["pdf_sha256"] == expected_source["sha256"]
                         and docs[key]["source"] == expected_source, "source_binding_mismatch", key)
                _require(len(doc["pages"]) == 1, "page_inventory_mismatch", key)
                page = doc["pages"][0]
                visual_page = next(p for p in docs[key]["pages"] if p["page"] == page["page"])
                _require(all(page[name] == visual_page[name] for name in ("page", "width", "height", "image_sha256", "ocr_sha256")),
                         "page_binding_mismatch", key)
                evidence = next(o for o in material_bundle["observations"] if o["issuer_id"] == doc["issuer_id"])["basis"]["scope_evidence"]
                _require(any(e["field"] == "consolidation_scope" and e["value"] == "consolidated"
                             and e["source_sha256"] == expected_source["sha256"] and e["page"] == doc["scope_evidence"]["page"] for e in evidence),
                         "consolidation_basis_mismatch", key)
                periods = {p["year"]: p for p in doc["periods"]}
                _require(len(periods) == len(doc["periods"]) == 2 and set(periods) == {2024, 2025}, "period_inventory_mismatch", key)
                indexed = {(f["year"], f["role"], f["category"]): f for f in doc["facts"]}
                expected_keys = {(year, role, category) for year in (2024, 2025) for role, category in
                                 (("revenue", "products"), ("revenue", "services"), ("revenue", "total"),
                                  ("cost", "products"), ("cost", "services"), ("cost", "total"), ("gross_profit", "total"))}
                _require(len(indexed) == len(doc["facts"]) == 14 and set(indexed) == expected_keys
                         and len({f["id"] for f in doc["facts"]}) == 14, "fact_inventory_mismatch", key)
                statements = []
                for year in (2024, 2025):
                    period = periods[year]
                    old_revenue = next(f for f in pack.facts if f.id == f"revenue-{year}")
                    revenue = indexed[(year, "revenue", "total")]
                    _require(revenue["raw_value"] == old_revenue.raw_value
                             and revenue["normalized_value"] == format(old_revenue.value, "f")
                             and revenue["unit"] == old_revenue.unit == period["unit"] == doc["source"]["unit"]
                             and revenue["page"] == old_revenue.pdf_location.page == page["page"]
                             and {"start": period["start"], "end": period["end"]} == revenue["period"]
                             == {"start": old_revenue.period_start.isoformat(), "end": old_revenue.period_end.isoformat()},
                             "prior_revenue_binding_mismatch", revenue["id"])
                    old_period = next(o["period"] for o in material_bundle["observations"] if o["issuer_id"] == doc["issuer_id"] and o["year"] == year)
                    _require(all(period[name] == old_period[name] for name in ("start", "end", "days", "fiscal_calendar")),
                             "prior_period_binding_mismatch", str(year))
                    for fact in (f for f in doc["facts"] if f["year"] == year):
                        box = fact["amount_bbox"]
                        _require(fact["source_id"] == expected_source["id"] and fact["source_sha256"] == expected_source["sha256"]
                                 and fact["unit"] == period["unit"] and fact["period"] == revenue["period"]
                                 and fact["page"] == page["page"] and fact["bbox_role"] == "approximate_locator"
                                 and fact["independent_human_confirmation"] == "pending" and fact["human_gold"] is False
                                 and fact["amount_parent"]["bbox"] == box
                                 and 0 <= box["x0"] < box["x1"] <= page["width"]
                                 and 0 <= box["top"] < box["bottom"] <= page["height"],
                                 "fact_source_or_locator_mismatch", fact["id"])
                        _require(fact["statement_scope"] == revenue["statement_scope"], "fact_scope_mismatch", fact["id"])
                    def convert(role, category):
                        f = indexed[(year, role, category)]
                        return {"id": f["id"], "label": f["label"], "raw_value": f["raw_value"], "value": f["normalized_value"],
                                "value_status": f["value_status"], "source_id": f["source_id"], "source_sha256": f["source_sha256"],
                                "unit": f["unit"], "period": deepcopy(f["period"]),
                                "location": {"page": f["page"], "locator": f["locator"], "amount_bbox": deepcopy(f["amount_bbox"])}}
                    categories = [{**deepcopy(category), "revenue": convert("revenue", category["id"]), "cost": convert("cost", category["id"])}
                                  for category in period["categories"]]
                    statements.append({"year": year, "issuer_id": doc["issuer_id"], "source_id": expected_source["id"],
                                       "source_sha256": expected_source["sha256"], "unit": period["unit"],
                                       "period": {**{name: period[name] for name in ("start", "end", "days", "fiscal_calendar")}, "kind": "annual"},
                                       "scope": deepcopy(revenue["statement_scope"]),
                                       "classification": {"basis": "product_service_revenue_classification", "definition": period["classification_definition"]},
                                       "categories": categories, "total_revenue": convert("revenue", "total"),
                                       "total_cost": convert("cost", "total"), "gross_profit": convert("gross_profit", "total")})
                payload = {"schema_version": 1, "id": f"{key}-margin-fy2024-fy2025", "issuer_id": doc["issuer_id"], "company": doc["company"],
                           "source": {name: expected_source[name] for name in ("id", "url", "sha256")}, "statements": statements}
                result.append({"document": deepcopy(doc), "payload": payload})
        # This detects observable changes across assembly, not a filesystem lock.
        for name, (before, _) in tracked.items():
            _require(_read(root/name)[0] == before, "source_changed_during_read", name)
        # Prior packs already use canonical JSON bindings. Formatting differences
        # between Windows worktrees must not invent a changed financial version.
        return source, result, {name: digest(value) for name, (_, value) in tracked.items()}
    except MarginExperimentError:
        raise
    except (ValueError, KeyError, TypeError, StopIteration, AttributeError) as exc:
        raise MarginExperimentError("source_binding_invalid", "source") from exc


def _oracle_check(payload, result):
    oracle = fraction_oracle(payload)
    display = result["calculation"]["display"]
    expected = {("base_margin_percent" if k == "previous_margin_percent" else k): v for k, v in oracle["display"].items()}
    _require(all(display[k] == value for k, value in expected.items()), "independent_fraction_disagreement", payload["id"])
    _require(display["components"] == oracle["components"], "independent_component_disagreement", payload["id"])
    with localcontext(Context(prec=100, rounding=ROUND_HALF_EVEN)):
        _require(abs(Decimal(result["calculation"]["residual_pp"])) <= Decimal("1e-24"), "unexplained_residual", payload["id"])
        rounding = Decimal(expected["total_change_pp"])-Decimal(expected["mix_effect_pp"])-Decimal(expected["within_effect_pp"])
        _require(Decimal(display["displayed_residual_pp"]) == rounding, "display_rounding_mismatch", payload["id"])
    return {**oracle, "status": "matched", "arithmetic_residual_exact": "0", "display_rounding_residual_pp": str(rounding)}


def build_margin_bridge_report(root: Path) -> dict:
    from .margin_bridge import evaluate_margin_bridge
    root = Path(root).resolve()
    source, records, hashes = _source_inputs(root)
    cases, bindings = [], []
    for record in records:
        doc, payload = record["document"], record["payload"]
        result = evaluate_margin_bridge(payload)
        expected = EXPECTED_DISPLAYS[doc["id"]]
        _require(result["status"] == "computed", "registered_case_blocked", payload["id"])
        _require(all(result["calculation"]["display"][k] == v for k, v in expected.items()), "registered_expected_disagreement", payload["id"])
        oracle = _oracle_check(payload, result)
        cases.append({"id": payload["id"], "document_id": doc["id"], "company": doc["company"],
                      "provenance": "registered_official_disclosure_ai_assisted_reference",
                      "source": deepcopy(doc["source"]), "page": deepcopy(doc["pages"][0]),
                      "input": payload, "input_sha256": digest(payload), "result": result,
                      "registered_observations": deepcopy(doc["facts"]), "independent_oracle": oracle,
                      "expected_display": deepcopy(expected), "passed": True})
        bindings.append({"document_id": doc["id"], "issuer_id": doc["issuer_id"], "pack_path": doc["pack_path"],
                         "pack_canonical_sha256": doc["pack_canonical_sha256"], "source_pdf_sha256": doc["source"]["pdf_sha256"],
                         "scope_evidence": deepcopy(doc["scope_evidence"]), "page": deepcopy(doc["pages"][0]),
                         "registered_observation_ids": [f["id"] for f in doc["facts"]]})
    controls = []
    for case in synthetic_controls():
        result = evaluate_margin_bridge(case["input"])
        expected = case["expected"]
        passed = result["status"] == expected["status"]
        oracle = None
        if result["status"] == "computed":
            oracle = _oracle_check(case["input"], result)
            passed = passed and all(result["calculation"]["display"][key] == value for key, value in (expected["display"] or {}).items())
        controls.append({**case, "input_sha256": digest(case["input"]), "result": result,
                         "independent_oracle": oracle, "passed": passed})
    return {"schema_version": 1, "experiment_version": EXPERIMENT_VERSION,
            "source_registry": {"path": SOURCE, "raw_sha256": SOURCE_RAW_SHA256, "canonical_sha256": digest(source),
                                "raw_hash_basis": "registered_utf8_lf_bytes",
                                "provenance": deepcopy(source["provenance"]), "registered_at": source["registered_at"]},
            "source_file_sha256": hashes, "source_file_hash_basis": "canonical_json_sha256", "source_bindings": bindings,
            "implementation_sha256": _file_hashes(root), "implementation_hash_basis": "python_utf8_lf",
            "scope": deepcopy(SCOPE), "cases": cases,
            "controls": {"cases": controls, "summary": {"cases": len(controls), "passed": sum(c["passed"] for c in controls),
                                                        "failed": sum(not c["passed"] for c in controls)}},
            "summary": {"registered_company_cases": len(cases), "computed": len(cases), "blocked": 0,
                        "registered_amount_observations": sum(len(c["registered_observations"]) for c in cases),
                        "independent_human_gold": 0},
            "limitations": list(LIMITATIONS), "read_only": True, "automatic_approval": False}


def validate_margin_bridge_report(root: Path, report: dict) -> dict:
    try:
        expected = build_margin_bridge_report(root)
        # All fields, including controls and narrative scope, must reproduce.
        _require(digest(report) == digest(expected), "report_does_not_reproduce", "report")
        return deepcopy(expected)
    except MarginExperimentError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise MarginExperimentError("invalid_report", "report") from exc


def load_margin_bridge_snapshot(root: Path) -> dict:
    root = Path(root).resolve()
    _require((root/REPORT).resolve().is_relative_to(root), "report_path_escape", REPORT)
    raw, report = _read(root/REPORT)
    normalized = validate_margin_bridge_report(root, report)
    return {"schema_version": 1, "report_sha256": hashlib.sha256(raw).hexdigest(), "report": normalized,
            "read_only": True, "validation_scope": {"registered_source_and_calculation_replayed": True,
                                                    "raw_pdf_bytes_checked": False, "full_ocr_replayed": False}}


def margin_image_metadata(snapshot: dict, case_id: str) -> dict:
    """Use only the same validated snapshot passed to the API response."""
    _require(snapshot.get("read_only") is True, "unverified_snapshot", "snapshot")
    matches = [c for c in snapshot["report"]["cases"] if c["id"] == case_id]
    _require(len(matches) == 1, "unknown_margin_case", "case_id")
    case = matches[0]
    _require(case["document_id"] in {"msft", "aapl"}, "unknown_document", "case_id")
    return {"case_id": case_id, "document_id": case["document_id"], **{k: case["page"][k] for k in ("page", "width", "height", "image_sha256")},
            "pdf_sha256": case["source"]["pdf_sha256"], "source_url": case["source"]["url"]}
