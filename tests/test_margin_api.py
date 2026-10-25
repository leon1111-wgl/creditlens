from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


class MarginApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        principals = [{'key_sha256': sha256(b'margin-reader').hexdigest(),
                       'user_id': 'reader', 'tenant_id': 'one', 'role': 'viewer'}]
        self.client = self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
            auth_mode='api_key', principals=principals)))
        self.headers = {'Authorization': 'Bearer margin-reader'}

    def databases(self):
        snapshots = {}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db:
                snapshots[path.name] = list(db.iterdump())
        return snapshots

    def test_auth_before_loading_registered_sources_and_no_write_api(self):
        with patch('creditlens.margin_routes.load_margin_bridge_snapshot') as load:
            self.assertEqual(self.client.get('/api/margin-bridge').status_code, 401)
            self.assertEqual(self.client.get('/api/margin-bridge/cases/unknown/page.png',
                params={'expected_report_sha256': '0'*64}).status_code, 401)
            load.assert_not_called()
        self.assertEqual(self.client.post('/api/margin-bridge', headers=self.headers,
            json={'amount': '100'}).status_code, 405)

    def test_registered_report_repeats_without_database_writes(self):
        before = self.databases()
        response = self.client.get('/api/margin-bridge', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data['read_only'])
        self.assertEqual(len(data['report']['cases']), 2)
        self.assertEqual(sum(len(c['registered_observations']) for c in data['report']['cases']), 28)
        self.assertTrue(all(c['result']['status'] == 'computed' for c in data['report']['cases']))
        self.assertFalse(data['validation_scope']['raw_pdf_bytes_checked'])
        self.assertFalse(data['validation_scope']['full_ocr_replayed'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.content, self.client.get('/api/margin-bridge', headers=self.headers).content)
        self.assertEqual(before, self.databases())

    def test_loader_failure_does_not_expose_local_paths(self):
        for error in (FileNotFoundError('private/path/marker'), ValueError('sensitive marker')):
            with patch('creditlens.margin_routes.load_margin_bridge_snapshot', side_effect=error):
                response = self.client.get('/api/margin-bridge', headers=self.headers)
                self.assertEqual(response.status_code, 409)
                self.assertNotIn('marker', response.text)

    def test_preview_is_bound_to_report_case_and_exact_image_bytes(self):
        saved = self.client.get('/api/margin-bridge', headers=self.headers).json()
        case = saved['report']['cases'][0]
        route = '/api/margin-bridge/cases/'+case['id']+'/page.png'
        params = {'expected_report_sha256': saved['report_sha256']}
        with patch('creditlens.margin_routes.VisualStore.image', return_value=b'wrong-version') as image:
            self.assertEqual(self.client.get(route, params={'expected_report_sha256': '0'*64}, headers=self.headers).status_code, 409)
            self.assertEqual(self.client.get('/api/margin-bridge/cases/unknown/page.png', params=params, headers=self.headers).status_code, 404)
            image.assert_not_called()
            self.assertEqual(self.client.get(route, params=params, headers=self.headers).status_code, 409)
        metadata = {'document_id':case['document_id'], 'page':case['page']['page'],
                    'image_sha256':sha256(b'registered-image').hexdigest()}
        self.enterContext(patch('creditlens.margin_routes.margin_image_metadata', return_value=metadata))
        with patch('creditlens.margin_routes.VisualStore.image', return_value=b'registered-image') as image:
            response = self.client.get(route, params=params, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, b'registered-image')
            image.assert_called_once_with(case['document_id'], case['page']['page'])
        for error, status in ((FileNotFoundError,404),(PermissionError,409),(ValueError,409)):
            with patch('creditlens.margin_routes.VisualStore.image', side_effect=error):
                self.assertEqual(self.client.get(route, params=params, headers=self.headers).status_code, status)


if __name__ == '__main__':
    unittest.main()
