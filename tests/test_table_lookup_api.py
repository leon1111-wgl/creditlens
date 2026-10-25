from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


class TableLookupApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=self.enterContext(tempfile.TemporaryDirectory())
        self.root=Path(self.temp)
        principals=[{'key_sha256':sha256(b'table-reader').hexdigest(),
                     'user_id':'reader','tenant_id':'one','role':'viewer'}]
        self.client=self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
            auth_mode='api_key',principals=principals)))
        self.headers={'Authorization':'Bearer table-reader'}

    def snapshots(self):
        values={}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db: values[path.name]=list(db.iterdump())
        return values

    def test_authentication_precedes_report_read_and_no_write_route(self):
        with patch('creditlens.table_lookup_routes.load_table_lookup_snapshot') as load:
            self.assertEqual(self.client.get('/api/table-lookup').status_code,401)
            self.assertEqual(self.client.get('/api/table-lookup/cases/unknown/page.png',
                             params={'expected_report_sha256':'0'*64}).status_code,401)
            load.assert_not_called()
        self.assertEqual(self.client.post('/api/table-lookup',headers=self.headers,
                                          json={'amount':'123'}).status_code,405)

    def test_viewer_reads_same_snapshot_without_database_changes(self):
        before=self.snapshots()
        response=self.client.get('/api/table-lookup',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        value=response.json()
        self.assertTrue(value['read_only'])
        self.assertFalse(value['full_ocr_replayed'])
        self.assertEqual(sum(len(d['cases']) for d in value['report']['documents']),42)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.content,self.client.get('/api/table-lookup',headers=self.headers).content)
        self.assertEqual(before,self.snapshots())

    def test_changed_or_missing_report_is_controlled_and_does_not_leak_paths(self):
        for error in [FileNotFoundError('private/path/marker'),ValueError('sensitive marker')]:
            with patch('creditlens.table_lookup_routes.load_table_lookup_snapshot',side_effect=error):
                response=self.client.get('/api/table-lookup',headers=self.headers)
                self.assertEqual(response.status_code,409)
                self.assertNotIn('marker',response.text)

    def test_image_requires_matching_report_and_registered_case(self):
        report=self.client.get('/api/table-lookup',headers=self.headers).json()
        doc=report['report']['documents'][0];case=doc['cases'][0]
        route='/api/table-lookup/cases/'+case['id']+'/page.png'
        params={'expected_report_sha256':report['report_sha256']}
        with patch('creditlens.table_lookup_routes.VisualStore.image',return_value=b'existing-page') as image:
            self.assertEqual(self.client.get(route,params={'expected_report_sha256':'0'*64},headers=self.headers).status_code,409)
            self.assertEqual(self.client.get('/api/table-lookup/cases/not-registered/page.png',params=params,headers=self.headers).status_code,404)
            image.assert_not_called()
            response=self.client.get(route,params=params,headers=self.headers)
            self.assertEqual(response.content,b'existing-page')
            image.assert_called_once_with(doc['id'],case['page'])
        with patch('creditlens.table_lookup_routes.VisualStore.image',side_effect=FileNotFoundError):
            self.assertEqual(self.client.get(route,params=params,headers=self.headers).status_code,404)
        with patch('creditlens.table_lookup_routes.VisualStore.image',side_effect=ValueError):
            self.assertEqual(self.client.get(route,params=params,headers=self.headers).status_code,409)


if __name__=='__main__': unittest.main()
