"""Replay role-aware lookup from retained WordPages or verify its saved report."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.experiment_paths import validate_write_path
from creditlens.role_table_experiment import build_role_table_lookup_report, validate_role_table_lookup_report, load_role_table_lookup_snapshot


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
    target = output_path(root, output or "role-table-lookup-report.json")
    report = build_role_table_lookup_report(root, ocr_root)
    validate_role_table_lookup_report(root, report)
    target = output_path(root, target); target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False); stream.write("\n")
    return {"output": str(target), "summary": report["summary"], "transitions": report["transitions"], "controls": report["controls"]["summary"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr-root", type=Path, help="Existing directory containing six registered WordPage JSON files")
    parser.add_argument("--output", type=Path, help="New JSON directly inside .run/reproductions")
    parser.add_argument("--verify-saved", action="store_true", help="Check saved evidence and replay synthetic controls; no complete OCR read")
    args = parser.parse_args(argv)
    if args.verify_saved and (args.ocr_root is not None or args.output is not None):
        parser.error("--verify-saved cannot be combined with --ocr-root or --output")
    try:
        if args.verify_saved:
            snapshot = load_role_table_lookup_snapshot(ROOT)
            result = {"verified": True, "report_sha256": snapshot["report_sha256"], "full_ocr_replayed": False,
                      "summary": snapshot["report"]["summary"], "validation_scope": snapshot["validation_scope"]}
        else:
            result = run(args.ocr_root, args.output, root=ROOT)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError):
        parser.exit(1, "Role-table replay failed; no input was downloaded or overwritten.\n")


if __name__ == "__main__":
    main()
