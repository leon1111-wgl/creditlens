"""Enumerate all explicit row-choice paths without choosing a financial answer."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

from .diligence import digest
from .visual import OcrPage, OcrSpan
from .table_lookup import normalize_row_label
from . import role_table_experiment as prior

VERSION = "row-disambiguation-experiment-1"
CONFIG = "configs/row-disambiguation.json"
REPORT = "artifacts/row-disambiguation-report.json"
RELEASE = "configs/row-disambiguation-artifact.json"
IMPLEMENTATION_FILES = (*prior.IMPLEMENTATION_FILES, "creditlens/row_disambiguation.py",
                        "creditlens/row_disambiguation_experiment.py", "scripts/evaluate_row_disambiguation.py")
LIMITATIONS = [
    "只枚举旧报告全部同名行不足的不同查询；6条旧记录对应4组查询和8条显式候选路径，不改变原42条评分。",
    "选择原行索引是新增条件，不是自动消除财务口径歧义。所有同名行和上下文保留，不按参考金额、分区或fact_id挑选。",
    "附近原行只是几何上下文，未自动认定流动、非流动、产品或其他财务含义。",
    "非流动Marketable securities路径没有原42记录中的参考值；本轮所有候选均不评分为正确答案。",
    "公开保存验证可重算可见候选的局部规则及引用，无法证明未公开整页不存在干扰；完整复演需保存的WordPage。",
    "无新OCR、模型调用、独立人工标注、数据库采用或批准；行级明确选择也不检验原生词或币种。",
]


class RowDisambiguationExperimentError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def require(condition, code, path):
    if not condition: raise RowDisambiguationExperimentError(code, path)


def _same(first, second):
    """Canonical JSON keeps bool/int/float substitutions distinct."""
    return digest(first) == digest(second)


def _read(path):
    try: return prior._read(path)
    except ValueError as exc: raise RowDisambiguationExperimentError("input_unavailable", Path(path).name) from exc


def _hashes(root):
    try:
        return {p: prior.old._sha((root/p).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()) for p in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc: raise RowDisambiguationExperimentError("implementation_unavailable", "implementation") from exc


def _query_specs(report):
    grouped = {}
    for document in report["documents"]:
        for case in document["cases"]:
            if case["methods"]["role"]["extraction"]["reason"] != "duplicate_row_label": continue
            identity = {"document_id": document["id"], "source_pdf_sha256": document["source"]["sha256"], "page": case["page"],
                        "row_label": normalize_row_label(case["query"]["row_label"]), "year": case["query"]["year"]}
            key = "query-"+digest(identity)[:20]
            if key not in grouped:
                grouped[key] = {"id": key, "document_id": document["id"], "page": case["page"], "query": deepcopy(case["query"]),
                    "source": deepcopy(document["source"]), "page_metadata": deepcopy(next(p for p in document["pages"] if p["page"] == case["page"])),
                    "baseline_case_ids": [], "baseline_line_result": deepcopy(case["methods"]["line"]["extraction"])}
            require(grouped[key]["baseline_line_result"] == case["methods"]["line"]["extraction"], "same_query_different_baseline", key)
            grouped[key]["baseline_case_ids"].append(case["id"])
    return list(grouped.values())


def _inputs(root):
    try:
        raw, config = _read(root/CONFIG); snapshot = prior.load_role_table_lookup_snapshot(root)
        require(set(config) == {"schema_version", "id", "rule_version", "role_report", "input_config", "selection_policy", "queries"}
                and type(config["schema_version"]) is int and config["schema_version"] == 1
                and config["id"] == "row-disambiguation-development-1" and config["rule_version"] == "row-disambiguation-1",
                "invalid_config", CONFIG)
        require(config["role_report"] == {"path": prior.REPORT, "sha256": snapshot["report_sha256"]}, "baseline_changed", "role_report")
        source_raw, source_config = _read(root/prior.CONFIG)
        require(config["input_config"] == {"path": prior.CONFIG, "sha256": prior.old._sha(source_raw)}, "source_config_changed", "input_config")
        require(config["selection_policy"] == "all_duplicate_row_label_queries_all_candidate_rows_no_reference_scoring", "selection_policy_changed", "selection_policy")
        specs = _query_specs(snapshot["report"])
        require(config["queries"] == [{k: s[k] for k in ("id", "document_id", "page", "query", "baseline_case_ids")} for s in specs],
                "query_inventory_changed", "queries")
        return config, prior.old._sha(raw), snapshot, source_config, specs
    except RowDisambiguationExperimentError: raise
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        raise RowDisambiguationExperimentError("registration_unavailable", "inputs") from exc


def extract_paths(page: OcrPage, query: dict) -> dict:
    from .row_disambiguation import enumerate_row_candidates, lookup_selected_row
    require(type(query) is dict and set(query) == {"row_label", "year"}, "invalid_query", "query")
    discovery = enumerate_row_candidates(page, **query)
    unselected = lookup_selected_row(page, **query)
    paths = [{"row_index": item["row_index"], "result": lookup_selected_row(page, **query, selection={
        "input_sha256": discovery["input_binding"]["input_sha256"], "row_index": item["row_index"]})} for item in discovery["candidates"]]
    return {"discovery": discovery, "unselected_result": unselected, "paths": paths}


def _validate_discovery(bundle, spec, seen):
    from . import row_disambiguation as core
    from .table_lookup import SCOPE as LINE_SCOPE
    require(type(bundle) is dict and set(bundle) == {"discovery", "unselected_result", "paths"}, "invalid_path_bundle", spec["id"])
    discovery = bundle["discovery"]; page = spec["page_metadata"]; query = spec["query"]
    q = {"page": spec["page"], **query, "normalized_row_label": normalize_row_label(query["row_label"])}
    binding = {"page_sha256": page["line_projection_sha256"], "input_sha256": digest({
        "rule_version": core.ROW_DISAMBIGUATION_VERSION, "page_sha256": page["line_projection_sha256"], "query": q})}
    require(type(discovery) is dict and set(discovery) == {"schema_version", "rule_version", "query", "input_binding", "status", "candidate_count",
            "candidates", "legacy_line_result", "scope", "automatic_approval"}
            and type(discovery["schema_version"]) is int and discovery["schema_version"] == 1
            and discovery["rule_version"] == core.ROW_DISAMBIGUATION_VERSION and _same(discovery["query"], q)
            and _same(discovery["input_binding"], binding) and discovery["scope"] == core.SCOPE and discovery["automatic_approval"] is False,
            "invalid_discovery", spec["id"])
    expected_rows = spec["baseline_line_result"]["evidence"]["row_label"]
    require(_same(discovery["legacy_line_result"], spec["baseline_line_result"]) and discovery["status"] == "selection_required"
            and type(discovery["candidate_count"]) is int and discovery["candidate_count"] == len(expected_rows) == len(discovery["candidates"])
            and [c["row_index"] for c in discovery["candidates"]] == [r["span_index"] for r in expected_rows], "candidate_inventory_mismatch", spec["id"])
    local = {}
    def entry_check(entry):
        require(type(entry) is dict and set(entry) == {"span_index", "span"} and type(entry["span_index"]) is int
                and 0 <= entry["span_index"] < page["line_count"], "invalid_span", spec["id"])
        value = OcrSpan.model_validate(entry["span"])
        require(value.bbox.x1 <= page["width"] and value.bbox.bottom <= page["height"], "span_out_of_page", spec["id"])
        index = entry["span_index"]
        require(index not in seen or _same(seen[index], entry["span"]), "inconsistent_shared_span", spec["id"])
        require(index not in local or _same(local[index], entry["span"]), "inconsistent_local_span", spec["id"])
        seen[index] = local[index] = entry["span"]
    for candidate, expected in zip(discovery["candidates"], expected_rows):
        require(type(candidate) is dict and set(candidate) == {"row_index", "row", "context", "extraction"}
                and _same(candidate["row"], expected) and type(candidate["row_index"]) is int,
                "invalid_candidate", spec["id"])
        entry_check(candidate["row"])
        context = candidate["context"]
        require(type(context) is dict and set(context) == {"above", "below", "basis"} and context["basis"] == core.CONTEXT_BASIS,
                "invalid_context", spec["id"])
        rowbox = candidate["row"]["span"]["bbox"]
        for side in ("above", "below"):
            entries = context[side]
            require(type(entries) is list and len(entries) <= core.CONTEXT_LIMIT and len({e["span_index"] for e in entries}) == len(entries),
                    "invalid_context_inventory", spec["id"])
            for entry in entries:
                entry_check(entry)
                require(entry["span_index"] != candidate["row_index"] and
                        (entry["span"]["bbox"]["bottom"] <= rowbox["top"] if side == "above" else entry["span"]["bbox"]["top"] >= rowbox["bottom"]),
                        "context_side_mismatch", spec["id"])
            key = lambda e: ((rowbox["top"]-e["span"]["bbox"]["bottom"] if side == "above" else e["span"]["bbox"]["top"]-rowbox["bottom"]),
                              e["span"]["bbox"]["x0"], e["span_index"])
            require(entries == sorted(entries, key=key), "context_order_mismatch", spec["id"])
        extraction = candidate["extraction"]
        require(extraction["method"] == "explicit_row_layout" and _same(extraction["evidence"]["row_label"], [expected]),
                "selected_row_binding_mismatch", spec["id"])
        wrapped = {"schema_version": 1, "rule_version": "table-lookup-1", "query": q,
            "methods": {method: {**deepcopy(extraction), "method": method} for method in ("text_sequence", "layout_column")},
            "scope": LINE_SCOPE, "automatic_approval": False}
        prior.old._validate_line(wrapped, query, {**page, "spans_count": page["line_count"]}, seen)
        for entries in extraction["evidence"].values():
            for entry in entries: entry_check(entry)
    # Recompute only the trace visible in this report, with original indices mapped back.
    indices = sorted(local); reverse = {index: position for position, index in enumerate(indices)}
    partial = OcrPage.model_validate({"page": page["page"], "width": page["width"], "height": page["height"],
        "coordinate_system": "pdf_points_top_left", "spans": [local[index] for index in indices]})
    def remap(value):
        if isinstance(value, list): return [remap(v) for v in value]
        if isinstance(value, dict):
            return {key: (indices[item] if key in {"span_index", "selected_span_index", "header_span_index"} and item is not None else remap(item))
                    for key,item in value.items()}
        return value
    for candidate in discovery["candidates"]:
        index = reverse[candidate["row_index"]]
        require(_same(remap(core._lookup_at_index(partial, query["row_label"], query["year"], index)), candidate["extraction"]),
                "visible_trace_replay_mismatch", spec["id"])
        require(_same(remap(core._context(partial, index)), candidate["context"]), "visible_context_replay_mismatch", spec["id"])
    require(_same(bundle["unselected_result"], {**discovery, "selection": None, "extraction": None}), "implicit_selection_forbidden", spec["id"])
    require(type(bundle["paths"]) is list and [p["row_index"] for p in bundle["paths"]] == [c["row_index"] for c in discovery["candidates"]],
            "path_inventory_mismatch", spec["id"])
    for path, candidate in zip(bundle["paths"], discovery["candidates"]):
        require(type(path) is dict and set(path) == {"row_index", "result"} and type(path["row_index"]) is int
                and _same(path["result"], {**discovery, "status": candidate["extraction"]["status"],
                    "selection": {"input_sha256": binding["input_sha256"], "row_index": candidate["row_index"], "explicit": True},
                    "extraction": candidate["extraction"]}), "selection_path_mismatch", spec["id"])


def _controls():
    """Fixed synthetic expectations, independent of outputs from the candidate rule."""
    def span(text, x0, top, x1, bottom, confidence=.99):
        return {"text": text, "confidence": confidence, "bbox": {"x0": x0, "top": top, "x1": x1, "bottom": bottom}}
    base = {"page": 1, "width": 500, "height": 700, "coordinate_system": "pdf_points_top_left", "spans": [
        span("(In millions)", 20, 10, 150, 20), span("Year ended June 30,", 20, 30, 150, 40),
        span("2025", 285, 30, 315, 40), span("2024", 385, 30, 415, 40),
        span("Current assets:", 20, 50, 180, 60),
        span("Marketable securities", 20, 80, 180, 90), span("100", 280, 80, 320, 90), span("200", 390, 80, 415, 90),
        span("Other assets:", 20, 110, 180, 120),
        span("Marketable securities", 20, 140, 180, 150), span("300", 290, 140, 320, 150), span("400", 390, 140, 420, 150)]}
    query = {"row_label": "Marketable securities", "year": 2025}
    cases = []
    def add(key, title, expected, *, page=None, selected=None, query_override=None, binding="current", mutation=None):
        cases.append({"id": key, "title": title, "provenance": "synthetic_development_control",
            "input_page": deepcopy(base if page is None else page), "query": deepcopy(query if query_override is None else query_override),
            "selection_request": selected, "binding_mode": binding, "page_mutation_after_binding": mutation,
            "expected": expected, "independent_human_confirmation": "pending"})
    add("RD-01", "两个同名行不默认选第一行", {"status": "selection_required", "candidate_count": 2, "normalized_value": None})
    add("RD-02", "明确选第一原行", {"status": "selected", "candidate_count": 2, "normalized_value": "100"}, selected={"row_index": 5})
    add("RD-03", "明确选第二原行", {"status": "selected", "candidate_count": 2, "normalized_value": "300"}, selected={"row_index": 9})
    add("RD-04", "改年份不能沿用旧选择摘要", {"error_code": "stale_selection"}, selected={"row_index": 5}, query_override={**query, "year": 2024}, binding="original_2025_query")
    add("RD-05", "页内容改变不能沿用旧选择摘要", {"error_code": "stale_selection"}, selected={"row_index": 5}, mutation={"span_index": 6, "text": "101"})
    add("RD-06", "布尔值不能当原行索引", {"error_code": "invalid_row_index"}, selected={"row_index": True})
    add("RD-07", "年份行不是候选行", {"error_code": "selection_not_candidate"}, selected={"row_index": 2})
    add("RD-08", "越界行索引拒绝", {"error_code": "row_index_out_of_range"}, selected={"row_index": 99})
    add("RD-09", "客户端不能附带参考答案", {"error_code": "invalid_selection"}, selected={"row_index": 5, "expected_amount": "100"})
    missing = deepcopy(base); missing["spans"].pop(6)
    add("RD-10", "选定行缺金额仍不足", {"status": "insufficient", "candidate_count": 2, "normalized_value": None}, selected={"row_index": 5}, page=missing)
    low = deepcopy(base); low["spans"][5]["confidence"] = .5
    add("RD-11", "选定行低置信度仍不足", {"status": "insufficient", "candidate_count": 2, "normalized_value": None}, selected={"row_index": 5}, page=low)
    negative = deepcopy(base); negative["spans"][6]["text"] = "(100)"
    add("RD-12", "完整负数符号保留", {"status": "selected", "candidate_count": 2, "normalized_value": "-100"}, selected={"row_index": 5}, page=negative)
    single = deepcopy(base); single["spans"][9]["text"] = "Different item"
    add("RD-13", "唯一同名行也必须明确选择", {"status": "selection_required", "candidate_count": 1, "normalized_value": None}, page=single)
    add("RD-14", "未找到行不凭空创建候选", {"status": "not_found", "candidate_count": 0, "normalized_value": None}, query_override={**query, "row_label": "Unregistered label"})
    extra = deepcopy(base); extra["spans"].append(span("$", 325, 80, 330, 90))
    add("RD-15", "选行后其他金额守卫仍保留", {"status": "insufficient", "candidate_count": 2, "normalized_value": None}, selected={"row_index": 5}, page=extra)
    return cases


def _run_controls():
    from .row_disambiguation import enumerate_row_candidates, lookup_selected_row, RowDisambiguationError
    results = []
    for spec in _controls():
        raw = deepcopy(spec["input_page"]); query = spec["query"]
        binding_query = {**query, "year": 2025} if spec["binding_mode"] == "original_2025_query" else query
        original = enumerate_row_candidates(OcrPage.model_validate(raw), **binding_query)
        request = deepcopy(spec["selection_request"])
        if request is not None: request["input_sha256"] = original["input_binding"]["input_sha256"]
        change = spec["page_mutation_after_binding"]
        if change: raw["spans"][change["span_index"]]["text"] = change["text"]
        try:
            result = lookup_selected_row(OcrPage.model_validate(raw), **query, selection=request)
            observed = {"status": result["status"], "candidate_count": result["candidate_count"],
                        "normalized_value": result["extraction"]["normalized_value"] if result["extraction"] else None}
        except RowDisambiguationError as exc:
            result = {"error_code": exc.code, "path": exc.path}; observed = {"error_code": exc.code}
        results.append({**spec, "input_sha256": digest({"page": raw, "query": query, "selection": request}),
                        "result": result, "observed": observed, "passed": observed == spec["expected"]})
    return {"cases": results, "summary": {"cases": len(results), "passed": sum(r["passed"] for r in results),
                                         "failed": sum(not r["passed"] for r in results)}}


def _assemble(root, registration, outputs):
    config, config_sha, previous, source_config, specs = registration
    seen = {}; queries = []
    require(set(outputs) == {s["id"] for s in specs}, "query_inventory_mismatch", "queries")
    for spec in specs:
        bundle = outputs[spec["id"]]
        _validate_discovery(bundle, spec, seen.setdefault((spec["document_id"], spec["page"]), {}))
        queries.append({**{k: deepcopy(v) for k,v in spec.items() if k != "baseline_line_result"}, **deepcopy(bundle)})
    statuses = Counter(path["result"]["status"] for query in queries for path in query["paths"])
    controls = _run_controls()
    require(controls["summary"]["failed"] == 0, "control_failure", "controls")
    return {"schema_version": 1, "experiment_version": VERSION,
        "config_file_sha256": config_sha, "config_sha256": digest(config), "role_report_sha256": previous["report_sha256"],
        "implementation_hash_basis": "python_utf8_lf", "implementation_sha256": _hashes(root),
        "input_inventory": [{"document_id": d["id"], "source_pdf_sha256": d["source_pdf_sha256"], "pages": deepcopy(d["pages"])}
                            for d in source_config["documents"]],
        "queries": queries,
        "summary": {"prior_registered_records": previous["report"]["query_inventory"]["registered_cases"],
            "prior_unique_queries": previous["report"]["query_inventory"]["unique_query_inputs"],
            "covered_baseline_records": sum(len(q["baseline_case_ids"]) for q in queries), "unique_queries": len(queries),
            "candidate_paths": sum(len(q["paths"]) for q in queries), "selected_paths": statuses["selected"],
            "insufficient_paths": statuses["insufficient"], "unselected_queries": len(queries),
            "financial_reference_scoring": False},
        "controls": controls,
        "scope": {"known_query_page": True, "known_query_labels": True, "all_duplicate_queries_included": True,
            "all_candidate_rows_included": True, "automatic_row_selection": False, "reference_given_to_extractor": False,
            "gold_scoring_performed": False, "independent_human_gold": 0, "model_calls": 0, "new_ocr_calls": 0,
            "raw_pdf_bytes_checked": False, "source_authenticity_verified": False, "held_out": False},
        "limitations": list(LIMITATIONS), "read_only": True, "automatic_approval": False}


def build_row_disambiguation_report(root: Path, ocr_root: Path | None = None) -> dict:
    root = Path(root).resolve(); registration = _inputs(root); hashes = _hashes(root)
    directory = Path(ocr_root).resolve() if ocr_root is not None else root/".run/reproductions/document-ai-words"
    pages = {(d["id"], p["page"]): prior.old._page(directory, p).line_page()
             for d in registration[3]["documents"] for p in d["pages"]}
    outputs = {spec["id"]: extract_paths(pages[spec["document_id"], spec["page"]], spec["query"]) for spec in registration[4]}
    report = _assemble(root, registration, deepcopy(outputs))
    require(_inputs(root) == registration and _hashes(root) == hashes, "inputs_changed_during_run", "inputs")
    return report


def validate_row_disambiguation_report(root: Path, report: dict, *, ocr_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    try:
        if ocr_root is not None:
            expected = build_row_disambiguation_report(root, ocr_root)
        else:
            registration = _inputs(root); hashes = _hashes(root)
            outputs = {q["id"]: {key: q[key] for key in ("discovery", "unselected_result", "paths")} for q in report["queries"]}
            require(len(outputs) == len(report["queries"]) == len(registration[4]), "query_inventory_mismatch", "queries")
            expected = _assemble(root, registration, outputs)
            require(_inputs(root) == registration and _hashes(root) == hashes, "inputs_changed_during_read", "inputs")
        require(digest(report) == digest(expected), "report_mismatch", "report")
        return deepcopy(report)
    except RowDisambiguationExperimentError: raise
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError, IndexError) as exc:
        raise RowDisambiguationExperimentError("invalid_report", "report") from exc


def load_row_disambiguation_snapshot(root: Path) -> dict:
    root = Path(root).resolve(); _, release = _read(root/RELEASE)
    require(type(release) is dict and set(release) == {"schema_version", "report_path", "report_sha256"}
            and type(release["schema_version"]) is int and release["schema_version"] == 1 and release["report_path"] == REPORT,
            "invalid_release", RELEASE)
    raw, report = _read(root/REPORT)
    require(prior.old._sha(raw) == release["report_sha256"], "published_report_changed", REPORT)
    normalized = validate_row_disambiguation_report(root, report)
    require(_read(root/RELEASE)[1] == release, "release_changed_during_read", RELEASE)
    return {"schema_version": 1, "report": normalized, "report_sha256": prior.old._sha(raw),
            "role_report_sha256": normalized["role_report_sha256"], "read_only": True, "full_ocr_replayed": False,
            "validation_scope": "发布摘要、全部旧同名行、所有显选路径、可见局部轨迹和合成控制重算；未读取或重新提取六份完整OCR。"}
