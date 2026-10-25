"""Replay reviewed issuer evidence and independent synthetic currency controls.

No OCR or download occurs. Synthetic expectations are manually declared before
candidate results; observed issuer diagnostics and controls have separate counts.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from .date_evidence import crosscheck_date_fact
from .diligence import Fact, digest
from .document_transfer import file_hash
from .issuer_evaluation import IssuerPack, read_json
from .issuer_store import MANIFEST, RELEASE, REPORT, issuer_snapshot
from .word_evidence import WordLine, WordPage


VERSION = "currency-diagnostic-1"
STATES = ("consistent", "conflict", "insufficient")
IMPLEMENTATION_FILES = (
    "creditlens/currency_evidence.py", "creditlens/currency_diagnostic.py",
    "scripts/evaluate_currency_diagnostic.py", "creditlens/issuer_store.py",
    "creditlens/issuer_evaluation.py", "creditlens/document_transfer.py",
    "creditlens/date_evidence.py", "creditlens/word_evidence.py",
    "creditlens/visual.py", "creditlens/diligence.py",
)
SCOPE = {
    "operation": "currency_rule_diagnostic_on_published_evidence",
    "evidence": "published_necessary_parent_lines_and_complete_word_groups",
    "evaluation": "observed_known_location_development_diagnostic",
    "model_inference_performed": False, "blind_evaluation": False,
    "full_ocr_verified": False, "image_pdf_bytes_verified": False,
    "source_model_execution_verified": False,
    "controls": "manually_specified_synthetic_ocr_not_model_accuracy",
    "not_checked": ["omitted_full_ocr", "source_image_and_pdf_bytes", "actual_model_execution", "document_authenticity"],
}

# Expectations are design requirements, not labels derived from candidate output.
_CASES = (
    ("merged_usd", "完整 USD 金额", "保持手工定义的 $1,234 完整父行和词框。", "consistent"),
    ("split_usd", "分开的美元符号", "将同父行拆成 $ 和 1,234 两词，金额词位于登记框。", "consistent"),
    ("negative_matching_parentheses", "正确负值括号金额", "手工负值 CFO 参考为 (1,234)，完整 OCR 词为 $(1,234)。", "consistent"),
    ("negative_matching_minus", "正确负号金额", "手工负值 CFO 参考为 -1,234，完整 OCR 词为 $-1,234。", "consistent"),
    ("negative_reference_missing_sign", "负值参考却漏了负号", "手工 CFO 参考为 -1,234，OCR 仍是正数 $1,234。", "not_consistent"),
    ("wrong_minus", "金额负号不符", "将完整金额改为 -$1,234，参考保持正值。", "not_consistent"),
    ("negative_parentheses", "金额括号负值", "将完整金额改为 ($1,234)，参考保持正值。", "not_consistent"),
    ("unclosed_parenthesis", "残缺金额括号", "将完整金额改为 $1,234)。", "not_consistent"),
    ("wrong_eur", "欧元与 USD 不符", "只把币种标记改成 €，不改变数值或框。", "not_consistent"),
    ("wrong_yen", "人民币日元标记不符", "只把币种标记改成 ¥，登记单位保持 USD。", "not_consistent"),
    ("cad_prefix", "其他美元币种前缀", "将完整金额改成 C$1,234，不能把 C$ 当成 USD。", "not_consistent"),
    ("joined_amounts", "两个金额合并", "将同一个完整词改成 $1,234 9,876，不许截取正确子串。", "not_consistent"),
    ("wrong_digits", "金额数字不符", "只将完整金额改为 $1,235。", "not_consistent"),
    ("cross_column", "金额框跨入旁列", "把完整金额词和父行右边界延长到相邻年份列区域。", "not_consistent"),
    ("nearby_numeric_empty_words", "邻近无词框数字父行", "在候选金额旁放置数字 7 的父行，words 为空。", "not_consistent"),
    ("nearby_minus", "分离的外部负号", "在候选金额框左侧邻域加入独立负号父行与词。", "not_consistent"),
    ("low_parent_confidence", "父行低置信度", "金额父行置信度降至 0.2，词分数仍高。", "not_consistent"),
    ("token_parent_mismatch", "父行与词不守恒", "父行末尾多一个 7，完整词不变。", "not_consistent"),
    ("duplicate_parent", "重复重叠金额父行", "增加同位置同金额父行，不能选一个作为唯一依据。", "not_consistent"),
    ("missing_words", "金额父行无词框", "清空金额父行的 words。", "not_consistent"),
    ("wrong_year", "年份仍有冲突", "当前列 OCR 年份改为 2026，参考仍为 2025。", "not_consistent"),
    ("missing_unit", "缺少单位依据", "删除单位父行，金额仍与参考相同。", "not_consistent"),
    ("wrong_unit", "单位口径冲突", "把单位文字 In millions 改为 In billions。", "not_consistent"),
    ("missing_amount", "没有金额依据", "删除金额父行，不能从参考值补出识别结果。", "not_consistent"),
    ("nearby_close_parenthesis", "分离右括号", "在候选金额右侧加入独立右括号。", "not_consistent"),
    ("nearby_eur", "附近其他币种父行", "在候选金额左侧加入 € 父行，words 为空。", "not_consistent"),
    ("separate_c_prefix", "分离的 C 美元前缀", "在 $ 金额左侧加入无 words 的 C 父行，不能忽略外部币种前缀。", "not_consistent"),
    ("separate_cad_prefix", "分离的 CAD 前缀", "在 $ 金额左侧加入无 words 的 CAD 父行。", "not_consistent"),
    ("separate_hk_prefix", "分离的 HK 前缀", "在 $ 金额左侧加入无 words 的 HK 父行。", "not_consistent"),
    ("separate_eur_prefix", "分离的 EUR 前缀", "在 $ 金额左侧加入无 words 的 EUR 父行。", "not_consistent"),
    ("separate_c_prefix_words", "分离的 C 前缀及词", "在 $ 金额左侧加入 C 父行及完整词。", "not_consistent"),
    ("separate_cad_prefix_words", "分离的 CAD 前缀及词", "在 $ 金额左侧加入 CAD 父行及完整词。", "not_consistent"),
    ("separate_hk_prefix_words", "分离的 HK 前缀及词", "在 $ 金额左侧加入 HK 父行及完整词。", "not_consistent"),
    ("separate_eur_prefix_words", "分离的 EUR 前缀及词", "在 $ 金额左侧加入 EUR 父行及完整词。", "not_consistent"),
)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def control_catalog() -> dict:
    return {"schema_version": 1, "id": "currency-synthetic-controls-1",
            "expectation_basis": "Manually declared financial-evidence invariants; not candidate-generated labels",
            "cases": [{"id": key, "title": title, "modification": modification,
                       "expected": {"overall_status": expected}, "provenance": "synthetic"}
                      for key, title, modification, expected in _CASES]}


def _box(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


def _entry(text, bbox, confidence=.99):
    span = {"text": text, "bbox": bbox, "confidence": confidence}
    return {"line": deepcopy(span), "words": [deepcopy(span)]}


def _controlled_input():
    """Entirely handmade evidence; never presented as a financial disclosure."""
    fact = Fact.model_validate({
        "id": "synthetic-cash-2025", "metric": "cash_equivalents", "label": "Synthetic cash",
        "value": "1234", "raw_value": "1,234", "unit": "USD_million", "period_end": "2025-12-27",
        "source_id": "synthetic-currency", "locator": "Handmade control; not a real issuer or OCR run",
        "pdf_location": {"page": 1, "page_width": 500, "page_height": 700,
            "coordinate_system": "pdf_points_top_left",
            "amount_bbox": _box(220, 150, 242, 160), "row_label": "Cash", "row_label_bbox": _box(30,150,80,160),
            "year": 2025, "year_bbox": _box(221,60,241,70), "period_header": "December 27, 2025",
            "period_bbox": _box(201,50,261,70), "unit_header": "In millions", "unit_bbox": _box(180,90,260,100),
            "parser_version": "handmade-currency-control-1"}})
    lines = [
        _entry("December 27,", _box(201,50,261,60)), _entry("2025", _box(221,60,241,70)),
        _entry("December 28,", _box(281,50,341,60)), _entry("2024", _box(301,60,321,70)),
        _entry("In millions", _box(180,90,260,100)), _entry("Cash", _box(30,150,80,160)),
        _entry("$1,234", _box(208,148,244,162)),
    ]
    lines[-1]["line"]["bbox"] = _box(206,148,246,162)
    return fact, {"page": 1, "width": 500, "height": 700, "lines": lines}


def synthetic_control_inputs() -> list[tuple[dict, Fact, WordPage]]:
    values = []
    substitutions = {"wrong_minus": "-$1,234", "negative_parentheses": "($1,234)",
                     "unclosed_parenthesis": "$1,234)", "wrong_eur": "€1,234", "wrong_yen": "¥1,234",
                     "cad_prefix": "C$1,234", "joined_amounts": "$1,234 9,876", "wrong_digits": "$1,235"}
    for spec in control_catalog()["cases"]:
        fact, page = _controlled_input()
        lines, key = page["lines"], spec["id"]
        target = lines[6]
        if key in substitutions:
            target["line"]["text"] = target["words"][0]["text"] = substitutions[key]
        elif key == "split_usd":
            target["words"] = [_entry("$", _box(208,148,216,162))["line"],
                               _entry("1,234", _box(220,150,242,160))["line"]]
        elif key in {"negative_matching_parentheses", "negative_matching_minus", "negative_reference_missing_sign"}:
            reference = fact.model_dump(mode="json")
            reference.update(metric="cfo", label="Synthetic negative operating cash flow", value="-1234",
                             raw_value="(1,234)" if key == "negative_matching_parentheses" else "-1,234",
                             period_start="2025-01-01")
            reference["pdf_location"].update(row_label="Operating cash flow", row_label_bbox=_box(30,150,145,160))
            fact = Fact.model_validate(reference)
            lines[5] = _entry("Operating cash flow", _box(30,150,145,160))
            marked = {"negative_matching_parentheses": "$(1,234)", "negative_matching_minus": "$-1,234",
                      "negative_reference_missing_sign": "$1,234"}[key]
            target["line"]["text"] = target["words"][0]["text"] = marked
        elif key == "cross_column":
            target["line"]["bbox"]["x1"] = 292
            target["words"][0]["bbox"]["x1"] = 290
        elif key == "nearby_numeric_empty_words":
            addition = _entry("7", _box(250,148,256,162)); addition["words"] = []; lines.append(addition)
        elif key == "nearby_minus":
            lines.append(_entry("-", _box(201,148,205,162)))
        elif key == "low_parent_confidence":
            target["line"]["confidence"] = .2
        elif key == "token_parent_mismatch":
            target["line"]["text"] += "7"
        elif key == "duplicate_parent":
            lines.append(deepcopy(target))
        elif key == "missing_words":
            target["words"] = []
        elif key == "wrong_year":
            lines[1]["line"]["text"] = lines[1]["words"][0]["text"] = "2026"
        elif key == "missing_unit":
            del lines[4]
        elif key == "wrong_unit":
            lines[4]["line"]["text"] = lines[4]["words"][0]["text"] = "In billions"
        elif key == "missing_amount":
            del lines[6]
        elif key == "nearby_close_parenthesis":
            lines.append(_entry(")", _box(248,148,251,162)))
        elif key == "nearby_eur":
            addition = _entry("€", _box(201,148,205,162)); addition["words"] = []; lines.append(addition)
        elif key.startswith("separate_"):
            prefix = {"separate_c_prefix": "C", "separate_cad_prefix": "CAD",
                      "separate_hk_prefix": "HK", "separate_eur_prefix": "EUR"}[key.removesuffix("_words")]
            addition = _entry(prefix, _box(201,148,205,162))
            if not key.endswith("_words"):
                addition["words"] = []
            lines.append(addition)
        values.append((spec, fact, WordPage.model_validate(page)))
    return values


def _source(root):
    snapshot = issuer_snapshot(root)
    raw_report, report = read_json(root/REPORT)
    raw_manifest, manifest = read_json(root/MANIFEST, 1_000_000)
    raw_release, release = read_json(root/RELEASE, 100_000)
    hashes = {REPORT: sha256(raw_report).hexdigest(), MANIFEST: sha256(raw_manifest).hexdigest()}
    _require(snapshot["report"] == report and snapshot["manifest"] == manifest and release.get("files") == hashes
             and report["manifest_sha256"] == hashes[MANIFEST], "Issuer release changed while capturing diagnostic input")
    packs, references = {}, {}
    for document in manifest["documents"]:
        path = (root/document["pack_path"]).resolve()
        _require(path.is_relative_to((root/"data/examples").resolve()), "Diagnostic reference escaped examples")
        raw, value = read_json(path, 5_000_000)
        _require(sha256(raw).hexdigest() == document["pack_sha256"], "Diagnostic reference hash changed")
        packs[document["id"]] = IssuerPack.model_validate(value)
        references[document["id"]] = {"path": document["pack_path"], "sha256": document["pack_sha256"]}
    profile_raw, profile = read_json(root/"configs/document-ai.json", 100_000)
    _require(sha256(profile_raw).hexdigest() == manifest["files"]["configs/document-ai.json"], "Frozen OCR threshold changed")
    provenance = {"report_path": REPORT, "report_sha256": hashes[REPORT], "manifest_path": MANIFEST,
                  "manifest_sha256": hashes[MANIFEST], "release_path": RELEASE, "release_sha256": sha256(raw_release).hexdigest(),
                  "experiment_id": report["experiment_id"], "report_generated_at": report["generated_at"],
                  "reference_packs": references}
    return snapshot, provenance, packs, profile["min_confidence"]


def _amount(result):
    matches = [check for check in result["checks"] if check["field"] == "amount"]
    _require(len(matches) == 1, "Exactly one amount check is required")
    return matches[0]


def _unchanged(old, new):
    return ([check for check in old["checks"] if check["field"] != "amount"] ==
            [check for check in new["checks"] if check["field"] != "amount"]
            and old["date_association"] == new["date_association"] and old["date_rule_version"] == new["date_rule_version"])


def _counts(items, key):
    return {state: sum(item[key]["status"] == state for item in items) for state in STATES}


def _summary(items, pages, documents):
    return {"facts": len(items), "distinct_pages": pages, "distinct_documents": documents,
            "date": _counts(items, "legacy_date"), "currency": _counts(items, "currency"),
            "merged_amount_candidates": sum(_amount(i["legacy_date"])["reason"] == "merged_or_misaligned_region" for i in items),
            "currency_association_applied": sum(i["currency"]["currency_association"]["applied"] for i in items),
            "non_amount_unchanged": all(i["non_amount_unchanged"] for i in items),
            "date_to_currency": {f"{old}->{new}": sum(i["legacy_date"]["status"] == old and i["currency"]["status"] == new for i in items)
                                 for old in STATES for new in STATES}}


def _controls(threshold, rule, version):
    cases = []
    for spec, fact, page in synthetic_control_inputs():
        old = crosscheck_date_fact(fact, page, min_confidence=threshold)
        result = rule(fact, page, min_confidence=threshold)
        unchanged = _unchanged(old, result)
        expected = spec["expected"]["overall_status"]
        correct = result["status"] == "consistent" if expected == "consistent" else result["status"] != "consistent"
        passed = bool(correct and unchanged and result.get("automatic_approval") is False
                      and result.get("currency_rule_version") == version
                      and result["currency_association"]["legacy_amount"] == _amount(old))
        inputs = {"fact": fact.model_dump(mode="json"), "page": page.model_dump(mode="json")}
        cases.append({**spec, "input": inputs, "input_sha256": digest(inputs), "legacy_date": old,
                      "currency": result, "non_amount_unchanged": unchanged, "passed": passed})
    return {"scope": "synthetic_manually_specified_ocr_controls", "catalog_sha256": digest(control_catalog()),
            "summary": {"cases": len(cases), "passed": sum(c["passed"] for c in cases),
                        "failed": sum(not c["passed"] for c in cases),
                        "positive_cases": sum(c["expected"]["overall_status"] == "consistent" for c in cases),
                        "negative_cases": sum(c["expected"]["overall_status"] == "not_consistent" for c in cases)}, "cases": cases}


def _generated(value, source_time):
    instant = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value) if isinstance(value, str) else value
    _require(isinstance(instant, datetime) and instant.tzinfo is not None and instant.utcoffset() is not None,
             "Diagnostic timestamp must include a timezone")
    _require(datetime.fromisoformat(source_time) <= instant <= datetime.now(timezone.utc), "Invalid diagnostic timestamp")
    return instant.astimezone(timezone.utc).isoformat()


def _build(root, source, provenance, packs, threshold, generated_at=None):
    from .currency_evidence import CURRENCY_RULE_VERSION, crosscheck_currency_fact

    documents, all_items = [], []
    page_count = 0
    for document in source["report"]["documents"]:
        by_id = {fact.id: fact for fact in packs[document["id"]].facts}
        legacy = {item["fact_id"]: item for item in document["facts"]}
        items, pages = [], []
        for registered in document["pages"]:
            page = WordPage(page=registered["page"], width=registered["width"], height=registered["height"],
                            lines=[WordLine.model_validate({k: line[k] for k in ("line", "words")}) for line in registered["evidence_lines"]])
            _require(digest(page.model_dump(mode="json")) == registered["evidence_sha256"], "Published page evidence changed")
            pages.append({k: deepcopy(registered[k]) for k in ("page", "width", "height", "image_sha256", "evidence_sha256",
                          "full_line_ocr_sha256", "full_word_ocr_sha256", "fact_ids")})
            for fid in registered["fact_ids"]:
                fact, old = by_id[fid], legacy[fid]
                old_date = crosscheck_date_fact(fact, page, min_confidence=threshold)
                _require(old_date == old["date"], "Legacy date result does not replay")
                result = crosscheck_currency_fact(fact, page, min_confidence=threshold)
                unchanged = _unchanged(old_date, result)
                _require(unchanged and result.get("automatic_approval") is False and
                         result.get("currency_rule_version") == CURRENCY_RULE_VERSION and
                         result["currency_association"]["legacy_amount"] == _amount(old_date),
                         "Currency diagnostic changed non-amount evidence or its legacy boundary")
                items.append({"fact_id": fid, "page": page.page, "date_group_id": old["date_group_id"],
                              "legacy_line": deepcopy(old["line"]), "legacy_word": deepcopy(old["word"]),
                              "legacy_date": deepcopy(old_date), "currency": result, "non_amount_unchanged": unchanged})
        documents.append({"id": document["id"], "company": document["company"], "raw_pdf_sha256": document["raw_pdf_sha256"],
                          "pages": pages, "facts": items, "summary": _summary(items, len(pages), 1)})
        all_items.extend(items); page_count += len(pages)
    return {"schema_version": 1, "suite_version": VERSION, "generated_at": _generated(generated_at, source["report"]["generated_at"]),
            "source": provenance, "rule_versions": {**source["report"]["rule_versions"], "currency": CURRENCY_RULE_VERSION},
            "implementation": {"hash_basis": "python_utf8_lf", "files": {relative: file_hash(root/relative) for relative in IMPLEMENTATION_FILES}},
            "min_confidence": threshold, "scope": deepcopy(SCOPE), "automatic_approval": False,
            "documents": documents, "summary": _summary(all_items, page_count, len(documents)),
            "controls": _controls(threshold, crosscheck_currency_fact, CURRENCY_RULE_VERSION)}


def build_currency_diagnostic(root: Path, *, generated_at=None) -> dict:
    root = Path(root)
    source, provenance, packs, threshold = _source(root)
    return _build(root, source, provenance, packs, threshold, generated_at)


def currency_diagnostic_view(root: Path, report: dict) -> dict:
    _require(isinstance(report, dict) and isinstance(report.get("generated_at"), str), "Diagnostic generated_at is required")
    root = Path(root)
    source, provenance, packs, threshold = _source(root)
    expected = _build(root, source, provenance, packs, threshold, report["generated_at"])
    _require(report == expected and digest(report) == digest(expected), "Currency diagnostic does not reproduce from reviewed source and current rules")
    return {"report": expected, "source": source, "read_only": True,
            "validation_scope": "Every published fact and every manually specified synthetic control is recomputed. Omitted OCR and original PDF/image bytes are not inspected; this observed diagnostic is not new inference, blind evaluation or an accuracy estimate."}


def validate_currency_diagnostic(root: Path, report: dict) -> dict:
    return currency_diagnostic_view(root, report)["report"]
