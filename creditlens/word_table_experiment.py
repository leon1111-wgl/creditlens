"""Same-input line/guarded-word lookup experiment; full OCR stays local."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

from .diligence import digest
from .table_lookup import SCOPE as LINE_SCOPE, normalize_row_label
from .table_lookup_experiment import (
    IMPLEMENTATION_FILES as LINE_FILES, TableLookupExperimentError, _read as _source_read, _sha,
    _validate_extraction as _validate_line, load_table_lookup_snapshot, score_extraction,
)
from .visual import OcrSpan
from .word_evidence import WordPage


VERSION = "word-table-experiment-1"
CONFIG = "configs/word-table-lookup.json"
REPORT = "artifacts/word-table-lookup-report.json"
RELEASE = "configs/word-table-lookup-artifact.json"
IMPLEMENTATION_FILES = tuple(dict.fromkeys((*LINE_FILES, "creditlens/word_evidence.py",
    "creditlens/word_table_lookup.py", "creditlens/word_table_experiment.py", "scripts/evaluate_word_table_lookup.py")))
STATES = ("matched", "wrong", "insufficient")
METHODS = ("line", "word")
BASE_FIELDS = {"method", "status", "reason", "message", "raw_value", "normalized_value", "selected_span_index", "unit", "evidence", "column", "automatic_approval"}
LIMITATIONS = [
    "给定旧登记报表页、行名和年份的 42 项已观察开发查询；参考金额与框只进入后置评分，不是独立盲测或自动找页。",
    "两法从同一 WordPage 出发：基线使用 line_page，词级方法使用父行与原生词；未新增 OCR、模型调用或下载。",
    "逐页核对父行投影与第29轮行级输入的规范摘要；输入变化与方法变化分开，不把新输入归为算法增益。",
    "词级候选框是由原词组成的派生组，不能冒充原父行框；保留完整相关父行/原词、映射和守卫问题。",
    "原生词分数与父行分数均不是已校准概率；金额匹配不证明币种、完整期间、来源真实性或业务批准。",
    "公开必要证据只能支持保存结果检查；完整提取复放需显式读取六份原 WordPage。独立人工金标准为0。",
]


class WordTableExperimentError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def require(condition, code, path):
    if not condition:
        raise WordTableExperimentError(code, path)


def _read(path, limit=8_000_000):
    try:
        return _source_read(path, limit)
    except TableLookupExperimentError as exc:
        raise WordTableExperimentError(exc.code, exc.path) from exc


def _hashes(root):
    try:
        return {p: _sha((root/p).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
                for p in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc:
        raise WordTableExperimentError("implementation_unavailable", "implementation") from exc


def _inputs(root):
    try:
        raw, config = _read(root/CONFIG)
        prior = load_table_lookup_snapshot(root)
        require(set(config) == {"schema_version", "id", "rule_version", "query_fields", "baseline", "word_source", "scoring_from", "documents", "scope"}
                and type(config["schema_version"]) is int and config["schema_version"] == 1
                and config["id"] == "word-table-lookup-development-1" and config["rule_version"] == "word-table-lookup-1"
                and config["query_fields"] == ["row_label", "year"] and config["scoring_from"] == "configs/table-lookup.json",
                "invalid_config", CONFIG)
        require(config["baseline"] == {"report_path": "artifacts/table-lookup-report.json", "report_sha256": prior["report_sha256"]}, "baseline_changed", "baseline")
        require(config["word_source"]["path"] == "artifacts/document-ai-word-report.json", "word_source_changed", "word_source")
        word_raw, source = _read(root/config["word_source"]["path"])
        require(_sha(word_raw) == config["word_source"]["sha256"] and digest(source) == config["word_source"]["canonical_sha256"], "word_source_changed", "word_source")
        require(len(config["documents"]) == 2 and {d["id"] for d in config["documents"]} == {"msft", "aapl"}, "document_inventory_mismatch", "documents")
        for doc in config["documents"]:
            old = next(d for d in prior["report"]["documents"] if d["id"] == doc["id"])
            original = next(d for d in source["documents"] if d["id"] == doc["id"])
            require(doc["source_pdf_sha256"] == old["source"]["sha256"] and doc["pack_sha256"] == old["pack_sha256"]
                    and doc["fact_ids"] == [c["fact_id"] for c in old["cases"]]
                    and original["source"] == old["source"] and original["pack_sha256"] == old["pack_sha256"], "reference_binding_mismatch", doc["id"])
            require(len(doc["pages"]) == len(old["pages"]) == 3 and {p["page"] for p in doc["pages"]} == {p["page"] for p in old["pages"]}, "page_inventory_mismatch", doc["id"])
            for page in doc["pages"]:
                before = next(p for p in old["pages"] if p["page"] == page["page"])
                word = next(p for p in original["pages"] if p["page"] == page["page"])
                require(all(page[k] == before[k] for k in ("page", "width", "height", "image_sha256"))
                        and page["word_sha256"] == word["word_ocr_sha256"]
                        and page["line_projection_sha256"] == word["current_line_ocr_sha256"]
                        and page["previous_line_sha256"] == before["ocr_sha256"]
                        and page["line_projection_matches_previous"] is (page["line_projection_sha256"] == page["previous_line_sha256"])
                        and page["line_count"] == before["spans_count"] and page["word_count"] == word["word_tokens"]
                        and page["ocr_relative_path"] == f"{doc['id']}/ocr-words-{page['page']}.json"
                        and re.fullmatch(r"[0-9a-f]{64}", page["word_file_sha256"]), "page_binding_mismatch", doc["id"])
        return config, _sha(raw), prior
    except WordTableExperimentError:
        raise
    except (TableLookupExperimentError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise WordTableExperimentError("registration_unavailable", "inputs") from exc


def _page(ocr_root, reg):
    path = ocr_root/reg["ocr_relative_path"]
    require(path.resolve().is_relative_to(ocr_root.resolve()), "ocr_path_escape", path.name)
    raw, data = _read(path, 4_000_000)
    require(_sha(raw) == reg["word_file_sha256"] and digest(data) == reg["word_sha256"], "word_hash_mismatch", path.name)
    page = WordPage.model_validate(data)
    require((page.page, page.width, page.height, len(page.lines), sum(len(l.words) for l in page.lines)) ==
            (reg["page"], reg["width"], reg["height"], reg["line_count"], reg["word_count"])
            and digest(page.line_page().model_dump(mode="json")) == reg["line_projection_sha256"], "word_geometry_mismatch", path.name)
    return page


def extract(page, query):
    from .word_table_lookup import lookup_word_table
    require(type(query) is dict and set(query) == {"row_label", "year"}, "invalid_query", "query")
    return lookup_word_table(page, row_label=query["row_label"], year=query["year"])


def _validate_base(method, query, page, spans_count):
    # Reuse the frozen saved-candidate structure checker, not its extractor.
    base = {k: deepcopy(method[k]) for k in BASE_FIELDS}
    methods = {key: {**base, "method": key} for key in ("text_sequence", "layout_column")}
    wrapped = {"schema_version": 1, "rule_version": "table-lookup-1", "query": query, "methods": methods,
               "scope": list(LINE_SCOPE), "automatic_approval": False}
    _validate_line(wrapped, {k: query[k] for k in ("row_label", "year")}, {**page, "spans_count": spans_count}, {})


def _validate_output(output, query, page, parents):
    from . import word_table_lookup as core
    require(type(output) is dict and set(output) == {"schema_version", "rule_version", "query", "methods", "scope", "automatic_approval"}
            and type(output["schema_version"]) is int and output["schema_version"] == 1 and output["rule_version"] == "word-table-lookup-1"
            and output["automatic_approval"] is False and output["scope"] == core.SCOPE, "invalid_output", "output")
    q = {"page": page["page"], **query, "normalized_row_label": normalize_row_label(query["row_label"])}
    require(output["query"] == q and set(output["methods"]) == {"line", "word"}, "query_binding_mismatch", "query")
    line, word = output["methods"]["line"], output["methods"]["word"]
    require(type(line) is dict and set(line)==BASE_FIELDS and line["method"]=="layout_column","invalid_line_output","line")
    _validate_base(line, q, page, page["line_count"])
    extra = {"selected_span_origin", "parent_evidence", "guard_issues", "adaptation", "confidence_basis"}
    require(set(word) == set(line) | extra and word["method"] == "word_layout"
            and word["automatic_approval"] is False
            and word["confidence_basis"] == "both_parent_and_native_word_scores_not_calibrated", "invalid_word_output", "word")
    # Word-specific refusal reasons are preserved. The base checker understands
    # the shared selected/insufficient grammar; validate the fixed word message
    # independently before substituting a neutral insufficient reason below.
    from .table_lookup import MESSAGES as LINE_MESSAGES
    messages={**LINE_MESSAGES,**core.MESSAGES}
    require(word["reason"] in messages and word["message"] == messages[word["reason"]], "invalid_word_reason", "word")
    adaptation = word["adaptation"]
    require(type(adaptation) is dict and set(adaptation)=={"status","line_input_sha256","derived_page_sha256","mapping_scope","source_map"}
            and adaptation["status"] in ("applied","not_applied") and adaptation["mapping_scope"] == "referenced_spans_only"
            and type(adaptation["source_map"]) is list and adaptation["line_input_sha256"]==page["line_projection_sha256"], "invalid_adaptation", "word")
    require((adaptation["status"]=="not_applied" and adaptation["derived_page_sha256"] is None)
            or (adaptation["status"]=="applied" and type(adaptation["derived_page_sha256"]) is str
                and re.fullmatch(r"[0-9a-f]{64}",adaptation["derived_page_sha256"])),"invalid_derived_digest","word")
    mapping = {}
    for entry in adaptation["source_map"]:
        require(type(entry) is dict and set(entry)=={"span_index","origin","parent_indices","word_refs"},"invalid_source_map","word")
        index = entry["span_index"]
        require(type(index) is int and index >= 0 and index not in mapping
                and entry["origin"] in ("original_parent", "derived_word_group")
                and type(entry["parent_indices"]) is list and type(entry["word_refs"]) is list
                and all(type(i) is int and 0 <= i < page["line_count"] for i in entry["parent_indices"]), "invalid_source_map", "word")
        mapping[index] = entry
    safe = deepcopy(word)
    from .table_lookup import MESSAGES
    safe["reason"] = "selected" if safe["status"] == "selected" else "row_not_found"
    safe["message"] = MESSAGES[safe["reason"]]
    _validate_base(safe, q, page, max([page["line_count"], *(i+1 for i in mapping)]))
    referenced = {e["span_index"] for entries in word["evidence"].values() for e in entries}
    require(set(mapping) == referenced, "mapping_evidence_mismatch", "word")
    local_parents={}
    require(type(word["parent_evidence"]) is list,"invalid_parent_evidence","word")
    for parent in word["parent_evidence"]:
        require(type(parent) is dict and set(parent)=={"line_index","line","words","text_conserved","geometry_valid"},"invalid_parent","word")
        index = parent["line_index"]
        require(type(index) is int and 0 <= index < page["line_count"] and type(parent["words"]) is list
                and all(type(w) is dict and set(w)=={"word_index","span"} and type(w["word_index"]) is int for w in parent["words"])
                and [w["word_index"] for w in parent["words"]] == list(range(len(parent["words"]))), "invalid_parent", "word")
        line_span = OcrSpan.model_validate(parent["line"])
        require(line_span.bbox.x1 <= page["width"] and line_span.bbox.bottom <= page["height"], "parent_out_of_page", "word")
        for word_entry in parent["words"]:
            span = OcrSpan.model_validate(word_entry["span"])
            require(span.bbox.x1 <= page["width"] and span.bbox.bottom <= page["height"], "word_out_of_page", "word")
        preserved = {"line": parent["line"], "words": parent["words"]}
        require(index not in parents or parents[index] == preserved, "inconsistent_shared_parent", "word")
        parents[index] = preserved
        require(type(parent["text_conserved"]) is bool and type(parent["geometry_valid"]) is bool, "invalid_parent_flags", "word")
        require(index not in local_parents,"duplicate_parent","word")
        local_parents[index]=parent
        compact=lambda value: re.sub(r"\s+","",value)
        conserved=bool(parent["words"]) and compact(parent["line"]["text"])=="".join(compact(w["span"]["text"]) for w in parent["words"])
        outer=parent["line"]["bbox"]
        geometry=bool(parent["words"]) and all(outer["x0"]<=w["span"]["bbox"]["x0"] and outer["top"]<=w["span"]["bbox"]["top"]
                     and w["span"]["bbox"]["x1"]<=outer["x1"] and w["span"]["bbox"]["bottom"]<=outer["bottom"] for w in parent["words"])
        require(parent["text_conserved"] is conserved and parent["geometry_valid"] is geometry,"parent_flag_mismatch","word")
    preserved_spans={}
    for entries in word["evidence"].values():
        for entry in entries:
            index=entry["span_index"]
            require(index not in preserved_spans or preserved_spans[index]==entry["span"],"inconsistent_derived_span","word")
            preserved_spans[index]=entry["span"]
    for index, entry in mapping.items():
        ids=entry["parent_indices"]
        require(type(ids) is list and ids==sorted(set(ids)) and len(ids)==1 and ids[0] in local_parents,"invalid_mapped_parent","word")
        parent=local_parents[ids[0]]
        if entry["origin"]=="original_parent":
            require(entry["word_refs"]==[] and preserved_spans[index]==parent["line"],"original_parent_mismatch","word")
        else:
            refs=entry["word_refs"]
            require(type(refs) is list and len(refs)>0 and all(type(r) is dict and set(r)=={"line_index","word_index"}
                    and type(r["line_index"]) is int and r["line_index"]==ids[0] and type(r["word_index"]) is int
                    and 0<=r["word_index"]<len(parent["words"]) for r in refs),"invalid_word_ref","word")
            require(len({r["word_index"] for r in refs})==len(refs),"duplicate_word_ref","word")
            spans=[parent["words"][r["word_index"]]["span"] for r in refs]
            require(spans==sorted(spans,key=lambda s:s["bbox"]["x0"]),"word_ref_order","word")
            derived={"text":"".join(s["text"].strip() for s in spans),
                     "confidence":min([parent["line"]["confidence"],*(s["confidence"] for s in spans)]),
                     "bbox":{"x0":min(s["bbox"]["x0"] for s in spans),"top":min(s["bbox"]["top"] for s in spans),
                             "x1":max(s["bbox"]["x1"] for s in spans),"bottom":max(s["bbox"]["bottom"] for s in spans)}}
            require(preserved_spans[index]==derived,"derived_span_mismatch","word")
    issues=word["guard_issues"]
    require(type(issues) is list,"invalid_guards","word")
    for issue in issues:
        require(type(issue) is dict and set(issue)=={"code","line_indices","word_refs"}
                and issue["code"] in core.MESSAGES and type(issue["line_indices"]) is list and type(issue["word_refs"]) is list
                and all(type(i) is int and i in local_parents for i in issue["line_indices"])
                and issue["line_indices"]==sorted(set(issue["line_indices"])),"invalid_guard","word")
        for ref in issue["word_refs"]:
            require(type(ref) is dict and set(ref)=={"line_index","word_index"} and type(ref["line_index"]) is int
                    and ref["line_index"] in local_parents and type(ref["word_index"]) is int
                    and 0<=ref["word_index"]<len(local_parents[ref["line_index"]]["words"]),"invalid_guard_word_ref","word")
            require(ref["line_index"] in issue["line_indices"],"guard_ref_parent_mismatch","word")
        require(len({(ref["line_index"],ref["word_index"]) for ref in issue["word_refs"]})==len(issue["word_refs"]),
                "duplicate_guard_word_ref","word")
    require(not issues or (word["status"]=="insufficient" and word["reason"]==issues[0]["code"]),"guard_status_mismatch","word")
    require(word["reason"] not in core.MESSAGES or bool(issues),"guard_reason_missing","word")
    require(word["selected_span_origin"] in (None, "derived_word_group"), "invalid_candidate_origin", "word")
    if word["status"] == "selected":
        require(word["selected_span_origin"] == "derived_word_group" and not word["guard_issues"]
                and mapping[word["selected_span_index"]]["origin"] == "derived_word_group", "selected_guard_conflict", "word")
        require(all(p["text_conserved"] and p["geometry_valid"] and p["line"]["confidence"]>=core.MIN_CONFIDENCE
                    and all(w["span"]["confidence"]>=core.MIN_CONFIDENCE for w in p["words"]) for p in local_parents.values()),
                "selected_parent_not_qualified","word")


def _summary(cases):
    result = {}
    for method in METHODS:
        scores = [c["methods"][method]["score"] for c in cases]
        counts = Counter(s["outcome"] for s in scores)
        result[method] = {"cases": len(scores), "matched": counts["matched"], "wrong": counts["wrong"],
                          "insufficient": counts["insufficient"], "selected": counts["matched"]+counts["wrong"],
                          "amount_match": sum(s["amount_match"] is True for s in scores),
                          "reference_location_match": sum(s["reference_location_match"] is True for s in scores)}
    return result


def _transitions(cases):
    return {before: {after: sum(c["transition"] == {"from": before, "to": after} for c in cases) for after in STATES} for before in STATES}


def _controls():
    def span(text, x, y, width=40, confidence=.99):
        return {"text": text, "confidence": confidence, "bbox": {"x0": x, "top": y, "x1": x+width, "bottom": y+10}}
    spans = [span("(In millions)",20,10,90),span("2025",250,30),span("2024",350,30),
             span("Cash from operations",20,60,150),span("100",250,60),span("200",350,60)]
    base = {"page":1,"width":500.,"height":300.,"coordinate_system":"pdf_points_top_left",
            "lines":[{"line":s,"words":[deepcopy(s)]} for s in spans]}
    planned = [("normal", "matched"), ("merged_two_columns", "matched"), ("missing_words", "insufficient"),
               ("parent_shift", "insufficient"), ("dropped_sign", "insufficient"),
               ("low_native_score", "insufficient"), ("duplicate_label", "insufficient")]
    cases=[]
    for operation, expected_word in planned:
        page=deepcopy(base)
        if operation=="merged_two_columns":
            page["lines"][4] = {"line":span("100 200",250,60,140),"words":[deepcopy(spans[4]),deepcopy(spans[5])]}
            page["lines"].pop()
        elif operation=="missing_words": page["lines"][5]["words"]=[]
        elif operation=="parent_shift":
            page["lines"][5]["line"]["bbox"]["x0"]+=50;page["lines"][5]["line"]["bbox"]["x1"]+=50
        elif operation=="dropped_sign": page["lines"][5]["line"]["text"]="(200)"
        elif operation=="low_native_score": page["lines"][5]["words"][0]["confidence"]=.2
        elif operation=="duplicate_label": page["lines"].append({"line":span("Cash from operations",20,90,150),"words":[span("Cash from operations",20,90,150)]})
        cases.append({"id":operation,"provenance":"synthetic_developer_control","query":{"row_label":"Cash from operations","year":2024},
                      "page":page,"reference":{"raw_value":"200","signed_amount":"200","amount_bbox":spans[5]["bbox"]},
                      "expected_word_outcome":expected_word})
    return cases


def _case(case_id, fact_id, page, query, reference, output):
    methods={key:{"extraction":deepcopy(output["methods"][key]),"score":score_extraction(output["methods"][key],reference)} for key in METHODS}
    return {"id":case_id,"fact_id":fact_id,"page":page,"query":deepcopy(query),"reference":deepcopy(reference),
            "extraction_sha256":digest(output),"extraction_metadata":{k:deepcopy(v) for k,v in output.items() if k!="methods"},
            "methods":methods,"transition":{"from":methods["line"]["score"]["outcome"],"to":methods["word"]["score"]["outcome"]}}


def _assemble(root,config,config_sha,prior,outputs,control_outputs):
    documents=[]
    comparison=[]
    for registration in config["documents"]:
        old=next(d for d in prior["report"]["documents"] if d["id"]==registration["id"])
        regs={p["page"]:p for p in registration["pages"]};parents={p:{} for p in regs};cases=[]
        for former in old["cases"]:
            output=outputs[former["id"]]
            _validate_output(output,former["query"],regs[former["page"]],parents[former["page"]])
            case=_case(former["id"],former["fact_id"],former["page"],former["query"],former["reference"],output)
            case["line_extraction_matches_previous"] = output["methods"]["line"]==former["methods"]["layout"]["extraction"]
            case["previous_line_extraction_sha256"] = digest(former["methods"]["layout"]["extraction"])
            cases.append(case)
        documents.append({"id":old["id"],"company":old["company"],"source":deepcopy(old["source"]),"pack_sha256":old["pack_sha256"],
                          "pages":deepcopy(registration["pages"]),"cases":cases,"summary":_summary(cases),"transitions":_transitions(cases)})
        comparison.extend({"document_id":old["id"],**{k:p[k] for k in ("page","line_projection_sha256","previous_line_sha256","line_projection_matches_previous","line_count","word_count")}} for p in registration["pages"])
    controls=[]
    for control in _controls():
        output=control_outputs[control["id"]]
        p=control["page"]
        _validate_output(output,control["query"],{**p,"line_count":len(p["lines"]),
            "line_projection_sha256":digest(WordPage.model_validate(p).line_page().model_dump(mode="json"))},{})
        checked=_case(control["id"],control["id"],p["page"],control["query"],control["reference"],output)
        controls.append({**{k:v for k,v in control.items() if k!="page"},"input_page":p,**checked,"input_sha256":digest({"page":p,"query":control["query"]}),
                         "passed":checked["methods"]["word"]["score"]["outcome"]==control["expected_word_outcome"]})
    all_cases=[c for d in documents for c in d["cases"]]
    query_groups={}
    for document in documents:
        for case in document["cases"]:
            key=(document["id"],case["page"],normalize_row_label(case["query"]["row_label"]),case["query"]["year"])
            query_groups.setdefault(key,[]).append(case["id"])
    by_id={c["id"]:c for c in all_cases}
    for ids in query_groups.values():
        require(len({by_id[case_id]["extraction_sha256"] for case_id in ids})==1,
                "same_query_different_extraction","queries")
    return {"schema_version":1,"experiment_version":VERSION,"config_sha256":digest(config),"config_file_sha256":config_sha,
            "baseline_report_sha256":prior["report_sha256"],"word_source":deepcopy(config["word_source"]),"implementation_sha256":_hashes(root),
            "implementation_hash_basis":"python_utf8_lf","input_comparison":{"method":"canonical_complete_line_projection_sha256","pages":comparison,
                "all_line_projections_match_previous":all(p["line_projection_matches_previous"] for p in comparison)},
            "scope":{"known_query_page":True,"known_query_labels":True,"gold_amount_or_bbox_given_to_extractor":False,
                     "previously_observed_cases":42,"independent_human_gold":0,"new_ocr_calls":0,"model_calls":0,"raw_pdf_bytes_checked":False,"held_out":False},
            "query_inventory":{"registered_cases":len(all_cases),"unique_query_inputs":len(query_groups),
                "duplicated_query_groups":[{"document_id":key[0],"page":key[1],"row_label":key[2],"year":key[3],"case_ids":ids}
                    for key,ids in query_groups.items() if len(ids)>1]},
            "documents":documents,"summary":_summary(all_cases),"transitions":_transitions(all_cases),
            "controls":{"cases":controls,"summary":{"cases":len(controls),"passed":sum(c["passed"] for c in controls),"failed":sum(not c["passed"] for c in controls)}},
            "limitations":list(LIMITATIONS),"read_only":True,"automatic_approval":False}


def build_word_table_lookup_report(root:Path,ocr_root:Path|None=None)->dict:
    root=Path(root).resolve();ocr_root=Path(ocr_root).resolve() if ocr_root else root/".run/reproductions/document-ai-words"
    config,sha,prior=_inputs(root);hashes=_hashes(root)
    pages={(d["id"],p["page"]):_page(ocr_root,p) for d in config["documents"] for p in d["pages"]}
    outputs={c["id"]:extract(pages[d["id"],c["page"]],c["query"]) for d in prior["report"]["documents"] for c in d["cases"]}
    frozen=deepcopy(outputs)
    controls={c["id"]:extract(WordPage.model_validate(c["page"]),c["query"]) for c in _controls()}
    report=_assemble(root,config,sha,prior,frozen,controls)
    last,lastsha,lastprior=_inputs(root)
    require(last==config and lastsha==sha and lastprior==prior and hashes==_hashes(root),"inputs_changed_during_run","inputs")
    return report


def validate_word_table_lookup_report(root:Path,report:dict,*,ocr_root:Path|None=None)->dict:
    root=Path(root).resolve()
    try:
        if ocr_root is not None: expected=build_word_table_lookup_report(root,ocr_root)
        else:
            config,sha,prior=_inputs(root)
            def restore(case): return {**case["extraction_metadata"],"methods":{k:case["methods"][k]["extraction"] for k in METHODS}}
            outputs={c["id"]:restore(c) for d in report["documents"] for c in d["cases"]}
            controls={c["id"]:restore(c) for c in report["controls"]["cases"]}
            require(len(outputs)==sum(len(d["cases"]) for d in report["documents"])==42
                    and set(outputs)=={c["id"] for d in prior["report"]["documents"] for c in d["cases"]}
                    and len(controls)==len(report["controls"]["cases"])==len(_controls()),"case_inventory_mismatch","cases")
            expected=_assemble(root,config,sha,prior,outputs,controls)
        require(digest(report)==digest(expected),"report_mismatch","report")
    except WordTableExperimentError: raise
    except (ValueError,TypeError,KeyError,StopIteration,AttributeError) as exc:
        raise WordTableExperimentError("invalid_report","report") from exc
    return deepcopy(report)


def load_word_table_lookup_snapshot(root:Path)->dict:
    root=Path(root).resolve();_,release=_read(root/RELEASE)
    require(set(release)=={"schema_version","report_path","report_sha256"} and type(release["schema_version"]) is int
            and release["schema_version"]==1 and release["report_path"]==REPORT,"invalid_release",RELEASE)
    raw,report=_read(root/REPORT)
    require(_sha(raw)==release["report_sha256"],"published_report_changed",REPORT)
    report=validate_word_table_lookup_report(root,report)
    require(_read(root/RELEASE)[1]==release,"release_changed_during_read",RELEASE)
    return {"schema_version":1,"read_only":True,"report":report,"report_sha256":_sha(raw),"full_ocr_replayed":False,
            "validation_scope":"发布摘要、来源与配置、完整案例集合及保存候选评分；未重新执行词级抽取或检查完整 OCR。"}
