"""Build the fixed NVIDIA transfer reference from PDF text, never OCR output.

The full issuer PDF and page renders remain local. This single-reference adapter
uses declared labels/date columns, not expected amounts, to locate the cells.
It writes a private candidate; publishing/freezing remains a separate review.
"""
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import Fact, digest, displayed_amount
from creditlens.document import EvidenceError, box, lines, pdfium_tokens, phrase
from creditlens.experiment_paths import validate_experiment_paths, validate_write_path

URL = "https://s201.q4cdn.com/141608511/files/doc_financials/2025/annual/NVIDIA-2025-Annual-Report.pdf"
PDF_SHA256 = "757b344ab62ba282f6e44e0702e8ce5f190d88fb23b5e0a2beaf4cb277a395cc"
PARSER_VERSION = "nvda-inline-dates-reference-1"
PERIODS = {2025: ("2024-01-29", "2025-01-26"), 2024: ("2023-01-30", "2024-01-28")}
TABLES = [
    {"page": 146, "printed_page": 54, "title": "Consolidated Balance Sheets",
     "unit": "(In millions, except par value)",
     "dates": ["Jan 26, 2025", "Jan 28, 2024"],
     "metrics": [
         {"metric": "cash_equivalents", "label": "现金及现金等价物", "row": ["Cash and cash equivalents"],
          "section": ["Current assets:", "Total current assets"]},
         {"metric": "short_investments", "label": "流动资产中的有价证券", "row": ["Marketable securities"],
          "section": ["Current assets:", "Total current assets"]},
         {"metric": "current_assets", "label": "流动资产", "row": ["Total current assets"]},
         {"metric": "current_liabilities", "label": "流动负债", "row": ["Total current liabilities"]},
         {"metric": "long_debt", "label": "长期债务", "row": ["Long-term debt"]},
     ]},
    {"page": 148, "printed_page": 56, "title": "Consolidated Statements of Cash Flows",
     "unit": "(In millions)", "duration_header": "Year Ended",
     "dates": ["Jan 26, 2025", "Jan 28, 2024", "Jan 29, 2023"],
     "metrics": [
         {"metric": "cfo", "label": "经营活动现金流", "row": ["Net cash provided by operating activities"]},
         {"metric": "net_income", "label": "净利润", "row": ["Net income"]},
         {"metric": "capex", "label": "购建固定资产与无形资产现金流出",
          "row": ["Purchases related to property and equipment and intangible", "assets"]},
     ]},
]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")


def verified_source(path: Path, download=False):
    if not path.is_file():
        if not download:
            raise EvidenceError("Official source missing; use --download or provide the registered local PDF")
        registered = ROOT.resolve()/"data/raw/nvda-fy2025-annual.pdf"
        destination = path.expanduser().resolve()
        metadata = destination.with_suffix(".download.json")
        if destination != registered or metadata.resolve() != registered.with_suffix(".download.json"):
            raise EvidenceError("New downloads are restricted to the registered cache without filesystem aliases")
        if metadata.exists() or metadata.is_symlink():
            raise EvidenceError("Download metadata already exists; refusing to replace it")
        with urllib.request.urlopen(URL, timeout=60) as response:
            raw = response.read(60_000_001)
            resolved_url = response.url
        if len(raw) > 60_000_000 or not raw.startswith(b"%PDF-") or sha256(raw).hexdigest() != PDF_SHA256:
            raise EvidenceError("Downloaded PDF differs from the registered source")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(raw)
        with metadata.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"requested_url": URL, "resolved_url": resolved_url,
                       "retrieved_at": datetime.now(timezone.utc).isoformat(), "sha256": PDF_SHA256,
                       "bytes": len(raw)}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    if path.stat().st_size > 60_000_000 or sha256(path.read_bytes()).hexdigest() != PDF_SHA256:
        raise EvidenceError("Registered PDF bytes changed")


