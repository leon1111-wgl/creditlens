"""Public registered facts can be inspected; client values never become facts."""
from hashlib import sha256
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


class CrossPageApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(tempfile.TemporaryDirectory())
        self.root=Path(self.temp)
        principals=[{'key_sha256':sha256(b'reader').hexdigest(),'user_id':'reader',
                     'tenant_id':'workspace-one','role':'viewer'}]
        self.client=self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
            auth_mode='api_key',principals=principals)))
        self.headers={'Authorization':'Bearer reader'}
        self.catalog=self.client.get('/api/cross-page-evidence',headers=self.headers).json()
        self.task=self.catalog['catalog']['tasks'][0]
        self.payload={'task_id':self.task['id'],
            'selected_ids':list(self.task['required_role_ids'].values()),
            'expected_catalog_sha256':self.catalog['catalog_sha256']}

    def post(self,value=None,**kwargs):
        return self.client.post('/api/cross-page-evidence/check',headers=self.headers,
                                json=self.payload if value is None else value,**kwargs)

    def snapshots(self):
        result={}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db:
                result[path.name]=list(db.iterdump())
        return result

    def test_viewer_computes_registered_facts_without_writes(self):
        before=self.snapshots()
        response=self.post()
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(result['result']['status'],'computed')
        self.assertFalse(result['saved_to_research_task'])
        self.assertFalse(result['automatic_approval'])
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.post().content,response.content)
        self.assertEqual(before,self.snapshots())

    def test_authorization_happens_before_material_inspection(self):
        with patch('creditlens.cross_page_routes.build_cross_page_materials') as load:
            self.assertEqual(self.client.get('/api/cross-page-evidence').status_code,401)
            self.assertEqual(self.client.post('/api/cross-page-evidence/check',json=self.payload).status_code,401)
            load.assert_not_called()

    def test_stale_catalog_never_evaluates_and_unknown_task_is_404(self):
        with patch('creditlens.cross_page_routes.evaluate_evidence_selection') as evaluate:
            self.assertEqual(self.post({**self.payload,'expected_catalog_sha256':'0'*64}).status_code,409)
            self.assertEqual(self.post({**self.payload,'task_id':'unknown'}).status_code,404)
            evaluate.assert_not_called()

    def test_omitted_scope_is_refusal_and_not_zero_calculation(self):
        selected=[v for k,v in self.task['required_role_ids'].items() if k!='consolidation_scope']
        result=self.post({**self.payload,'selected_ids':selected})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['result']['status'],'refused')
        self.assertIsNone(result.json()['result']['calculation'])

    def test_client_amounts_invalid_lists_and_duplicate_json_keys_rejected(self):
        for value in [{**self.payload,'amount':'999'},
                      {**self.payload,'selected_ids':[1]},
                      {**self.payload,'selected_ids':['x'*257]},
                      {**self.payload,'selected_ids':['x']*21}]:
            self.assertEqual(self.post(value).status_code,422)
        response=self.client.post('/api/cross-page-evidence/check',headers=self.headers,
            content='{"task_id":"a","task_id":"b"}')
        self.assertEqual(response.status_code,422)

    def test_preview_is_authorized_version_bound_and_missing_image_explained(self):
        card=self.task['required_role_ids']['cfo']
        path='/api/cross-page-evidence/cards/'+card+'/page.png'
        query={'expected_catalog_sha256':self.catalog['catalog_sha256']}
        with patch('creditlens.cross_page_routes.VisualStore.image',return_value=b'known-local-png') as image:
            self.assertEqual(self.client.get(path,params=query).status_code,401)
            self.assertEqual(self.client.get(path,params={'expected_catalog_sha256':'0'*64},headers=self.headers).status_code,409)
            image.assert_not_called()
            response=self.client.get(path,params=query,headers=self.headers)
            self.assertEqual(response.content,b'known-local-png')
            self.assertEqual(response.headers['content-type'],'image/png')
        with patch('creditlens.cross_page_routes.VisualStore.image',side_effect=FileNotFoundError):
            self.assertEqual(self.client.get(path,params=query,headers=self.headers).status_code,404)
        scope='/api/cross-page-evidence/cards/'+self.task['required_role_ids']['consolidation_scope']+'/page.png'
        self.assertEqual(self.client.get(scope,params=query,headers=self.headers).status_code,404)


if __name__=='__main__':
    unittest.main()
