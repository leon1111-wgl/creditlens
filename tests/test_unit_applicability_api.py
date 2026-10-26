from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.unit_applicability_experiment import load_unit_applicability_snapshot


class UnitApplicabilityApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = load_unit_applicability_snapshot(Path(__file__).resolve().parents[1])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        principals = [{'key_sha256': sha256(b'unit-reader').hexdigest(),
                       'user_id': 'reader', 'tenant_id': 'public-demo', 'role': 'viewer'}]
        self.client = self.enterContext(TestClient(create_app(
            audit_db=self.root/'audit.sqlite', auth_mode='api_key', principals=principals)))
        self.headers = {'Authorization': 'Bearer unit-reader'}
        self.params = {'expected_role_report_sha256': self.saved['role_report_sha256']}

    def databases(self):
        result = {}
        for p in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(p)) as db:
                result[p.name] = list(db.iterdump())
        return result

    def test_unauthenticated_reads_never_load_reports(self):
        with patch('creditlens.unit_applicability_routes.load_unit_applicability_snapshot') as load:
            for path, params in [('/api/unit-applicability', self.params),
                                 ('/api/unit-applicability/cases/unknown', {**self.params, 'method': 'role'}),
                                 ('/api/unit-applicability/queries/unknown/selection',
                                  {**self.params, 'row_index': 0, 'expected_row_report_sha256': '0'*64})]:
                self.assertEqual(self.client.get(path, params=params).status_code, 401)
            load.assert_not_called()

    def test_public_report_is_stable_read_only_and_has_no_write_endpoint(self):
        before = self.databases()
        response = self.client.get('/api/unit-applicability', params=self.params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['read_only'])
        self.assertFalse(response.json()['full_ocr_replayed'])
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.content, self.client.get('/api/unit-applicability', params=self.params, headers=self.headers).content)
        self.assertEqual(self.client.post('/api/unit-applicability', headers=self.headers, json={'amount': '100'}).status_code, 405)
        self.assertEqual(before, self.databases())

    def test_four_methods_are_bound_to_the_requested_case(self):
        case = self.saved['report']['cases'][0]
        before = self.databases()
        for method in ('text', 'line', 'word', 'role'):
            response = self.client.get('/api/unit-applicability/cases/'+case['id'],
                params={**self.params, 'method': method}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            value = response.json()
            self.assertEqual(value['target']['case_id'], case['id'])
            self.assertEqual(value['target']['method'], method)
            self.assertEqual(value['target']['query'], case['query'])
            self.assertFalse(value['automatic_approval'])
            self.assertEqual(value['role_report_sha256'], self.params['expected_role_report_sha256'])
            self.assertFalse(value['unit_check']['automatic_approval'])
        self.assertEqual(before, self.databases())

    def test_explicit_row_requires_both_source_versions(self):
        chosen = self.saved['report']['selections'][0]
        params = {**self.params, 'row_index': chosen['row_index'],
                  'expected_row_report_sha256': self.saved['row_report_sha256']}
        path = '/api/unit-applicability/queries/'+chosen['query_id']+'/selection'
        response = self.client.get(path, params=params, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        target = response.json()['target']
        self.assertEqual(target['kind'], 'explicit_selection')
        self.assertEqual(target['row_index'], chosen['row_index'])
        for field in ('expected_role_report_sha256', 'expected_row_report_sha256'):
            self.assertEqual(self.client.get(path, params={**params, field: '0'*64}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.get(path, params={**params, 'row_index': 99999}, headers=self.headers).status_code, 404)

    def test_bad_binding_method_and_unknown_target_are_not_silently_replaced(self):
        case = self.saved['report']['cases'][0]
        path = '/api/unit-applicability/cases/'+case['id']
        for params, expected in [({'method': 'role', 'expected_role_report_sha256': '0'*64}, 409),
                                 ({**self.params, 'method': 'unknown'}, 422),
                                 ({'method': 'role', 'expected_role_report_sha256': 'invalid'}, 422),
                                 ({**self.params}, 422)]:
            self.assertEqual(self.client.get(path, params=params, headers=self.headers).status_code, expected)
        self.assertEqual(self.client.get('/api/unit-applicability/cases/unregistered',
            params={**self.params, 'method': 'role'}, headers=self.headers).status_code, 404)

    def test_missing_or_damaged_report_has_safe_recovery_message(self):
        for exc in (FileNotFoundError('private-marker'), ValueError('private-marker')):
            with patch('creditlens.unit_applicability_routes.load_unit_applicability_snapshot', side_effect=exc):
                response = self.client.get('/api/unit-applicability', params=self.params, headers=self.headers)
                self.assertEqual(response.status_code, 409)
                self.assertNotIn('private-marker', response.text)
                self.assertIn('reload', response.text)


if __name__ == '__main__':
    unittest.main()
