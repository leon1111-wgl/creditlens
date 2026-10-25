"""Compare fixed table lookups using existing local OCR; never download or infer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.table_lookup_experiment import build_table_lookup_report, load_table_lookup_snapshot, validate_table_lookup_report
from scripts.evaluate_currency_diagnostic import output_path


def run(ocr_root: Path | None = None, output: Path | None = None) -> dict:
    selected = Path(output) if output is not None else Path("table-lookup-report.json")
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = ROOT/".run/reproductions"/selected
    target = output_path(ROOT, selected)
    report = build_table_lookup_report(ROOT, ocr_root)
    validate_table_lookup_report(ROOT, report)
    target = output_path(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(target), "summary": report["summary"], "controls": report["controls"]["summary"]}, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-root", type=Path, help="Existing root containing msft/ocr-50.json etc.; default .run/document-ai")
    parser.add_argument("--output", type=Path, help="New .json directly inside .run/reproductions")
    parser.add_argument("--verify-saved", action="store_true", help="Verify only the published small report; no full OCR read or extraction")
    args = parser.parse_args(argv)
    if args.verify_saved and (args.ocr_root is not None or args.output is not None):
        parser.error("--verify-saved cannot be combined with --ocr-root or --output")
    try:
        if args.verify_saved:
            snapshot = load_table_lookup_snapshot(ROOT)
            print(json.dumps({"verified": True, "report_sha256": snapshot["report_sha256"],
                              "full_ocr_replayed": False, "summary": snapshot["report"]["summary"]}, ensure_ascii=False))
        else:
            run(args.ocr_root, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Table lookup replay failed: " + str(exc).splitlines()[0] + "; no data were downloaded.\n")


if __name__ == "__main__":
    main()
