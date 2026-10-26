"""Replay explicit row choices from retained OCR, or verify saved small evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.experiment_paths import validate_write_path
from creditlens.row_disambiguation_experiment import (
    build_row_disambiguation_report, validate_row_disambiguation_report, load_row_disambiguation_snapshot,
)


def output_path(root, path):
    root = Path(root).resolve(); directory = root/".run/reproductions"; path = Path(path)
    if directory.resolve() != directory:
        raise ValueError("Output directory aliases are unsupported")
    if not path.is_absolute(): path = directory/path
    target = validate_write_path(root, path)
    if target.parent != directory or target.suffix != ".json" or target.exists() or target.is_symlink():
        raise ValueError("Only a new JSON directly inside .run/reproductions is allowed")
    return target


def run(ocr_root=None, output=None, *, root=ROOT):
    target = output_path(root, output or "row-disambiguation-report.json")
    report = build_row_disambiguation_report(root, ocr_root)
    validate_row_disambiguation_report(root, report)
    target = output_path(root, target); target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False); stream.write("\n")
    return {"output": str(target), "summary": report["summary"], "controls": report["controls"]["summary"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-root", type=Path, help="Existing registered six-WordPage directory; no download")
    parser.add_argument("--output", type=Path, help="New JSON directly inside .run/reproductions")
    parser.add_argument("--verify-saved", action="store_true", help="Verify published evidence without full OCR input")
    args = parser.parse_args(argv)
    if args.verify_saved and (args.ocr_root is not None or args.output is not None):
        parser.error("--verify-saved cannot be combined with --ocr-root or --output")
    try:
        if args.verify_saved:
            snapshot = load_row_disambiguation_snapshot(ROOT)
            result = {"verified": True, "report_sha256": snapshot["report_sha256"], "full_ocr_replayed": False,
                      "summary": snapshot["report"]["summary"], "validation_scope": snapshot["validation_scope"]}
        else:
            result = run(args.ocr_root, args.output, root=ROOT)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError):
        parser.exit(1, "Explicit row replay failed; no input was downloaded or overwritten.\n")


if __name__ == "__main__":
    main()
