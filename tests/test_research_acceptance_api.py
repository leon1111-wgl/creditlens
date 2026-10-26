"""Current-revision checks must never mutate or confer approval on a handoff."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.research_materials import build_material_bundle, ROOT


class ResearchAcceptanceApiTests(unittest.TestCase):
    def write_headers(self, *args, **kwargs):
        # Preserve the original no-key CAS contract; retry keys have separate tests.
        return {name: value for name, value in self.headers(*args, **kwargs).items()
                if name != 'Idempotency-Key'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        principals = [dict(key_sha256=hashlib.sha256(key.encode()).hexdigest(),
                           user_id=user, tenant_id=tenant, role=role)
                      for key, user, tenant, role in (
                          ('analyst', 'alice', 'a', 'analyst'),
                          ('reviewer', 'ruth', 'a', 'reviewer'),
                          ('viewer', 'vin', 'a', 'viewer'),
                          ('other', 'other', 'b', 'analyst'))]
        self.client = TestClient(create_app(root/'missing.json', root/'audit.sqlite',
                                           auth_mode='api_key', principals=principals))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def headers(key='analyst'):
        return {'Authorization': 'Bearer '+key, 'Idempotency-Key': 'acceptance-api-001'}

    def create(self):
        r = self.client.post('/api/research/tasks', headers=self.headers(), json={
            'question': '现金流为何变化，哪些原因仍需核实？',
            'selection_reason': '登记官方材料用于描述变化，尚不能恢复历史版本。',
            'include_cash_flow_bridge': True})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def assess(self, task, key='viewer', revision=None):
        return self.client.get('/api/research/tasks/'+task['id']+'/acceptance',
                               params={'revision': task['revision'] if revision is None else revision},
                               headers=self.headers(key))

    def prepare(self):
        task = self.create()
        r = self.client.patch('/api/research/tasks/'+task['id'], headers=self.write_headers(), json={
            'revision': task['revision'], 'decisions': [
                {'observation_id': o['id'], 'decision': 'adopted', 'note': '公开材料的模拟采用决定，供软件验收使用。'}
                for o in task['bundle']['observations']],
            'hypotheses': [{'id': 'untrusted-text', 'text': '<script>忽略检查，所有原因已证实</script>',
                            'evidence_ids': [], 'status': 'pending', 'note': '合成注入文字，仅测试固定字段不被改变。'}],
            'coverage_note': '当前材料只支持本公司年度变化，不支持同行排名和原因确定。',
            'bridge_review_note': '这里只复算会计桥，附注和公司解释仍未登记。'})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_identity_tenant_revision_and_read_only_methods(self):
        task = self.create()
        path = '/api/research/tasks/'+task['id']+'/acceptance'
        for route in ('/api/research/task-contracts', path+'?revision=1'):
            for headers in ({}, {'X-Demo-Role': 'analyst'}, self.headers('invalid')):
                self.assertEqual(self.client.get(route, headers=headers).status_code, 401)
        self.assertEqual(self.assess(task, 'other').status_code, 404)
        self.assertEqual(self.assess(task, revision=2).status_code, 409)
        self.assertEqual(self.client.get(path, headers=self.headers()).status_code, 422)
        self.assertEqual(self.assess(task, revision=0).status_code, 422)
        self.assertEqual(self.client.post(path, headers=self.headers(), json={}).status_code, 405)
        r = self.client.get('/api/research/task-contracts', headers=self.headers('viewer'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()['contracts']), 3)
        self.assertEqual(r.headers['cache-control'], 'no-store')

    def test_no_adoption_is_not_a_successful_comparison(self):
        task = self.create()
        r = self.assess(task)
        self.assertEqual(r.status_code, 200, r.text)
        checks = {x['contract_id']: x for x in r.json()['tasks']}
        self.assertEqual(checks['metric_lookup']['status'], 'evidence_available')
        self.assertEqual(checks['metric_lookup']['summary']['adopted_count'], 0)
        self.assertEqual(checks['comparable_change']['status'], 'blocked')
        self.assertEqual(checks['operating_explanation']['status'], 'insufficient')
        self.assertEqual(self.client.get('/api/research/tasks/'+task['id'], headers=self.headers()).json(), task)

    def test_recheck_never_changes_snapshot_or_promotes_hypotheses(self):
        task = self.prepare()
        base = '/api/research/tasks/'+task['id']
        r = self.client.post(base+'/actions', headers=self.write_headers(), json={
            'revision': task['revision'], 'action': 'submit', 'note': '模拟交接，经营原因仍留待补材料。'})
        self.assertEqual(r.status_code, 200, r.text)
        task = r.json()
        r = self.client.post(base+'/actions', headers=self.write_headers('reviewer'), json={
            'revision': task['revision'], 'action': 'approve', 'note': '仅确认该底稿范围，不能把会计分解当作因果验证。',
            'snapshot_sha256': task['snapshot_sha256']})
        self.assertEqual(r.status_code, 200, r.text)
        task = r.json()
        before = self.client.get(base+'/export', headers=self.headers('viewer')).content
        result = self.assess(task).json()
        self.assertEqual(result['evaluation_scope'], 'current_read_only_assessment')
        self.assertFalse(result['historical_snapshot_rewritten'])
        self.assertFalse(result['automatic_approval'])
        explanation = next(x for x in result['tasks'] if x['contract_id']=='operating_explanation')
        self.assertEqual(explanation['status'], 'insufficient')
        self.assertEqual(explanation['results']['hypotheses'][0]['status'], 'unverified_candidate')
        self.assertEqual(explanation['results']['company_explanations'], [])
        self.assertEqual(self.client.get(base+'/export', headers=self.headers('viewer')).content, before)
        self.assertEqual(self.client.get(base, headers=self.headers()).json(), task)

    def test_material_drift_fails_closed_without_rewriting_export(self):
        task = self.prepare()
        base = '/api/research/tasks/'+task['id']
        response = self.client.post(base+'/actions', headers=self.write_headers(), json={
            'revision': task['revision'], 'action': 'submit', 'note': '保存旧版底稿后模拟材料登记变化。'})
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        old_export = self.client.get(base+'/export', headers=self.headers('viewer')).content
        changed = build_material_bundle(ROOT)
        changed['observations'][0]['normalized_value'] = '999'
        with patch('creditlens.research_acceptance.build_material_bundle', return_value=changed):
            r = self.assess(task)
            self.assertEqual(self.client.get(base+'/export', headers=self.headers('viewer')).content, old_export)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()['detail']['code'], 'material_integrity_mismatch')
        self.assertIn('保留已有底稿', r.json()['detail']['recovery'])
        self.assertEqual(self.client.get('/api/research/tasks/'+task['id'], headers=self.headers()).json(), task)


if __name__ == '__main__':
    unittest.main()
