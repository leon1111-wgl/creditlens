"""Fixed-label table lookup comparison on six previously observed OCR pages.

Extraction receives only a complete OcrPage, row label and year. Financial
reference values/boxes are introduced only by the separate scoring function.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import re

from .diligence import CasePack, PdfBox, digest, displayed_amount
from .visual import OcrPage, OcrSpan, coverage
from .visual_store import VisualStore


EXPERIMENT_VERSION = "table-lookup-development-1"
CONFIG_PATH = "configs/table-lookup.json"
REPORT_PATH = "artifacts/table-lookup-report.json"
RELEASE_PATH = "configs/table-lookup-artifact.json"
METHODS = {"text_sequence": "text_sequence", "layout": "layout_column"}
IMPLEMENTATION_FILES = (
    "creditlens/table_lookup.py", "creditlens/table_lookup_experiment.py",
    "creditlens/visual.py", "creditlens/visual_store.py", "creditlens/diligence.py",
    "scripts/evaluate_table_lookup.py", "scripts/evaluate_currency_diagnostic.py",
    "creditlens/experiment_paths.py",
)
SCORING = {"expected_amount_basis": "signed_raw_display", "reference_coverage_min": 0.5,
           "center_tolerance_points": 8.0}
LIMITATIONS = [
    "42 项已观察开发事实、6 页保留 OCR；未新增公司、OCR 推理或独立人工金标准，不是盲测。",
    "提取器得到整页 OCR、参考行名及年份，不得到参考金额、框、分区或财务事实对象；这是给定行名查表，不是开放式问答。",
    "金额匹配与原格定位分别评分；金额偶合而位置不对不能记为匹配。无法唯一选取的情况保留不足。",
    "未证明原文真实性、OCR 执行身份、币种或完整财务期间；million 只表示数量级，不能据此推断美元。",
    "公开报告只保留必要候选证据；离线报告检查不重新扫描完整 OCR，完整复放需要显式提供原本地 OCR 文件。",
]


class TableLookupExperimentError(ValueError):
    def __init__(self, code: str, path: str):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def _require(condition, code, path):
    if not condition:
        raise TableLookupExperimentError(code, path)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _read(path: Path, limit=8_000_000):
    try:
        _require(path.stat().st_size <= limit, "file_too_large", path.name)
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        _require(type(value) is dict, "invalid_json", path.name)
        return raw, value
    except TableLookupExperimentError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise TableLookupExperimentError("input_unavailable", path.name) from exc


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _code_hashes(root):
    try:
        return {p: _sha((root/p).read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))
                for p in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc:
        raise TableLookupExperimentError("implementation_unavailable", "implementation") from exc


def _inputs(root):
    # Legacy pack validation parses parenthesized values with Decimal unary
    # minus. Do not inherit a caller's small arithmetic precision here.
    with localcontext() as context:
        context.prec = 100
        return _inputs_impl(root)


def _inputs_impl(root):
    raw, config = _read(root/CONFIG_PATH)
    _require(set(config) == {"schema_version", "id", "rule_version", "query_fields", "source_report", "scoring", "documents", "scope"}
             and type(config.get("schema_version")) is int and config["schema_version"] == 1
             and config["id"] == "registered-table-lookup-development-1"
             and config.get("rule_version") == "table-lookup-1" and config.get("query_fields") == ["row_label", "year"]
             and config.get("scoring") == SCORING
             and config["scope"] == {"previously_observed_development_facts": True, "independent_human_gold": 0, "new_ocr_calls": 0, "held_out": False}, "config_mismatch", CONFIG_PATH)
    _require(type(config["source_report"]) is dict and set(config["source_report"]) == {"path", "sha256", "canonical_sha256"}, "invalid_source_report", CONFIG_PATH)
    _require(config["source_report"]["path"] == "artifacts/document-ai-report.json", "source_report_mismatch", CONFIG_PATH)
    old_raw, old = _read(root/config["source_report"]["path"])
    _require(_sha(old_raw) == config["source_report"]["sha256"] and digest(old) == config["source_report"]["canonical_sha256"],
             "source_report_changed", "source_report")
    try:
        VisualStore(root)._validate(old)
        docs = config["documents"]
        _require(type(docs) is list and len(docs) == 2 and {d["id"] for d in docs} == {"msft", "aapl"},
                 "document_inventory_mismatch", "documents")
        packs = {}
        for document in docs:
            doc_id = document["id"]
            expected_path = f"data/examples/{doc_id}-fy2025-pdf.json"
            _require(document["pack_path"] == expected_path, "pack_path_mismatch", doc_id)
            _, data = _read(root/expected_path)
            pack = CasePack.model_validate(data)
            baseline = next(d for d in old["documents"] if d["id"] == doc_id)
            _require(digest(data) == document["pack_sha256"] == baseline["pack_sha256"]
                     and pack.sources[0].sha256 == document["source_pdf_sha256"]
                     and document["fact_ids"] == [f.id for f in pack.facts], "reference_inventory_mismatch", doc_id)
            expected_pages = {p["page"]: p for p in baseline["pages"]}
            _require(len(document["pages"]) == len(expected_pages) and {p["page"] for p in document["pages"]} == set(expected_pages),
                     "page_inventory_mismatch", doc_id)
            for page in document["pages"]:
                prior = expected_pages[page["page"]]
                _require(all(page[k] == prior[k] for k in ("page", "width", "height", "ocr_sha256", "image_sha256"))
                         and page["spans_count"] == prior["ocr_lines"]
                         and page["ocr_relative_path"] == f"{doc_id}/ocr-{page['page']}.json"
                         and re.fullmatch(r"[0-9a-f]{64}", page["ocr_file_sha256"]), "ocr_registration_mismatch", doc_id)
            packs[doc_id] = pack
        _require(sum(len(p.facts) for p in packs.values()) == 42, "fact_count_mismatch", "documents")
    except TableLookupExperimentError:
        raise
    except (ValueError, KeyError, TypeError, StopIteration, AttributeError) as exc:
        raise TableLookupExperimentError("invalid_registration", "documents") from exc
    return config, _sha(raw), packs


def _load_ocr(ocr_root, registration):
    path = ocr_root/registration["ocr_relative_path"]
    _require(path.resolve().is_relative_to(ocr_root.resolve()), "ocr_path_escape", path.name)
    raw, data = _read(path, 3_000_000)
    _require(_sha(raw) == registration["ocr_file_sha256"] and digest(data) == registration["ocr_sha256"],
             "ocr_hash_mismatch", path.name)
    try:
        page = OcrPage.model_validate(data)
    except ValueError as exc:
        raise TableLookupExperimentError("invalid_ocr", path.name) from exc
    _require((page.page, page.width, page.height, len(page.spans)) == (
        registration["page"], registration["width"], registration["height"], registration["spans_count"]),
        "ocr_geometry_mismatch", path.name)
    return page


def extract(page: OcrPage, query: dict) -> dict:
    """Hard API boundary: no Fact, amount, unit, section or reference box."""
    from .table_lookup import lookup_table
    _require(type(query) is dict and set(query) == {"row_label", "year"}, "invalid_query", "query")
    return lookup_table(page, row_label=query["row_label"], year=query["year"])


def reference_for_fact(fact):
    loc = fact.pdf_location
    with localcontext() as context:
        context.prec = 100
        signed = format(displayed_amount(fact.raw_value), "f")
    return {"raw_value": fact.raw_value, "signed_amount": signed,
            "business_normalized_value": format(fact.value, "f"), "transformation": fact.transformation,
            "unit": fact.unit, "period_start": fact.period_start.isoformat() if fact.period_start else None,
            "period_end": fact.period_end.isoformat(), "amount_bbox": loc.amount_bbox.model_dump(mode="json"),
            "locator": fact.locator, "independent_human_confirmation": "pending"}


def score_extraction(method: dict, reference: dict) -> dict:
    if method["status"] == "insufficient":
        return {"outcome": "insufficient", "amount_match": None, "reference_location_match": None,
                "reference_coverage": None, "unit_proven": False, "complete_period_proven": False}
    selected = next(e["span"] for e in method["evidence"]["amounts"] if e["span_index"] == method["selected_span_index"])
    amount, wanted = Decimal(method["normalized_value"]), Decimal(reference["signed_amount"])
    amount_match = amount == wanted and amount.is_signed() == wanted.is_signed()
    box, target = PdfBox.model_validate(selected["bbox"]), PdfBox.model_validate(reference["amount_bbox"])
    overlap = coverage(target, box)
    cx, cy = (box.x0+box.x1)/2, (box.top+box.bottom)/2
    tolerance = SCORING["center_tolerance_points"]
    located = overlap >= SCORING["reference_coverage_min"] and target.x0-tolerance <= cx <= target.x1+tolerance and target.top-tolerance <= cy <= target.bottom+tolerance
    return {"outcome": "matched" if amount_match and located else "wrong", "amount_match": amount_match,
            "reference_location_match": located, "reference_coverage": round(overlap, 6),
            "unit_proven": False, "complete_period_proven": False}


def _validate_extraction(result, query, page, seen):
    from .table_lookup import MESSAGES, SCOPE, normalize_row_label, parse_lookup_amount
    _require(type(result) is dict and set(result) == {"schema_version", "rule_version", "query", "methods", "scope", "automatic_approval"}
             and type(result["schema_version"]) is int and result["schema_version"] == 1
             and result["rule_version"] == "table-lookup-1" and result["automatic_approval"] is False,
             "invalid_extraction", "extraction")
    q = result["query"]
    _require(set(q) == {"page", "row_label", "normalized_row_label", "year"} and q["page"] == page["page"]
             and q["row_label"] == query["row_label"] and q["year"] == query["year"]
             and q["normalized_row_label"] == normalize_row_label(query["row_label"]) and result["scope"] == SCOPE,
             "query_binding_mismatch", "extraction.query")
    _require(set(result["methods"]) == set(METHODS.values()), "method_inventory_mismatch", "methods")
    for method_id, method in result["methods"].items():
        _require(type(method) is dict and set(method) == {"method", "status", "reason", "message", "raw_value", "normalized_value", "selected_span_index", "unit", "evidence", "column", "automatic_approval"}
                 and method["method"] == method_id and method["status"] in ("selected", "insufficient")
                 and type(method["reason"]) is str and len(method["reason"]) <= 200
                 and type(method["message"]) is str and len(method["message"]) <= 1000 and method["automatic_approval"] is False,
                 "invalid_method", method_id)
        _require(method["reason"] in MESSAGES and method["message"] == MESSAGES[method["reason"]]
                 and ((method["status"] == "selected") == (method["reason"] == "selected")), "invalid_reason", method_id)
        unit = method["unit"]
        _require(type(unit) is dict and set(unit) == {"raw_text", "scale", "currency", "qualifier_present"}
                 and unit["currency"] is None and type(unit["qualifier_present"]) is bool
                 and all(unit[k] is None or type(unit[k]) is str for k in ("raw_text", "scale")), "invalid_unit_claim", method_id)
        evidence = method["evidence"]
        _require(type(evidence) is dict and set(evidence) == {"row_label", "year_headers", "unit", "amounts", "guards"}, "invalid_evidence", method_id)
        for entries in evidence.values():
            _require(type(entries) is list and len(entries) <= page["spans_count"], "invalid_evidence", method_id)
            for entry in entries:
                _require(type(entry) is dict and set(entry) == {"span_index", "span"} and type(entry["span_index"]) is int
                         and 0 <= entry["span_index"] < page["spans_count"], "invalid_span_index", method_id)
                span = OcrSpan.model_validate(entry["span"])
                _require(span.bbox.x1 <= page["width"] and span.bbox.bottom <= page["height"], "evidence_out_of_page", method_id)
                index = entry["span_index"]
                _require(index not in seen or seen[index] == entry["span"], "inconsistent_shared_span", method_id)
                seen[index] = entry["span"]
        if method["status"] == "selected":
            index = method["selected_span_index"]
            _require(type(index) is int and type(method["raw_value"]) is str and type(method["normalized_value"]) is str
                     and re.fullmatch(r"-?\d+(?:\.\d+)?", method["normalized_value"]) is not None,
                     "invalid_selected_value", method_id)
            _require(any(e["span_index"] == index and e["span"]["text"] == method["raw_value"] for e in evidence["amounts"]),
                     "selected_evidence_missing", method_id)
            _require(parse_lookup_amount(method["raw_value"]) == method["normalized_value"], "selected_amount_mismatch", method_id)
        else:
            _require(all(method[k] is None for k in ("raw_value", "normalized_value", "selected_span_index")), "insufficient_has_value", method_id)
        column = method["column"]
        if column is not None:
            _require(type(column) is dict and set(column) == {"year", "left", "right", "header_span_index"}
                     and column["year"] == query["year"] and all(type(column[k]) in (int, float) for k in ("left", "right"))
                     and 0 <= column["left"] < column["right"] <= page["width"]
                     and type(column["header_span_index"]) is int
                     and any(e["span_index"] == column["header_span_index"] for e in evidence["year_headers"]), "invalid_column", method_id)


def _summary(cases):
    result = {}
    for key in METHODS:
        scores = [case["methods"][key]["score"] for case in cases]
        counts = Counter(s["outcome"] for s in scores)
        result[key] = {"cases": len(scores), "selected": counts["matched"]+counts["wrong"],
                       "matched": counts["matched"], "wrong": counts["wrong"], "insufficient": counts["insufficient"],
                       "amount_match": sum(s["amount_match"] is True for s in scores),
                       "reference_location_match": sum(s["reference_location_match"] is True for s in scores),
                       "amount_match_wrong_location": sum(s["amount_match"] is True and s["reference_location_match"] is False for s in scores)}
    return result


def _synthetic_cases():
    def span(text, x, y, width=40, confidence=.99):
        return {"text": text, "confidence": confidence, "bbox": {"x0": x, "top": y, "x1": x+width, "bottom": y+10}}
    base = {"page": 1, "width": 500., "height": 300., "coordinate_system": "pdf_points_top_left", "spans": [
        span("(In millions)", 20, 10, 90), span("2025", 250, 30), span("2024", 350, 30),
        span("Cash from operations", 20, 60, 150), span("100", 250, 60), span("200", 350, 60)]}
    expected = {"normal": {"text_sequence": "matched", "layout": "matched"},
                "sequence_swapped": {"text_sequence": "wrong", "layout": "matched"},
                "missing_amount": {"text_sequence": "insufficient", "layout": "insufficient"},
                "duplicate_label": {"text_sequence": "insufficient", "layout": "insufficient"},
                "merged_amounts": {"text_sequence": "insufficient", "layout": "insufficient"},
                "low_confidence": {"text_sequence": "insufficient", "layout": "insufficient"}}
    cases = []
    for operation, statuses in expected.items():
        data = deepcopy(base)
        if operation == "sequence_swapped": data["spans"][4], data["spans"][5] = data["spans"][5], data["spans"][4]
        elif operation == "missing_amount": data["spans"].pop()
        elif operation == "duplicate_label": data["spans"].append(span("Cash from operations", 20, 90, 150))
        elif operation == "merged_amounts": data["spans"][5]["text"] = "100 200"
        elif operation == "low_confidence": data["spans"][5]["confidence"] = .3
        cases.append({"id": operation, "provenance": "synthetic_developer_control", "query": {"row_label": "Cash from operations", "year": 2024},
                      "page": data, "reference": {"raw_value": "200", "signed_amount": "200", "amount_bbox": base["spans"][5]["bbox"]},
                      "expected_outcomes": statuses})
    return cases


def _assemble(root, config, config_sha, packs, outputs, control_outputs):
    documents = []
    for registered in config["documents"]:
        doc_id, pack = registered["id"], packs[registered["id"]]
        pages = {p["page"]: p for p in registered["pages"]}
        seen = {p: {} for p in pages}
        cases = []
        for fact in pack.facts:
            case_id, loc = f"{doc_id}:{fact.id}", fact.pdf_location
            query = {"row_label": loc.row_label, "year": loc.year}
            output = outputs[case_id]
            _validate_extraction(output, query, pages[loc.page], seen[loc.page])
            reference = reference_for_fact(fact)
            cases.append({"id": case_id, "fact_id": fact.id, "page": loc.page, "query": query,
                          "extraction_sha256": digest(output), "extraction_metadata": {k: deepcopy(v) for k, v in output.items() if k != "methods"},
                          "reference": reference, "methods": {key: {"extraction": deepcopy(output["methods"][core_key]),
                            "score": score_extraction(output["methods"][core_key], reference)} for key, core_key in METHODS.items()}})
        documents.append({"id": doc_id, "company": pack.company, "source": pack.sources[0].model_dump(mode="json"),
                          "pack_sha256": registered["pack_sha256"], "pages": deepcopy(registered["pages"]), "cases": cases, "summary": _summary(cases)})
    controls = []
    for case in _synthetic_cases():
        output = control_outputs[case["id"]]
        page = case["page"]
        _validate_extraction(output, case["query"], {**page, "spans_count": len(page["spans"])}, {})
        methods = {key: {"extraction": deepcopy(output["methods"][core_key]), "score": score_extraction(output["methods"][core_key], case["reference"])} for key, core_key in METHODS.items()}
        controls.append({**case, "input_sha256": digest({"page": page, "query": case["query"]}), "extraction_sha256": digest(output),
                         "extraction_metadata": {k: deepcopy(v) for k, v in output.items() if k != "methods"}, "methods": methods,
                         "passed": all(methods[k]["score"]["outcome"] == case["expected_outcomes"][k] for k in METHODS)})
    return {"schema_version": 1, "experiment_version": EXPERIMENT_VERSION, "config_sha256": digest(config), "config_file_sha256": config_sha,
            "source_report": deepcopy(config["source_report"]), "implementation_sha256": _code_hashes(root), "implementation_hash_basis": "python_utf8_lf",
            "scope": {"previously_observed_development_cases": 42, "known_query_page": True, "known_query_labels": True, "gold_amount_or_bbox_given_to_extractor": False,
                      "independent_human_gold": 0, "new_ocr_calls": 0, "model_calls": 0, "held_out": False,
                      "raw_pdf_bytes_checked": False, "open_ended_qa_evaluated": False},
            "documents": documents, "summary": _summary([c for d in documents for c in d["cases"]]),
            "controls": {"cases": controls, "summary": {"cases": len(controls), "passed": sum(c["passed"] for c in controls), "failed": sum(not c["passed"] for c in controls)}},
            "limitations": list(LIMITATIONS), "read_only": True, "automatic_approval": False}


def build_table_lookup_report(root: Path, ocr_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    ocr_root = Path(ocr_root).resolve() if ocr_root else root/".run/document-ai"
    config, config_sha, packs = _inputs(root)
    initial_hashes = _code_hashes(root)
    pages = {(d["id"], p["page"]): _load_ocr(ocr_root, p) for d in config["documents"] for p in d["pages"]}
    outputs = {}
    # Phase one completes all extraction without inspecting expected amounts or
    # reference boxes. The snapshot below is fixed before scoring begins.
    for doc_id, pack in packs.items():
        for fact in pack.facts:
            loc = fact.pdf_location
            outputs[f"{doc_id}:{fact.id}"] = extract(pages[doc_id, loc.page], {"row_label": loc.row_label, "year": loc.year})
    frozen_outputs = deepcopy(outputs)
    controls = {c["id"]: extract(OcrPage.model_validate(c["page"]), c["query"]) for c in _synthetic_cases()}
    report = _assemble(root, config, config_sha, packs, frozen_outputs, controls)
    final_config, final_sha, _ = _inputs(root)
    _require(config == final_config and config_sha == final_sha and initial_hashes == _code_hashes(root), "inputs_changed_during_run", "inputs")
    return report


def validate_table_lookup_report(root: Path, report: dict, *, ocr_root: Path | None = None) -> dict:
    root = Path(root).resolve()
    try:
        if ocr_root is not None:
            expected = build_table_lookup_report(root, ocr_root)
        else:
            config, config_sha, packs = _inputs(root)
            outputs = {c["id"]: {**c["extraction_metadata"], "methods": {core_key: c["methods"][key]["extraction"] for key, core_key in METHODS.items()}}
                       for d in report["documents"] for c in d["cases"]}
            controls = {c["id"]: {**c["extraction_metadata"], "methods": {core_key: c["methods"][key]["extraction"] for key, core_key in METHODS.items()}}
                        for c in report["controls"]["cases"]}
            _require(len(outputs) == sum(len(d["cases"]) for d in report["documents"]) == 42
                     and set(outputs) == {f"{doc_id}:{f.id}" for doc_id, pack in packs.items() for f in pack.facts}
                     and len(controls) == len(report["controls"]["cases"]) == len(_synthetic_cases()), "case_inventory_mismatch", "cases")
            expected = _assemble(root, config, config_sha, packs, outputs, controls)
        _require(digest(report) == digest(expected), "report_mismatch", "report")
    except TableLookupExperimentError:
        raise
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError) as exc:
        raise TableLookupExperimentError("invalid_report", "report") from exc
    return deepcopy(report)


def load_table_lookup_snapshot(root: Path) -> dict:
    root = Path(root).resolve()
    _, release = _read(root/RELEASE_PATH)
    _require(set(release) == {"schema_version", "report_path", "report_sha256"}
             and type(release["schema_version"]) is int and release["schema_version"] == 1
             and release["report_path"] == REPORT_PATH, "invalid_release", RELEASE_PATH)
    raw, report = _read(root/REPORT_PATH)
    _require(_sha(raw) == release["report_sha256"], "published_report_changed", REPORT_PATH)
    normalized = validate_table_lookup_report(root, report)
    _require(_read(root/RELEASE_PATH)[1] == release, "release_changed_during_read", RELEASE_PATH)
    return {"schema_version": 1, "read_only": True, "report": normalized, "report_sha256": _sha(raw),
            "full_ocr_replayed": False, "validation_scope": "已发布字节摘要、配置/来源/完整案例集合与保存候选的逐项评分；未重新执行抽取或读取完整 OCR。"}
