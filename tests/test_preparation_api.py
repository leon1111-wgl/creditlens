"""Actual batch preparation HTTP flows on temporary state, no model inference."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from creditlens.api import create_app, ROOT
from creditlens.preparation_examples import preparation_examples


class PreparationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.principals=[{'key_sha256':sha256(key.encode()).hexdigest(),'user_id':user,'tenant_id':tenant,'role':role}
            for key,user,tenant,role in [('writer','alice','one','analyst'),('reader','bob','one','viewer'),
              ('reviewer','ruth','one','reviewer'),('other','eve','two','analyst')]]
        self.client=self.open();self.samples=preparation_examples(ROOT)['examples']
        self.base='/api/document-preparation/tasks'

    def open(self):
        client=TestClient(create_app(self.path/'absent.json',self.path/'audit.sqlite',auth_mode='api_key',principals=self.principals))
        client.__enter__();self.addCleanup(client.__exit__,None,None,None);return client

    def headers(self,key='writer',idem='batch-create'):
        return {'Authorization':'Bearer '+key,'Idempotency-Key':idem}

    def create(self,kind='missing',key='writer',idem='batch-create'):
        return self.client.post(self.base,json=self.samples[kind],headers=self.headers(key,idem))

    def test_fail_fix_history_and_idempotent_delivery(self):
        created=self.create();self.assertEqual(created.status_code,201,created.text);job=created.json()
        self.assertEqual(job['status'],'blocked');self.assertFalse(job['queryable'])
        self.assertEqual(self.create().json(),job)
        url=self.base+'/'+job['id'];aid=job['latest_attempt']['id']
        old=self.client.get(url+'/export?attempt_id='+aid,headers=self.headers('reader'))
        self.assertEqual(old.status_code,200);self.assertEqual(old.headers['cache-control'],'no-store')
        bad=self.client.post(url+'/retry',json={'revision':1,'payload':self.samples['damaged']},headers=self.headers(idem='retry-bad'))
        self.assertEqual(bad.status_code,200,bad.text);self.assertEqual(bad.json()['status'],'blocked')
        good=self.client.post(url+'/retry',json={'revision':2,'payload':self.samples['complete']},headers=self.headers(idem='retry-good'))
        self.assertEqual(good.status_code,200,good.text);self.assertEqual(good.json()['status'],'prepared_not_indexed')
        self.assertEqual(good.json()['revision'],3)
        self.assertEqual(self.client.get(url+'/export?attempt_id='+aid,headers=self.headers('reader')).content,old.content)
        self.assertEqual(self.client.post(url+'/retry',json={'revision':3,'payload':self.samples['complete']},headers=self.headers(idem='terminal')).status_code,409)
        self.assertEqual(self.client.post(url+'/retry',json={'revision':1,'payload':self.samples['complete']},headers=self.headers(idem='stale')).status_code,409)

    def test_identity_tenant_and_server_role_cannot_be_overridden(self):
        self.assertEqual(self.client.get(self.base).status_code,401)
        for key in ('reader','reviewer'):self.assertEqual(self.create(key=key).status_code,403)
        job=self.create().json();url=self.base+'/'+job['id']
        for suffix in ('','/export'):
            self.assertEqual(self.client.get(url+suffix,headers=self.headers('other')).status_code,404)
        forged={**self.headers('reader'),'X-Demo-Role':'analyst'}
        self.assertEqual(self.client.post(url+'/retry',json={'revision':1,'payload':self.samples['complete']},headers=forged).status_code,403)
        self.assertEqual(self.client.get(url,headers=self.headers('reviewer')).status_code,200)

    def test_strict_json_and_invalid_shape_do_not_create_records(self):
        original=self.client.get(self.base,headers=self.headers()).content
        for raw in ('{"schema_version":1,"schema_version":1}', '{"x":NaN}', '{"x":1e309}', '[]', '['*1500+']'*1500):
            response=self.client.post(self.base,content=raw,headers={**self.headers(),'Content-Type':'application/json'})
            self.assertEqual(response.status_code,422,response.text)
        unknown={**self.samples['complete'],'private-canary-unknown-field':'DO-NOT-ECHO'}
        response=self.client.post(self.base,json=unknown,headers=self.headers())
        self.assertEqual(response.status_code,422);self.assertNotIn('DO-NOT-ECHO',response.text)
        self.assertEqual(self.client.get(self.base,headers=self.headers()).content,original)

    def test_body_limits_cross_origin_and_revision_type(self):
        self.assertEqual(self.client.post(self.base,content=b' '*1_000_001,headers=self.headers()).status_code,413)
        self.assertEqual(self.client.post(self.base,json=self.samples['complete'],headers={**self.headers(),'Origin':'https://other.example'}).status_code,403)
        job=self.create().json()
        for revision in (True,0,'1',1.5):
            r=self.client.post(self.base+'/'+job['id']+'/retry',json={'revision':revision,'payload':self.samples['complete']},headers=self.headers(idem='badrev'))
            self.assertEqual(r.status_code,422)

    def test_state_survives_store_recreation_without_saving_raw_text(self):
        payload=deepcopy(self.samples['missing']);canary='PRIVATE_OCR_TEXT_6f184ce9'
        payload['pages'][0]['ocr_input']['result']['txts'][0]=canary
        payload['pages'][0]['ocr_input']['result']['word_results'][0][0][0]=canary
        response=self.client.post(self.base,json=payload,headers=self.headers());self.assertEqual(response.status_code,201,response.text)
        job=response.json();url=self.base+'/'+job['id'];self.assertNotIn(canary,response.text)
        old=self.client.get(url+'/export',headers=self.headers()).content
        restarted=self.open();self.assertEqual(restarted.get(url+'/export',headers=self.headers()).content,old)
        r=restarted.post(url+'/retry',json={'revision':1,'payload':self.samples['complete']},headers=self.headers(idem='after-restart'))
        self.assertEqual(r.status_code,200,r.text)
        for path in self.path.glob('*sqlite*'):self.assertNotIn(canary.encode(),path.read_bytes(),path.name)

    def test_examples_are_declared_synthetic_and_workspace_is_available(self):
        response=self.client.get('/api/document-preparation/examples',headers=self.headers('reader'))
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['source_bytes_read']);self.assertFalse(response.json()['model_inference_performed'])
        self.assertTrue(all(p['provenance']=='synthetic' for p in response.json()['examples'].values()))
        self.assertEqual(self.client.get('/document-preparation').status_code,200)


if __name__=='__main__':unittest.main()
