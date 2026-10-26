"""HTTP retries use isolated SQLite and synthetic actors with registered facts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.diligence import digest
from scripts.replay_research_write_retry import research_update, explanation_update, action_body, metrics, NOTE, build_replay, validate_replay, REPORT

ROOT=Path(__file__).resolve().parents[1]


class ResearchWriteRetryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(tempfile.TemporaryDirectory()); self.root=Path(self.temp)
        users=[('author','writer','one','analyst'),('editor','editor','one','analyst'),('reviewer','reviewer','one','reviewer'),
            ('viewer','viewer','one','viewer'),('self-reviewer','writer','one','reviewer'),('other','outsider','two','analyst')]
        principals=[{'key_sha256':hashlib.sha256(key.encode()).hexdigest(),'user_id':user,'tenant_id':tenant,'role':role}
                    for key,user,tenant,role in users]
        self.app=create_app(self.root/'missing.json',self.root/'audit.sqlite',auth_mode='api_key',principals=principals)
        self.client=self.enterContext(TestClient(self.app,raise_server_exceptions=False))

    @staticmethod
    def headers(user='author',key=None):
        headers={'Authorization':'Bearer '+user}
        if key is not None:headers['Idempotency-Key']=key
        return headers

    def create(self,key='http-parent-create'):
        response=self.client.post('/api/research/tasks',headers=self.headers(key=key),json={
            'question':'How should a saved research write recover after an absent response?',
            'selection_reason':'Synthetic software fault controls using registered official materials.',
            'include_cash_flow_bridge':True})
        self.assertEqual(response.status_code,201,response.text);return response.json()

    def call(self,method,base,body,key,user='author'):
        return self.client.request(method,base,headers=self.headers(user,key),json=body)

    def repeat(self,method,base,body,key,user='author'):
        first=self.call(method,base,body,key,user)
        self.assertEqual(first.status_code,200,first.text)
        # Only the harness retains these bytes. The simulated caller uses retry.
        route=base.removesuffix('/actions')
        before=self.client.get(route,headers=self.headers('viewer')).content
        retry=self.call(method,base,body,key,user)
        self.assertEqual(retry.status_code,200,retry.text);self.assertEqual(retry.content,first.content)
        self.assertEqual(self.client.get(route,headers=self.headers('viewer')).content,before)
        return retry.json(),first.content

    def approved_parent(self):
        task=self.create();base='/api/research/tasks/'+task['id']
        task,_=self.repeat('PATCH',base,research_update(task),'parent-save-one')
        task,_=self.repeat('POST',base+'/actions',action_body(task,'submit'),'parent-submit-one')
        task,_=self.repeat('POST',base+'/actions',action_body(task,'approve'),'parent-approve-one','reviewer')
        return task

    def child(self,parent):
        response=self.client.post('/api/research/tasks/'+parent['id']+'/explanation-reviews',headers=self.headers('editor','child-create-one'),
            json={'parent_revision':parent['revision'],'parent_snapshot_sha256':parent['snapshot_sha256']})
        self.assertEqual(response.status_code,201,response.text);return response.json()

    def test_parent_save_submit_return_resubmit_approve_and_late_replay(self):
        task=self.create();base='/api/research/tasks/'+task['id'];saved_request=research_update(task)
        task,old_save=self.repeat('PATCH',base,saved_request,'parent-save-one')
        submit_request=action_body(task,'submit')
        task,old_submit=self.repeat('POST',base+'/actions',submit_request,'parent-submit-one')
        task,_=self.repeat('POST',base+'/actions',action_body(task,'return'),'parent-return-one','reviewer')
        previous=task['snapshot']['id']
        old_export=self.client.get(base+'/export',params={'snapshot_id':previous},headers=self.headers('viewer')).content
        task,_=self.repeat('PATCH',base,research_update(task,' Updated coverage.'),'parent-save-two')
        task,_=self.repeat('POST',base+'/actions',action_body(task,'submit'),'parent-submit-two')
        task,_=self.repeat('POST',base+'/actions',action_body(task,'approve'),'parent-approve-one','reviewer')
        self.assertEqual(self.call('PATCH',base,saved_request,'parent-save-one').content,old_save)
        self.assertEqual(self.call('POST',base+'/actions',submit_request,'parent-submit-one').content,old_submit)
        self.assertEqual(metrics(self.client.get(base,headers=self.headers('viewer')).json()),
            {'revision':7,'status':'reviewed','events':7,'snapshots':2})
        self.assertEqual(self.client.get(base+'/export',params={'snapshot_id':previous},headers=self.headers('viewer')).content,old_export)

    def test_child_all_write_paths_preserve_parent_and_previous_export(self):
        parent=self.approved_parent();parentbase='/api/research/tasks/'+parent['id']
        parent_export=self.client.get(parentbase+'/export',headers=self.headers('viewer')).content
        child=self.child(parent);base='/api/research/explanation-reviews/'+child['id'];save=explanation_update(child)
        child,old_save=self.repeat('PATCH',base,save,'child-save-one','editor')
        submit=action_body(child,'submit')
        child,old_submit=self.repeat('POST',base+'/actions',submit,'child-submit-one','editor')
        child,_=self.repeat('POST',base+'/actions',action_body(child,'return'),'child-return-one','reviewer')
        previous=child['snapshot']['id'];old=self.client.get(base+'/export',params={'snapshot_id':previous},headers=self.headers('viewer')).content
        child,_=self.repeat('PATCH',base,explanation_update(child,'adopt_as_company_statement'),'child-save-two','editor')
        child,_=self.repeat('POST',base+'/actions',action_body(child,'submit'),'child-submit-two','editor')
        child,_=self.repeat('POST',base+'/actions',action_body(child,'approve'),'child-approve-one','reviewer')
        self.assertEqual(self.call('PATCH',base,save,'child-save-one','editor').content,old_save)
        self.assertEqual(self.call('POST',base+'/actions',submit,'child-submit-one','editor').content,old_submit)
        self.assertEqual(self.client.get(base+'/export',params={'snapshot_id':previous},headers=self.headers('viewer')).content,old)
        self.assertEqual(self.client.get(parentbase+'/export',headers=self.headers('viewer')).content,parent_export)
        self.assertEqual(metrics(child),{'revision':7,'status':'reviewed','events':7,'snapshots':2})
        self.assertFalse(child['independently_verified'])

    def test_authorization_body_and_global_key_binding_before_replay(self):
        task=self.create();base='/api/research/tasks/'+task['id'];body=research_update(task)
        task,_=self.repeat('PATCH',base,body,'bound-save-one')
        for user,status in [('viewer',403),('other',404),('editor',409),('self-reviewer',403)]:
            self.assertEqual(self.call('PATCH',base,body,'bound-save-one',user).status_code,status)
        changed={**body,'coverage_note':'Different synthetic request with the same key.'}
        self.assertEqual(self.call('PATCH',base,changed,'bound-save-one').status_code,409)
        self.assertEqual(self.call('POST',base+'/actions',action_body(task,'submit'),'bound-save-one').status_code,409)
        another=self.create('different-parent-create');otherbase='/api/research/tasks/'+another['id']
        self.assertEqual(self.call('PATCH',otherbase,research_update(another),'bound-save-one').status_code,409)
        self.assertEqual(self.client.get(base,headers=self.headers('viewer')).json()['revision'],2)

    def test_no_key_keeps_revision_conflicts_and_header_validation_is_explicit(self):
        task=self.create();base='/api/research/tasks/'+task['id'];body=research_update(task)
        first=self.call('PATCH',base,body,None);self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(self.call('PATCH',base,body,None).status_code,409)
        for invalid in ('short','x'*129):
            self.assertEqual(self.call('PATCH',base,{**body,'revision':2},invalid).status_code,422)
        submit=action_body(first.json(),'submit')
        response=self.call('POST',base+'/actions',submit,None);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.call('POST',base+'/actions',submit,None).status_code,409)

    def test_live_source_failure_blocks_new_write_but_not_completed_receipt(self):
        task=self.create();base='/api/research/tasks/'+task['id'];body=research_update(task)
        task,old=self.repeat('PATCH',base,body,'source-save-one')
        store=self.app.state.research_tasks
        with patch.object(store,'_material_bundle',side_effect=HTTPException(409,'Synthetic source failure')):
            self.assertEqual(self.call('PATCH',base,body,'source-save-one').content,old)
            self.assertEqual(self.call('POST',base+'/actions',action_body(task,'submit'),'source-submit-blocked').status_code,409)
        self.assertEqual(self.client.get(base,headers=self.headers('viewer')).json()['revision'],2)
        task,_=self.repeat('POST',base+'/actions',action_body(task,'submit'),'source-submit-blocked')
        task,_=self.repeat('POST',base+'/actions',action_body(task,'approve'),'source-approve-one','reviewer')
        child=self.child(task);childbase='/api/research/explanation-reviews/'+child['id'];childbody=explanation_update(child)
        child,old=self.repeat('PATCH',childbase,childbody,'child-source-save','editor')
        with patch.object(self.app.state.explanation_reviews,'_build_evidence',side_effect=HTTPException(409,'Synthetic source failure')):
            self.assertEqual(self.call('PATCH',childbase,childbody,'child-source-save','editor').content,old)
            self.assertEqual(self.call('POST',childbase+'/actions',action_body(child,'submit'),'child-source-blocked','editor').status_code,409)
        self.repeat('POST',childbase+'/actions',action_body(child,'submit'),'child-source-blocked','editor')

    def test_receipt_failure_rolls_back_update_and_can_retry_same_intent(self):
        task=self.create();base='/api/research/tasks/'+task['id'];body=research_update(task);store=self.app.state.research_tasks
        before=self.client.get(base,headers=self.headers('viewer')).content
        with store.db(write=True) as db:
            db.execute("CREATE TRIGGER fail_retry_receipt BEFORE INSERT ON research_write_receipts BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        failed=self.call('PATCH',base,body,'rollback-save-one');self.assertEqual(failed.status_code,503,failed.text)
        self.assertEqual(self.client.get(base,headers=self.headers('viewer')).content,before)
        with store.db(write=True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_write_receipts').fetchone()[0],0)
            db.execute('DROP TRIGGER fail_retry_receipt')
        task,_=self.repeat('PATCH',base,body,'rollback-save-one');self.assertEqual(task['revision'],2)
        submit=action_body(task,'submit');before=self.client.get(base,headers=self.headers('viewer')).content
        with store.db(write=True) as db:
            db.execute("CREATE TRIGGER fail_retry_receipt BEFORE INSERT ON research_write_receipts BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        failed=self.call('POST',base+'/actions',submit,'rollback-submit-one');self.assertEqual(failed.status_code,503,failed.text)
        self.assertEqual(self.client.get(base,headers=self.headers('viewer')).content,before)
        with store.db(write=True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM research_snapshots').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM research_write_receipts').fetchone()[0],1)
            db.execute('DROP TRIGGER fail_retry_receipt')
        task,_=self.repeat('POST',base+'/actions',submit,'rollback-submit-one');self.assertEqual(len(task['snapshots']),1)

    def test_corrupt_receipt_returns_409_without_repeating_mutation(self):
        task=self.create();base='/api/research/tasks/'+task['id'];body=research_update(task)
        task,_=self.repeat('PATCH',base,body,'corrupt-save-one');store=self.app.state.research_tasks
        before=self.client.get(base,headers=self.headers('viewer')).content
        with store.db(write=True) as db:
            db.execute("UPDATE research_write_receipts SET response='{}' WHERE request_key='corrupt-save-one'")
        failed=self.call('PATCH',base,body,'corrupt-save-one');self.assertEqual(failed.status_code,409,failed.text)
        self.assertEqual(failed.json()['detail']['code'],'write_receipt_integrity_failed')
        self.assertEqual(self.client.get(base,headers=self.headers('viewer')).content,before)

    def test_concurrent_same_key_commits_one_response_event_and_snapshot(self):
        task=self.create();base='/api/research/tasks/'+task['id']
        task,_=self.repeat('PATCH',base,research_update(task),'concurrent-save')
        body=action_body(task,'submit')
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:self.call('POST',base+'/actions',body,'concurrent-submit'),range(2)))
        self.assertEqual([r.status_code for r in responses],[200,200])
        self.assertEqual(responses[0].content,responses[1].content)
        self.assertEqual(metrics(self.client.get(base,headers=self.headers('viewer')).json()),
            {'revision':3,'status':'in_review','events':3,'snapshots':1})

    def test_replay_report_is_deterministic_and_contains_no_main_database_state(self):
        report=json.loads((ROOT/REPORT).read_text(encoding='utf8'))
        self.assertEqual(build_replay(ROOT),report)
        self.assertFalse(report['scope']['main_database_modified']);self.assertFalse(report['scope']['real_network_disruption'])
        self.assertEqual(report['summary']['successful_write_intents'],12)
        changed=deepcopy(report);changed['summary']['persisted_receipts']=13
        with self.assertRaises(ValueError):validate_replay(ROOT,changed)


if __name__=='__main__':unittest.main()
