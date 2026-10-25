"""HTTP workflow controls use explicitly synthetic observations and principals."""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app


SYNTHETIC = {'status': 'answerable', 'raw_value': '123.45',
             'amount_bbox': {'x0': 10, 'top': 10, 'x1': 40, 'bottom': 20},
             'unit_text': 'synthetic unit', 'currency_text': None,
             'section_text': 'synthetic section', 'section_note': None,
             'reason': 'Synthetic annotation for software testing, not a financial reference.'}


class ReferenceReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.keys = [('a', 'observer-a', 'one', 'analyst'), ('a-alias', 'observer-a', 'one', 'reviewer'),
                     ('b', 'observer-b', 'one', 'analyst'), ('r', 'reviewer-c', 'one', 'reviewer'),
                     ('v', 'viewer', 'one', 'viewer'), ('other', 'observer-a', 'two', 'analyst')]
        principals = [{'key_sha256': sha256(key.encode()).hexdigest(), 'user_id': user,
                       'tenant_id': tenant, 'role': role} for key, user, tenant, role in self.keys]
        self.client = self.enterContext(TestClient(create_app(audit_db=self.root/'audit.sqlite',
                                            auth_mode='api_key', principals=principals)))
        self.catalog = self.client.get('/api/reference-review/catalog', headers=self.h('a')).json()['catalog']
        self.task = self.catalog['tasks'][0]

    @staticmethod
    def h(key, idem=None):
        return {'Authorization': 'Bearer '+key, **({'Idempotency-Key': idem} if idem else {})}

    def create(self, key='a', idem='create-one', **extra):
        return self.client.post('/api/reference-review/reviews', headers=self.h(key, idem),
            json={'task_id': self.task['id'], 'expected_catalog_sha256': self.catalog['catalog_sha256'],
                  'reason': 'Synthetic software workflow only.', **extra})

    def submit(self, review, key, idem, obs=None):
        return self.client.post('/api/reference-review/reviews/'+review['id']+'/observations',
            headers=self.h(key, idem), json={'revision': review['revision'], 'observation': obs or SYNTHETIC})

    def action(self, review, action, key='r'):
        return self.client.post('/api/reference-review/reviews/'+review['id']+'/'+action,
            headers=self.h(key), json={'revision': review['revision'], 'note': 'Synthetic workflow review only.'})

    def test_authentication_precedes_catalog_state_and_images(self):
        with patch('creditlens.reference_routes.build_reference_catalog') as build:
            for path in ['/api/reference-review/catalog', '/api/reference-review/reviews',
                         '/api/reference-review/reviews/unknown',
                         '/api/reference-review/reviews/unknown/page.png?expected_task_sha256='+'0'*64]:
                self.assertEqual(self.client.get(path).status_code, 401)
            build.assert_not_called()
        self.assertEqual(self.create(key='v').status_code, 403)

    def test_catalog_contains_only_answer_hidden_tasks_and_readonly_state(self):
        self.assertEqual(len(self.catalog['tasks']), 40)
        blocked={'reference','raw_value','normalized_value','amount_bbox','methods','fact_id','case_id'}
        def visit(value):
            if isinstance(value,dict):
                self.assertFalse(blocked.intersection(value))
                for v in value.values():visit(v)
            elif isinstance(value,list):
                for v in value:visit(v)
        visit(self.catalog)
        db=self.root/'reference-reviews.sqlite'
        with closing(sqlite3.connect(db)) as c: before=list(c.iterdump())
        result=self.client.get('/api/reference-review/reviews',headers=self.h('v'))
        self.assertEqual(result.json(),[])
        self.assertEqual(result.headers['cache-control'],'no-store')
        with closing(sqlite3.connect(db)) as c:self.assertEqual(before,list(c.iterdump()))

    def test_two_observers_and_separate_reviewer_with_private_first_submission(self):
        created=self.create();self.assertEqual(created.status_code,201,created.text)
        first=self.submit(created.json(),'a','first');self.assertEqual(first.status_code,200,first.text)
        review=first.json();url='/api/reference-review/reviews/'+review['id']
        first_hash=review['observations'][0]['observation']['observation_sha256']
        for key in ['b','r','v']:
            response=self.client.get(url,headers=self.h(key))
            self.assertIsNone(response.json()['observations'][0]['observation'])
            for secret in ['123.45','synthetic unit',first_hash]:self.assertNotIn(secret,response.text)
        self.assertEqual(self.submit(review,'a-alias','same-user').status_code,403)
        self.assertEqual(self.client.get(url,headers=self.h('other')).status_code,404)
        self.assertEqual(self.client.get(url+'/export',headers=self.h('v')).status_code,409)
        second=self.submit(review,'b','second');self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(second.json()['status'],'agreed')
        self.assertEqual(self.action(second.json(),'freeze','a-alias').status_code,403)
        frozen=self.action(second.json(),'freeze');self.assertEqual(frozen.status_code,200,frozen.text)
        exported=self.client.get(url+'/export',headers=self.h('v'))
        self.assertEqual(exported.status_code,200)
        self.assertFalse(exported.json()['human_gold'])
        self.assertTrue(exported.json()['independence_not_verified'])
        self.assertEqual(exported.content,self.client.get(url+'/export',headers=self.h('v')).content)
        with patch('creditlens.reference_contract.build_reference_catalog',side_effect=FileNotFoundError):
            self.assertEqual(exported.content,self.client.get(url+'/export',headers=self.h('v')).content)

    def test_retry_stale_revision_and_cross_origin_write(self):
        created=self.create().json()
        self.assertEqual(created,self.create().json())
        first=self.submit(created,'a','first')
        self.assertEqual(first.json(),self.submit(created,'a','first').json())
        self.assertEqual(self.submit(created,'b','stale').status_code,409)
        self.assertEqual(self.create(key='b',idem='create-one').status_code,409)
        self.assertEqual(self.client.post('/api/reference-review/reviews',headers={**self.h('a','x'),'Origin':'https://other.invalid'},json={}).status_code,403)

    def test_disagreement_closure_and_new_round_preserve_old(self):
        first=self.submit(self.create().json(),'a','first').json()
        second=self.submit(first,'b','second',{**SYNTHETIC,'raw_value':'999.99'}).json()
        self.assertEqual(second['status'],'disagreed')
        self.assertEqual(self.action(second,'freeze').status_code,409)
        old=self.action(second,'close').json()
        self.assertEqual(old['status'],'closed')
        new=self.create(idem='next-round',previous_review_id=old['id']).json()
        self.assertEqual(new['round'],2);self.assertEqual(new['observations'],[])
        self.assertEqual(self.client.get('/api/reference-review/reviews/'+old['id'],headers=self.h('a')).json(),old)

    def test_strict_body_and_unavailable_catalog(self):
        for raw in ['{"task_id":"x","task_id":"y"}','{"revision":NaN}', '[]']:
            r=self.client.post('/api/reference-review/reviews',headers={**self.h('a','bad'),'Content-Type':'application/json'},content=raw)
            self.assertEqual(r.status_code,422,r.text)
        with patch('creditlens.reference_routes.build_reference_catalog',side_effect=OSError('private/path')):
            r=self.client.get('/api/reference-review/catalog',headers=self.h('a'))
            self.assertEqual(r.status_code,409);self.assertNotIn('private/path',r.text)

    def test_page_is_bound_to_task_and_missing_images_have_clear_status(self):
        review=self.create().json();route='/api/reference-review/reviews/'+review['id']+'/page.png'
        params={'expected_task_sha256':review['task_sha256']}
        with patch('creditlens.reference_routes.VisualStore.image',return_value=b'wrong-page') as image:
            self.assertEqual(self.client.get(route,params={'expected_task_sha256':'0'*64},headers=self.h('a')).status_code,409)
            self.assertEqual(self.client.get(route,params=params,headers=self.h('other')).status_code,404)
            image.assert_not_called()
            self.assertEqual(self.client.get(route,params=params,headers=self.h('a')).status_code,409)
        for error,status in [(FileNotFoundError,404),(PermissionError,409)]:
            with patch('creditlens.reference_routes.VisualStore.image',side_effect=error):
                self.assertEqual(self.client.get(route,params=params,headers=self.h('a')).status_code,status)


if __name__=='__main__':unittest.main()
