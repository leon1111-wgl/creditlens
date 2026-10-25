"""Extract every operating cash-flow reconciliation row from two pinned PDFs.

Offline, positioned text only. No OCR, amount-based cell search or downloads.
The command writes a NEW local candidate; it cannot replace a registered source.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.cash_flow_sources import (
    DATASET_ID, SOURCE_CONTRACT, SOURCE_SPECS, canonical_sha256, row_section, validate_source_bundle,
)
from creditlens.document import (
    EvidenceError, box, extract_stacked_table, extract_table, lines, pdfium_tokens, phrase,
)
from creditlens.experiment_paths import validate_write_path
from creditlens.research_materials import build_material_bundle


YEARS = [2025, 2024, 2023]


def validate_operating_rows(tokens, spec):
    """Require the entire operating section, including headings, in exact order.

    This prevents a zero row, cancelling pair or unregistered extra row being
    silently omitted just because the chosen subset happens to reconcile.
    """
    rows = lines(tokens)
    first, _ = phrase(rows, spec["start"])
    last, _ = phrase(rows, spec["end"])
    if first[0].cy >= last[0].cy:
        raise EvidenceError("Operating section boundaries are out of order")
    section = [r for r in rows if first[0].cy < r[0].cy < last[0].cy]
    expected, previous_role = [], None
    for _, role, label, _ in spec["rows"]:
        if role == "reconciliation_adjustments" and previous_role != role:
            expected.append((spec["adjustment_heading"], False))
        if role == "operating_assets_and_liabilities" and previous_role != role:
            expected.append((spec["change_heading"], False))
        expected.append((label, True))
        previous_role = role
    if len(section) != len(expected):
        raise EvidenceError("Operating section has missing or extra rows")
    for row, (label, numeric) in zip(section, expected):
        label_words = label.split()
        if [t.text for t in row[:len(label_words)]] != label_words:
            raise EvidenceError("Operating row label/order differs: " + label)
        remainder = [t.text for t in row[len(label_words):]]
        if numeric:
            amounts = [word for word in remainder if word != "$"]
            if len(amounts) != len(YEARS) or len(remainder)-len(amounts) not in (0, len(YEARS)):
                raise EvidenceError("Operating row does not have three complete amount columns: " + label)
            # Sign/notation checks and column alignment are performed by the
            # positioned extractor below, never by reference amount lookup.
        elif remainder:
            raise EvidenceError("Unexpected numeric/text content in an operating heading")
    return {"start_label": spec["start"], "end_label": spec["end"],
            "start_bbox": box(first), "end_bbox": box(last)}


def extract_operating_page(tokens, *, spec, width, height):
    validate_operating_rows(tokens, spec)
    common = {"page": spec["page"], "width": width, "height": height,
              "title": spec["title"], "unit_header": "(In millions)", "years": YEARS}
    if "date_headers" in spec:
        metrics = {key: {"label": label, "section": list(row_section(spec, role))}
                   for key, role, label, _ in spec["rows"]}
        extracted = extract_stacked_table(tokens, **common, date_headers=spec["date_headers"],
                                         duration_header="Years ended", metrics=metrics)
    else:
        extracted = extract_table(tokens, **common, period_header=spec["period_header"],
                                  metrics={key: label for key, _, label, _ in spec["rows"]})
        rows, roles = lines(tokens), {key: role for key, role, _, _ in spec["rows"]}
        for item in extracted:
            start, end = row_section(spec, roles[item["metric"]])
            start_row, _ = phrase(rows, start)
            end_row, _ = phrase(rows, end)
            item["pdf_location"]["section"] = {"start_label": start, "end_label": end,
                                                  "start_bbox": box(start_row), "end_bbox": box(end_row)}
    return extracted


def verified_inputs(pdf_dir: Path, materials: dict) -> dict[str, Path]:
    """Verify all source bytes before loading optional PDF code or extracting."""
    pdf_dir = Path(pdf_dir).resolve()
    sources = {s["issuer_id"]: s for s in materials["source_versions"]}
    paths = {}
    for spec in SOURCE_SPECS:
        path = pdf_dir/spec["filename"]
        if not path.resolve().is_relative_to(pdf_dir) or not path.is_file():
            raise EvidenceError("Missing source or PDF path escapes input directory")
        if path.stat().st_size > 64*1024*1024:
            raise EvidenceError("Registered PDF exceeds 64 MiB safety bound")
        sha, size = hashlib.sha256(), 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024*1024), b""):
                size += len(chunk)
                if size > 64*1024*1024:
                    raise EvidenceError("Registered PDF exceeds 64 MiB safety bound")
                sha.update(chunk)
        if sha.hexdigest() != sources[spec["issuer_id"]]["sha256"]:
            raise EvidenceError("Registered PDF SHA256 mismatch: " + spec["issuer_id"])
        paths[spec["issuer_id"]] = path
    return paths


def assemble_bundle(extracted: dict[str, list[dict]], materials: dict) -> dict:
    """Attach source registration and old CFO identity after extraction."""
    source_map = {s["issuer_id"]: s for s in materials["source_versions"]}
    cfo_map = {(o["issuer_id"], o["year"]): o for o in materials["observations"] if o["metric"] == "cfo"}
    sources, statements = [], []
    for spec in SOURCE_SPECS:
        issuer = spec["issuer_id"]
        source = source_map[issuer]
        sources.append({**{k: source[k] for k in ("id", "issuer_id", "source_id", "title", "url", "sha256", "published_at")},
            "retrieved_at": spec["retrieved_at"], "filing_metadata_url": spec["filing_metadata_url"],
            "document_pages": spec["document_pages"], "cash_flow_page": spec["page"], "printed_page": spec["printed_page"],
            "license": "Public issuer filing; no general redistribution grant. Original PDF, page images and full extracted text stay local.",
            "server_trusted": True, "registered_source_hash_verified": True, "raw_pdf_rehashed": False,
            "build_pdf_sha256_verified": True, "historical_version_verified": False})
        records = extracted[issuer]
        by_key = {(r["metric"], r["year"]): r for r in records}
        expected_keys = {(row[0], year) for row in spec["rows"] for year in YEARS}
        if set(by_key) != expected_keys or len(records) != len(expected_keys):
            raise EvidenceError("Missing, repeated or unexpected extracted operating cells")
        for year in (2024, 2025):
            cfo = cfo_map[issuer, year]
            facts = []
            for row_key, role, label_en, label in spec["rows"]:
                record = by_key[row_key, year]
                location = deepcopy(record["pdf_location"])
                facts.append({"id": f"{source['id']}:cf-bridge-{row_key}-{year}", "row_key": row_key,
                    "role": role, "label": label, "label_en": label_en,
                    "raw_value": record["raw_value"], "signed_value": record["value"],
                    "source_sha256": source["sha256"], "page": spec["page"],
                    "bbox": deepcopy(location["amount_bbox"]), "location": location})
            statements.append({"id": f"{issuer}:cfo-bridge:{year}", "issuer_id": issuer, "company": cfo["company"],
                "year": year, "period": deepcopy(cfo["period"]), "unit": cfo["unit"],
                "scope": "reported_consolidated_statement_total", "basis": deepcopy(cfo["basis"]),
                "source_id": source["id"], "source_sha256": source["sha256"],
                "reported_cfo_observation_id": cfo["id"], "total_fact_id": facts[-1]["id"],
                "expected_row_keys": [r[0] for r in spec["rows"] if r[1] != "cfo_total"], "facts": facts})
    result = {"schema_version": 1, "id": DATASET_ID, "title": "微软/苹果经营现金流会计调节资料，非严格同行基准",
              "material_mode": "current_verified_materials", "source_versions": sources, "statements": statements,
              "limitations": [
                  "固定 FY2025 官方报告及其 FY2024 比较列，非实时数据；未证明当前 PDF 字节在历史报告日已可得。",
                  "分组沿用现金流表原有分区：净利润调节项并非全为非现金，经营资产负债变化并非全为狭义营运资本。",
                  "这是有符号金额的会计分解；项目变动不是经验证的经济原因、风险因果或预测。",
                  "仅报表列示合并经营现金流总额，持续/终止经营细分未知；不补造该分解。",
                  "微软和苹果财年结束日期、日历及经营结构不同；不据此跨公司排名。",
                  "构建时核对原 PDF 字节和完整分区；运行时只验证冻结 JSON 与旧 CFO 登记，不重新读取 PDF，也不鉴定真实性。",
              ], "contract": deepcopy(SOURCE_CONTRACT)}
    validate_source_bundle(result, materials)
    return result


def build(root: Path, pdf_dir: Path) -> dict:
    materials = build_material_bundle(root)
    inputs = verified_inputs(pdf_dir, materials)
    import pypdfium2 as pdfium
    extracted = {}
    for spec in SOURCE_SPECS:
        with pdfium.PdfDocument(inputs[spec["issuer_id"]]) as pdf:
            if len(pdf) != spec["document_pages"]:
                raise EvidenceError("Unexpected registered PDF page count")
            page = pdf[spec["page"]-1]
            try:
                extracted[spec["issuer_id"]] = extract_operating_page(
                    pdfium_tokens(page), spec=spec, width=page.get_width(), height=page.get_height())
            finally:
                page.close()
    return assemble_bundle(extracted, materials)


def validate_output(root: Path, output: Path, pdf_dir: Path) -> Path:
    path = validate_write_path(root, output)
    protected = [Path(pdf_dir).resolve()] + [(Path(root)/".run"/name).resolve()
                  for name in ("document-ai", "document-transfer", "issuer-evaluation")]
    if any(path.is_relative_to(p) or p.is_relative_to(path) for p in protected):
        raise ValueError("Candidate output cannot overwrite inputs or registered page evidence")
    if path.exists():
        raise ValueError("Candidate output already exists; choose a new local filename")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=ROOT/".run/cash-flow-bridge-input")
    parser.add_argument("--output", type=Path, default=ROOT/".run/reproductions/cash-flow-bridge-v1.json")
    args = parser.parse_args(argv)
    try:
        output = validate_output(ROOT, args.output, args.pdf_dir)
        data = build(ROOT, args.pdf_dir)
        payload = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+"\n").encode("utf-8")
        # Recheck aliases and use exclusive creation; a rerun cannot silently
        # replace prior evidence. This is not an OS concurrent-write sandbox.
        validate_output(ROOT, output, args.pdf_dir)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(payload)
        print(json.dumps({"status": "built", "dataset_id": DATASET_ID, "statements": len(data["statements"]),
                          "facts": sum(len(s["facts"]) for s in data["statements"]),
                          "canonical_sha256": canonical_sha256(data), "output": str(output)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
