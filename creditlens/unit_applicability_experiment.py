"""Read-only unit applicability replay over already published small evidence."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .diligence import digest
from . import role_table_experiment as role_experiment
from . import row_disambiguation_experiment as row_experiment
from .table_lookup_experiment import load_table_lookup_snapshot, REPORT_PATH as TABLE_REPORT
from .table_lookup import lookup_text_sequence
from .role_table_lookup import lookup_role_table
from .row_disambiguation import enumerate_row_candidates, lookup_selected_row
from .word_evidence import WordPage
from .unit_applicability import inspect_unit_applicability

VERSION="unit-applicability-development-1"
REPORT="artifacts/unit-applicability-report.json"
METHODS=("text","line","word","role")
CONTROL_METHODS=METHODS+("explicit",)
STATUSES=("unit_ready","blocked","not_selected")
IMPLEMENTATION_FILES=tuple(dict.fromkeys((*role_experiment.IMPLEMENTATION_FILES,*row_experiment.IMPLEMENTATION_FILES,
    "creditlens/unit_applicability.py","creditlens/unit_applicability_experiment.py","scripts/evaluate_unit_applicability.py")))
SOURCE_PATHS={"role":role_experiment.REPORT,"table":TABLE_REPORT,"row":row_experiment.REPORT}


class UnitApplicabilityExperimentError(ValueError):
    def __init__(self,code,path):
        self.code,self.path=code,path
        super().__init__("Unit applicability report validation failed: "+code)


def require(condition,code,path):
    if not condition:raise UnitApplicabilityExperimentError(code,path)


def _same(a,b):
    try:return json.dumps(a,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)==json.dumps(b,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False)
    except (TypeError,ValueError,UnicodeError,RecursionError):return False


def _read(path,limit=2_000_000):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError("duplicate key")
            result[key]=value
        return result
    try:
        with Path(path).open("rb") as stream:raw=stream.read(limit+1)
        if len(raw)>limit:raise ValueError("size limit")
        data=json.loads(raw.decode("utf8"),object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError("nonfinite")))
        if type(data) is not dict:raise ValueError("object required")
        json.dumps(data,ensure_ascii=False,allow_nan=False).encode("utf8")
        return raw,data
    except (ValueError,OSError,UnicodeError,TypeError,RecursionError) as exc:
        raise UnitApplicabilityExperimentError("invalid_report_json","report") from exc


def _hashes(root):
    try:return {p:sha256((root/p).read_text(encoding="utf8").replace("\r\n","\n").replace("\r","\n").encode()).hexdigest() for p in IMPLEMENTATION_FILES}
    except (ValueError,OSError,UnicodeError) as exc:raise UnitApplicabilityExperimentError("implementation_unavailable","implementation") from exc


def _sources(root):
    try:
        role=role_experiment.load_role_table_lookup_snapshot(root)
        row=row_experiment.load_row_disambiguation_snapshot(root)
        table=load_table_lookup_snapshot(root)
    except (ValueError,OSError) as exc:raise UnitApplicabilityExperimentError("baseline_unavailable","source_reports") from exc
    require(row["role_report_sha256"]==role["report_sha256"],"baseline_binding_mismatch","row_report")
    return {"role":role,"row":row,"table":table}


def control_specs():
    """Fixed expected outcomes, written before executing the candidate guard."""
    specs=[]
    def add(key,title,unit="(In millions)",label="Revenue",status="blocked",scale=None,value=None,modification="unit_text",year=2025):
        specs.append({"id":key,"title":title,"unit_text":unit,"row_label":label,"year":year,"modification":modification,
            "expected":{"status":status,"scale":scale,"scaled_value":value},"provenance":"synthetic_control",
            "independent_human_verified":False})
    add("UA-01","明确百万量级",status="unit_ready",scale="million",value="100000000")
    add("UA-02","明确千量级",unit="(In thousands)",status="unit_ready",scale="thousand",value="100000")
    add("UA-03","明确十亿量级",unit="(In billions)",status="unit_ready",scale="billion",value="100000000000")
    add("UA-04","合法负金额保留符号",status="unit_ready",scale="million",value="-100000000",modification="negative_amount")
    add("UA-05","每股例外下明确收入金额行",unit="(In millions, except per share amounts)",status="unit_ready",scale="million",value="100000000")
    add("UA-06","股数用千、金额用百万的完整已知说明",unit="(In millions, except number of shares, which are reflected in thousands, and per-share amounts)",status="unit_ready",scale="million",value="100000000")
    add("UA-07","股数和面值例外不覆盖收入金额",unit="(In millions, except number of shares, which are reflected in thousands, and par value)",status="unit_ready",scale="million",value="100000000")
    add("UA-08","收入另用十亿，不截取百万前缀",unit="(In millions except revenue in billions)")
    add("UA-09","特定年份另用千量级",unit="(In millions except 2024 in thousands)",year=2024)
    add("UA-10","unless otherwise 条件未解析",unit="(In millions unless otherwise noted)")
    add("UA-11","一行混入两个金额量级",unit="(In millions and billions)")
    add("UA-12","完整说明之后的未知尾部",unit="(In millions) other unit overrides follow")
    add("UA-13","Basic不能猜是金额、股数或每股值",unit="(In millions, except per share amounts)",label="Basic")
    add("UA-14","Diluted不能猜单位类别",unit="(In millions, except per share amounts)",label="Diluted")
    add("UA-15","例外下未知行类别不自动归金额",unit="(In millions, except per share amounts)",label="Unclassified measure")
    add("UA-16","低分单位使原提取不足",status="not_selected",modification="low_unit_confidence")
    add("UA-17","自报单位与保留原行不符",modification="unit_metadata_mismatch")
    add("UA-18","选中候选却缺单位原行",modification="missing_unit_evidence")
    add("UA-19","已披露零值仍可按明确量级解释",status="unit_ready",scale="million",value="0",modification="zero_amount")
    add("UA-20","明确每股行不当总金额",unit="(In millions, except per share amounts)",label="Earnings per share",status="not_selected")
    return specs


def _control_inputs(spec):
    def span(text,x,y,width=40,confidence=.99):
        return {"text":text,"confidence":confidence,"bbox":{"x0":x,"top":y,"x1":x+width,"bottom":y+10}}
    value="(100)" if spec["modification"]=="negative_amount" else "0" if spec["modification"]=="zero_amount" else "100"
    spans=[span(spec["unit_text"],20,10,460,.89 if spec["modification"]=="low_unit_confidence" else .99),
           span("2025",250,30),span("2024",350,30),span(spec["row_label"],20,60,160),span(value,250,60),span("200",350,60)]
    page=WordPage.model_validate({"page":1,"width":500.,"height":300.,"coordinate_system":"pdf_points_top_left",
        "lines":[{"line":s,"words":[deepcopy(s)]} for s in spans]})
    query={"row_label":spec["row_label"],"year":spec["year"]}
    result=lookup_role_table(page,**query)
    discovered=enumerate_row_candidates(page.line_page(),**query)
    explicit=lookup_selected_row(page.line_page(),**query,selection={"input_sha256":discovered["input_binding"]["input_sha256"],"row_index":3})
    methods={"text":lookup_text_sequence(page.line_page(),**query),"line":result["methods"]["line"],
        "word":result["methods"]["word"],"role":result["methods"]["role"],"explicit":explicit["extraction"]}
    if spec["modification"]=="unit_metadata_mismatch":
        for method in methods.values():method["unit"].update(raw_text="(In billions)",scale="billion")
    if spec["modification"]=="missing_unit_evidence":
        for method in methods.values():method["evidence"]["unit"]=[]
    return page,query,methods


def _unit(row_label,extraction):
    before=digest(extraction)
    result=inspect_unit_applicability(row_label=row_label,extraction=extraction)
    require(digest(extraction)==before,"legacy_extraction_mutated","extraction")
    return {"legacy_extraction_sha256":before,"legacy_status":extraction["status"],"legacy_unit":deepcopy(extraction["unit"]),"unit_check":result}


def _controls():
    results=[]
    for spec in control_specs():
        page,query,methods=_control_inputs(spec)
        checks={}
        for name,method in methods.items():
            result=_unit(query["row_label"],method)
            actual=result["unit_check"]
            fixed={"status":actual["status"],"scale":actual["scale"],"scaled_value":actual["scaled_value"]}
            result.update(expected=deepcopy(spec["expected"]),passed=_same(fixed,spec["expected"]))
            checks[name]=result
        results.append({**deepcopy(spec),"query":query,"synthetic_page_sha256":digest(page.model_dump(mode="json")),
            "input_sha256":digest({"query":query,"methods":methods}),"methods":checks,
            "passed":all(m["passed"] for m in checks.values())})
    return {"cases":results,"cases_sha256":digest(control_specs()),
        "summary":{"cases":len(results),"method_checks":len(results)*len(CONTROL_METHODS),
            "passed":sum(c["passed"] for c in results),"failed":sum(not c["passed"] for c in results),
            "passed_method_checks":sum(m["passed"] for c in results for m in c["methods"].values()),
            "old_selected_now_blocked":sum(m["legacy_status"]=="selected" and m["unit_check"]["status"]=="blocked" for c in results for m in c["methods"].values())}}


def _stats(records):
    return {state:sum(r["unit_check"]["status"]==state for r in records) for state in STATUSES}


def _build(root,sources):
    hashes=_hashes(root)
    table_cases={}
    for document in sources["table"]["report"]["documents"]:
        for case in document["cases"]:
            require(case["id"] not in table_cases,"duplicate_case","table_report")
            table_cases[case["id"]]=(document,case)
    cases=[];seen=set();documents=[]
    for document in sources["role"]["report"]["documents"]:
        documents.append({k:deepcopy(document[k]) for k in ("id","company","source","pack_sha256","pages")})
        for case in document["cases"]:
            require(case["id"] not in seen and case["id"] in table_cases,"case_inventory_mismatch","cases")
            seen.add(case["id"]);old_doc,old_case=table_cases[case["id"]]
            require(_same([old_doc["id"],old_doc["source"],old_doc["pack_sha256"],old_case["page"],old_case["query"]],
                [document["id"],document["source"],document["pack_sha256"],case["page"],case["query"]]),"case_binding_mismatch",case["id"])
            methods={"text":_unit(case["query"]["row_label"],old_case["methods"]["text_sequence"]["extraction"])}
            methods.update({name:_unit(case["query"]["row_label"],case["methods"][name]["extraction"]) for name in METHODS[1:]})
            cases.append({"id":case["id"],"fact_id":case["fact_id"],"document_id":document["id"],"page":case["page"],
                "query":deepcopy(case["query"]),"methods":methods})
    require(seen==set(table_cases) and len(cases)==42,"case_inventory_mismatch","cases")
    selections=[];seen_selections=set()
    for query in sources["row"]["report"]["queries"]:
        for path in query["paths"]:
            identity=(query["id"],path["row_index"])
            require(identity not in seen_selections,"duplicate_selection","selections");seen_selections.add(identity)
            selections.append({"query_id":query["id"],"row_index":path["row_index"],"document_id":query["document_id"],
                "page":query["page"],"query":deepcopy(query["query"]),"input_sha256":path["result"]["selection"]["input_sha256"],
                "selection_sha256":digest(path["result"]),**_unit(query["query"]["row_label"],path["result"]["extraction"])})
    require(len(selections)==8,"selection_inventory_mismatch","selections")
    controls=_controls()
    require(controls["summary"]["failed"]==0,"control_failure","controls")
    require(_hashes(root)==hashes,"implementation_changed_during_read","implementation")
    for name,path in SOURCE_PATHS.items():
        require(sha256((root/path).read_bytes()).hexdigest()==sources[name]["report_sha256"],"baseline_changed_during_read",name)
    return {"schema_version":1,"experiment_version":VERSION,"implementation_hash_basis":"python_utf8_lf","implementation_sha256":hashes,
        "role_report_sha256":sources["role"]["report_sha256"],"table_report_sha256":sources["table"]["report_sha256"],
        "row_report_sha256":sources["row"]["report_sha256"],"query_inventory":deepcopy(sources["role"]["report"]["query_inventory"]),
        "documents":documents,"cases":cases,"selections":selections,"controls":controls,
        "summary":{"registered_records":42,"unique_query_inputs":sources["role"]["report"]["query_inventory"]["unique_query_inputs"],
            "automatic_method_checks":len(cases)*len(METHODS),"explicit_selection_paths":len(selections),"compatibility_checks":len(cases)*len(METHODS)+len(selections),
            "methods":{name:_stats([c["methods"][name] for c in cases]) for name in METHODS},"explicit":_stats(selections)},
        "scope":{"evidence_source":"published_necessary_extraction_evidence_only","compatibility_not_new_samples":True,
            "independent_human_gold":0,"human_confirmation":"pending","model_accuracy_measured":False,"new_ocr_calls":0,
            "network_calls":0,"raw_pdf_bytes_checked":False,"full_ocr_replayed":False,"source_authenticity_verified":False,
            "unit_ready_proves_currency":False,"unit_ready_proves_period_or_scope":False,"legacy_reports_rewritten":False},
        "limitations":["42登记记录对应40组唯一查询；四种自动方法及8条显选路径重复检查同一批已观察证据，176次检查不是176个独立样本。",
            "新层只检查候选与保留的完整单位说明是否可用，不重新扫描整页OCR，也不改变旧方法状态或旧参考评分。",
            "单位适用不能推出USD、期间一致、报表范围一致或自动采用；支持完整基本单位说明，存在例外时还需明确支持的金额类别。",
            "合成单位说明与输出篡改单独计数；固定控制通过不代表自然语言理解、模型准确率或独立人工金标准。"],
        "read_only":True,"automatic_approval":False}


def build_unit_applicability_report(root:Path)->dict:
    root=Path(root).resolve()
    return _build(root,_sources(root))


def validate_unit_applicability_report(root:Path,report:dict)->dict:
    expected=build_unit_applicability_report(root)
    require(type(report) is dict and _same(report,expected),"report_mismatch","report")
    return deepcopy(report)


def load_unit_applicability_snapshot(root:Path)->dict:
    root=Path(root).resolve();raw,report=_read(root/REPORT)
    report=validate_unit_applicability_report(root,report)
    require((root/REPORT).read_bytes()==raw,"report_changed_during_read","report")
    return {"schema_version":1,"report":report,"report_sha256":sha256(raw).hexdigest(),
        **{name:report[name] for name in ("role_report_sha256","table_report_sha256","row_report_sha256")},
        "read_only":True,"full_ocr_replayed":False,
        "validation_scope":"旧发布报告先验收；新单位检查和合成控制逐字段复算；没有读取完整OCR或原PDF。"}


def _envelope(snapshot,target,record):
    return {"schema_version":1,**{name:snapshot[name] for name in ("report_sha256","role_report_sha256","table_report_sha256","row_report_sha256")},
        "target":target,"unit_check":deepcopy(record["unit_check"]),"legacy_extraction_sha256":record["legacy_extraction_sha256"],
        "read_only":True,"automatic_approval":False}


def get_unit_case(snapshot:dict,case_id:str,method:str)->dict:
    require(type(case_id) is str and bool(case_id) and len(case_id)<=256,"invalid_case_id","case_id")
    require(type(method) is str and method in METHODS,"invalid_method","method")
    matches=[c for c in snapshot["report"]["cases"] if c["id"]==case_id]
    require(len(matches)==1,"case_not_registered","case_id")
    case=matches[0]
    return _envelope(snapshot,{"kind":"automatic","case_id":case_id,"method":method,
        **{k:deepcopy(case[k]) for k in ("document_id","page","query")}},case["methods"][method])


def get_unit_selection(snapshot:dict,query_id:str,row_index:int)->dict:
    require(type(query_id) is str and bool(query_id) and len(query_id)<=256,"invalid_query_id","query_id")
    require(type(row_index) is int and row_index>=0,"invalid_row_index","row_index")
    matches=[s for s in snapshot["report"]["selections"] if s["query_id"]==query_id and s["row_index"]==row_index]
    require(len(matches)==1,"selection_not_registered","selection")
    selection=matches[0]
    return _envelope(snapshot,{"kind":"explicit_selection","query_id":query_id,"row_index":row_index,
        **{k:deepcopy(selection[k]) for k in ("document_id","page","query")}},selection)


class UnitApplicabilityStore:
    """Load validated saved reports and return copies; no persistence operations."""
    def __init__(self,root:Path):self.root=Path(root).resolve()
    def snapshot(self):return load_unit_applicability_snapshot(self.root)
    def case(self,case_id,method):return get_unit_case(self.snapshot(),case_id,method)
    def selection(self,query_id,row_index):return get_unit_selection(self.snapshot(),query_id,row_index)
