"""HTTP boundaries for separately reviewed company-statement supplements."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from creditlens.api import create_app


class ExplanationReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        principals = [dict(key_sha256=hashlib.sha256(key.encode()).hexdigest(),
                           user_id=user, tenant_id=tenant, role=role)
                      for key, user, tenant, role in (
                          ('analyst', 'alice', 'a', 'analyst'),
                          ('reviewer', 'ruth', 'a', 'reviewer'),
                          ('self-reviewer', 'alice', 'a', 'reviewer'),
                          ('viewer', 'vin', 'a', 'viewer'),
                          ('other', 'outsider', 'b', 'analyst'))]
        self.client = TestClient(create_app(root/'missing.json', root/'audit.sqlite',
                                           auth_mode='api_key', principals=principals))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def headers(key='analyst'):
        return {'Authorization': 'Bearer '+key, 'Idempotency-Key': 'explanation-api-001'}

    def parent(self, approved=True):
        response = self.client.post('/api/research/tasks', headers=self.headers(), json={
            'question': '怎样区分经营现金流的会计分解和公司的经营解释？',
            'selection_reason': '仅使用登记的官方资料验证新增解释的独立复核流程。',
            'include_cash_flow_bridge': True})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        base = '/api/research/tasks/'+data['id']
        if not approved:
            return data
        response = self.client.patch(base, headers=self.headers(), json={
            'revision': data['revision'], 'decisions': [
                {'observation_id': item['id'], 'decision': 'adopted',
                 'note': '模拟核查已登记原文及期间口径，仅作软件验证。'}
                for item in data['bundle']['observations']],
            'hypotheses': [], 'coverage_note': '只复核公开事实和计算，不证明经营原因。',
            'bridge_review_note': '会计分解对应原始金额；新增解释须另行处理。'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        response = self.client.post(base+'/actions', headers=self.headers(), json={
            'revision': data['revision'], 'action': 'submit',
            'note': '提交公开材料的模拟交接，解释补充另行采用。'})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        response = self.client.post(base+'/actions', headers=self.headers('reviewer'), json={
            'revision': data['revision'], 'action': 'approve',
            'note': '模拟独立确认冻结事实范围，不确认任何经营因果。',
            'snapshot_sha256': data['snapshot_sha256']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create(self, parent):
        response = self.client.post('/api/research/tasks/'+parent['id']+'/explanation-reviews',
                                    headers=self.headers(), json={
                                        'parent_revision': parent['revision'],
                                        'parent_snapshot_sha256': parent['snapshot_sha256']})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def save(self, data, decision='adopt_as_company_statement'):
        response = self.client.patch('/api/research/explanation-reviews/'+data['id'],
                                     headers=self.headers(), json={
                                         'revision': data['revision'],
                                         'decisions': [{'entry_id': entry['id'], 'decision': decision,
                                                        'note': '公司明示解释与原数字一致，尚无独立原因证据。'}
                                                       for entry in data['evidence']['attribution']['entries']],
                                         'coverage_note': '只对公司说法及金额对应作出处理，缺少外部独立原因证据。'})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def act(self, data, action, key='analyst', **changes):
        payload = {'revision': data['revision'], 'action': action,
                   'note': '模拟复核处理意见与证据范围，保留尚未解决的缺口。',
                   'snapshot_sha256': data.get('snapshot', {}).get('sha256') if data.get('snapshot') else None}
        payload.update(changes)
        return self.client.post('/api/research/explanation-reviews/'+data['id']+'/actions',
                                headers=self.headers(key), json=payload)

    def test_auth_tenant_strict_payload_and_pending_parent(self):
        draft = self.parent(approved=False)
        base = '/api/research/tasks/'+draft['id']+'/explanation-reviews'
        self.assertEqual(self.client.get(base).status_code, 401)
        self.assertEqual(self.client.get(base, headers=self.headers('other')).status_code, 404)
        payload = {'parent_revision': draft['revision'], 'parent_snapshot_sha256': 'a'*64}
        self.assertEqual(self.client.post(base, json=payload, headers=self.headers()).status_code, 409)
        self.assertEqual(self.client.post(base, json={**payload, 'evidence': {}},
                                         headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.post(base, json=payload, headers=self.headers('viewer')).status_code, 403)

    def test_lifecycle_preserves_old_exports_and_parent(self):
        parent = self.parent()
        parent_base = '/api/research/tasks/'+parent['id']
        parent_bytes = self.client.get(parent_base+'/export', headers=self.headers()).content
        data = self.create(parent)
        self.assertEqual(self.create(parent), data)
        base = '/api/research/explanation-reviews/'+data['id']
        self.assertEqual(self.client.get(base, headers=self.headers('other')).status_code, 404)
        self.assertEqual(self.client.get(base, headers=self.headers('viewer')).headers['cache-control'], 'no-store')
        self.assertNotIn('parent_export', data)
        self.assertIn('parent_export', data['evidence'])
        data = self.save(data, 'needs_evidence')
        response = self.act(data, 'submit')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(self.act(data, 'approve', 'self-reviewer').status_code, 403)
        self.assertEqual(self.act(data, 'approve', 'reviewer', snapshot_sha256='0'*64).status_code, 409)
        self.assertEqual(self.act(data, 'approve', 'reviewer', note='太短').status_code, 422)
        response = self.act(data, 'return', 'reviewer')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        old_id = data['snapshot']['id']
        old_bytes = self.client.get(base+'/export', params={'snapshot_id': old_id}, headers=self.headers()).content
        data = self.save(data)
        response = self.act(data, 'submit')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        response = self.act(data, 'approve', 'reviewer')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['status'], 'reviewed')
        self.assertFalse(data['independently_verified'])
        self.assertEqual(len(data['snapshots']), 2)
        self.assertEqual(self.client.get(base+'/export', params={'snapshot_id': old_id},
                                         headers=self.headers()).content, old_bytes)
        self.assertEqual(self.client.get(parent_base+'/export', headers=self.headers()).content, parent_bytes)
        self.assertEqual(self.client.get(parent_base, headers=self.headers()).json(), parent)
        self.assertEqual(self.act(data, 'submit').status_code, 409)

    def test_revision_and_source_scope_cannot_be_overridden(self):
        parent = self.parent()
        data = self.create(parent)
        base = '/api/research/explanation-reviews/'+data['id']
        entry = data['evidence']['attribution']['entries'][0]
        payload = {'revision': data['revision'], 'decisions': [{'entry_id': entry['id'],
                    'decision': 'adopt_as_company_statement', 'note': '只采用公司说法，不表示经营原因已经证实。'}],
                    'coverage_note': '原始材料说明范围，不赋予额外的因果证明。'}
        self.assertEqual(self.client.patch(base, headers=self.headers(),
                                         json={**payload, 'independently_verified': True}).status_code, 422)
        payload['decisions'][0]['support_scope'] = 'causal_proof'
        self.assertEqual(self.client.patch(base, headers=self.headers(), json=payload).status_code, 422)
        del payload['decisions'][0]['support_scope']
        self.assertEqual(self.client.patch(base, headers=self.headers('viewer'), json=payload).status_code, 403)
        saved = self.save(data)
        self.assertEqual(self.client.patch(base, headers=self.headers(), json=payload).status_code, 409)
        self.assertEqual(saved['decisions'][0]['support_scope'], 'company_statement_only')
        self.assertEqual(self.client.get(base, headers=self.headers()).json(), saved)


if __name__ == '__main__':
    unittest.main()
