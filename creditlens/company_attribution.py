"""Registered company statements, separate from causal verification and approval."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path

from .research_acceptance import ResearchAcceptanceError, build_task_acceptance


ATTRIBUTION_RULE_VERSION = "company-attribution-1"
ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = "data/sources/company-attributions.json"
CATALOG_SHA256 = "16e5435d1d906011d74639c1ee75160e933afcb8c158584e6739b396d4bf3a33"
MAX_CATALOG_BYTES = 128 * 1024
MAX_HTML_BYTES = 4_000_000


class CompanyAttributionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _digest(value) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CompanyAttributionError("invalid_catalog", "目录包含无效 JSON 或非有限数字。") from exc
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CompanyAttributionError("invalid_catalog", "目录包含重复 JSON 键。")
        result[key] = value
    return result


def _constant(value):
    raise CompanyAttributionError("invalid_catalog", "目录包含非有限数字。")


def load_company_attribution_catalog(root: Path | None = None) -> dict:
    """Load the one pinned catalog; whitespace changes do not change identity."""
    path = Path(root if root is not None else ROOT) / CATALOG_PATH
    try:
        if path.stat().st_size > MAX_CATALOG_BYTES:
            raise CompanyAttributionError("invalid_catalog", "目录超过大小上限。")
        with path.open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
    except OSError as exc:
        raise CompanyAttributionError("catalog_unavailable", "登记目录缺失或无法读取。") from exc
    if len(raw) > MAX_CATALOG_BYTES:
        raise CompanyAttributionError("invalid_catalog", "目录超过大小上限。")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CompanyAttributionError):
            raise
        raise CompanyAttributionError("invalid_catalog", "目录不是合法 UTF-8 JSON。") from exc
    if _digest(value) != CATALOG_SHA256:
        raise CompanyAttributionError("catalog_integrity_mismatch", "目录与已核对版本不同，需重新登记来源，不自动接受修改。")
    return value


class _Paragraphs(HTMLParser):
    """Select by the registered article and headings, never by desired amounts."""
    def __init__(self, article_id: str):
        super().__init__(convert_charrefs=True)
        self.article_id, self.in_article = article_id, False
        self.article_count, self.current, self.blocks = 0, None, []

    def handle_starttag(self, tag, attrs):
        if tag == "article" and dict(attrs).get("id") == self.article_id:
            self.in_article = True
            self.article_count += 1
        if self.in_article and tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6"):
            if self.current is not None:
                raise CompanyAttributionError("source_location_mismatch", "段落结构不完整。")
            self.current = [tag, []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if self.current is not None and tag == self.current[0]:
            self.blocks.append((tag, " ".join("".join(self.current[1]).split())))
            self.current = None
        if tag == "article":
            self.in_article = False


def inspect_company_html(raw: bytes, root: Path | None = None) -> dict:
    """Offline recheck of supplied response bytes; never downloads or saves HTML.

    The caller must obtain the official URL. This function only checks bytes and
    location against registration; it does not authenticate transport or a date.
    """
    if type(raw) is not bytes or not raw or len(raw) > MAX_HTML_BYTES:
        raise CompanyAttributionError("invalid_source", "需要大小受限的 HTML 响应字节。")
    source = load_company_attribution_catalog(root)["entries"][0]["source"]
    parser = _Paragraphs(source["article_id"])
    try:
        parser.feed(raw.decode("utf-8-sig"))
        parser.close()
    except UnicodeError as exc:
        raise CompanyAttributionError("invalid_source", "HTML 不是有效 UTF-8。") from exc
    selected = [i for i, (_, text) in enumerate(parser.blocks) if text == source["subheading"]]
    if (parser.article_count != 1 or parser.current is not None or len(selected) != 1
            or selected[0] + 1 >= len(parser.blocks)
            or not any(text == source["section_heading"] for _, text in parser.blocks[:selected[0]])):
        raise CompanyAttributionError("source_location_mismatch", "登记章节或唯一段落定位失败。")
    tag, paragraph = parser.blocks[selected[0] + 1]
    if tag != "p" or not paragraph or not paragraph.startswith(source["anchor_en"]):
        raise CompanyAttributionError("source_location_mismatch", "登记标题后的首段与短锚不符。")
    response_sha, paragraph_sha = hashlib.sha256(raw).hexdigest(), hashlib.sha256(paragraph.encode("utf-8")).hexdigest()
    if (response_sha != source["response_sha256"] or paragraph_sha != source["paragraph_sha256"]
            or len(raw) != source["response_bytes"]):
        raise CompanyAttributionError("source_version_mismatch", "完整响应或段落与抓取登记不同；不能假定网页从未更新。")
    return {"response_sha256": response_sha, "paragraph_sha256": paragraph_sha,
            "response_bytes": len(raw), "location_verified": True, "full_text_retained": False,
            "publication_date_verified": False, "independently_verified": False}


def _numeric_check(key: str, observations: list[dict], reported: dict) -> dict:
    with localcontext() as context:
        context.prec = 40
        before, current = (Decimal(o["normalized_value"]) for o in observations)
        exact = current if key == "current_cfo" else current - before
        converted = exact / Decimal("1000")
        rounded = converted.quantize(Decimal(reported["precision"]), rounding=ROUND_HALF_UP)
        expected = Decimal(reported[key])
        if not expected.is_finite() or rounded != expected:
            raise CompanyAttributionError("attribution_binding_mismatch", "登记网页摘要数与 PDF 精确值不满足舍入核对。")
        used = observations[1:] if key == "current_cfo" else observations
        return {"id": key, "status": "consistent_rounded", "input_ids": [o["id"] for o in used],
                "inputs": [{"observation_id": o["id"], "issuer_id": o["issuer_id"], "year": o["year"],
                            "value": o["normalized_value"], "raw_value": o["raw_value"], "unit": o["unit"],
                            "period": deepcopy(o["period"]), "source": deepcopy(o["source"])} for o in used],
                "formula": "current_cfo / 1000" if key == "current_cfo" else "(current_cfo - previous_cfo) / 1000",
                "exact_value": format(exact, "f"), "exact_unit": "USD_million",
                "conversion_divisor": "1000", "converted_value": format(converted, "f"),
                "rounded_value": format(rounded, "f"), "reported_rounded_value": reported[key],
                "reported_unit": reported["unit"], "currency": reported["currency"],
                "precision": reported["precision"], "rounding": "ROUND_HALF_UP",
                "rounding_note": "本工具按 0.1 十亿美元精度核对；不推定披露者明示了相同舍入算法。",
                "exact_match": converted == expected, "rounding_delta_billion": format(expected - converted, "f"),
                "check_scope": "registered_numeric_consistency_only", "independently_verified": False}


def build_company_attribution(task: dict, root: Path | None = None) -> dict:
    """Assess a new supplement for the current task without editing any snapshot."""
    try:
        acceptance = build_task_acceptance(task)
    except ResearchAcceptanceError as exc:
        raise CompanyAttributionError(exc.code, exc.message) from exc
    catalog = load_company_attribution_catalog(root)
    decisions = {d["observation_id"]: d["decision"] for d in task["decisions"]}
    entries = []
    for registered in catalog["entries"]:
        observations = sorted((o for o in task["bundle"]["observations"]
                               if o["issuer_id"] == registered["issuer_id"] and o["metric"] == registered["metric"]),
                              key=lambda o: o["year"])
        if (len(observations) != 2 or [o["year"] for o in observations] != [registered["previous_year"], registered["current_year"]]
                or registered["reported_numbers"]["unit"] != "USD_billion"
                or registered["reported_numbers"]["currency"] != "USD"
                or registered["reported_numbers"]["precision"] != "0.1"):
            raise CompanyAttributionError("attribution_binding_mismatch", "公司、指标、期间或网页金额单位未绑定。")
        for observation, period in zip(observations, ("previous", "current")):
            if (observation["company"] != registered["company"] or observation["unit"] != "USD_million"
                    or observation["source"]["sha256"] != registered["pdf_source_sha256"]
                    or observation["period"] != registered["periods"][period]
                    or observation["basis"]["operations_scope"] != "reported_consolidated_statement_total"):
                raise CompanyAttributionError("attribution_binding_mismatch", "精确财务值的来源、范围或实际期间与解释登记不符。")
        numeric_checks = [_numeric_check(key, observations, registered["reported_numbers"]) for key in ("current_cfo", "cfo_change")]
        ids = [o["id"] for o in observations]
        statuses = [decisions.get(oid, "unassessed") for oid in ids]
        link_status = ("blocked" if "rejected" in statuses else "consistent_rounded"
                       if all(status == "adopted" for status in statuses) else "awaiting_input_adoption")
        gaps = [
            {"code": "independent_corroboration_missing", "message": "公司解释尚未独立核实；数值相符不能证明原因。", "observation_ids": ids},
            {"code": "supplement_not_in_frozen_snapshot", "message": "这是当前只读补充，未纳入既有冻结底稿或其复核批准。", "observation_ids": []},
        ]
        if link_status != "consistent_rounded":
            gaps.append({"code": "input_rejected" if link_status == "blocked" else "input_not_adopted",
                         "message": "相关 CFO 被拒绝用于此任务。" if link_status == "blocked" else "须明确采用两期 CFO 后才能关联本任务。",
                         "observation_ids": [oid for oid, status in zip(ids, statuses) if status != "adopted"]})
        entries.append({"id": registered["id"], "issuer_id": registered["issuer_id"], "company": registered["company"],
                        "metric": registered["metric"], "periods": deepcopy(registered["periods"]),
                        "source": {**deepcopy(registered["source"]), "registration_verified": True, "html_revalidated_this_call": False},
                        "paraphrase_zh": registered["paraphrase_zh"], "qualifiers": deepcopy(registered["qualifiers"]),
                        "numeric_checks": numeric_checks, "evidence_ids": ids,
                        "input_adoption": [{"observation_id": oid, "status": status} for oid, status in zip(ids, statuses)],
                        "link_status": link_status, "gaps": gaps, "independently_verified": False, "automatic_approval": False})
    return {"schema_version": 1, "rule_version": ATTRIBUTION_RULE_VERSION,
            "task_id": task["id"], "revision": task["revision"], "bundle_sha256": task["bundle_sha256"],
            "input_binding": deepcopy(acceptance["input_binding"]), "catalog_sha256": _digest(catalog),
            "entries": entries, "coverage": deepcopy(catalog["coverage"]), "limitations": deepcopy(catalog["limitations"]),
            "read_only": True, "included_in_frozen_snapshot": False, "independently_verified": False,
            "automatic_approval": False, "historical_snapshot_rewritten": False,
            "natural_language_question_understood": False, "acceptance_rule_version": acceptance["rule_version"]}
