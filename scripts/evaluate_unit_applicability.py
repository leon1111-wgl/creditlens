"""Replay the unit guard from published small evidence; never run OCR."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from creditlens.unit_applicability_experiment import build_unit_applicability_report,load_unit_applicability_snapshot
from scripts.evaluate_row_disambiguation import output_path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--output",type=Path,help="New JSON directly inside .run/reproductions")
    group.add_argument("--verify-saved",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.verify_saved:
            snapshot=load_unit_applicability_snapshot(ROOT);report=snapshot["report"]
        else:
            target=output_path(ROOT,args.output or "unit-applicability-report.json")
            report=build_unit_applicability_report(ROOT)
            target=output_path(ROOT,target);target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf8",newline="\n") as stream:
                json.dump(report,stream,ensure_ascii=False,separators=(",",":"),allow_nan=False);stream.write("\n")
        print(json.dumps({"verified":True,"summary":report["summary"],"controls":report["controls"]["summary"],"full_ocr_replayed":False},ensure_ascii=False));return 0
    except (ValueError,OSError):
        print(json.dumps({"verified":False,"code":"unit_applicability_validation_failed"}));return 1


if __name__=="__main__":raise SystemExit(main())
