"""Same-input line/word/role table lookup; references enter only after extraction."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

from .diligence import digest
from .table_lookup import normalize_row_label
from .table_lookup_experiment import score_extraction
from .visual import OcrSpan
from .word_evidence import WordPage
from . import word_table_experiment as old

VERSION = "role-table-experiment-1"
CONFIG = "configs/role-table-lookup.json"
REPORT = "artifacts/role-table-lookup-report.json"
RELEASE = "configs/role-table-lookup-artifact.json"
IMPLEMENTATION_FILES = (*old.IMPLEMENTATION_FILES, "creditlens/role_table_lookup.py",
                        "creditlens/role_table_experiment.py", "scripts/evaluate_role_table_lookup.py")
METHODS = ("line", "word", "role")
STATES = old.STATES
LIMITATIONS = [
    "42条已观察登记记录、40组不同查询、六页保存WordPage；给定报表页、行名和年份，未新增OCR或独立人工金标准。",
    "旧行级和词级结果逐字段保持；新方法区分行级定位锚与金额原词。角色区分和符号配对一起变化，不是单因素消融。",
    "参考金额、框和分区只参与后置评分，不进入抽取；同查询对应不同参考时仍必须输出相同候选。",
    "所有改善、回退、错取与不足完整保留；固定合成破坏性控制与真实登记材料分别统计。",
    "公开小报告只核验保存结果和可见局部证据；完整抽取复演须显式读取六份WordPage，不能由局部证据证明整页无冲突。",
    "数量级与美元符号不能单独确定币种；分数不是校准概率，匹配不表示来源验真、完整财务期间核实或批准。",
]


class RoleTableExperimentError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def require(condition, code, path):
    if not condition:
        raise RoleTableExperimentError(code, path)


def _read(path, limit=8_000_000):
    try:
        return old._read(path, limit)
    except ValueError as exc:
        raise RoleTableExperimentError("input_unavailable", Path(path).name) from exc


def _hashes(root):
    try:
        return {name: old._sha((root/name).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode())
                for name in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc:
        raise RoleTableExperimentError("implementation_unavailable", "implementation") from exc


def _inputs(root):
    try:
        raw, config = _read(root/CONFIG)
        previous = old.load_word_table_lookup_snapshot(root)
        word_raw, word_config = _read(root/old.CONFIG)
        require(type(config["schema_version"]) is int and config["schema_version"] == 1
                and set(config) == {"schema_version", "id", "rule_version", "query_fields", "baseline", "thresholds", "documents", "scope"}
                and config["id"] == "role-table-lookup-development-1" and config["rule_version"] == "role-table-lookup-1"
                and config["query_fields"] == ["row_label", "year"], "invalid_config", CONFIG)
        require(config["baseline"] == {"report_path": old.REPORT, "report_sha256": previous["report_sha256"],
                "config_path": old.CONFIG, "config_sha256": old._sha(word_raw)}, "baseline_changed", "baseline")
        require(digest(config["documents"]) == digest(word_config["documents"]), "input_inventory_changed", "documents")
        require(digest(config["thresholds"]) == digest({"minimum_confidence": .9, "parent_containment_tolerance_points": 0.,
                "same_parent_symbol_gap_points": 8., "cross_parent_policy": "whole_row_dollar_amount_pairs_with_left_barrier",
                "cross_parent_gap_limit": None}), "threshold_changed", "thresholds")
        require(digest(config["scope"]) == digest({"previously_observed": True, "independent_human_gold": 0, "new_ocr_calls": 0,
                                    "known_query_page": True}), "scope_changed", "scope")
        return config, old._sha(raw), previous
    except RoleTableExperimentError:
        raise
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise RoleTableExperimentError("registration_unavailable", "inputs") from exc


def extract(page, query):
    from .role_table_lookup import lookup_role_table
    require(type(query) is dict and set(query) == {"row_label", "year"}, "invalid_query", "query")
    return lookup_role_table(page, row_label=query["row_label"], year=query["year"])


def _validate_prefix(entry, parents, mappings, spans, anchors, refs_checked, union, associated):
    from .table_lookup import parse_lookup_amount
    from .word_table_lookup import _same_row
    keys = {"span_index", "column_year", "column_left", "column_right", "prefix_ref", "amount_ref", "left_barrier", "gap",
            "raw_value", "numeric_bbox", "display_bbox", "geometry_basis"}
    require(type(entry) is dict and set(entry) == keys, "invalid_prefix_association", "role")
    index = entry["span_index"]
    require(type(index) is int and index in mappings and index not in associated
            and mappings[index]["origin"] == "derived_accounting_cell", "invalid_prefix_mapping", "role")
    refs = [entry["prefix_ref"], entry["amount_ref"]]
    words = refs_checked(refs, set(parents)-set(anchors))
    prefix, number = words
    require(mappings[index]["word_refs"] == refs and prefix["text"].strip() == "$"
            and not any(marker in number["text"] for marker in ("$", "¥", "￥")), "invalid_prefix_tokens", "role")
    parse_lookup_amount(number["text"])
    require(entry["numeric_bbox"] == number["bbox"] and entry["display_bbox"] == union(words)
            and entry["raw_value"] == "$"+number["text"].strip() == spans[index]["text"]
            and entry["geometry_basis"] == "numeric_token_for_column_and_scoring; complete_group_union_for_display",
            "prefix_geometry_mismatch", "role")
    require(all(type(entry[key]) in (int, float) for key in ("column_left", "column_right", "left_barrier", "gap"))
            and type(entry["column_year"]) is int and entry["gap"] == number["bbox"]["x0"]-prefix["bbox"]["x1"]
            and entry["left_barrier"] <= prefix["bbox"]["x0"] < prefix["bbox"]["x1"] <= number["bbox"]["x0"]
            and entry["column_left"] <= number["bbox"]["x0"] < number["bbox"]["x1"] <= entry["column_right"]
            and _same_row(OcrSpan.model_validate(prefix), OcrSpan.model_validate(number)), "invalid_prefix_position", "role")
    associated[index] = entry


def _validate_output(output, query, page, shared_parents):
    from . import role_table_lookup as core
    from . import word_table_lookup as word_core
    from .table_lookup import MESSAGES as LINE_MESSAGES
    require(type(output) is dict and set(output) == {"schema_version", "rule_version", "query", "methods", "scope", "automatic_approval"}
            and type(output["schema_version"]) is int and output["schema_version"] == 1
            and output["rule_version"] == "role-table-lookup-1" and output["scope"] == core.SCOPE
            and output["automatic_approval"] is False and set(output["methods"]) == set(METHODS), "invalid_output", "output")
    expected_query = {"page": page["page"], **query, "normalized_row_label": normalize_row_label(query["row_label"])}
    require(output["query"] == expected_query, "query_binding_mismatch", "query")
    legacy = {"schema_version": 1, "rule_version": "word-table-lookup-1", "query": expected_query,
              "methods": {k: output["methods"][k] for k in ("line", "word")}, "scope": word_core.SCOPE, "automatic_approval": False}
    old._validate_output(legacy, query, page, shared_parents)
    role = output["methods"]["role"]
    extra = {"selected_span_origin", "parent_evidence", "guard_issues", "adaptation", "confidence_basis",
             "evidence_roles", "nonblocking_anchor_issues", "prefix_associations"}
    require(type(role) is dict and set(role) == old.BASE_FIELDS | extra and role["method"] == "role_layout"
            and role["confidence_basis"] == "parent_line_for_anchors; parent_and_native_words_for_amounts; scores_not_calibrated",
            "invalid_role_output", "role")
    messages = {**LINE_MESSAGES, **word_core.MESSAGES, **core.MESSAGES}
    require(role["reason"] in messages and role["message"] == messages[role["reason"]], "invalid_role_reason", "role")
    adaptation = role["adaptation"]
    require(type(adaptation) is dict and set(adaptation) == {"status", "line_input_sha256", "derived_page_sha256", "mapping_scope", "source_map"}
            and adaptation["status"] in {"applied", "not_applied"} and adaptation["line_input_sha256"] == page["line_projection_sha256"]
            and adaptation["mapping_scope"] == "referenced_spans_only" and type(adaptation["source_map"]) is list,
            "invalid_adaptation", "role")
    require((adaptation["status"] == "not_applied" and adaptation["derived_page_sha256"] is None)
            or (adaptation["status"] == "applied" and type(adaptation["derived_page_sha256"]) is str
                and re.fullmatch(r"[0-9a-f]{64}", adaptation["derived_page_sha256"])), "invalid_derived_digest", "role")
    parents = {}
    require(type(role["parent_evidence"]) is list, "invalid_parents", "role")
    for parent in role["parent_evidence"]:
        require(type(parent) is dict and set(parent) == {"line_index", "line", "words", "text_conserved", "geometry_valid"}, "invalid_parent", "role")
        index = parent["line_index"]
        require(type(index) is int and 0 <= index < page["line_count"] and index not in parents
                and type(parent["words"]) is list, "invalid_parent", "role")
        require(all(type(w) is dict and set(w) == {"word_index", "span"} and type(w["word_index"]) is int for w in parent["words"])
                and [w["word_index"] for w in parent["words"]] == list(range(len(parent["words"]))), "invalid_parent_words", "role")
        for data in [parent["line"], *(w["span"] for w in parent["words"])]:
            span = OcrSpan.model_validate(data)
            require(span.bbox.x1 <= page["width"] and span.bbox.bottom <= page["height"], "span_out_of_page", "role")
        compact = lambda text: "".join(text.split())
        text_ok = bool(parent["words"]) and compact(parent["line"]["text"]) == "".join(compact(w["span"]["text"]) for w in parent["words"])
        outer = parent["line"]["bbox"]
        geometry = bool(parent["words"]) and all(outer["x0"] <= w["span"]["bbox"]["x0"] < w["span"]["bbox"]["x1"] <= outer["x1"]
                    and outer["top"] <= w["span"]["bbox"]["top"] < w["span"]["bbox"]["bottom"] <= outer["bottom"] for w in parent["words"])
        require(parent["text_conserved"] is text_ok and parent["geometry_valid"] is geometry, "parent_flag_mismatch", "role")
        preserved = {"line": parent["line"], "words": parent["words"]}
        require(index not in shared_parents or shared_parents[index] == preserved, "inconsistent_shared_parent", "role")
        shared_parents[index] = preserved; parents[index] = parent
    roles = role["evidence_roles"]
    require(type(roles) is dict and set(roles) == {"line_anchors", "amount_parents"}
            and type(roles["line_anchors"]) is list and type(roles["amount_parents"]) is list, "invalid_evidence_roles", "role")
    anchors = {}
    for entry in roles["line_anchors"]:
        require(type(entry) is dict and set(entry) == {"line_index", "roles"} and type(entry["line_index"]) is int
                and entry["line_index"] in parents and entry["line_index"] not in anchors and type(entry["roles"]) is list
                and len(entry["roles"]) == len(set(entry["roles"])) > 0
                and set(entry["roles"]) <= {"row_label", "unit", "year_header"}, "invalid_anchor_role", "role")
        anchors[entry["line_index"]] = set(entry["roles"])
    amount_ids = roles["amount_parents"]
    require(all(type(i) is int and i in parents for i in amount_ids) and amount_ids == sorted(set(amount_ids))
            and not set(amount_ids) & set(anchors), "invalid_amount_roles", "role")
    expected_anchors = {}
    for key, label in (("row_label", "row_label"), ("unit", "unit"), ("year_headers", "year_header")):
        for entry in output["methods"]["line"]["evidence"][key]:
            expected_anchors.setdefault(entry["span_index"], set()).add(label)
    require(anchors == expected_anchors, "anchor_role_mismatch", "role")
    require(set(parents) == set(anchors) | set(amount_ids), "unclassified_parent", "role")
    mappings = {}
    for entry in adaptation["source_map"]:
        require(type(entry) is dict and set(entry) == {"span_index", "origin", "parent_indices", "word_refs"}, "invalid_source_map", "role")
        index = entry["span_index"]; ids = entry["parent_indices"]
        require(type(index) is int and index >= 0 and index not in mappings and type(ids) is list
                and all(type(i) is int and i in parents for i in ids) and ids == sorted(set(ids)) and ids
                and entry["origin"] in {"original_parent", "derived_word_group", "derived_accounting_cell"}, "invalid_source_map", "role")
        mappings[index] = entry
    safe = deepcopy(role)
    safe["reason"] = "selected" if role["status"] == "selected" else "row_not_found"
    safe["message"] = LINE_MESSAGES[safe["reason"]]
    old._validate_base(safe, expected_query, page, max([page["line_count"], *(i+1 for i in mappings)]))
    spans = {}
    for entries in role["evidence"].values():
        for entry in entries:
            index = entry["span_index"]
            require(index not in spans or spans[index] == entry["span"], "inconsistent_derived_span", "role")
            spans[index] = entry["span"]
    require(set(mappings) == set(spans), "mapping_evidence_mismatch", "role")
    def refs_checked(refs, allowed):
        require(type(refs) is list, "invalid_word_refs", "role")
        keys = []
        for ref in refs:
            require(type(ref) is dict and set(ref) == {"line_index", "word_index"}
                    and type(ref["line_index"]) is int and ref["line_index"] in allowed
                    and type(ref["word_index"]) is int and 0 <= ref["word_index"] < len(parents[ref["line_index"]]["words"]),
                    "invalid_word_ref", "role")
            keys.append((ref["line_index"], ref["word_index"]))
        require(len(keys) == len(set(keys)), "duplicate_word_ref", "role")
        return [parents[i]["words"][j]["span"] for i, j in keys]
    def union(values):
        return {"x0": min(s["bbox"]["x0"] for s in values), "top": min(s["bbox"]["top"] for s in values),
                "x1": max(s["bbox"]["x1"] for s in values), "bottom": max(s["bbox"]["bottom"] for s in values)}
    associated = {}
    require(type(role["prefix_associations"]) is list, "invalid_prefix_associations", "role")
    for entry in role["prefix_associations"]:
        # All visible pairing fields are recomputed below, not treated as self-reported success.
        _validate_prefix(entry, parents, mappings, spans, anchors, refs_checked, union, associated)
    if associated:
        headers = sorted([i for i in anchors if "year_header" in anchors[i]
                          and re.fullmatch(r"(?:19|20|21)[0-9]{2}", parents[i]["line"]["text"].strip())],
                         key=lambda i: (parents[i]["line"]["bbox"]["x0"]+parents[i]["line"]["bbox"]["x1"])/2)
        labels = [i for i in anchors if "row_label" in anchors[i]]
        require(len(headers) == len(associated) >= 2 and len(labels) == 1, "incomplete_accounting_row", "role")
        centers = [(parents[i]["line"]["bbox"]["x0"]+parents[i]["line"]["bbox"]["x1"])/2 for i in headers]
        bounds = [max(0., centers[0]-(centers[1]-centers[0])/2), *[(a+b)/2 for a,b in zip(centers,centers[1:])],
                  min(page["width"], centers[-1]+(centers[-1]-centers[-2])/2)]
        barrier = parents[labels[0]]["line"]["bbox"]["x1"]
        consumed = []
        for position, entry in enumerate(role["prefix_associations"]):
            require(entry["column_year"] == int(parents[headers[position]]["line"]["text"].strip())
                    and entry["column_left"] == bounds[position] and entry["column_right"] == bounds[position+1]
                    and entry["left_barrier"] == barrier, "accounting_column_or_barrier_mismatch", "role")
            barrier = entry["numeric_bbox"]["x1"]
            consumed.extend((entry[key]["line_index"], entry[key]["word_index"]) for key in ("prefix_ref", "amount_ref"))
        all_words = {(i,j) for i in amount_ids for j in range(len(parents[i]["words"]))}
        require(len(consumed) == len(set(consumed)) and set(consumed) == all_words,
                "unassigned_accounting_words", "role")
    for index, entry in mappings.items():
        ids = entry["parent_indices"]
        if entry["origin"] == "original_parent":
            require(len(ids) == 1 and entry["word_refs"] == [] and spans[index] == parents[ids[0]]["line"], "original_parent_mismatch", "role")
        else:
            refs = entry["word_refs"]; words = refs_checked(refs, set(ids))
            require(words and words == sorted(words, key=lambda s: s["bbox"]["x0"])
                    and sorted({ref["line_index"] for ref in refs}) == ids and set(ids) <= set(amount_ids), "invalid_derived_refs", "role")
            bbox = union(words)
            if entry["origin"] == "derived_accounting_cell":
                require(index in associated, "missing_prefix_association", "role")
                bbox = associated[index]["numeric_bbox"]
            else:
                require(len(ids) == 1, "unexpected_cross_parent_group", "role")
            derived = {"text": "".join(w["text"].strip() for w in words), "bbox": bbox,
                       "confidence": min([parents[i]["line"]["confidence"] for i in ids] + [w["confidence"] for w in words])}
            require(derived == spans[index], "derived_span_mismatch", "role")
    if adaptation["status"] == "applied":
        from .table_lookup import parse_lookup_amount, TableLookupError
        from .word_table_lookup import _same_row
        derived_maps = [entry for entry in mappings.values() if entry["origin"] != "original_parent"]
        consumed = [(ref["line_index"], ref["word_index"]) for entry in derived_maps for ref in entry["word_refs"]]
        all_words = {(i,j) for i in amount_ids for j in range(len(parents[i]["words"]))}
        require(len(consumed) == len(set(consumed)) and set(consumed) == all_words,
                "unassigned_amount_words", "role")
        labels = [i for i in anchors if "row_label" in anchors[i]]
        require(len(labels) == 1, "derived_row_not_unique", "role")
        row = OcrSpan.model_validate(parents[labels[0]]["line"])
        require(all(_same_row(row, OcrSpan.model_validate(w["span"])) for i in amount_ids for w in parents[i]["words"]),
                "derived_amount_row_mismatch", "role")
        if not associated:
            headers = sorted([i for i in anchors if "year_header" in anchors[i]
                              and re.fullmatch(r"(?:19|20|21)[0-9]{2}", parents[i]["line"]["text"].strip())],
                             key=lambda i: (parents[i]["line"]["bbox"]["x0"]+parents[i]["line"]["bbox"]["x1"])/2)
            require(len(headers) == len(derived_maps) >= 2, "derived_column_inventory", "role")
            centers = [(parents[i]["line"]["bbox"]["x0"]+parents[i]["line"]["bbox"]["x1"])/2 for i in headers]
            bounds = [max(0., centers[0]-(centers[1]-centers[0])/2), *[(a+b)/2 for a,b in zip(centers,centers[1:])],
                      min(page["width"], centers[-1]+(centers[-1]-centers[-2])/2)]
            used_columns = []
            for entry in derived_maps:
                words = refs_checked(entry["word_refs"], set(amount_ids))
                columns = [k for k in range(len(headers)) if all(bounds[k] <= w["bbox"]["x0"]
                           and w["bbox"]["x1"] <= bounds[k+1] for w in words)]
                require(len(columns) == 1, "derived_column_mismatch", "role"); used_columns.extend(columns)
                require(all(0 <= b["bbox"]["x0"]-a["bbox"]["x1"] <= 8. for a,b in zip(words, words[1:])),
                        "derived_word_order_or_gap", "role")
                numbers = 0
                for word in words:
                    try:
                        parse_lookup_amount(word["text"]); numbers += 1
                    except TableLookupError:
                        require(word["text"].strip() in {"$", "¥", "￥", "+", "-", "(", ")"}, "unsupported_derived_symbol", "role")
                require(numbers == 1, "derived_numeric_inventory", "role")
                parse_lookup_amount(spans[entry["span_index"]]["text"])
            require(sorted(used_columns) == list(range(len(headers))), "duplicate_derived_column", "role")
    for key, allowed in (("guard_issues", set(parents)), ("nonblocking_anchor_issues", set(anchors))):
        require(type(role[key]) is list, "invalid_guards", "role")
        for issue in role[key]:
            require(type(issue) is dict and set(issue) == {"code", "line_indices", "word_refs"}
                    and issue["code"] in messages and type(issue["line_indices"]) is list
                    and all(type(i) is int and i in allowed for i in issue["line_indices"])
                    and issue["line_indices"] == sorted(set(issue["line_indices"])), "invalid_guard", "role")
            refs_checked(issue["word_refs"], set(issue["line_indices"]))
    expected_nonblocking = []
    for index in sorted(anchors):
        parent = parents[index]; refs = [{"line_index": index, "word_index": j} for j in range(len(parent["words"]))]
        def issue(code, references):
            return {"code": code, "line_indices": [index], "word_refs": references}
        if not parent["words"]:
            expected_nonblocking.append(issue("missing_word_evidence", [])); continue
        if not parent["text_conserved"]: expected_nonblocking.append(issue("parent_word_text_mismatch", refs))
        outer = parent["line"]["bbox"]
        outside = [refs[j] for j,w in enumerate(parent["words"]) if not (outer["x0"] <= w["span"]["bbox"]["x0"]
                   and w["span"]["bbox"]["x1"] <= outer["x1"] and outer["top"] <= w["span"]["bbox"]["top"]
                   and w["span"]["bbox"]["bottom"] <= outer["bottom"])]
        if outside: expected_nonblocking.append(issue("word_outside_parent", outside))
        if any(w["span"]["confidence"] < .9 for w in parent["words"]): expected_nonblocking.append(issue("word_low_confidence", refs))
    require(role["nonblocking_anchor_issues"] == expected_nonblocking, "anchor_issue_mismatch", "role")
    require(not role["guard_issues"] or (role["status"] == "insufficient" and role["reason"] == role["guard_issues"][0]["code"]),
            "guard_status_mismatch", "role")
    require(role["selected_span_origin"] in {None, "derived_word_group", "derived_accounting_cell"}
            and (role["status"] == "selected" or role["selected_span_origin"] is None), "invalid_candidate_origin", "role")
    if role["status"] == "selected":
        require(not role["guard_issues"] and role["selected_span_origin"] in {"derived_word_group", "derived_accounting_cell"}
                and mappings[role["selected_span_index"]]["origin"] == role["selected_span_origin"], "selected_guard_conflict", "role")
        require(all(parents[i]["line"]["confidence"] >= .9 for i in anchors), "anchor_parent_not_qualified", "role")
        require(all(parents[i]["text_conserved"] and parents[i]["geometry_valid"] and parents[i]["line"]["confidence"] >= .9
                    and all(w["span"]["confidence"] >= .9 for w in parents[i]["words"]) for i in amount_ids), "amount_parent_not_qualified", "role")


def _summary(cases):
    summary = {}
    for method in METHODS:
        scores = [case["methods"][method]["score"] for case in cases]
        counts = Counter(score["outcome"] for score in scores)
        summary[method] = {"cases": len(scores), **{state: counts[state] for state in STATES},
                           "selected": counts["matched"]+counts["wrong"],
                           "amount_match": sum(s["amount_match"] is True for s in scores),
                           "reference_location_match": sum(s["reference_location_match"] is True for s in scores)}
    return summary


def _transitions(cases):
    return {method+"_to_role": {before: {after: sum(case["methods"][method]["score"]["outcome"] == before
                and case["methods"]["role"]["score"]["outcome"] == after for case in cases) for after in STATES} for before in STATES}
            for method in ("line", "word")}


def _case(former, output):
    methods = {method: {"extraction": deepcopy(output["methods"][method]),
                        "score": score_extraction(output["methods"][method], former["reference"])} for method in METHODS}
    return {"id": former["id"], "fact_id": former["fact_id"], "page": former["page"],
            "query": deepcopy(former["query"]), "reference": deepcopy(former["reference"]),
            "extraction_metadata": {k: deepcopy(v) for k, v in output.items() if k != "methods"},
            "extraction_sha256": digest(output), "methods": methods,
            "transitions": {method+"_to_role": {"from": methods[method]["score"]["outcome"],
                                                 "to": methods["role"]["score"]["outcome"]} for method in ("line", "word")}}


def _controls():
    span = lambda text, x, y, width=40, confidence=.99: {"text": text, "confidence": confidence,
        "bbox": {"x0": x, "top": y, "x1": x+width, "bottom": y+10}}
    spans = [span("(In millions)",20,10,90), span("2025",250,30), span("2024",350,30),
             span("Cash from operations",20,60,150), span("100",250,60), span("200",350,60)]
    base = {"page": 1, "width": 500., "height": 300., "coordinate_system": "pdf_points_top_left",
            "lines": [{"line": s, "words": [deepcopy(s)]} for s in spans]}
    planned = [("normal", "matched"), ("anchor_low_native", "matched"), ("anchor_native_outside", "matched"),
               ("amount_low_native", "insufficient"), ("amount_outside_parent", "insufficient"),
               ("missing_amount_words", "insufficient"), ("dropped_parentheses", "insufficient"),
               ("merged_columns", "matched"), ("leading_dollars", "matched"), ("leading_negative", "matched"),
               ("trailing_dollar", "insufficient"), ("duplicate_prefix", "insufficient"),
               ("wrong_currency", "insufficient"), ("two_numeric_tokens", "insufficient"),
               ("duplicate_label", "insufficient")]
    cases = []
    for operation, expected in planned:
        data = deepcopy(base)
        if operation == "anchor_low_native": data["lines"][3]["words"][0]["confidence"] = .2
        elif operation == "anchor_native_outside": data["lines"][0]["words"][0]["bbox"]["x1"] += .5
        elif operation == "amount_low_native": data["lines"][5]["words"][0]["confidence"] = .2
        elif operation == "amount_outside_parent": data["lines"][5]["words"][0]["bbox"]["x1"] += .5
        elif operation == "missing_amount_words": data["lines"][5]["words"] = []
        elif operation == "dropped_parentheses": data["lines"][5]["line"]["text"] = "(200)"
        elif operation == "merged_columns":
            data["lines"][4] = {"line": span("100 200",250,60,140), "words": [deepcopy(spans[4]), deepcopy(spans[5])]}
            data["lines"].pop()
        elif operation in ("leading_dollars", "leading_negative", "trailing_dollar", "duplicate_prefix", "wrong_currency"):
            for x in (244, 344):
                symbol = span("$", x, 60, 4)
                data["lines"].append({"line": symbol, "words": [deepcopy(symbol)]})
            if operation == "trailing_dollar":
                extra = data["lines"][-1]
                for s in (extra["line"], extra["words"][0]): s["bbox"].update(x0=392, x1=396)
            elif operation == "duplicate_prefix":
                symbol = span("$",338,60,4)
                data["lines"].append({"line": symbol, "words": [deepcopy(symbol)]})
            elif operation == "wrong_currency":
                data["lines"][-1]["line"]["text"] = data["lines"][-1]["words"][0]["text"] = "¥"
            elif operation == "leading_negative":
                data["lines"][5]["line"]["text"] = data["lines"][5]["words"][0]["text"] = "(200)"
        elif operation == "two_numeric_tokens":
            extra = span("3",394,60,4)
            data["lines"].append({"line": extra, "words": [deepcopy(extra)]})
        elif operation == "duplicate_label":
            extra = span("Cash from operations",20,90,150)
            data["lines"].append({"line": extra, "words": [deepcopy(extra)]})
        cases.append({"id": operation, "provenance": "synthetic_development_control", "query": {"row_label": "Cash from operations", "year": 2024},
                      "input_page": data, "reference": {"raw_value": "(200)" if operation == "leading_negative" else "200",
                          "signed_amount": "-200" if operation == "leading_negative" else "200", "amount_bbox": spans[5]["bbox"]},
                      "expected_role_outcome": expected})
    return cases


def _assemble(root, config, config_sha, previous, outputs, control_outputs):
    documents = []
    for registration in config["documents"]:
        before = next(d for d in previous["report"]["documents"] if d["id"] == registration["id"])
        pages = {p["page"]: p for p in registration["pages"]}
        shared_parents = {page: {} for page in pages}
        cases = []
        for former in before["cases"]:
            output = outputs[former["id"]]
            _validate_output(output, former["query"], pages[former["page"]], shared_parents[former["page"]])
            require(all(output["methods"][method] == former["methods"][method]["extraction"] for method in ("line", "word")),
                    "legacy_extraction_changed", former["id"])
            case = _case(former, output)
            case["legacy_extractions_match_previous"] = True
            case["previous_extraction_sha256"] = former["extraction_sha256"]
            cases.append(case)
        documents.append({"id": before["id"], "company": before["company"], "source": deepcopy(before["source"]),
                          "pack_sha256": before["pack_sha256"], "pages": deepcopy(registration["pages"]),
                          "cases": cases, "summary": _summary(cases), "transitions": _transitions(cases)})
    controls = []
    for spec in _controls():
        data = spec["input_page"]; page = WordPage.model_validate(data)
        registration = {"page": page.page, "width": page.width, "height": page.height, "line_count": len(page.lines),
                        "line_projection_sha256": digest(page.line_page().model_dump(mode="json"))}
        output = control_outputs[spec["id"]]
        _validate_output(output, spec["query"], registration, {})
        former = {"id": spec["id"], "fact_id": spec["id"], "page": page.page, "query": spec["query"], "reference": spec["reference"]}
        result = _case(former, output)
        controls.append({**deepcopy(spec), **result, "input_sha256": digest({"page": data, "query": spec["query"]}),
                         "passed": result["methods"]["role"]["score"]["outcome"] == spec["expected_role_outcome"]})
    cases = [case for doc in documents for case in doc["cases"]]
    groups = {}
    for doc in documents:
        for case in doc["cases"]:
            key = (doc["id"], doc["source"]["sha256"], case["page"], normalize_row_label(case["query"]["row_label"]), case["query"]["year"])
            groups.setdefault(key, []).append(case)
    require(all(len({case["extraction_sha256"] for case in items}) == 1 for items in groups.values()),
            "same_query_different_extraction", "queries")
    return {"schema_version": 1, "experiment_version": VERSION, "config_file_sha256": config_sha,
            "config_sha256": digest(config), "baseline_report_sha256": previous["report_sha256"],
            "implementation_hash_basis": "python_utf8_lf", "implementation_sha256": _hashes(root),
            "input_comparison": {"baseline_word_report_sha256": previous["report_sha256"], "all_word_inputs_match_previous": True,
                                 "all_legacy_extractions_match_previous": True, "previous_line_comparison": deepcopy(previous["report"]["input_comparison"])},
            "query_inventory": {"registered_cases": len(cases), "unique_query_inputs": len(groups),
                "duplicated_query_groups": [{"document_id": key[0], "source_pdf_sha256": key[1], "page": key[2],
                    "row_label": key[3], "year": key[4], "case_ids": [case["id"] for case in values]}
                    for key, values in groups.items() if len(values) > 1]},
            "documents": documents, "summary": _summary(cases), "transitions": _transitions(cases),
            "controls": {"cases": controls, "summary": {"cases": len(controls), "passed": sum(c["passed"] for c in controls),
                                                         "failed": sum(not c["passed"] for c in controls)}},
            "scope": {"known_query_page": True, "known_query_labels": True, "gold_amount_or_bbox_given_to_extractor": False,
                      "previously_observed_cases": 42, "independent_human_gold": 0, "new_ocr_calls": 0,
                      "model_calls": 0, "raw_pdf_bytes_checked": False, "held_out": False},
            "limitations": list(LIMITATIONS), "read_only": True, "automatic_approval": False}


def build_role_table_lookup_report(root: Path, ocr_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    directory = Path(ocr_root).resolve() if ocr_root is not None else root/".run/reproductions/document-ai-words"
    config, config_sha, previous = _inputs(root); hashes = _hashes(root)
    pages = {(d["id"], p["page"]): old._page(directory, p) for d in config["documents"] for p in d["pages"]}
    outputs = {case["id"]: extract(pages[doc["id"], case["page"]], case["query"])
               for doc in previous["report"]["documents"] for case in doc["cases"]}
    # No financial reference is passed until every extraction is complete.
    frozen = deepcopy(outputs)
    control_outputs = {c["id"]: extract(WordPage.model_validate(c["input_page"]), c["query"]) for c in _controls()}
    report = _assemble(root, config, config_sha, previous, frozen, control_outputs)
    after, after_sha, after_previous = _inputs(root)
    require(after == config and after_sha == config_sha and after_previous == previous and hashes == _hashes(root),
            "inputs_changed_during_run", "inputs")
    return report


def validate_role_table_lookup_report(root: Path, report: dict, *, ocr_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    try:
        if ocr_root is not None:
            expected = build_role_table_lookup_report(root, ocr_root)
        else:
            config, config_sha, previous = _inputs(root)
            def restore(case):
                return {**case["extraction_metadata"], "methods": {method: case["methods"][method]["extraction"] for method in METHODS}}
            outputs = {case["id"]: restore(case) for doc in report["documents"] for case in doc["cases"]}
            require(len(outputs) == sum(len(doc["cases"]) for doc in report["documents"]) == 42
                    and set(outputs) == {case["id"] for doc in previous["report"]["documents"] for case in doc["cases"]},
                    "case_inventory_mismatch", "cases")
            controls = {c["id"]: extract(WordPage.model_validate(c["input_page"]), c["query"]) for c in _controls()}
            expected = _assemble(root, config, config_sha, previous, outputs, controls)
        require(digest(report) == digest(expected), "report_mismatch", "report")
        return deepcopy(report)
    except RoleTableExperimentError:
        raise
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError) as exc:
        raise RoleTableExperimentError("invalid_report", "report") from exc


def load_role_table_lookup_snapshot(root: Path) -> dict:
    root = Path(root).resolve(); _, release = _read(root/RELEASE)
    require(set(release) == {"schema_version", "report_path", "report_sha256"} and type(release["schema_version"]) is int
            and release["schema_version"] == 1 and release["report_path"] == REPORT, "invalid_release", RELEASE)
    raw, report = _read(root/REPORT)
    require(old._sha(raw) == release["report_sha256"], "published_report_changed", REPORT)
    normalized = validate_role_table_lookup_report(root, report)
    require(_read(root/RELEASE)[1] == release, "release_changed_during_read", RELEASE)
    return {"schema_version": 1, "read_only": True, "report": normalized, "report_sha256": old._sha(raw),
            "full_ocr_replayed": False,
            "validation_scope": "发布摘要、旧结果不变、完整记录、保存候选局部关系与评分；合成控制重算，未重新抽取六份完整OCR。"}