def extract_page(page, spec):
    rows = lines(pdfium_tokens(page))
    title, _ = phrase(rows, spec["title"])
    unit, _ = phrase(rows, spec["unit"])
    header, _ = phrase(rows, " ".join(spec["dates"]))
    if not title[0].cy < unit[0].cy < header[0].cy:
        raise EvidenceError("Statement anchors out of order")
    if spec.get("duration_header"):
        duration, _ = phrase(rows, spec["duration_header"])
        if not unit[0].cy < duration[0].cy < header[0].cy:
            raise EvidenceError("Duration anchor out of order")
    date_tokens, index = [], 0
    for value in spec["dates"]:
        count = len(value.split())
        selected = header[index:index+count]
        if [token.text for token in selected] != value.split():
            raise EvidenceError("Date tokens changed")
        date_tokens.append(selected)
        index += count
    centers = [(group[0].x0+group[-1].x1)/2 for group in date_tokens]
    edges = [centers[0]-(centers[1]-centers[0])/2]
    edges += [(left+right)/2 for left, right in zip(centers, centers[1:])]
    edges += [centers[-1]+(centers[-1]-centers[-2])/2]
    if edges[0] <= 0 or edges[-1] >= page.get_width() or any(b-a < 20 for a, b in zip(edges, edges[1:])):
        raise EvidenceError("Invalid date-column geometry")
    facts = []
    for metric in spec["metrics"]:
        label_rows, label_tokens = [], []
        for fragment in metric["row"]:
            candidates = rows
            if label_rows:
                candidates = [row for row in rows if label_rows[-1][0].cy < row[0].cy <= label_rows[-1][0].cy+18]
            row, tokens = phrase(candidates, fragment, prefix=True)
            label_rows.append(row)
            label_tokens.extend(tokens)
        if min(t.cy for t in label_tokens) <= header[0].cy or max(t.x1 for t in label_tokens) >= edges[0]:
            raise EvidenceError("Row label crosses the declared date columns")
        row_tokens = [token for row in label_rows for token in row]
        section = None
        if metric.get("section"):
            start_label, end_label = metric["section"]
            start, _ = phrase(rows, start_label)
            end, end_tokens = phrase(rows, end_label, prefix=True)
            if not start[0].cy < label_rows[0][0].cy < end[0].cy:
                raise EvidenceError("Row is outside the intended statement section")
            section = {"start_label": start_label, "end_label": end_label,
                       "start_bbox": box(start), "end_bbox": box(end_tokens)}
        for column, date_text in enumerate(spec["dates"]):
            year = int(date_text.split()[-1])
            if year not in PERIODS:
                continue
            selected = [token for token in row_tokens if edges[column] <= token.x0 < token.x1 <= edges[column+1]
                        and token.text != "$"]
            if len(selected) != 1:
                raise EvidenceError("Expected one unambiguous numeric token in the fixed row/date column")
            raw = selected[0].text
            amount = displayed_amount(raw)
            is_flow = metric["metric"] in {"cfo", "net_income", "capex"}
            loc = {"page": spec["page"], "page_width": page.get_width(), "page_height": page.get_height(),
                   "coordinate_system": "pdf_points_top_left", "amount_bbox": box(selected),
                   "row_label": " ".join(metric["row"]), "row_label_bbox": box(label_tokens), "year": year,
                   "year_bbox": box([date_tokens[column][-1]]), "period_header": date_text,
                   "period_bbox": box(date_tokens[column]), "unit_header": spec["unit"], "unit_bbox": box(unit),
                   "parser_version": PARSER_VERSION}
            if section:
                loc["section"] = section
            facts.append(Fact.model_validate({"id": f"{metric['metric']}-{year}", "metric": metric["metric"],
                "label": metric["label"], "value": str(abs(amount) if metric["metric"] == "capex" else amount),
                "unit": "USD_million", "period_start": PERIODS[year][0] if is_flow else None,
                "period_end": PERIODS[year][1], "source_id": "nvda-fy25-annual-wrapper",
                "locator": f"Annual-report PDF p.{spec['page']} (10-K printed {spec['printed_page']}) / {loc['row_label']} / FY{year}",
                "raw_value": raw, "transformation": "absolute_cash_outflow" if metric["metric"] == "capex" else "displayed_magnitude",
                "pdf_location": loc}))
    return facts


