from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


class MarginClaimApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        principals = [{'key_sha256': sha256(b'claim-reader').hexdigest(),
                       'user_id': 'reader', 'tenant_id': 'one', 'role': 'viewer'}]
        self.client = self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
            auth_mode='api_key', principals=principals)))
        self.headers = {'Authorization': 'Bearer claim-reader'}
        self.digest = self.client.get('/api/margin-bridge', headers=self.headers).json()['report_sha256']
        self.params = {'expected_margin_report_sha256': self.digest}

    def databases(self):
        snapshots = {}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db:
                snapshots[path.name] = list(db.iterdump())
        return snapshots

    def test_auth_before_source_loading_and_read_only(self):
        with patch('creditlens.margin_claim_routes.load_margin_claim_snapshot') as load:
            self.assertEqual(self.client.get('/api/margin-claims', params=self.params).status_code, 401)
            load.assert_not_called()
        self.assertEqual(self.client.post('/api/margin-claims', headers=self.headers,
            params=self.params, json={'claim':'change'}).status_code, 405)

    def test_exact_parent_version_and_parameter_validation(self):
        self.assertEqual(self.client.get('/api/margin-claims', headers=self.headers,
            params={'expected_margin_report_sha256':'0'*64}).status_code, 409)
        for params in ({}, {'expected_margin_report_sha256':'bad'}):
            self.assertEqual(self.client.get('/api/margin-claims', headers=self.headers,
                params=params).status_code, 422)

    def test_repeatable_company_scoped_results_without_database_writes(self):
        before = self.databases()
        response = self.client.get('/api/margin-claims', headers=self.headers, params=self.params)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data['read_only'])
        self.assertEqual(data['margin_report_sha256'], self.digest)
        self.assertEqual(len(data['report']['cases']), 8)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        again = self.client.get('/api/margin-claims', headers=self.headers, params=self.params)
        self.assertEqual(response.content, again.content)
        self.assertEqual(before, self.databases())

    def test_source_failure_does_not_expose_local_paths(self):
        for error in (FileNotFoundError('private/marker'), ValueError('private marker')):
            with patch('creditlens.margin_claim_routes.load_margin_claim_snapshot', side_effect=error):
                response = self.client.get('/api/margin-claims', headers=self.headers, params=self.params)
                self.assertEqual(response.status_code, 409)
                self.assertNotIn('marker', response.text)


if __name__ == '__main__':
    unittest.main()
