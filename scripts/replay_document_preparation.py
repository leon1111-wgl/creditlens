"""Reproduce three synthetic preparation attempts in disposable local state."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from creditlens.batch_attempt_store import BatchAttemptStore
from creditlens.preparation_examples import preparation_examples
from creditlens.workflow import Actor
from scripts.evaluate_currency_diagnostic import output_path


def run(output):
    target=Path(output)
    if not target.is_absolute() and target.parent==Path('.'):
        target=ROOT/'.run/reproductions'/target
    target=output_path(ROOT,target)
    samples=preparation_examples(ROOT)
    actor=Actor('simulated-preparation-analyst','synthetic-preparation','analyst')
    with tempfile.TemporaryDirectory(prefix='creditlens-preparation-') as directory:
        path=Path(directory)/'attempts.sqlite'
        store=BatchAttemptStore(path,ROOT)
        first=store.create(actor,samples['examples']['missing'],'create-missing')
        assert first['status']=='blocked'
        old=store.export(first['id'],actor,first['latest_attempt']['id'])
        assert store.create(actor,samples['examples']['missing'],'create-missing')==first
        second=store.retry(actor,first['id'],samples['examples']['damaged'],1,'retry-damaged')
        assert second['status']=='blocked'
        middle=store.export(first['id'],actor,second['latest_attempt']['id'])
        # New service object, same committed database. No interrupted worker.
        resumed=BatchAttemptStore(path,ROOT)
        assert resumed.export(first['id'],actor,first['latest_attempt']['id'])==old
        third=resumed.retry(actor,first['id'],samples['examples']['complete'],2,'retry-complete')
        assert third['status']=='prepared_not_indexed' and third['revision']==3
        exports=[old,middle,resumed.export(first['id'],actor,third['latest_attempt']['id'])]
        assert resumed.export(first['id'],actor,first['latest_attempt']['id'])==old
        for item in exports:
            serialized=json.dumps(item,ensure_ascii=False)
            assert 'SAMPLE' not in serialized and not item['queryable']
        assert b'SAMPLE' not in path.read_bytes()
        report={'schema_version':1,'kind':'synthetic_document_preparation_workflow',
            'provenance':'synthetic','model_inference_performed':False,'source_bytes_read':False,
            'scope':'Committed diagnostic history and explicit retry, not background worker recovery or financial approval.',
            'checks':{'missing_page_blocked':True,'damaged_page_blocked':True,'corrected_batch_prepared':True,
              'create_replay_exact':True,'old_attempt_export_unchanged':True,'new_store_reads_committed_history':True,
              'raw_marker_absent_from_database_and_exports':True,'temporary_database_retained':False},
            'attempt_exports':exports}
    target=output_path(ROOT,target);target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({'output':str(target),'attempts':len(exports),'checks':report['checks']},ensure_ascii=False))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='document-preparation-replay.json')
    run(parser.parse_args().output)
