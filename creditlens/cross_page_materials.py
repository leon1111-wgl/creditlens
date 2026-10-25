"""Pinned cross-statement evidence cards; no OCR, remote reads or client facts."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re

from .diligence import CasePack, digest, displayed_amount
from .research_materials import MaterialsError, build_material_bundle


MATERIALS_VERSION = "cross-page-materials-1"
MATERIALS_ID = "msft-aapl-cfo-revenue-fy2025"
REGISTERED_AT = "2026-10-26"
SCOPE = {"consolidation": "consolidated", "statement_basis": "as_reported_statement_total",
         "operations_partition": "unknown"}
_SOURCE_EXPECTATIONS = {
    "MSFT": {"pack_path": "data/examples/msft-fy2025-pdf.json",
             "pack_sha256": "bd776c972631cbc6ff6e0cba585cdab2c32d875d21e950ae43987ffc6a033850",
             "report_sha256": "1d13a76168650bb5efb42f29c6da81aa673e44217d082357ec0815afc70abf2e",
             "revenue_page": 50, "cfo_page": 53, "scope_page": 55,
             "revenue_label": "Total revenue",
             "scope_quote": "The consolidated financial statements include the accounts of Microsoft Corporation and its subsidiaries."},
    "AAPL": {"pack_path": "data/examples/aapl-fy2025-pdf.json",
             "pack_sha256": "e26e93213bd8041fdd93b228749a468ce241952e673776cb5fa37ad11f2fec43",
             "report_sha256": "3eb270b22acb7d8d8e9c32a43dc221dec3345f8e4bca02755fdbc1ee16c823de",
             "revenue_page": 32, "cfo_page": 36, "scope_page": 37,
             "revenue_label": "Total net sales",
             "scope_quote": "The consolidated financial statements include the accounts of Apple Inc. and its wholly owned subsidiaries."},
}
LIMITATIONS = [
    "4 个任务、12 张角色卡来自 8 个金额事实与 2 个共享范围短锚；角色卡不是 12 个独立标注样本。",
    "仅关联固定 FY2025 报告中的收入表、现金流量表和 Note 1 合并基础；不是同一张跨页续表的自动解析。",
    "FY2024 是 FY2025 报告中的比较列，不代表 FY2024 当时可得版本；不支持历史时点回放。",
    "金额框复用已有文本层参考；范围依据仅有页级定位，不虚构附注文字框。此前登记为工具辅助定位，独立人工确认 pending。",
    "经营现金流净额占营业收入比例是描述性报表比例，不是收现率或销售回款率，不证实经济原因或盈利质量。",
    "分子与分母仅按合并报表原行总额对应，持续/终止经营分解未知，不推断该分解已对齐。",
    "不跨公司排名；微软与苹果财年日期和业务结构不同。未重读 PDF 字节、未运行 OCR 或模型，也不鉴定来源真实性。",
]


class CrossPageMaterialsError(ValueError):
    def __init__(self, code: str, path: str):
        self.code, self.path = code, path
        super().__init__(f"{code}: {path}")


def _require(condition, code: str, path: str):
    if not condition:
        raise CrossPageMaterialsError(code, path)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _read_pack(root: Path, source: dict, expected: dict) -> CasePack:
    relative = expected["pack_path"]
    _require(source["pack_path"] == relative and source["pack_sha256"] == expected["pack_sha256"]
             and source["sha256"] == expected["report_sha256"], "source_registry_changed", relative)
    path = root / relative
    try:
        _require(path.resolve().is_relative_to(root), "source_path_escape", relative)
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
        _require(digest(data) == expected["pack_sha256"], "source_pack_changed", relative)
        return CasePack.model_validate(data)
    except CrossPageMaterialsError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise CrossPageMaterialsError("source_pack_unavailable", relative) from exc


def _card_base(source: dict, year: int, role: str, period: dict) -> dict:
    return {
        "schema_version": 1, "id": f"{source['issuer_id']}:{source['sha256']}:{role}-{year}",
        "label": {"cfo": "经营现金流净额", "revenue": "营业收入", "consolidation_scope": "Note 1 合并基础"}[role],
        "role": role, "issuer_id": source["issuer_id"], "report_sha256": source["sha256"],
        "year": year, "period": deepcopy(period), "unit": "USD_million" if role != "consolidation_scope" else None,
        "financial_scope": deepcopy(SCOPE),
        "source": {"id": source["source_id"], "title": source["title"], "url": source["url"]},
        "availability": {"registered_at": REGISTERED_AT, "source_published_at": source["published_at"],
                         "source_date_basis": "registered_source_date", "historical_verified": False},
        "provenance": {"kind": "registered_official_disclosure",
                       "reference_method": "复用此前工具辅助定位的固定参考；独立人工确认 pending；本次未运行 OCR。",
                       "independent_human_verified": False, "model_output_verified": False},
    }


def _amount_card(source: dict, fact, year: int, period: dict) -> dict:
    location = fact.pdf_location.model_dump(mode="json")
    card = _card_base(source, year, fact.metric, period)
    printed = re.search(r"\(printed (\d+)\)", fact.locator)
    card.update(value_status="disclosed", raw_value=fact.raw_value, normalized_value=format(fact.value, "f"),
                location={"page": location["page"], "printed_page": printed.group(1) if printed else None,
                          "locator": fact.locator, "page_width": location["page_width"], "page_height": location["page_height"],
                          "amount_bbox": location["amount_bbox"], "year_bbox": location["year_bbox"], "unit_bbox": location["unit_bbox"]})
    return card


def build_cross_page_materials(root: Path) -> dict:
    """Construct twelve cards from two immutable registered fact packs.

    The first registered bundle is bound to strict pack reads; the second read
    rejects an observable source change during assembly. No PDF bytes are read.
    """
    root = Path(root).resolve()
    try:
        bundle = build_material_bundle(root)
        sources = {s["issuer_id"]: s for s in bundle["source_versions"]}
        _require(len(sources) == len(bundle["source_versions"]) == 2 and set(sources) == set(_SOURCE_EXPECTATIONS),
                 "source_registry_changed", "source_versions")
        tasks, cards, bindings = [], [], []
        for issuer_id, expected in _SOURCE_EXPECTATIONS.items():
            source = sources[issuer_id]
            pack = _read_pack(root, source, expected)
            _require(pack.entity_id == issuer_id and len(pack.sources) == 1 and pack.sources[0].sha256 == source["sha256"],
                     "source_binding_mismatch", issuer_id)
            facts = {f.id: f for f in pack.facts}
            task_ids, card_ids, scope_refs = [], [], []
            for year in (2024, 2025):
                pair = [facts[f"{role}-{year}"] for role in ("cfo", "revenue")]
                cfo, revenue = pair
                period = {"start": cfo.period_start.isoformat(), "end": cfo.period_end.isoformat()}
                for role, fact in zip(("cfo", "revenue"), pair):
                    _require(fact.metric == role and fact.source_id == source["source_id"] and fact.unit == "USD_million"
                             and fact.transformation == "displayed_magnitude" and fact.pdf_location is not None
                             and fact.pdf_location.year == year and fact.pdf_location.page == expected[f"{role}_page"]
                             and fact.period_start == cfo.period_start and fact.period_end == cfo.period_end
                             and displayed_amount(fact.raw_value) == fact.value,
                             "fact_basis_mismatch", f"{issuer_id}.{fact.id}")
                _require(revenue.pdf_location.row_label == expected["revenue_label"], "fact_basis_mismatch", revenue.id)
                observed = next(o for o in bundle["observations"] if o["issuer_id"] == issuer_id and o["fact_id"] == cfo.id)
                _require(observed["raw_value"] == cfo.raw_value and observed["normalized_value"] == format(cfo.value, "f")
                         and observed["source"]["location"] == cfo.pdf_location.model_dump(mode="json")
                         and all(observed["period"][k] == period[k] for k in period),
                         "cfo_bundle_mismatch", observed["id"])
                scope = next(e for e in observed["basis"]["scope_evidence"] if e["field"] == "consolidation_scope")
                _require(scope["source_sha256"] == source["sha256"] and scope["value"] == "consolidated"
                         and scope["page"] == expected["scope_page"] and scope["quote"] == expected["scope_quote"],
                         "scope_registry_changed", issuer_id)
                year_cards = [_amount_card(source, fact, year, period) for fact in pair]
                scope_card = _card_base(source, year, "consolidation_scope", period)
                scope_card["provenance"]["reference_method"] = (
                    "复用此前工具辅助定位的 Note 1 合并基础；卡片 year/period 是任务适用上下文，"
                    "不是短锚逐字包含两年度日期；独立人工确认 pending。")
                scope_card.update(value_status="text_evidence", raw_value=None, normalized_value=None,
                    location={"page": scope["page"], "printed_page": None,
                              "locator": f"PDF p.{scope['page']} / Note 1 / Basis of presentation: {scope['quote']} "
                                         "范围说明复用该报告 Note 1，不是分别出具的年度审计结论；此前工具辅助定位，独立人工确认 pending。",
                              "page_width": None, "page_height": None, "amount_bbox": None, "year_bbox": None, "unit_bbox": None})
                year_cards.append(scope_card)
                cards.extend(year_cards)
                task_id = f"{issuer_id.lower()}-cfo-revenue-{year}"
                tasks.append({"schema_version": 1, "id": task_id,
                    "title": f"{pack.company} FY{year} 经营现金流净额占营业收入比例",
                    "issuer_id": issuer_id, "report_sha256": source["sha256"], "year": year,
                    "period": period, "unit": "USD_million", "financial_scope": deepcopy(SCOPE),
                    "required_role_ids": {card["role"]: card["id"] for card in year_cards},
                    "availability": {"mode": "current_verified_materials", "as_of": REGISTERED_AT}})
                task_ids.append(task_id)
                card_ids.extend(card["id"] for card in year_cards)
                scope_refs.append({"task_id": task_id, "scope_card_id": scope_card["id"],
                                   "existing_observation_id": observed["id"], "page": scope["page"], "quote": scope["quote"],
                                   "reference_method": "tool_assisted_prior_reference", "independent_human_confirmation": "pending"})
            bindings.append({"issuer_id": issuer_id, "source_id": source["source_id"], "report_sha256": source["sha256"],
                "pack_path": source["pack_path"], "pack_sha256": source["pack_sha256"], "pack_hash_basis": "canonical_json_sha256",
                "task_ids": task_ids, "card_ids": card_ids, "scope_references": scope_refs,
                "raw_pdf_bytes_checked": False, "historical_version_verified": False})
        _require(digest(bundle) == digest(build_material_bundle(root)), "source_changed_during_read", "source_versions")
    except CrossPageMaterialsError:
        raise
    except MaterialsError as exc:
        raise CrossPageMaterialsError("registered_material_unavailable", "source_versions") from exc
    except (KeyError, StopIteration, AttributeError, TypeError, ValueError) as exc:
        raise CrossPageMaterialsError("invalid_registered_material", "source_versions") from exc
    result = {"schema_version": 1, "rule_version": MATERIALS_VERSION, "id": MATERIALS_ID,
              "tasks": tasks, "cards": cards, "source_bindings": bindings, "limitations": list(LIMITATIONS)}
    result["material_sha256"] = digest(result)
    return result