def build(pdf_path: Path, render_dir: Path | None = None):
    import pypdfium2 as pdfium
    verified_source(pdf_path)
    facts, pages = [], []
    with pdfium.PdfDocument(pdf_path) as pdf:
        if len(pdf) != 181:
            raise EvidenceError("Unexpected annual-wrapper page count")
        for spec in TABLES:
            page = pdf[spec["page"]-1]
            try:
                if page.get_rotation() != 0:
                    raise EvidenceError("Rotated pages need a separately registered reference")
                selected = extract_page(page, spec)
                facts.extend(selected)
                metadata = {"page": spec["page"], "printed_page": spec["printed_page"], "width": page.get_width(),
                            "height": page.get_height(), "fact_ids": [f.id for f in selected]}
                if render_dir is not None:
                    render_dir.mkdir(parents=True, exist_ok=True)
                    bitmap = page.render(scale=2)
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                    path = render_dir/f"page-{spec['page']}.png"
                    image.save(path)
                    metadata.update(image_width=image.width, image_height=image.height,
                                    image_sha256=sha256(path.read_bytes()).hexdigest())
                pages.append(metadata)
            finally:
                page.close()
    if len(facts) != 16 or len({f.id for f in facts}) != 16:
        raise EvidenceError("The declared reference inventory changed")
    for fact in facts:
        if fact.period_start and (fact.period_end-fact.period_start).days+1 != 364:
            raise EvidenceError("Selected fiscal flow period must contain 52 weeks")
    pack = {"schema_version": 1, "purpose": "known_location_transfer", "entity_id": "NVDA",
            "company": "NVIDIA Corporation", "title": "FY2025 NVIDIA 新发行人文档迁移参考（非时点研究数据）",
            "sources": [{"id": "nvda-fy25-annual-wrapper", "title": "NVIDIA FY2025 annual report wrapper / underlying Form 10-K",
                "url": URL, "sha256": PDF_SHA256, "wrapper_publication_date": None,
                "underlying_filing_date": "2025-02-26", "source_date_basis": "wrapper_publication_unknown",
                "point_in_time_eligible": False}], "facts": [f.model_dump(mode="json") for f in facts],
            "fiscal_calendar": "week_52_53"}
    return pack, {"pages": pages, "pack_canonical_sha256": digest(pack), "parser_version": PARSER_VERSION,
                  "table_selection_sha256": digest(TABLES), "pypdfium2_version": str(pdfium.PYPDFIUM_INFO),
                  "pdfium_version": str(pdfium.PDFIUM_INFO), "reference_preparation": "text_layer_and_visual_review_not_ocr",
                  "unscored": [{"page": 146, "row": "Short-term debt", "reason": "FY2025 dash notation is not a numeric Fact; neither year is scored in this transfer. Do not fill zero."}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=ROOT/"data/raw/nvda-fy2025-annual.pdf")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT/".run/nvda-reference/reference-pack.json")
    parser.add_argument("--render-dir", type=Path, default=ROOT/".run/nvda-reference/pages")
    args = parser.parse_args()
    paths = validate_experiment_paths(ROOT, args.output, args.render_dir,
                                      read_only_dirs=(args.pdf.parent, ROOT/".run/document-transfer"))
    output, render_dir = paths.output, paths.local_dir
    provenance_path = validate_write_path(ROOT, output.with_suffix(".provenance.json"))
    verified_source(args.pdf, args.download)
    pack, provenance = build(args.pdf, render_dir)
    write_json(output, pack)
    write_json(provenance_path, provenance)
    print(json.dumps({"facts": len(pack["facts"]), "pack_canonical_sha256": digest(pack), "pages": provenance["pages"]}))


if __name__ == "__main__":
    main()
