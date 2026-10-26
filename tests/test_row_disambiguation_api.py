from contextlib import closing
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


class RowDisambiguationApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        principals = [{'key_sha256': sha256(b'row-viewer').hexdigest(),
                       'user_id': 'reader', 'tenant_id': 'one', 'role': 'viewer'}]
        self.client = self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
                            auth_mode='api_key', principals=principals)))
        self.headers = {'Authorization': 'Bearer row-viewer'}

    def load(self):
        role = self.client.get('/api/role-table-lookup', headers=self.headers).json()
        response = self.client.get('/api/row-disambiguation', headers=self.headers,
                      params={'expected_role_report_sha256': role['report_sha256']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def selection(self, saved, query, **overrides):
        params = {'expected_report_sha256': saved['report_sha256'],
                  'row_index': query['paths'][0]['row_index'], **overrides}
        return self.client.get('/api/row-disambiguation/queries/'+query['id']+'/selection',
                               params=params, headers=self.headers)

    def db_dump(self):
        result = {}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db:
                result[path.name] = list(db.iterdump())
        return result

    def test_authentication_precedes_loading_and_no_mutating_route(self):
        with patch('creditlens.row_disambiguation_routes.load_row_disambiguation_snapshot') as load:
            for route in ['/api/row-disambiguation', '/api/row-disambiguation/queries/unknown/selection',
                          '/api/row-disambiguation/queries/unknown/page.png']:
                self.assertEqual(self.client.get(route).status_code, 401)
            load.assert_not_called()
        self.assertEqual(self.client.post('/api/row-disambiguation', headers=self.headers,
                                         json={'row_index': 13}).status_code, 405)

    def test_all_queries_and_both_rows_remain_visible_and_read_only(self):
        before = self.db_dump()
        saved = self.load()
        self.assertEqual(len(saved['report']['queries']), 4)
        outputs = []
        for query in saved['report']['queries']:
            self.assertEqual(query['discovery']['status'], 'selection_required')
            self.assertEqual(len(query['discovery']['candidates']), 2)
            for path in query['paths']:
                response = self.selection(saved, query, row_index=path['row_index'])
                self.assertEqual(response.status_code, 200, response.text)
                value = response.json()
                self.assertEqual(value['result'], path['result'])
                self.assertEqual(value['discovery'], query['discovery'])
                self.assertFalse(value['persisted'])
                self.assertFalse(value['automatic_approval'])
                self.assertFalse(value['full_ocr_replayed'])
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(response.content, self.selection(saved, query, row_index=path['row_index']).content)
                outputs.append(value)
        self.assertEqual(len(outputs), 8)
        self.assertEqual(before, self.db_dump())

    def test_missing_row_never_defaults_and_invalid_values_fail(self):
        saved = self.load(); query = saved['report']['queries'][0]
        base = '/api/row-disambiguation/queries/'+query['id']+'/selection'
        for params in [{'expected_report_sha256': saved['report_sha256']},
                       {'expected_report_sha256': saved['report_sha256'], 'row_index': '-1'},
                       {'expected_report_sha256': saved['report_sha256'], 'row_index': 'true'},
                       {'expected_report_sha256': saved['report_sha256'], 'row_index': '1.5'}]:
            self.assertEqual(self.client.get(base, params=params, headers=self.headers).status_code, 422)
        self.assertEqual(self.selection(saved, query, row_index=9999).status_code, 409)
        other = next(q for q in saved['report']['queries'] if q['query']['row_label'] != query['query']['row_label'])
        self.assertEqual(self.selection(saved, query, row_index=other['paths'][0]['row_index']).status_code, 409)

    def test_changed_parent_or_selection_report_is_rejected(self):
        saved = self.load(); query = saved['report']['queries'][0]
        self.assertEqual(self.client.get('/api/row-disambiguation', headers=self.headers,
                           params={'expected_role_report_sha256': '0'*64}).status_code, 409)
        self.assertEqual(self.selection(saved, query, expected_report_sha256='0'*64).status_code, 409)
        missing = deepcopy(query); missing['id'] = 'not-registered'
        self.assertEqual(self.selection(saved, missing).status_code, 404)

    def test_failed_snapshot_cannot_leak_local_paths(self):
        for error in [FileNotFoundError('private/path/marker'), ValueError('private marker')]:
            with patch('creditlens.row_disambiguation_routes.load_row_disambiguation_snapshot', side_effect=error):
                response = self.client.get('/api/row-disambiguation', headers=self.headers,
                           params={'expected_role_report_sha256': '0'*64})
                self.assertEqual(response.status_code, 409)
                self.assertNotIn('marker', response.text)

    def test_image_must_match_same_query_report_and_original_digest(self):
        saved = self.load(); query = saved['report']['queries'][0]
        route = '/api/row-disambiguation/queries/'+query['id']+'/page.png'
        params = {'expected_report_sha256': saved['report_sha256']}
        with patch('creditlens.row_disambiguation_routes.VisualStore.image', return_value=b'wrong'):
            self.assertEqual(self.client.get(route, params=params, headers=self.headers).status_code, 409)
        query['page_metadata']['image_sha256'] = sha256(b'test image').hexdigest()
        self.enterContext(patch('creditlens.row_disambiguation_routes.load_row_disambiguation_snapshot', return_value=saved))
        with patch('creditlens.row_disambiguation_routes.VisualStore.image', return_value=b'test image') as image:
            self.assertEqual(self.client.get(route, params={'expected_report_sha256':'0'*64}, headers=self.headers).status_code, 409)
            image.assert_not_called()
            self.assertEqual(self.client.get(route, params=params, headers=self.headers).content, b'test image')
            image.assert_called_once_with(query['document_id'], query['page'])
        for error, status in [(FileNotFoundError, 404), (ValueError, 409), (PermissionError, 409)]:
            with patch('creditlens.row_disambiguation_routes.VisualStore.image', side_effect=error):
                self.assertEqual(self.client.get(route, params=params, headers=self.headers).status_code, status)


if __name__ == '__main__': unittest.main()
