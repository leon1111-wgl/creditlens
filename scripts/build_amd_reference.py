"""Build a fixed AMD text-layer reference before observing any AMD OCR output.

Only declared labels, section boundaries and date-column geometry select cells.
The official PDF, complete tokens, unmarked renders and verification overlays
stay local. This script writes a candidate; release freezing is a separate step.
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
from creditlens.document import EvidenceError, PDF_GEOMETRY_VERSION, box, lines, pdfium_tokens, phrase
from creditlens.experiment_paths import validate_experiment_paths, validate_write_path

URL = "https://ir.amd.com/financial-information/sec-filings/content/0001193125-26-129106/0001193125-26-129106.pdf"
PDF_SHA256 = "c7dac911d91ed1b38afa3fbf3b3408cbd0c105d1c820a4b6fa15f553e2892187"
PARSER_VERSION = "amd-stacked-dates-reference-1"
SOURCE_ID = "amd-fy25-ir-annual"
PERIODS = {2025: ("2024-12-29", "2025-12-27"), 2024: ("2023-12-31", "2024-12-28")}
SOURCE = {
    "id": SOURCE_ID, "title": "AMD FY2025 annual report on official investor-relations website",
    "url": URL, "sha256": PDF_SHA256, "underlying_filing_date": "2026-02-04",
    "wrapper_filing_date": "2026-03-27",
    "wrapper_filing_evidence_url": "https://www.sec.gov/Archives/edgar/data/2488/000119312526129106/0001193125-26-129106-index.htm",
    "exact_asset_publication_date": None, "first_available_at": None,
    "source_date_basis": "exact_asset_publication_unknown", "publication_evidence_url": None,
    "publication_note": "The issuer IR ARS listing associates this 124-page annual report with 2026-03-27, also the SEC ARS filing date. The SEC PDF attachment differs in bytes from the current IR asset; first public availability of these exact IR bytes is not established. The 10-K filing event is not this asset's publication date.",
    "revision_note": "The underlying 10-K and 10-K/A (0000002488-26-000021) were filed on 2026-02-04. The amendment corrects the swapped Client unit-shipment and ASP growth percentages; the IR wrapper body already contains the corrected 15% and 31% figures. This does not mean a separate complete 10-K/A is appended. Note 2 says some FY2024 and FY2023 amounts were reclassified: these are FY2025-presented comparisons, not original FY2024 observations. Equivalence of the current IR asset and the SEC ARS attachment is not asserted.",
    "retrieved_at": "2026-10-25T08:22:40.588016+00:00",
    "historical_snapshot_verified": False, "point_in_time_eligible": False,
}
TABLES = [
    {"page": 74, "printed_page": 62, "title": "Consolidated Balance Sheets",
     "years": [2025, 2024], "dates": ["December 27,", "December 28,"],
     "unit_rows": ["(In millions, except par value", "amounts)"],
     "metrics": [
         {"metric": "cash_equivalents", "label": "现金及现金等价物", "row": "Cash and cash equivalents",
          "scope": "consolidated_balance_sheet", "section": ["Current assets:", "Total current assets"]},
         {"metric": "current_assets", "label": "流动资产", "row": "Total current assets", "scope": "consolidated_balance_sheet"},
         {"metric": "current_liabilities", "label": "流动负债", "row": "Total current liabilities", "scope": "consolidated_balance_sheet"},
         {"metric": "long_debt", "label": "长期债务（扣除流动部分后的净额）", "row": "Long-term debt, net of current portion", "scope": "noncurrent_debt_net"},
     ]},
    {"page": 76, "printed_page": 64, "title": "Consolidated Statements of Cash Flows",
     "years": [2025, 2024, 2023], "dates": ["December 27,", "December 28,", "December 30,"],
     "unit_rows": ["(In millions)"], "duration_header": "Year Ended",
     "metrics": [
         {"metric": "cfo", "label": "持续经营活动现金流", "row": "Net cash provided by operating activities of continuing operations", "scope": "continuing_operations"},
         {"metric": "net_income", "label": "整体净利润（包含终止经营）", "row": "Net income", "scope": "consolidated_including_discontinued_operations"},
         {"metric": "capex", "label": "持续经营购建固定资产现金流出", "row": "Purchases of property and equipment",
          "scope": "continuing_operations", "section": ["Cash flows from investing activities:", "Net cash used in investing activities of continuing operations"]},
     ]},
]
EXCLUSIONS = [
    {"selection": "FY2023 comparison column", "reason": "Only the latest two fiscal years were selected before OCR. The third column remains visible to the model and is validated for column geometry."},
    {"selection": "Total and discontinued operating cash flow", "reason": "Continuing operating cash flow was selected to match continuing capital expenditure. This is a scope decision made before OCR, not a recognition-quality exclusion."},
    {"selection": "Discontinued-operations capital expenditure", "reason": "The second repeated capex row belongs to discontinued investing activities. FY2024 displays a dash, which is not converted to zero. Both years are outside the preselected continuing-operations scope."},
    {"selection": "Current portion of long-term debt", "reason": "The fixed seven-metric inventory uses noncurrent debt. FY2024 current debt is a dash, which is not converted to zero; the financial debt coverage is incomplete."},
    {"selection": "Other rows, non-cash capex and short-term investments", "reason": "Outside the declared seven-metric, two-year inventory. No other row was selected or discarded using OCR output."},
]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")


def verified_source(path: Path, download=False):
    path = Path(path)
    if not path.is_file():
        if not download:
            raise EvidenceError("Official source missing; use --download for the registered cache")
        registered = ROOT.resolve()/"data/raw/amd-fy2025-annual.pdf"
        destination = path.expanduser().resolve()
        metadata = destination.with_suffix(".download.json")
        if destination != registered or metadata.resolve() != registered.with_suffix(".download.json"):
            raise EvidenceError("Downloads require the registered cache without filesystem aliases")
        if metadata.exists() or metadata.is_symlink():
            raise EvidenceError("Existing download metadata will not be replaced")
        with urllib.request.urlopen(URL, timeout=60) as response:
            raw = response.read(10_000_001)
            resolved_url = response.url
        if len(raw) > 10_000_000 or not raw.startswith(b"%PDF-") or sha256(raw).hexdigest() != PDF_SHA256:
            raise EvidenceError("Downloaded source differs from the registered PDF")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(raw)
        with metadata.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"requested_url": URL, "resolved_url": resolved_url,
                       "retrieved_at": datetime.now(timezone.utc).isoformat(),
                       "sha256": PDF_SHA256, "bytes": len(raw)}, stream, indent=2)
            stream.write("\n")
    if path.stat().st_size > 10_000_000 or sha256(path.read_bytes()).hexdigest() != PDF_SHA256:
        raise EvidenceError("Registered PDF bytes changed")


def _row(rows, label):
    """Select an exact label followed only by cell/presentation tokens.

    Prefix similarity cannot select 'total' instead of 'continuing operations'.
    Leader dots may lie on a separate baseline or on the same row; they are
    presentation tokens, not financial digits or expected-value search keys.
    """
    words = label.split()
    matches = []
    for row in rows:
        if [t.text for t in row[:len(words)]] != words:
            continue
        try:
            for token in row[len(words):]:
                if token.text not in {".", "$", "\u2014"}:
                    displayed_amount(token.text)
        except ValueError:
            continue
        matches.append((row, row[:len(words)]))
    if len(matches) != 1:
        raise EvidenceError("Missing or ambiguous exact row label: "+label)
    return matches[0]


def extract_tokens(tokens, spec, *, width, height):
    rows = lines(tokens)
    title, _ = phrase(rows, spec["title"])
    years, _ = phrase(rows, " ".join(str(y) for y in spec["years"]))
    dates, _ = phrase(rows, " ".join(spec["dates"]))
    units = [phrase(rows, text)[0] for text in spec["unit_rows"]]
    if not (title[0].cy < dates[0].cy < years[0].cy < units[0][0].cy and
            years[0].cy-dates[0].cy <= 18):
        raise EvidenceError("Stacked date and unit anchors changed order")
    if any(not a[0].cy < b[0].cy <= a[0].cy+18 for a,b in zip(units,units[1:])):
        raise EvidenceError("Unit-header continuation is missing or displaced")
    if spec.get("duration_header"):
        duration, _ = phrase(rows, spec["duration_header"])
        if not title[0].cy < duration[0].cy < dates[0].cy:
            raise EvidenceError("Fiscal duration header is misplaced")
    centers = [(t.x0+t.x1)/2 for t in years]
    edges = [centers[0]-(centers[1]-centers[0])/2]
    edges += [(a+b)/2 for a,b in zip(centers,centers[1:])]
    edges += [centers[-1]+(centers[-1]-centers[-2])/2]
    if edges[0] <= 0 or edges[-1] >= width or any(b-a<20 for a,b in zip(edges,edges[1:])):
        raise EvidenceError("Date-column geometry is invalid")
    date_groups = []
    for index, label in enumerate(spec["dates"]):
        group = [t for t in dates if edges[index] <= t.x0 < t.x1 <= edges[index+1]]
        if [t.text for t in group] != label.split():
            raise EvidenceError("Date text crosses or mismatches its year column")
        date_groups.append(group)
    facts = []
    for metric in spec["metrics"]:
        candidates, section = rows, None
        if metric.get("section"):
            start_label, end_label = metric["section"]
            start, start_tokens = _row(rows,start_label)
            end, end_tokens = _row(rows,end_label)
            if not units[-1][0].cy < start[0].cy < end[0].cy:
                raise EvidenceError("Financial section boundaries are out of order")
            candidates = [row for row in rows if start[0].cy < row[0].cy < end[0].cy]
            section = dict(start_label=start_label,end_label=end_label,
                           start_bbox=box(start_tokens),end_bbox=box(end_tokens))
        row, label_tokens = _row(candidates,metric["row"])
        if row[0].cy <= units[-1][0].cy or max(t.x1 for t in label_tokens) >= edges[0]:
            raise EvidenceError("Row label is outside the declared statement layout")
        if any(t.text=="." and t.x1>=edges[0] for t in row[len(label_tokens):]):
            raise EvidenceError("A dot inside a value column is not a removable leader")
        # Never ignore an unknown suffix or select a substring matching a gold
        # number. All remaining non-presentation cells must form the full row.
        cells = [t for t in row[len(label_tokens):] if t.text not in {".","$"}]
        if len(cells) != len(years):
            raise EvidenceError("Financial row contains missing or extra amount cells")
        for i,(year,year_token,cell) in enumerate(zip(spec["years"],years,cells)):
            if not edges[i] <= cell.x0 < cell.x1 <= edges[i+1]:
                raise EvidenceError("Amount lies outside its declared date column")
            if year not in PERIODS:
                continue
            amount = displayed_amount(cell.text)
            flow = metric["metric"] in {"cfo","net_income","capex"}
            loc = dict(page=spec["page"],page_width=width,page_height=height,
                coordinate_system="pdf_points_top_left",amount_bbox=box([cell]),
                row_label=metric["row"],row_label_bbox=box(label_tokens),year=year,
                year_bbox=box([year_token]),period_header=f"{spec['dates'][i]} {year}",
                period_bbox=box(date_groups[i]+[year_token]),
                unit_header=" ".join(spec["unit_rows"]),unit_bbox=box([t for group in units for t in group]),
                parser_version=PARSER_VERSION)
            if section:
                loc["section"] = section
            facts.append(Fact.model_validate(dict(
                id=f"{metric['metric']}-{year}",metric=metric["metric"],label=metric["label"],
                value=str(abs(amount) if metric["metric"]=="capex" else amount),unit="USD_million",
                period_start=PERIODS[year][0] if flow else None,period_end=PERIODS[year][1],
                source_id=SOURCE_ID,locator=f"Annual PDF p.{spec['page']} (printed {spec['printed_page']}) / {metric['row']} / {metric['scope']} / FY{year}",
                raw_value=cell.text,transformation="absolute_cash_outflow" if metric["metric"]=="capex" else "displayed_magnitude",
                pdf_location=loc)))
    return facts


def build(pdf_path: Path, render_dir: Path | None = None):
    verified_source(pdf_path)
    if render_dir is not None:
        render_dir = validate_write_path(ROOT,render_dir,directory=True)
    import pypdfium2 as pdfium
    from PIL import __version__ as pillow_version
    facts, pages = [], []
    with pdfium.PdfDocument(pdf_path) as pdf:
        if len(pdf) != 124:
            raise EvidenceError("Unexpected registered annual-report page count")
        for spec in TABLES:
            page = pdf[spec["page"]-1]
            try:
                tokens = pdfium_tokens(page)  # Includes visible-box and rotation checks.
                selected = extract_tokens(tokens,spec,width=page.get_width(),height=page.get_height())
                facts.extend(selected)
                metadata = dict(page=spec["page"],printed_page=spec["printed_page"],
                    width=page.get_width(),height=page.get_height(),visible_bbox=list(page.get_bbox()),
                    media_box=list(page.get_mediabox()),crop_box=list(page.get_cropbox()),rotation=page.get_rotation(),
                    fact_ids=[f.id for f in selected],text_token_count=len(tokens))
                if render_dir is not None:
                    render_dir.mkdir(parents=True,exist_ok=True)
                    bitmap=page.render(scale=2)
                    try:
                        image=bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                    path=render_dir/f"page-{spec['page']}.png"
                    image.save(path,format="PNG")
                    metadata.update(image_width=image.width,image_height=image.height,
                                    image_sha256=sha256(path.read_bytes()).hexdigest())
                pages.append(metadata)
            finally:
                page.close()
    if len(facts)!=14 or len({f.id for f in facts})!=14:
        raise EvidenceError("The preselected fourteen-fact inventory changed")
    for fact in facts:
        if fact.period_start and (fact.period_end-fact.period_start).days+1 != 364:
            raise EvidenceError("Selected flow period is not the disclosed 52-week year")
    pack = dict(schema_version=1,purpose="frozen_issuer_evaluation",entity_id="AMD",
        company="Advanced Micro Devices, Inc.",title="AMD FY2025 新发行人固定参考（仅文档评测，非历史时点数据）",
        sources=[SOURCE],facts=[f.model_dump(mode="json") for f in facts],fiscal_calendar="week_52_53")
    provenance = dict(pages=pages,parser_version=PARSER_VERSION,geometry_version=PDF_GEOMETRY_VERSION,
        pack_canonical_sha256=digest(pack),table_selection_sha256=digest(TABLES),source_pdf_sha256=PDF_SHA256,
        render=dict(engine="pypdfium2",version=str(pdfium.PYPDFIUM_INFO),pillow_version=pillow_version,
                    scale=2.0,rotation=0,image_format="PNG"),pdfium_version=str(pdfium.PDFIUM_INFO),
        reference_preparation="text_layer_and_visual_review_before_amd_ocr",unscored=EXCLUSIONS,
        metric_scopes={m["metric"]:m["scope"] for spec in TABLES for m in spec["metrics"]},
        limitation="Evaluation only. Net income includes discontinued operations while CFO and capex are continuing-operations amounts; do not calculate a mixed-scope cash-conversion ratio or assert consolidated free cash flow from this pack.")
    return pack,provenance


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf",type=Path,default=ROOT/"data/raw/amd-fy2025-annual.pdf")
    parser.add_argument("--download",action="store_true")
    parser.add_argument("--output",type=Path,default=ROOT/".run/amd-reference/reference-pack.json")
    parser.add_argument("--render-dir",type=Path,default=ROOT/".run/amd-reference/pages")
    args=parser.parse_args()
    paths=validate_experiment_paths(ROOT,args.output,args.render_dir,
        read_only_dirs=(args.pdf.parent,ROOT/".run/document-transfer",ROOT/".run/issuer-evaluation"))
    provenance_path=validate_write_path(ROOT,paths.output.with_suffix(".provenance.json"))
    verified_source(args.pdf,args.download)
    pack,provenance=build(args.pdf,paths.local_dir)
    write_json(paths.output,pack);write_json(provenance_path,provenance)
    print(json.dumps(dict(facts=len(pack["facts"]),pack_canonical_sha256=digest(pack),pages=provenance["pages"])))


if __name__=="__main__":
    main()
