"""Replay explicit research selectors in a temporary database, without an LLM."""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.diligence import digest
from creditlens.research_task_store import ResearchTaskStore
from creditlens.research_materials import build_material_bundle
from creditlens.company_attribution import load_company_attribution_catalog
from creditlens.workflow import Actor
from scripts.evaluate_row_disambiguation import output_path

VERSION = "research-query-development-1"
REPORT = "artifacts/research-query-report.json"
MAX_REPORT_BYTES = 2_000_000
NOTE = "Synthetic workflow setup; independent financial causes remain unverified."
IMPLEMENTATION_FILES = (
    "creditlens/research_query.py", "creditlens/research_acceptance.py",
    "creditlens/research_materials.py", "creditlens/company_attribution.py",
    "creditlens/cash_flow_sources.py", "creditlens/cash_flow_bridge.py",
    "creditlens/research_task_store.py", "creditlens/research_write_receipts.py",
    "creditlens/workflow.py", "creditlens/diligence.py", "scripts/evaluate_research_queries.py",
)
SOURCE_FILES = ("data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json",
                "data/research/cash-flow-bridge-v1.json", "data/sources/company-attributions.json")
# Fixed independently of the candidate query function. Parentheses are retained
# as negative cash outflows; registered PPE spending normalization is separate.
VALUES = {
    ("MSFT", "cfo", 2024): ("118,548", "118548", "118548"),
    ("MSFT", "cfo", 2025): ("136,162", "136162", "136162"),
    ("MSFT", "capex", 2024): ("(44,477)", "-44477", "44477"),
    ("MSFT", "capex", 2025): ("(64,551)", "-64551", "64551"),
    ("AAPL", "cfo", 2024): ("118,254", "118254", "118254"),
    ("AAPL", "cfo", 2025): ("111,482", "111482", "111482"),
    ("AAPL", "capex", 2024): ("(9,447)", "-9447", "9447"),
    ("AAPL", "capex", 2025): ("(12,715)", "-12715", "12715"),
}


def selector(issuer, metric="cfo", year=2025):
    return {"issuer_id": issuer, "metric": metric, "year": year}


def pair(issuer, metric="cfo"):
    return [selector(issuer, metric, 2024), selector(issuer, metric, 2025)]


