"""Reproduce fixed input controls without models, source downloads or writes to old reports."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from creditlens.input_diagnostic import build_input_diagnostic, input_diagnostic_view
from scripts.evaluate_currency_diagnostic import output_path


def run(output=None):
    target=Path(output) if output is not None else Path("document-input-checks.json")
    if not target.is_absolute() and target.parent==Path('.'):
        target=ROOT/'.run/reproductions'/target
    target=output_path(ROOT,target)
    report=build_input_diagnostic(ROOT)
    input_diagnostic_view(ROOT,report)
    target=output_path(ROOT,target)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output':str(target),'summary':report['summary']},ensure_ascii=False))
    if report['summary']['raw_passed']!=report['summary']['raw_cases'] or report['summary']['asset_passed']!=report['summary']['asset_cases']:
        raise ValueError("Fixed controls failed; complete candidate diagnostics were saved for inspection, not publication")
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    run(args.output)
