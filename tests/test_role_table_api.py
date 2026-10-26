from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


class RoleTableApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(tempfile.TemporaryDirectory())
        self.root=Path(self.temp)
        principals=[{'key_sha256':sha256(b'role-table-reader').hexdigest(),
                     'user_id':'reader','tenant_id':'one','role':'viewer'}]
        self.client=self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
            auth_mode='api_key',principals=principals)))
        self.headers={'Authorization':'Bearer role-table-reader'}

    def snapshots(self):
        values={}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db: values[path.name]=list(db.iterdump())
        return values

    def test_authentication_before_snapshot_or_image_and_no_write_route(self):
        with patch('creditlens.role_table_routes.load_role_table_lookup_snapshot') as load:
            self.assertEqual(self.client.get('/api/role-table-lookup').status_code,401)
            self.assertEqual(self.client.get('/api/role-table-lookup/cases/unknown/page.png',
                             params={'expected_report_sha256':'0'*64}).status_code,401)
            load.assert_not_called()
        self.assertEqual(self.client.post('/api/role-table-lookup',headers=self.headers,
                                          json={'amount':'123'}).status_code,405)

    def test_viewer_snapshot_is_stable_complete_and_read_only(self):
        before=self.snapshots()
        response=self.client.get('/api/role-table-lookup',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        value=response.json()
        self.assertTrue(value['read_only'])
        self.assertFalse(value['full_ocr_replayed'])
        cases=[c for d in value['report']['documents'] for c in d['cases']]
        self.assertEqual(len(cases),42)
        self.assertTrue(all(set(c['methods'])=={'line','word','role'} for c in cases))
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.content,self.client.get('/api/role-table-lookup',headers=self.headers).content)
        self.assertEqual(before,self.snapshots())

    def test_snapshot_failures_are_controlled_without_private_paths(self):
        for error in [FileNotFoundError('private/path/marker'),ValueError('sensitive marker')]:
            with patch('creditlens.role_table_routes.load_role_table_lookup_snapshot',side_effect=error):
                response=self.client.get('/api/role-table-lookup',headers=self.headers)
                self.assertEqual(response.status_code,409)
                self.assertNotIn('marker',response.text)

    def test_image_binding_rejects_old_experiment_and_unknown_cases(self):
        report=self.client.get('/api/role-table-lookup',headers=self.headers).json()
        old=self.client.get('/api/word-table-lookup',headers=self.headers).json()
        doc=report['report']['documents'][0];case=doc['cases'][0]
        route='/api/role-table-lookup/cases/'+case['id']+'/page.png'
        params={'expected_report_sha256':report['report_sha256']}
        # A valid image under a changed local registration must still match the
        # page pinned by the report already selected for this request.
        with patch('creditlens.role_table_routes.VisualStore.image',return_value=b'other-version'):
            self.assertEqual(self.client.get(route,params=params,headers=self.headers).status_code,409)
        next(p for p in doc['pages'] if p['page']==case['page'])['image_sha256']=sha256(b'existing-page').hexdigest()
        self.enterContext(patch('creditlens.role_table_routes.load_role_table_lookup_snapshot',return_value=report))
        with patch('creditlens.role_table_routes.VisualStore.image',return_value=b'existing-page') as image:
            for digest in ('0'*64,old['report_sha256']):
                self.assertEqual(self.client.get(route,params={'expected_report_sha256':digest},headers=self.headers).status_code,409)
            self.assertEqual(self.client.get('/api/role-table-lookup/cases/not-registered/page.png',params=params,headers=self.headers).status_code,404)
            image.assert_not_called()
            response=self.client.get(route,params=params,headers=self.headers)
            self.assertEqual(response.content,b'existing-page')
            image.assert_called_once_with(doc['id'],case['page'])
        for exc,code in [(FileNotFoundError,404),(ValueError,409),(PermissionError,409)]:
            with patch('creditlens.role_table_routes.VisualStore.image',side_effect=exc):
                self.assertEqual(self.client.get(route,params=params,headers=self.headers).status_code,code)


if __name__=='__main__': unittest.main()