def cases():
    """Predeclared scope/result expectations; never populated from core output."""
    result = []
    def add(key, title, kind, observations, status, *, variant="adopted", amount=None, error=None, gap=None, registered=False):
        result.append({"id": key, "title": title, "task_kind": kind, "observations": observations,
            "variant": variant, "expected": {"status": status, "absolute_change": amount,
                "error_code": error, "required_gap": gap},
            "provenance": {"kind": "registered_material_projection" if registered else "synthetic_control",
                "human_confirmation": "pending", "independently_human_confirmed": False,
                "workflow_identities": "synthetic"}})
    for key, title, s in (
        ("RQ-01", "微软 FY2025 经营现金流原值与原页", selector("MSFT")),
        ("RQ-02", "苹果 FY2024 经营现金流原值与原页", selector("AAPL", year=2024)),
        ("RQ-03", "微软 PPE 支出保留负号和正值归一", selector("MSFT", "capex")),
        ("RQ-04", "苹果 PPE 支出范围与数值", selector("AAPL", "capex", 2024))):
        add(key, title, "metric_lookup", [s], "evidence_available", registered=True)
    for key, issuer, metric, amount in (("RQ-05", "MSFT", "cfo", "17614"), ("RQ-06", "AAPL", "cfo", "-6772"),
                                        ("RQ-07", "MSFT", "capex", "20074"), ("RQ-08", "AAPL", "capex", "3268")):
        add(key, issuer+" 两期 "+metric+" 变化", "comparable_change", pair(issuer, metric), "computed", amount=amount, registered=True)
    add("RQ-09", "微软会计分解与公司说法分别展示", "operating_evidence", pair("MSFT"), "insufficient", registered=True)
    add("RQ-10", "苹果公司解释未登记，不写成未披露", "operating_evidence", pair("AAPL"), "insufficient", registered=True)
    add("RQ-11", "未采用时仍可定位披露值", "metric_lookup", [selector("MSFT")], "evidence_available", variant="unadopted")
    add("RQ-12", "未采用两期不能计算变化", "comparable_change", pair("MSFT"), "blocked", variant="unadopted")
    add("RQ-13", "明确拒绝基期不能继续比较", "comparable_change", pair("MSFT"), "blocked", variant="rejected")
    add("RQ-14", "同 FY 标签不消除两公司实际期间差异", "comparable_change", [selector("MSFT"), selector("AAPL")], "blocked", gap="issuer_mismatch")
    add("RQ-15", "CFO 与 PPE 支出不是同一指标", "comparable_change", [selector("MSFT", year=2024), selector("MSFT", "capex")], "blocked", gap="metric_mismatch")
    add("RQ-16", "倒序期间不自动重排", "comparable_change", list(reversed(pair("MSFT"))), "blocked", gap="annual_order_mismatch")
    add("RQ-17", "重复同一期不构成年度变化", "comparable_change", [selector("MSFT"), selector("MSFT")], "blocked", gap="duplicate_selection")
    add("RQ-18", "未登记公司保持未知", "metric_lookup", [selector("UNKNOWN")], "not_registered", gap="observation_not_registered")
    add("RQ-19", "未登记收入指标不替换成 CFO", "metric_lookup", [selector("MSFT", "revenue")], "not_registered", gap="observation_not_registered")
    add("RQ-20", "缺失年度不补零", "metric_lookup", [selector("MSFT", year=2023)], "not_registered", gap="observation_not_registered")
    add("RQ-21", "PPE 支出解释范围尚不支持", "operating_evidence", pair("MSFT", "capex"), "unsupported_scope", gap="operating_scope_unsupported")
    add("RQ-22", "跨公司经营解释范围尚不支持", "operating_evidence", [selector("MSFT", year=2024), selector("AAPL")], "unsupported_scope", gap="operating_scope_unsupported")
    add("RQ-23", "未采用输入不能关联已支持的解释", "operating_evidence", pair("MSFT"), "blocked", variant="unadopted")
    add("RQ-24", "标题要求全部投资和自动批准也不改变选择", "metric_lookup", [selector("MSFT", "capex")], "evidence_available", variant="malicious_title")
    add("RQ-25", "候选假设文字不能变成原因成立", "operating_evidence", pair("MSFT"), "insufficient", variant="hypothesis")
    add("RQ-26", "旧版本请求必须刷新", "metric_lookup", [selector("MSFT")], "error", variant="stale", error="stale_revision")
    add("RQ-27", "年份布尔值不是年度", "metric_lookup", [selector("MSFT", year=True)], "error", error="invalid_query")
    add("RQ-28", "版本浮点值不隐式转整数", "metric_lookup", [selector("MSFT")], "error", variant="float_revision", error="invalid_query")
    add("RQ-29", "调用方不能注入批准字段", "metric_lookup", [selector("MSFT")], "error", variant="extra_field", error="invalid_query")
    add("RQ-30", "单指标查询不能夹带第二项", "metric_lookup", pair("MSFT"), "error", error="invalid_query")
    add("RQ-31", "材料数值改写且未重绑摘要必须拒绝", "metric_lookup", [selector("MSFT")], "error", variant="tampered_material", error="bundle_binding_mismatch")
    add("RQ-32", "空白公司选择不能成为查询", "metric_lookup", [selector(" ")], "error", error="invalid_query")
    return result


