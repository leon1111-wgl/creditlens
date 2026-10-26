"""Compare a selection export with an installed, validated frozen response.

This does not trust an uploaded self-declared hash, authenticate a document or
replay OCR. The pure verifier's snapshot must come from the registered loader.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, DecimalException
from hashlib import sha256
import json
import math
from pathlib import Path
import re

from .row_disambiguation_experiment import load_row_disambiguation_snapshot

ROW_SELECTION_VERIFICATION_VERSION = "row-selection-verification-1"
MAX_BYTES = MAX_INPUT_BYTES = 128 * 1024
MAX_DEPTH = 64
MAX_NODES = 20000
MAX_ISSUES = 16
_HASH = re.compile(r"[0-9a-f]{64}")
_RULE = "row-disambiguation-1"
_EXPERIMENT = "row-disambiguation-experiment-1"
_MESSAGES = {
    "missing_field": "缺少冻结响应中的字段。",
    "extra_field": "该位置包含冻结响应未登记的字段。",
    "type_mismatch": "字段类型与冻结响应不同；布尔、整数和浮点数不混用。",
    "value_mismatch": "字段内容与冻结响应不同。",
    "length_mismatch": "列表长度与冻结响应不同。",
    "unsupported_schema_version": "该导出结构版本未登记。",
    "unsupported_rule_version": "该规则版本未登记。",
    "unregistered_report": "报告摘要不属于当前已校验的冻结报告。",
    "unregistered_role_report": "上游报告摘要与冻结登记不符。",
    "unregistered_query": "查询未登记在当前冻结报告中。",
    "unregistered_row": "该查询没有登记这个显式行选择。",
}
LIMITATIONS = [
    "仅核对与安装仓库已校验冻结响应逐字段一致，不独立验证财务数值、所选会计范围或识别正确性。",
    "摘要用于内容一致性，不是数字签名，不证明上传者身份、文件真实性、获取时间或独立人工参考。",
    "完整替换为另一条合法登记路径仍会匹配；本次原始字节摘要没有预先可信收件摘要可比，不能证明历史原件从未改变。",
    "不读取PDF、图像或完整OCR，不重新取数或调用模型；不按上传的来源链接访问网络。",
    "缺失或失效的安装登记会阻止核验；不将上传文件自身摘要作为恢复或批准依据。",
    "纯verify函数依赖调用方提供可信loader输出；它不能认证任意由客户端构造的snapshot。",
]


class RowSelectionVerificationError(ValueError):
    def __init__(self, code: str, path: str, message: str = "Selection export input is invalid"):
        self.code, self.path = code, path
        self.http_status = 413 if code == "input_too_large" else 400
        super().__init__(message)


class RowSelectionSourceError(ValueError):
    def __init__(self, code: str = "source_unavailable", path: str = "snapshot", message: str = "Frozen selection source is unavailable"):
        self.code, self.path, self.http_status = code, path, 409
        super().__init__(message)


def _fail(code):
    raise RowSelectionVerificationError(code, "$")


def _depth_scan(text):
    depth, quoted, escaped = 0, False, False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                _fail("excessive_depth")
        elif char in "]}":
            depth -= 1


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _fail("duplicate_key")
        result[key] = value
    return result


def _integer(text):
    if len(text) > 1024:
        _fail("invalid_number")
    return int(text)


def _float(text):
    if len(text) > 1024:
        _fail("invalid_number")
    try:
        value = float(text)
        if not math.isfinite(value):
            _fail("non_finite_number")
        # Do not let 1e-9999 or 1.0000000000000000001 silently become a
        # registered 0.0 or 1.0. Ordinary JSON emitted from Python floats
        # round-trips exactly through its shortest decimal representation.
        if Decimal(text) != Decimal(str(value)):
            _fail("invalid_number")
        return value
    except RowSelectionVerificationError:
        raise
    except (ValueError, OverflowError, DecimalException):
        _fail("invalid_number")


def _validate_json(value):
    if type(value) is not dict:
        _fail("invalid_root")
    stack, active, nodes = [(value, 1, False)], set(), 0
    while stack:
        item, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(item))
            continue
        nodes += 1
        if nodes > MAX_NODES:
            _fail("excessive_nodes")
        kind = type(item)
        if kind in (dict, list):
            if depth > MAX_DEPTH:
                _fail("excessive_depth")
            if id(item) in active:
                # Reused sibling containers can serialize as ordinary JSON;
                # an ancestor cycle cannot. Count every serialized occurrence.
                _fail("invalid_json")
            active.add(id(item))
            stack.append((item, depth, True))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        _fail("invalid_json")
                    try:
                        key.encode("utf8")
                    except UnicodeError:
                        _fail("invalid_unicode")
                    stack.append((child, depth+1, False))
            else:
                stack.extend((child, depth+1, False) for child in item)
        elif kind is str:
            try:
                item.encode("utf8")
            except UnicodeError:
                _fail("invalid_unicode")
        elif kind is float:
            if not math.isfinite(item):
                _fail("non_finite_number")
        elif kind is int:
            if item.bit_length() > 3400:
                _fail("invalid_number")
        elif item is not None and kind is not bool:
            _fail("invalid_json")


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf8")
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail("invalid_json")


def parse_selection_bytes(raw: bytes) -> dict:
    """Bounded strict UTF-8 JSON parsing, before any trusted-source loading."""
    if type(raw) is not bytes:
        _fail("invalid_bytes")
    if len(raw) > MAX_BYTES:
        _fail("input_too_large")
    try:
        text = raw.decode("utf8", errors="strict")
    except UnicodeError:
        _fail("invalid_utf8")
    _depth_scan(text)
    try:
        value = json.loads(text, object_pairs_hook=_pairs, parse_int=_integer, parse_float=_float,
                           parse_constant=lambda _: _fail("non_finite_number"))
    except RowSelectionVerificationError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError):
        _fail("invalid_json")
    _validate_json(value)
    return value


def _inventory(snapshot):
    """Check the supported loader DTO; this does not replace its validation."""
    try:
        if (type(snapshot) is not dict or type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1
                or snapshot["read_only"] is not True or snapshot["full_ocr_replayed"] is not False
                or any(type(snapshot[k]) is not str or not _HASH.fullmatch(snapshot[k]) for k in ("report_sha256", "role_report_sha256"))):
            raise RowSelectionSourceError()
        report = snapshot["report"]
        if (type(report) is not dict or type(report["schema_version"]) is not int or report["schema_version"] != 1
                or report["experiment_version"] != _EXPERIMENT):
            raise RowSelectionSourceError("unsupported_source_version")
        queries = report["queries"]
        if type(queries) is not list or not queries:
            raise RowSelectionSourceError()
        inventory = {}
        for query in queries:
            qid = query["id"]
            if type(qid) is not str or qid in inventory or query["discovery"]["rule_version"] != _RULE:
                raise RowSelectionSourceError("unsupported_source_version")
            paths = query["paths"]
            if type(paths) is not list or not paths:
                raise RowSelectionSourceError()
            rows = {}
            for path in paths:
                index = path["row_index"]
                if (type(index) is not int or index < 0 or index in rows
                        or path["result"]["rule_version"] != _RULE
                        or type(path["result"]["selection"]["row_index"]) is not int
                        or path["result"]["selection"]["row_index"] != index):
                    raise RowSelectionSourceError()
                rows[index] = path
            inventory[qid] = (query, rows)
        return inventory
    except RowSelectionSourceError:
        raise
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise RowSelectionSourceError() from None


def _expected(snapshot, query, path):
    # Exact v1 GET /api/row-disambiguation/queries/{id}/selection response.
    return {"schema_version": 1, "report_sha256": snapshot["report_sha256"],
            "role_report_sha256": snapshot["role_report_sha256"],
            "query_id": query["id"], "document_id": query["document_id"], "page": query["page"],
            "query": deepcopy(query["query"]), "source": deepcopy(query["source"]),
            "page_metadata": deepcopy(query["page_metadata"]), "discovery": deepcopy(query["discovery"]),
            "result": deepcopy(path["result"]), "read_only": True, "persisted": False,
            "full_ocr_replayed": False, "automatic_approval": False,
            "selection_basis": "explicit_request_for_saved_line_layout_result"}


def expected_selection_payload(snapshot: dict, query_id: str, row_index: int) -> dict:
    """Build the exact registered response; not a producer of user approvals."""
    inventory = _inventory(snapshot)
    if type(query_id) is not str or query_id not in inventory:
        raise RowSelectionVerificationError("unregistered_query", "$.query_id")
    query, rows = inventory[query_id]
    if type(row_index) is not int or row_index not in rows:
        raise RowSelectionVerificationError("unregistered_row", "$.result.selection.row_index")
    try:
        return _expected(snapshot, query, rows[row_index])
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RowSelectionSourceError() from None


def _issue(code, path):
    return {"code": code, "path": path, "message": _MESSAGES[code]}


def _differences(actual, expected):
    issues = []

    def add(code, path):
        issues.append(_issue(code, path))

    def walk(value, wanted, path):
        if len(issues) > MAX_ISSUES:
            return
        if type(value) is not type(wanted):
            add("type_mismatch", path)
        elif type(wanted) is dict:
            for key in wanted:
                if key not in value:
                    add("missing_field", path+"."+key)
                else:
                    walk(value[key], wanted[key], path+"."+key)
                if len(issues) > MAX_ISSUES:
                    return
            if any(key not in wanted for key in value):
                add("extra_field", path)  # Never include an uploaded unknown key.
        elif type(wanted) is list:
            if len(value) != len(wanted):
                add("length_mismatch", path)
            for i, (child, reference) in enumerate(zip(value, wanted)):
                walk(child, reference, path+f"[{i}]")
                if len(issues) > MAX_ISSUES:
                    return
        elif value != wanted or (type(wanted) is float and repr(value) != repr(wanted)):
            code = ("unsupported_schema_version" if path.endswith(".schema_version") else
                    "unsupported_rule_version" if path.endswith(".rule_version") else
                    "unregistered_report" if path == "$.report_sha256" else
                    "unregistered_role_report" if path == "$.role_report_sha256" else "value_mismatch")
            add(code, path)

    walk(actual, expected, "$")
    return issues[:MAX_ISSUES], len(issues) > MAX_ISSUES


def verify_row_selection(payload: dict, snapshot: dict) -> dict:
    """Type-sensitive whole-response comparison against a trusted snapshot."""
    _validate_json(payload)
    canonical = _canonical(payload)
    if len(canonical) > MAX_BYTES:
        _fail("input_too_large")
    inventory = _inventory(snapshot)
    issues, truncated, query, path = [], False, None, None
    if "query_id" not in payload:
        issues.append(_issue("missing_field", "$.query_id"))
    elif type(payload["query_id"]) is not str:
        issues.append(_issue("type_mismatch", "$.query_id"))
    elif payload["query_id"] not in inventory:
        issues.append(_issue("unregistered_query", "$.query_id"))
    else:
        query, rows = inventory[payload["query_id"]]
        value, location = payload, "$"
        for key in ("result", "selection", "row_index"):
            if type(value) is not dict:
                issues.append(_issue("type_mismatch", location)); break
            if key not in value:
                issues.append(_issue("missing_field", location+"."+key)); break
            value, location = value[key], location+"."+key
        else:
            if type(value) is not int:
                issues.append(_issue("type_mismatch", location))
            elif value not in rows:
                issues.append(_issue("unregistered_row", location))
            else:
                path = rows[value]
                try:
                    expected = _expected(snapshot, query, path)
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise RowSelectionSourceError() from None
                issues, truncated = _differences(payload, expected)
    matched = not issues
    binding = None
    if matched:
        binding = {"report_sha256": snapshot["report_sha256"], "role_report_sha256": snapshot["role_report_sha256"],
                   "query_id": query["id"], "document_id": query["document_id"], "page": query["page"],
                   "row_index": path["row_index"], "input_sha256": path["result"]["selection"]["input_sha256"]}
    return {"schema_version": 1, "verification_version": ROW_SELECTION_VERIFICATION_VERSION,
            "status": "matched" if matched else "mismatch", "issues": issues, "issues_truncated": truncated,
            "raw_sha256": None, "canonical_sha256": sha256(canonical).hexdigest(),
            "trusted_report_sha256": snapshot["report_sha256"], "bound_selection": binding,
            "hash_basis": {"raw_sha256": "SHA256 of supplied bytes; null when only a parsed object is provided",
                           "canonical_sha256": "Python JSON sort_keys=True ensure_ascii=False separators=(',', ':') allow_nan=False; number types retained; not a signature"},
            "scope": {"read_only": True, "persisted": False, "full_ocr_replayed": False,
                      "source_bytes_checked": False, "source_authenticity_verified": False,
                      "independent_financial_correctness": False, "human_gold": False, "automatic_approval": False},
            "limitations": list(LIMITATIONS)}


def load_and_verify_row_selection(raw: bytes, root: Path) -> dict:
    payload = parse_selection_bytes(raw)
    try:
        snapshot = load_row_disambiguation_snapshot(Path(root))
    except (ValueError, OSError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise RowSelectionSourceError() from None
    result = verify_row_selection(payload, snapshot)
    result["raw_sha256"] = sha256(raw).hexdigest()
    return result
