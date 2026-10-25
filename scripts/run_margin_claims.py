"""Replay registered company margin statements and independent arithmetic controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.experiment_paths import validate_write_path
from creditlens.margin_claim_experiment import build_margin_claim_report, load_margin_claim_snapshot, validate_margin_claim_report


def output_path(root, path):
    root, path = Path(root).resolve(), Path(path)
    directory = root/".run/reproductions"
    if directory.resolve() != directory:
        raise ValueError("Reproduction directory aliases are unsupported")
    if not path.is_absolute():
        path = directory/path
    target = validate_write_path(root, path)
    if target.parent != directory or target.suffix != ".json":
        raise ValueError("Only a new JSON directly inside .run/reproductions is allowed")
    if target.exists() or target.is_symlink():
        raise ValueError("Output already exists")
    return target


def run(root=ROOT, output=None):
    target = output_path(root, output or "margin-claims-report.json")
    report = build_margin_claim_report(root)
    validate_margin_claim_report(root, report)
    target = output_path(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        stream.write("\n")
    return {"output": str(target), "summary": report["summary"], "controls": report["controls"]["summary"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON filename directly inside .run/reproductions")
    parser.add_argument("--verify-saved", action="store_true", help="Replay the saved statement checks without fetching HTML or running OCR")
    args = parser.parse_args(argv)
    if args.verify_saved and args.output is not None:
        parser.error("--verify-saved cannot be combined with --output")
    try:
        if args.verify_saved:
            snapshot = load_margin_claim_snapshot(ROOT)
            result = {"verified": True, "report_sha256": snapshot["report_sha256"],
                      "margin_report_sha256": snapshot["margin_report_sha256"], "summary": snapshot["report"]["summary"],
                      "validation_scope": snapshot["validation_scope"]}
        else:
            result = run(output=args.output)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError):
        parser.exit(1, "Margin statement replay failed; no source or existing report was modified.\n")


if __name__ == "__main__":
    main()
