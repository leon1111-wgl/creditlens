"""Recompute a date-rule diagnostic from the already reviewed transfer release."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.date_diagnostic import build_date_diagnostic, validate_date_diagnostic
from creditlens.experiment_paths import validate_write_path


def output_path(root: Path, output: Path) -> Path:
    for directory in (root/".run", root/".run/reproductions"):
        if directory.is_symlink() or (hasattr(directory, "is_junction") and directory.is_junction()):
            raise ValueError("Date diagnostic output directories cannot be filesystem aliases")
    target = validate_write_path(root, output)
    if target.suffix.lower() != ".json" or target.exists():
        raise ValueError("Date diagnostic output must be a new .json file")
    if target.parent != (root/".run/reproductions").resolve():
        raise ValueError("Date diagnostic reports must be directly inside .run/reproductions")
    protected = (".run/document-ai", ".run/document-transfer", ".run/transfer-attempts",
                 ".run/reproductions/document-transfer", ".run/reproductions/document-ai",
                 ".run/reproductions/document-ai-words", ".run/reproductions/visual-robustness")
    for relative in protected:
        source = (root/relative).resolve()
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise ValueError("Date diagnostic cannot write into original experiment or preview directories")
    return target


def run(output: Path | None = None) -> dict:
    selected = Path(output) if output is not None else Path("date-diagnostic-report.json")
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = ROOT/".run/reproductions"/selected
    target = output_path(ROOT, selected)
    report = build_date_diagnostic(ROOT)
    validate_date_diagnostic(ROOT, report)
    # All checks precede the first write. Exclusive creation protects existing
    # files; this preflight is not a sandbox against concurrent path changes.
    target = output_path(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"operation": report["scope"]["operation"], "summary": report["summary"],
                      "output": str(target)}, ensure_ascii=False))
    return report


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New .json directly inside the repository .run/reproductions")
    args = parser.parse_args(argv)
    try:
        run(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Date diagnostic failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
