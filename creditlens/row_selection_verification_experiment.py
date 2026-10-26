"""Replay all saved row exports and separately registered synthetic mutations."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .diligence import digest
from . import row_disambiguation_experiment as source

VERSION = "row-selection-verification-experiment-1"
REPORT = "artifacts/row-selection-verification-report.json"
IMPLEMENTATION_FILES = (*source.IMPLEMENTATION_FILES, "creditlens/row_selection_verification.py",
    "creditlens/row_selection_verification_experiment.py", "scripts/verify_row_selection.py")
LIMITATIONS = [
    "8项是此前已观察材料中保存的选行响应，不是8个独立金融问题、人工金标准或模型质量测量。",
    "检查上传封套及所有嵌套证据是否与当前冻结可信报告逐字段相同；自报哈希不能证明来源或身份。",
    "允许JSON空白和对象键顺序变化；布尔、整数、浮点等类型不得互换。原始字节摘要和规范内容摘要分开。",
    "合成篡改与格式错误单独计数，包含预期拒绝；没有新的模型、OCR、网络请求或业务数据库写入。",
    "可信报告检查沿用公开保存证据的局部验证范围；没有重跑完整OCR、核验原PDF字节或认证操作者。",
    "本实验由冻结报告构造与旧GET响应同结构的输入；真实HTTP端点响应由主开发另行核对。",
]


class RowSelectionExperimentError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def require(condition, code, path):
    if not condition: raise RowSelectionExperimentError(code, path)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8")


def _hashes(root):
    try:
        return {name: sha256((root/name).read_text(encoding="utf8").replace("\r\n", "\n").replace("\r", "\n").encode("utf8")).hexdigest()
                for name in IMPLEMENTATION_FILES}
    except (OSError, UnicodeError) as exc:
        raise RowSelectionExperimentError("implementation_unavailable", "implementation") from exc


def registered_exports(snapshot):
    """Produce the unchanged GET shape; no new fields are added to old exports."""
    from .row_selection_verification import expected_selection_payload
    return [{"query_id": query["id"], "row_index": path["row_index"],
             "payload": expected_selection_payload(snapshot, query["id"], path["row_index"])}
            for query in snapshot["report"]["queries"] for path in query["paths"]]


def _replace(value, target, replacement):
    if isinstance(value, list): return [_replace(item, target, replacement) for item in value]
    if isinstance(value, dict): return {key: _replace(item, target, replacement) for key,item in value.items()}
    return replacement if type(value) is type(target) and value == target else value


def control_inputs(payload):
    """Fixed expectations; payloads stay in memory, never in the public report."""
    from .row_selection_verification import MAX_INPUT_BYTES
    controls = []
    def add(key, title, modification, expected, transform):
        value = deepcopy(payload); transform(value)
        controls.append({"id": key, "title": title, "modification": modification, "expected": expected, "raw": _bytes(value)})
    mismatch = {"status": "mismatch"}
    add("RV-01", "修改选中金额", "只修改result.extraction的金额", mismatch,
        lambda p: p["result"]["extraction"].update(raw_value="999", normalized_value="999"))
    add("RV-02", "替换来源链接", "把source.url换成合成地址", mismatch, lambda p: p["source"].update(url="https://example.invalid/source"))
    add("RV-03", "替换来源摘要", "修改source.sha256", mismatch, lambda p: p["source"].update(sha256="0"*64))
    add("RV-04", "替换原页登记", "修改image_sha256", mismatch, lambda p: p["page_metadata"].update(image_sha256="0"*64))
    add("RV-05", "改查询行名", "修改顶层query.row_label", mismatch, lambda p: p["query"].update(row_label="Synthetic different label"))
    add("RV-06", "改查询年份", "修改顶层query.year", mismatch, lambda p: p["query"].update(year=2023))
    other_index = payload["discovery"]["candidates"][-1]["row_index"]
    add("RV-07", "改选择但不换结果", "只把选择索引换成另一合法候选", mismatch,
        lambda p: p["result"]["selection"].update(row_index=other_index))
    def drop_candidate(p):
        p["discovery"]["candidates"].pop(); p["discovery"]["candidate_count"] -= 1
        p["result"]["candidates"] = deepcopy(p["discovery"]["candidates"]); p["result"]["candidate_count"] -= 1
    add("RV-08", "删去未选同名候选", "两份候选清单与计数同步删减", mismatch, drop_candidate)
    add("RV-09", "改附近原文", "修改候选的上文内容", mismatch,
        lambda p: p["discovery"]["candidates"][0]["context"]["above"][0]["span"].update(text="SYNTHETIC_UNTRUSTED_CONTEXT"))
    add("RV-10", "增加未约定字段", "增加调用方自报质量字段", mismatch, lambda p: p.update(SYNTHETIC_UNTRUSTED_KEY="SYNTHETIC_UNTRUSTED_VALUE"))
    add("RV-11", "删去来源字段", "移除完整source", mismatch, lambda p: p.pop("source"))
    add("RV-12", "布尔冒充版本整数", "schema_version由1变true", mismatch, lambda p: p.update(schema_version=True))
    add("RV-13", "浮点冒充行索引", "合法索引改为同值浮点", mismatch,
        lambda p: p["result"]["selection"].update(row_index=float(p["result"]["selection"]["row_index"])))
    add("RV-14", "整数冒充明确选择", "explicit由true变1", mismatch, lambda p: p["result"]["selection"].update(explicit=1))
    add("RV-15", "零冒充未批准", "automatic_approval由false变0", mismatch, lambda p: p.update(automatic_approval=0))
    add("RV-16", "伪造报告摘要", "report_sha256换成未登记摘要", mismatch, lambda p: p.update(report_sha256="0"*64))
    add("RV-17", "伪造上游摘要", "role_report_sha256换成未登记摘要", mismatch, lambda p: p.update(role_report_sha256="0"*64))
    add("RV-18", "重写选择输入摘要", "重复位置同步改成同一伪摘要", mismatch,
        lambda p: p.update(_replace(p, p["result"]["selection"]["input_sha256"], "0"*64)))
    def rewrite_all(p):
        raw = p["result"]["extraction"]["raw_value"]; number = p["result"]["extraction"]["normalized_value"]
        changed = _replace(_replace(p, raw, "99,999"), number, "99999")
        # Rehash the caller's altered discovery and selection together; this never changes the trusted registry.
        fake = digest(changed["discovery"])
        changed = _replace(changed, changed["result"]["selection"]["input_sha256"], fake)
        p.clear(); p.update(changed)
    add("RV-19", "整包自洽改金额与摘要", "所有同值原文和数字同步修改并重算调用方摘要", mismatch, rewrite_all)
    add("RV-20", "伪造可写状态", "read_only改为false", mismatch, lambda p: p.update(read_only=False))
    add("RV-21", "伪造已持久化", "persisted改为true", mismatch, lambda p: p.update(persisted=True))
    add("RV-22", "伪造自动批准", "automatic_approval改为true", mismatch, lambda p: p.update(automatic_approval=True))
    add("RV-23", "改文档与物理页", "document_id与page同时替换", mismatch, lambda p: p.update(document_id="msft", page=50))
    raw = _bytes(payload)
    def raw_case(key, title, value, code=None):
        controls.append({"id": key, "title": title, "modification": title,
                         "expected": {"status": "input_error", "code": code} if code else {"status": "matched"}, "raw": value})
    raw_case("RV-24", "相同重复JSON键也拒绝", b'{"schema_version":1,"schema_version":1}', "duplicate_key")
    raw_case("RV-25", "非有限NaN拒绝", b'{"schema_version":NaN}', "non_finite_number")
    raw_case("RV-26", "浮点溢出拒绝", b'{"schema_version":1e400}', "non_finite_number")
    raw_case("RV-27", "超过嵌套深度拒绝", b'{"nested":'+b'['*70+b'0'+b']'*70+b'}', "excessive_depth")
    raw_case("RV-28", "错误UTF8拒绝", b'\xff', "invalid_utf8")
    raw_case("RV-29", "孤立Unicode代理项拒绝", b'{"schema_version":"\\ud800"}', "invalid_unicode")
    raw_case("RV-30", "超过128KiB拒绝", b' '*(MAX_INPUT_BYTES+1), "input_too_large")
    raw_case("RV-31", "截断JSON拒绝", raw[:-1], "invalid_json")
    raw_case("RV-32", "非对象根拒绝", b'[]', "invalid_root")
    raw_case("RV-33", "空白和键顺序不改变内容", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf8"))
    require(len(raw) < MAX_INPUT_BYTES, "fixture_too_large", "controls")
    raw_case("RV-34", "恰好128KiB合法对象可检查", raw+b' '*(MAX_INPUT_BYTES-len(raw)))
    return controls


def observe(raw, snapshot):
    from .row_selection_verification import parse_selection_bytes, verify_row_selection, RowSelectionVerificationError
    try:
        parsed = parse_selection_bytes(raw)
        result = verify_row_selection(parsed, snapshot)
        result["raw_sha256"] = sha256(raw).hexdigest()
        return {"status": result["status"], "verification": result}
    except RowSelectionVerificationError as exc:
        return {"status": "input_error", "code": exc.code, "path": exc.path, "http_status": exc.http_status}


def _agrees(observed, expected):
    return all(observed.get(key) == value for key, value in expected.items())


def build_row_selection_verification_report(root: Path) -> dict:
    root = Path(root).resolve(); snapshot = source.load_row_disambiguation_snapshot(root); hashes = _hashes(root)
    registered = registered_exports(snapshot)
    require(len(registered) == 8 and len({(item["query_id"],item["row_index"]) for item in registered}) == 8,
            "source_inventory_changed", "registered_paths")
    cases = []
    for item in registered:
        raw = _bytes(item["payload"]); actual = observe(raw, snapshot)
        cases.append({"query_id": item["query_id"], "row_index": item["row_index"],
            "provenance": "registered_saved_selection_response_shape", "input_bytes": len(raw),
            "input_sha256": sha256(raw).hexdigest(), "expected": {"status": "matched"}, "observed": actual,
            "passed": actual["status"] == "matched"})
    controls = []
    for spec in control_inputs(registered[0]["payload"]):
        raw = spec["raw"]; actual = observe(raw, snapshot)
        controls.append({**{key:value for key,value in spec.items() if key != "raw"},
            "provenance": "synthetic_development_control", "input_bytes": len(raw), "input_sha256": sha256(raw).hexdigest(),
            "observed": actual, "passed": _agrees(actual, spec["expected"]), "independent_human_confirmation": "pending"})
    def summary(items): return {"cases": len(items), "passed": sum(c["passed"] for c in items), "failed": sum(not c["passed"] for c in items)}
    require(all(c["passed"] for c in [*cases,*controls]), "unexpected_verification_outcome", "cases")
    require(source.load_row_disambiguation_snapshot(root) == snapshot and _hashes(root) == hashes,
            "source_changed_during_run", "snapshot")
    return {"schema_version": 1, "experiment_version": VERSION,
        "row_report_sha256": snapshot["report_sha256"], "role_report_sha256": snapshot["role_report_sha256"],
        "implementation_hash_basis": "python_utf8_lf", "implementation_sha256": hashes,
        "registered_paths": {"cases": cases, "summary": summary(cases)},
        "synthetic_controls": {"cases": controls, "summary": summary(controls)},
        "scope": {"real_http_responses_used": False, "uploaded_payloads_persisted": False, "complete_ocr_replayed": False,
            "source_bytes_authenticated": False, "independent_human_gold": 0, "model_calls": 0, "new_ocr_calls": 0,
            "network_calls": 0, "database_writes": 0, "model_accuracy_measured": False},
        "limitations": list(LIMITATIONS), "read_only": True, "automatic_approval": False}


def validate_row_selection_verification_report(root: Path, report: dict) -> dict:
    try:
        expected = build_row_selection_verification_report(root)
        require(digest(report) == digest(expected), "report_mismatch", "report")
        return deepcopy(report)
    except RowSelectionExperimentError: raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise RowSelectionExperimentError("invalid_report", "report") from exc


def load_row_selection_verification_snapshot(root: Path) -> dict:
    raw, report = source._read(Path(root)/REPORT)
    result = validate_row_selection_verification_report(Path(root), report)
    return {"schema_version": 1, "report": result, "report_sha256": sha256(raw).hexdigest(), "read_only": True,
            "full_ocr_replayed": False, "validation_scope": "冻结公开响应封套逐字段比较及合成格式/篡改控制重放；不认证原始来源或模型质量。"}
