from contextlib import closing
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.row_selection_verification import MAX_BYTES, RowSelectionSourceError


class RowSelectionVerificationApiTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        principals=[{'key_sha256':sha256(b'selection-reader').hexdigest(),
                     'user_id':'reader','tenant_id':'one','role':'viewer'}]
        self.client=self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
                         auth_mode='api_key',principals=principals)))
        self.headers={'Authorization':'Bearer selection-reader','Content-Type':'application/json'}
        self.route='/api/row-selection-verification'

    def exported(self):
        role=self.client.get('/api/role-table-lookup',headers=self.headers).json()
        saved=self.client.get('/api/row-disambiguation',headers=self.headers,
                    params={'expected_role_report_sha256':role['report_sha256']}).json()
        for query in saved['report']['queries']:
            for path in query['paths']:
                url='/api/row-disambiguation/queries/'+query['id']+'/selection'
                response=self.client.get(url,headers=self.headers,params={
                    'expected_report_sha256':saved['report_sha256'],'row_index':path['row_index']})
                self.assertEqual(response.status_code,200)
                yield response.content

    def db_dump(self):
        result={}
        for path in self.root.glob('*.sqlite'):
            with closing(sqlite3.connect(path)) as db:result[path.name]=list(db.iterdump())
        return result

    def test_actual_eight_export_responses_match_without_database_changes(self):
        before=self.db_dump();count=0
        for raw in self.exported():
            response=self.client.post(self.route,content=raw,headers=self.headers)
            self.assertEqual(response.status_code,200,response.text)
            value=response.json();self.assertEqual(value['status'],'matched')
            self.assertEqual(value['raw_sha256'],sha256(raw).hexdigest())
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertIsNotNone(value['bound_selection']);count+=1
        self.assertEqual(count,8);self.assertEqual(before,self.db_dump())

    def test_authentication_precedes_body_read_and_validation(self):
        async def forbidden_stream(request):
            raise AssertionError('Unauthenticated body was read')
            yield b''
        with patch('starlette.requests.Request.stream',forbidden_stream),patch(
              'creditlens.row_selection_verification_routes.load_and_verify_row_selection') as verify:
            response=self.client.post(self.route,content=b'{bad',headers={'Content-Type':'application/json'})
            self.assertEqual(response.status_code,401);verify.assert_not_called()

    def test_stream_limit_stops_before_next_chunk_without_trusting_length(self):
        visited=[]
        async def oversized(request):
            visited.append('first');yield b'x'*(MAX_BYTES+1)
            visited.append('second');yield b'private marker'
        with patch('starlette.requests.Request.stream',oversized),patch(
              'creditlens.row_selection_verification_routes.load_and_verify_row_selection') as verify:
            response=self.client.post(self.route,content=b'x',headers={**self.headers,'Content-Length':'1'})
            self.assertEqual(response.status_code,413);verify.assert_not_called()
        self.assertEqual(visited,['first'])

    def test_content_type_origin_and_method_are_checked(self):
        self.assertEqual(self.client.post(self.route,content=b'{}',headers={**self.headers,'Content-Type':'text/plain'}).status_code,415)
        self.assertEqual(self.client.post(self.route,content=b'{}',headers={**self.headers,'Origin':'https://elsewhere.example'}).status_code,403)
        self.assertEqual(self.client.get(self.route,headers=self.headers).status_code,405)

    def test_invalid_json_utf8_duplicate_keys_and_nonfinite_are_bad_requests(self):
        before=self.db_dump()
        for raw in [b'{bad',b'\xff',b'{"schema_version":1,"schema_version":1}',b'{"n":NaN}',
                    b'{"n":1e309}',b'['*100+b']'*100,b'[]',b'']:
            response=self.client.post(self.route,content=raw,headers=self.headers)
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(before,self.db_dump())

    def test_changed_values_and_unknown_keys_are_mismatch_without_echo(self):
        original=json.loads(next(self.exported()))
        changed=deepcopy(original);changed['source']['url']='https://private-marker.example/secret'
        changed['private-key-marker']='private-value-marker'
        response=self.client.post(self.route,json=changed,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'mismatch')
        self.assertIsNone(response.json()['bound_selection'])
        for marker in ['private-marker','private-key-marker','private-value-marker']:
            self.assertNotIn(marker,response.text)
        changed=deepcopy(original);changed['result']['selection']['row_index']=float(changed['result']['selection']['row_index'])
        self.assertEqual(self.client.post(self.route,json=changed,headers=self.headers).json()['status'],'mismatch')

    def test_unavailable_trusted_source_is_not_mismatch_and_retry_can_succeed(self):
        raw=next(self.exported())
        with patch('creditlens.row_selection_verification_routes.load_and_verify_row_selection',
                   side_effect=RowSelectionSourceError('source_unavailable','$','private path marker')):
            response=self.client.post(self.route,content=raw,headers=self.headers)
            self.assertEqual(response.status_code,409);self.assertNotIn('marker',response.text)
        self.assertEqual(self.client.post(self.route,content=raw,headers=self.headers).json()['status'],'matched')

    def test_whitespace_and_object_key_order_do_not_change_semantic_match(self):
        raw=next(self.exported());value=json.loads(raw)
        altered=json.dumps(dict(reversed(list(value.items()))),indent=2).encode()
        response=self.client.post(self.route,content=altered,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'matched')
        self.assertEqual(response.json()['raw_sha256'],sha256(altered).hexdigest())
        self.assertNotEqual(sha256(raw).hexdigest(),response.json()['raw_sha256'])


if __name__=='__main__':unittest.main()
