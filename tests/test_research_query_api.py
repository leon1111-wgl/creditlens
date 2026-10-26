"""Explicit read-only queries use isolated demo workflows and registered facts."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.research_query import ResearchQueryError


class ResearchQueryApiTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        principals = [dict(key_sha256=hashlib.sha256(key.encode()).hexdigest(),
                           user_id=user, tenant_id=tenant, role=role)
                      for key, user, tenant, role in (
                          ('author', 'author', 'a', 'analyst'),
                          ('reviewer', 'reviewer', 'a', 'reviewer'),
                          ('viewer', 'viewer', 'a', 'viewer'),
                          ('other', 'other', 'b', 'viewer'))]
        self.app = create_app(root/'missing.json', root/'audit.sqlite',
                              auth_mode='api_key', principals=principals)
        self.client = self.enterContext(TestClient(self.app))
        response = self.client.post('/api/research/tasks',
            headers={**self.headers(), 'Idempotency-Key': 'query-api-create'}, json={
                'question': '未登记公司的毛利率明年为什么增长？自由问题仅作背景。',
                'selection_reason': '模拟任务使用官方历史材料检验明确选择范围。',
                'include_cash_flow_bridge': True})
        self.assertEqual(response.status_code, 201)
        self.task = response.json()

    @staticmethod
    def headers(key='author'):
        return {'Authorization': 'Bearer '+key}

    def payload(self, kind='metric_lookup', issuer='MSFT', metric='cfo', year=2025):
        observations=[dict(issuer_id=issuer, metric=metric, year=year)]
        if kind != 'metric_lookup':
            observations=[dict(issuer_id=issuer, metric=metric, year=2024),observations[0]]
        return dict(revision=self.task['revision'], task_kind=kind, observations=observations)

    def query(self, payload=None, key='viewer', task_id=None):
        return self.client.post('/api/research/tasks/'+(task_id or self.task['id'])+'/evidence-query',
                                json=payload or self.payload(), headers=self.headers(key))

    def adopt(self):
        response=self.client.patch('/api/research/tasks/'+self.task['id'], headers=self.headers(), json={
            'revision': self.task['revision'],
            'decisions':[dict(observation_id=o['id'],decision='adopted',note='模拟采用；不构成独立人工财务参考。') for o in self.task['bundle']['observations']],
            'hypotheses':[], 'coverage_note':'年度边界按登记原文核对，仅描述自身变化。',
            'bridge_review_note':'会计分解不证明经营原因，解释仍需核实。'})
        self.assertEqual(response.status_code,200,response.text);self.task=response.json()

    def test_identity_tenant_and_revision_checks_precede_builder(self):
        url='/api/research/tasks/'+self.task['id']+'/evidence-query'
        with patch('creditlens.research_query_routes.build_research_query') as build:
            self.assertEqual(self.client.post(url,json=self.payload()).status_code,401)
            self.assertEqual(self.query(key='other').status_code,404)
            self.assertEqual(self.query(task_id='missing-task').status_code,404)
            payload=self.payload();payload['revision']+=1
            self.assertEqual(self.query(payload).status_code,409)
            build.assert_not_called()

    def test_viewer_queries_only_selected_evidence_without_writes(self):
        before=self.client.get('/api/research/tasks/'+self.task['id'],headers=self.headers()).content
        response=self.query();self.assertEqual(response.status_code,200,response.text)
        value=response.json();self.assertEqual(value['query'],self.payload())
        self.assertEqual(len(value['selected_evidence']),1)
        self.assertEqual(value['selected_evidence'][0]['card']['issuer_id'],'MSFT')
        self.assertEqual(value['status'],'evidence_available')
        self.assertTrue(value['read_only']);self.assertFalse(value['natural_language_question_understood'])
        self.assertFalse(value['automatic_approval']);self.assertFalse(value['included_in_frozen_snapshot'])
        after=self.client.get('/api/research/tasks/'+self.task['id'],headers=self.headers()).content
        self.assertEqual(before,after)

    def test_unregistered_choice_is_not_zero_or_disclosure_claim(self):
        for issuer,metric,year in [('MSFT','cfo',2023),('NVDA','cfo',2025),('AAPL','total_capital_investment',2025)]:
            with self.subTest(issuer=issuer,metric=metric,year=year):
                value=self.query(self.payload(issuer=issuer,metric=metric,year=year)).json()
                self.assertEqual(value['status'],'not_registered')
                self.assertIsNone(value['selected_evidence'][0]['card'])
                self.assertIsNone(value['calculation'])

    def test_adoption_blocks_comparison_and_correct_pair_computes(self):
        request=self.payload('comparable_change')
        self.assertEqual(self.query(request).json()['status'],'blocked')
        self.adopt();request=self.payload('comparable_change')
        value=self.query(request).json();self.assertEqual(value['status'],'computed')
        old=self.task['comparison']['annual_changes']
        expected=next(c for c in old if c['issuer_id']=='MSFT' and c['metric']=='cfo')
        self.assertEqual(value['calculation']['absolute_change'],expected['absolute_change'])
        self.assertEqual(value['calculation']['formula'],expected['formula'])
        self.assertEqual(len(value['calculation']['inputs']),2)

    def test_cross_company_reverse_and_duplicate_do_not_compute(self):
        self.adopt();normal=self.payload('comparable_change');cases=[]
        cross=deepcopy(normal);cross['observations'][1]['issuer_id']='AAPL';cases.append(cross)
        reverse=deepcopy(normal);reverse['observations'].reverse();cases.append(reverse)
        duplicate=deepcopy(normal);duplicate['observations'][1]=duplicate['observations'][0];cases.append(duplicate)
        for request in cases:
            with self.subTest(request=request):
                value=self.query(request).json();self.assertEqual(value['status'],'blocked')
                self.assertIsNone(value['calculation']);self.assertTrue(value['gaps'])

    def test_operating_evidence_keeps_disclosure_and_independent_checks_separate(self):
        self.adopt()
        for issuer,count in [('MSFT',1),('AAPL',0)]:
            with self.subTest(issuer=issuer):
                value=self.query(self.payload('operating_evidence',issuer=issuer)).json()
                self.assertEqual(value['status'],'insufficient')
                operating=value['operating_evidence']
                self.assertEqual(len(operating['company_statements']),count)
                self.assertFalse(operating['independent_verification']['independently_verified'])
                self.assertIsNone(operating['causal_conclusion'])

    def test_research_snapshot_and_current_record_unchanged_by_new_queries(self):
        self.adopt()
        response=self.client.post('/api/research/tasks/'+self.task['id']+'/actions',headers=self.headers(),
            json={'revision':self.task['revision'],'action':'submit','note':'模拟提交底稿供只读范围查询检验，不能作为原因证明。'})
        self.assertEqual(response.status_code,200,response.text);self.task=response.json()
        url='/api/research/tasks/'+self.task['id']+'/export'
        before=self.client.get(url,headers=self.headers()).content
        for kind in ['metric_lookup','comparable_change','operating_evidence']:
            self.assertEqual(self.query(self.payload(kind)).status_code,200)
        self.assertEqual(before,self.client.get(url,headers=self.headers()).content)
        self.assertEqual(self.task,self.client.get('/api/research/tasks/'+self.task['id'],headers=self.headers()).json())

    def test_malformed_selector_and_cardinality_are_rejected_before_builder(self):
        good=self.payload();cases=[]
        for field,value in [('revision',True),('revision','1'),('task_kind','answer_anything'),('observations',[])]:
            case=deepcopy(good);case[field]=value;cases.append(case)
        for field,value in [('year',2025.0),('year','2025'),('year',True),('metric',' '),('issuer_id',5)]:
            case=deepcopy(good);case['observations'][0][field]=value;cases.append(case)
        case=deepcopy(good);case['instructions']='execute me';cases.append(case)
        case=deepcopy(good);case['observations']*=2;cases.append(case)
        with patch('creditlens.research_query_routes.build_research_query') as build:
            for case in cases:self.assertEqual(self.query(case).status_code,422)
            build.assert_not_called()

    def test_material_failure_is_controlled_and_preserves_record(self):
        with patch('creditlens.research_query_routes.build_research_query',
                   side_effect=ResearchQueryError('source_unavailable','source','登记来源暂不可用。')):
            response=self.query();self.assertEqual(response.status_code,409)
            self.assertEqual(response.json()['detail']['code'],'source_unavailable')
        self.assertEqual(self.task,self.client.get('/api/research/tasks/'+self.task['id'],headers=self.headers()).json())
