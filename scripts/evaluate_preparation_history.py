"""Replay historical checks against small isolated registration changes.

The changes are synthetic, not real issuer revisions. No PDF/image/OCR fetched.
"""
from copy import deepcopy
from hashlib import sha256
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from creditlens.preparation_verifier import verify_preparation_export
from creditlens.preparation_applicability import assess_preparation_applicability
from scripts.evaluate_currency_diagnostic import output_path


def _write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')


def run(output):
    target=Path(output)
    if not target.is_absolute() and target.parent==Path('.'):
        target=ROOT/'.run/reproductions'/target
    target=output_path(ROOT,target)
    raw=(ROOT/'artifacts/document-preparation-replay.json').read_bytes()
    bundle=json.loads(raw)
    verified=[verify_preparation_export(v) for v in bundle['attempt_exports']]
    cases=[]
    for record in verified:
        result=assess_preparation_applicability(ROOT,record)
        assert result['status']=='matching_registration'
        assert result['ready_for_content_review']==(record['preparation_status']=='prepared_not_indexed')
        cases.append({'case':'unchanged_attempt_'+str(record['revision']),'assessment':result})

    with tempfile.TemporaryDirectory(prefix='creditlens-registration-') as directory:
        work=Path(directory)
        manifest=json.loads((ROOT/'configs/document-transfer.json').read_text(encoding='utf-8'))
        paths={'configs/document-transfer.json',*manifest['files'],*manifest['reference_files'],
               *(d['pack_path'] for d in manifest['documents'])}
        for name in sorted(paths):
            source=(ROOT/name).resolve();dest=(work/name).resolve()
            if not source.is_relative_to(ROOT) or not dest.is_relative_to(work):
                raise ValueError('Registration path escaped its workspace')
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,dest)
        record=verified[-1]
        assert assess_preparation_applicability(work,record)['status']=='matching_registration'
        changed=deepcopy(manifest)
        changed['selection_reason']='Synthetic registry editorial update for reproducible compatibility checks.'
        _write(work/'configs/document-transfer.json',changed)
        result=assess_preparation_applicability(work,record)
        assert result['status']=='inventory_changed' and not result['ready_for_content_review']
        cases.append({'case':'synthetic_catalog_note_change','assessment':result})
        doc=next(d for d in changed['documents'] if d['id']==record['document_id'])
        pack=work/doc['pack_path']
        data=json.loads(pack.read_text(encoding='utf-8'));data['title']='Synthetic registration title edit, not an issuer revision'
        _write(pack,data);doc['pack_sha256']=sha256(pack.read_bytes()).hexdigest()
        _write(work/'configs/document-transfer.json',changed)
        result=assess_preparation_applicability(work,record)
        assert result['status']=='document_registration_changed' and not result['ready_for_content_review']
        cases.append({'case':'synthetic_selected_pack_change','assessment':result})
        (work/'configs/document-transfer.json').write_text('{ invalid synthetic registry',encoding='utf-8')
        result=assess_preparation_applicability(work,record)
        assert result['status']=='registration_unavailable' and not result['ready_for_content_review']
        cases.append({'case':'synthetic_unreadable_registry','assessment':result})
    # Verifying the original history never depends on the altered registry.
    assert verified==[verify_preparation_export(v) for v in bundle['attempt_exports']]
    report={'schema_version':1,'kind':'historical_preparation_registration_controls',
      'provenance':'synthetic_registration_changes_with_existing_synthetic_attempts',
      'source_replay_sha256':sha256(raw).hexdigest(),'cases':cases,'passed':len(cases),
      'original_exports_unchanged':True,'raw_source_downloaded':False,'model_inference_performed':False,
      'scope':'Internal consistency and current registered metadata; not authenticated source, actual PDF revision, OCR correctness or financial approval.'}
    target=output_path(ROOT,target);target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({'cases':len(cases),'output':str(target),'old_exports_unchanged':True}))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default='preparation-history-checks.json')
    run(parser.parse_args().output)
