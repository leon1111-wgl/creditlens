"""Recompute all reviewed issuer facts and independent synthetic currency controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.currency_diagnostic import build_currency_diagnostic, validate_currency_diagnostic
from creditlens.experiment_paths import validate_write_path


def output_path(root: Path, output: Path) -> Path:
    root = Path(root).resolve()
    for directory in (root/".run", root/".run/reproductions"):
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            raise ValueError("Currency diagnostic directories cannot be filesystem aliases")
    target = validate_write_path(root, output)
    if target.suffix.lower() != ".json" or target.exists() or target.parent != (root/".run/reproductions").resolve():
        raise ValueError("Currency diagnostic must create a new .json directly inside .run/reproductions")
    for relative in (".run/document-ai", ".run/document-transfer", ".run/transfer-attempts"):
        protected = (root/relative).resolve()
        if target.is_relative_to(protected) or protected.is_relative_to(target):
            raise ValueError("Cannot write into original experiment evidence")
    return target


def run(output: Path | None = None) -> dict:
    selected = Path(output) if output is not None else Path("currency-diagnostic-report.json")
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = ROOT/".run/reproductions"/selected
    target = output_path(ROOT, selected)
    report = build_currency_diagnostic(ROOT)
    validate_currency_diagnostic(ROOT, report)
    target = output_path(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(target), "summary": report["summary"], "synthetic_controls": report["controls"]["summary"]}, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New .json directly inside repository .run/reproductions")
    args = parser.parse_args(argv)
    try:
        run(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Currency diagnostic failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
