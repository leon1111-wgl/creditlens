"""Replay unconditional amount guards; no network, OCR, or frozen-file writes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.evidence_guard_diagnostic import build_evidence_guard_diagnostic, validate_evidence_guard_diagnostic
from scripts.evaluate_currency_diagnostic import output_path


def run(output: Path | None = None) -> dict:
    selected = Path(output) if output is not None else Path("evidence-guard-report.json")
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = ROOT/".run/reproductions"/selected
    target = output_path(ROOT, selected)
    report = build_evidence_guard_diagnostic(ROOT)
    validate_evidence_guard_diagnostic(ROOT, report)
    target = output_path(ROOT, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(target), "summary": report["summary"], "synthetic_controls": report["controls"]["summary"]}, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON directly inside repository .run/reproductions")
    args = parser.parse_args(argv)
    try:
        run(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Evidence guard diagnostic failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