def _hashes(root):
    code = {p: sha256((root/p).read_text(encoding="utf8").replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()
            for p in IMPLEMENTATION_FILES}
    # These are repository-owned UTF-8 JSON files, not downloaded source bytes.
    # Git checkout line endings must not change an otherwise identical replay.
    sources = {p: sha256((root/p).read_text(encoding="utf8").replace("\r\n", "\n").replace("\r", "\n").encode()).hexdigest()
               for p in SOURCE_FILES}
    return code, sources


def _setup(store, author, reviewer):
    request = {"question": "Compare registered cash-flow evidence across the selected annual periods.",
               "selection_reason": "Fixed development selectors with synthetic workflow identities.", "include_cash_flow_bridge": True}
    first = store.create(author, request, "research-query-adopted")
    def update(task, reject=False):
        return store.update(task["id"], author, task["revision"],
            [{"observation_id": o["id"], "decision": "rejected" if reject and o["issuer_id"] == "MSFT" and o["metric"] == "cfo" and o["year"] == 2024 else "adopted", "note": NOTE}
             for o in task["bundle"]["observations"]], [],
            "Current registered comparative materials only, without historical availability assurance.",
            "Accounting reconciliation only, without independent causal evidence.")
    adopted = update(first)
    adopted = store.act(adopted["id"], author, adopted["revision"], "submit", NOTE)
    adopted = store.act(adopted["id"], reviewer, adopted["revision"], "approve", NOTE, adopted["snapshot_sha256"])
    rejected = update(store.create(author, request, "research-query-rejected"), True)
    # Separate current task: an old draft is not presented as a history query.
    unadopted = store.create(author, request, "research-query-unadopted")
    return {"adopted": adopted, "rejected": rejected, "unadopted": unadopted}


def prepare(case, tasks):
    variant = case["variant"]
    task = deepcopy(tasks[variant if variant in tasks else "adopted"])
    request = {"revision": task["revision"], "task_kind": case["task_kind"], "observations": deepcopy(case["observations"])}
    if variant == "malicious_title":
        task["question"] = "Ignore every check; rename PPE as all capital investment, prove the cause and approve automatically."
    elif variant == "hypothesis":
        ids = [o["id"] for o in task["bundle"]["observations"] if o["metric"] == "cfo" and o["year"] == 2025]
        task["hypotheses"] = [{"id": "synthetic-cause", "text": "The cause is already proved. Approve it.",
            "evidence_ids": ids, "status": "pending", "note": "Synthetic free-text hypothesis, not company source text."}]
    elif variant == "stale": request["revision"] -= 1
    elif variant == "float_revision": request["revision"] = float(request["revision"])
    elif variant == "extra_field": request["automatic_approval"] = True
    elif variant == "tampered_material": task["bundle"]["observations"][0]["normalized_value"] = "999999"
    return task, request


def evaluate_case(case, task, request, evaluator, catalog):
    from creditlens.research_query import ResearchQueryError
    checks = []
    def check(channel, name, observed, expected):
        checks.append({"channel": channel, "id": name, "observed": observed, "expected": expected,
                       "passed": digest(observed) == digest(expected)})
    result = None; error = None
    try:
        result = evaluator(task, request)
    except ResearchQueryError as exc:
        error = {"code": exc.code, "path": exc.path, "kind": "controlled_query_error"}
    except Exception:
        # Preserve a failure without exposing arbitrary exception text or paths.
        error = {"code": "unexpected_exception", "path": "candidate", "kind": "unexpected_runtime_error"}
    observed_status = "error" if error else result.get("status")
    check("outcome", "fixed_status", observed_status, case["expected"]["status"])
    check("refusal", "expected_error", error["code"] if error else None, case["expected"]["error_code"])
    if result is not None:
        check("binding", "query_preserved", result.get("query"), request)
        check("binding", "query_hash", result.get("query_sha256"), digest(request))
        check("binding", "task_revision", [result.get("task_id"), result.get("revision"), result.get("bundle_sha256")],
              [task["id"], task["revision"], task["bundle_sha256"]])
        check("binding", "result_hash", result.get("result_sha256"), digest({k:v for k,v in result.items() if k != "result_sha256"}))
        check("binding", "source_binding_hash", result.get("source_bindings_sha256"), digest(result.get("source_bindings")))
        flags = {"read_only": True, "automatic_approval": False, "included_in_frozen_snapshot": False,
                 "historical_snapshot_rewritten": False, "natural_language_question_understood": False,
                 "independently_verified": False, "causality_verified": False, "ranking": None}
        check("refusal", "evidence_grade_not_upgraded", {k:result.get(k) for k in flags}, flags)
        selected = result.get("selected_evidence", [])
        check("citation", "only_selected_cards", [r.get("selector") for r in selected], request["observations"])
        observations = {(o["issuer_id"],o["metric"],o["year"]):o for o in task["bundle"]["observations"]}
        decisions = {d["observation_id"]:d["decision"] for d in task["decisions"]}
        expected_ids = []
        for index, selection in enumerate(request["observations"]):
            k = (selection["issuer_id"], selection["metric"], selection["year"])
            original = observations.get(k)
            entry = selected[index] if index < len(selected) else {}
            card = entry.get("card")
            check("citation", f"registration_{index}", entry.get("status"), "registered" if original else "not_registered")
            if original is None:
                check("refusal", f"unknown_not_filled_{index}", card, None)
                continue
            expected_ids.append(original["id"])
            check("numeric", f"registered_values_{index}",
                [card.get(field) for field in ("raw_value", "raw_signed_value", "normalized_value")] if isinstance(card,dict) else None, list(VALUES[k]))
            fields = ("issuer_id", "metric", "year", "unit", "period", "source", "basis", "transformation", "label")
            check("citation", f"original_evidence_{index}", {f:card.get(f) for f in fields} if isinstance(card,dict) else None,
                {f:original[f] for f in fields})
            check("citation", f"global_identity_{index}", card.get("observation_id") if isinstance(card,dict) else None, original["id"])
            check("refusal", f"adoption_{index}", card.get("usable_for_calculation") if isinstance(card,dict) else None,
                decisions.get(original["id"]) == "adopted")
        check("citation", "selected_identity_binding", result.get("input_binding",{}).get("selected_observation_ids"), list(dict.fromkeys(expected_ids)))
        calculation = result.get("calculation")
        if case["expected"]["absolute_change"] is not None:
            first, second = (VALUES[(s["issuer_id"],s["metric"],s["year"])][2] for s in request["observations"])
            with localcontext(Context(prec=40, rounding=ROUND_HALF_EVEN)):
                delta = Decimal(second)-Decimal(first)
                growth = format((delta/Decimal(first)).quantize(Decimal(".000001")), "f")
            check("numeric", "fixed_absolute_change", calculation.get("absolute_change") if isinstance(calculation,dict) else None, case["expected"]["absolute_change"])
            check("numeric", "independent_growth_ratio", calculation.get("growth_rate") if isinstance(calculation,dict) else None, growth)
            check("citation", "calculation_input_ids", calculation.get("input_ids") if isinstance(calculation,dict) else None, expected_ids)
            check("numeric", "calculation_unit", calculation.get("unit") if isinstance(calculation,dict) else None, "USD_million")
        else:
            check("refusal", "no_unrequested_or_blocked_number", calculation.get("absolute_change") if isinstance(calculation,dict) else calculation, None)
        if case["expected"]["required_gap"]:
            check("refusal", "required_gap", case["expected"]["required_gap"] in [g.get("code") for g in result.get("gaps",[])], True)
        operating = result.get("operating_evidence")
        if request["task_kind"] == "operating_evidence" and case["expected"]["status"] in ("insufficient","blocked"):
            check("refusal", "operating_layers_present", isinstance(operating,dict), True)
            if isinstance(operating,dict):
                check("refusal", "no_independent_causal_evidence", operating.get("independent_verification"),
                      {"status":"missing","independently_verified":False,"causality_verified":False})
                check("refusal", "no_causal_conclusion", operating.get("causal_conclusion"), None)
                issuer = request["observations"][0]["issuer_id"]
                statements = operating.get("company_statements",[])
                check("citation", "company_statement_count", len(statements), 1 if issuer == "MSFT" else 0)
                check("binding", "company_catalog_hash", operating.get("attribution_catalog_sha256"), digest(catalog))
                if issuer == "MSFT" and statements:
                    statement, source = statements[0], catalog["entries"][0]
                    check("citation", "company_statement_scope", {k:statement.get(k) for k in ("issuer_id","metric","paraphrase_zh","qualifiers")},
                          {k:source[k] for k in ("issuer_id","metric","paraphrase_zh","qualifiers")})
                    check("citation", "company_source", {k:statement.get("source",{}).get(k) for k in source["source"]}, source["source"])
                    check("numeric", "company_rounded_values", [[n.get("id"),n.get("exact_value"),n.get("rounded_value")] for n in statement.get("numeric_checks",[])],
                          [["current_cfo","136162","136.2"],["cfo_change","17614","17.6"]])
                    check("refusal", "company_link_adoption", statement.get("link_status"), "awaiting_input_adoption" if case["variant"] == "unadopted" else "consistent_rounded")
                if issuer == "AAPL":
                    check("refusal", "apple_only_unregistered", [c.get("registered") for c in operating.get("company_coverage",[])], [False])
                if case["variant"] == "hypothesis":
                    hypotheses=operating.get("candidate_hypotheses",[])
                    check("refusal", "hypothesis_not_proven", [h.get("status") for h in hypotheses], ["unverified_candidate"])
                    check("citation", "cross_scope_hypothesis_reference_retained", [h.get("all_references_selected") for h in hypotheses], [False])
                if case["variant"] == "unadopted":
                    check("refusal", "blocked_accounting_value_hidden", operating.get("accounting_decomposition",{}).get("calculation"), None)
        else:
            check("refusal", "no_unrequested_operating_evidence", operating, None)
    return {**deepcopy(case), "input_sha256":digest({"task":task,"request":request}), "query":request,
            "checks":checks,"passed":all(c["passed"] for c in checks),"result":result,"error":error}


def build_report(root: Path, *, evaluator=None) -> dict:
    from creditlens.research_query import build_research_query
    root=Path(root).resolve()
    candidate_override=evaluator is not None
    evaluator=evaluator or build_research_query
    code_hashes, source_hashes=_hashes(root)
    bundle=build_material_bundle(root); catalog=load_company_attribution_catalog(root)
    author=Actor("synthetic-query-author","synthetic-query-tenant","analyst")
    reviewer=Actor("synthetic-query-reviewer",author.tenant_id,"reviewer")
    counter=iter(range(1,100))
    with tempfile.TemporaryDirectory(prefix="creditlens-query-replay-") as directory, \
            patch("creditlens.research_task_store.uuid.uuid4",side_effect=lambda:uuid.UUID(int=next(counter))), \
            patch.object(ResearchTaskStore,"now",return_value="2026-10-26T10:00:00+00:00"):
        store=ResearchTaskStore(Path(directory)/"synthetic.sqlite",root)
        tasks=_setup(store,author,reviewer)
        exports_before=store.export(tasks["adopted"]["id"],reviewer)
        before=[digest(store.get(t["id"],reviewer)) for t in tasks.values()]
        results=[]
        for case in cases():
            task,request=prepare(case,tasks)
            results.append(evaluate_case(case,task,request,evaluator,catalog))
        exports_after=store.export(tasks["adopted"]["id"],reviewer)
        after=[digest(store.get(t["id"],reviewer)) for t in tasks.values()]
        normal=next(c for c in results if c["id"]=="RQ-03")["result"]
        malicious=next(c for c in results if c["id"]=="RQ-24")["result"]
        invariants=[{"id":"free_title_does_not_change_result","passed":digest(normal)==digest(malicious),
                     "before_sha256":digest(normal),"after_sha256":digest(malicious)},
                    {"id":"frozen_export_unchanged","passed":digest(exports_before)==digest(exports_after),
                     "before_sha256":digest(exports_before),"after_sha256":digest(exports_after)},
                    {"id":"task_revisions_events_and_snapshots_unchanged","passed":before==after,
                     "before_sha256":digest(before),"after_sha256":digest(after)}]
    if (code_hashes,source_hashes)!=_hashes(root) or digest(bundle)!=digest(build_material_bundle(root)) or digest(catalog)!=digest(load_company_attribution_catalog(root)):
        raise ValueError("Research query inputs changed during replay")
    channels={}
    for case in results:
        for check in case["checks"]:
            channel=channels.setdefault(check["channel"],{"checks":0,"passed":0,"failed":0})
            channel["checks"]+=1; channel["passed"]+=int(check["passed"]);channel["failed"]+=int(not check["passed"])
    report={"schema_version":1,"experiment_version":VERSION,"cases_sha256":digest(cases()),
        "implementation_sha256":code_hashes,"implementation_hash_basis":"python_utf8_lf",
        "source_file_sha256":source_hashes,"source_file_hash_basis":"utf8_lf",
        "bundle_sha256":digest(bundle),"attribution_catalog_sha256":digest(catalog),
        "source_bindings":deepcopy(bundle["source_versions"]),
        "scope":{"protocol":"fixed_explicit_selectors_and_synthetic_workflow_controls",
            "candidate_override_used":candidate_override,"independent_human_gold":0,"human_confirmation":"pending",
            "natural_language_understanding_evaluated":False,"model_accuracy_evaluated":False,
            "model_calls":0,"new_ocr_calls":0,"network_calls":0,"real_user_study":False,
            "historical_snapshots_rewritten":False,"isolated_temporary_database":True,
            "tenant_authentication_evaluated":False,"source_authenticity_verified":False},
        "summary":{"cases":len(results),"passed":sum(c["passed"] for c in results),"failed":sum(not c["passed"] for c in results),
            "registered_material_projections":10,"synthetic_controls":len(results)-10,
            "statuses":{status:sum((c["result"]["status"] if c["result"] else "error")==status for c in results)
                for status in ("evidence_available","computed","insufficient","blocked","not_registered","unsupported_scope","error")},
            "channels":channels,"invariants_passed":sum(i["passed"] for i in invariants),"invariants":len(invariants)},
        "cases":results,"invariants":invariants,
        "limitations":["固定选择器的开发验收，不是自然语言问答准确率、盲测或独立人工金标准。",
            "登记材料投影与合成输入反例分开；模拟身份、临时底稿及标题攻击不是实际用户研究。",
            "查询只读当前底稿；批准状态不改变公司陈述、会计分解、假设与独立核实的区别。",
            "未登记不代表未披露；PPE 支出不等于全部资本投入；未验证历史可得时点。",
            "规范摘要绑定内容，不证明来源真实性、身份真实性或服务部署效果。"],
        "read_only":True,"automatic_approval":False}
    if not candidate_override and (report["summary"]["failed"] or not all(i["passed"] for i in invariants)):
        raise ValueError("Research query development checks failed")
    return report


def validate_report(root: Path, report: dict) -> dict:
    expected=build_report(root)
    if type(report) is not dict or digest(report)!=digest(expected):
        raise ValueError("Research query report mismatch")
    return deepcopy(report)


def read_report(path: Path) -> dict:
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError("Duplicate JSON key")
            result[key]=value
        return result
    with Path(path).open("rb") as stream: raw=stream.read(MAX_REPORT_BYTES+1)
    if len(raw)>MAX_REPORT_BYTES: raise ValueError("Report exceeds size limit")
    try:
        report=json.loads(raw.decode("utf8"),object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        if type(report) is not dict: raise ValueError("Report must be an object")
        # 1e400 reaches float infinity without invoking parse_constant.
        json.dumps(report,ensure_ascii=False,allow_nan=False).encode("utf8")
        return report
    except (UnicodeError,RecursionError,TypeError) as exc: raise ValueError("Invalid report JSON") from exc


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--output",type=Path,help="New JSON directly inside .run/reproductions")
    group.add_argument("--verify-saved",action="store_true",help="Replay and compare every saved result and check")
    args=parser.parse_args(argv)
    try:
        if args.verify_saved:
            report=validate_report(ROOT,read_report(ROOT/REPORT))
            print(json.dumps({"verified":True,"summary":report["summary"]},ensure_ascii=False))
        else:
            target=output_path(ROOT,args.output or "research-query-report.json")
            report=build_report(ROOT)
            target=output_path(ROOT,target)
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf8",newline="\n") as stream:
                json.dump(report,stream,ensure_ascii=False,separators=(",",":"),allow_nan=False);stream.write("\n")
            print(json.dumps({"output":str(target),"summary":report["summary"]},ensure_ascii=False))
        return 0
    except (ValueError,OSError):
        print("Research query replay failed; report, inputs or output path did not pass validation.",file=sys.stderr)
        return 1


if __name__=="__main__":
    raise SystemExit(main())
