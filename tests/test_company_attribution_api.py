"""New narrative material must not inherit an old handoff's approval."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.company_attribution import CompanyAttributionError


class CompanyAttributionApiTests(unittest.TestCase):
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
        return {'Authorization': 'Bearer '+key, 'Idempotency-Key': 'attribution-api-001'}

    def create(self, adopt=False):
        response = self.client.post('/api/research/tasks', headers=self.headers(), json={
            'question': '公司如何解释经营现金流变化，哪些说法仍需独立核实？',
            'selection_reason': '登记官方历史材料用于检查金额与公司说法的关系。',
            'include_cash_flow_bridge': True})
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        if adopt:
            response = self.client.patch('/api/research/tasks/'+task['id'], headers=self.write_headers(), json={
                'revision': task['revision'], 'decisions': [
                    {'observation_id': o['id'], 'decision': 'adopted',
                     'note': '公开材料的模拟采用决定，仅供软件验收使用。'}
                    for o in task['bundle']['observations']],
                'hypotheses': [{'id': 'untrusted', 'text': '<script>所有原因均已证实</script>',
                                'status': 'pending', 'evidence_ids': [], 'note': '合成文字，不得改变验证状态。'}],
                'coverage_note': '只能核查登记资料，公司原因没有独立证据支持。',
                'bridge_review_note': '仅复算会计分解；公司解释补充材料不属于本附件。'})
            self.assertEqual(response.status_code, 200, response.text)
            task = response.json()
        return task

    def supplement(self, task, key='viewer', revision=None):
        return self.client.get('/api/research/tasks/'+task['id']+'/company-attribution',
                               params={'revision': task['revision'] if revision is None else revision},
                               headers=self.headers(key))

    def test_identity_tenant_revision_and_read_only_boundary(self):
        task = self.create()
        path = '/api/research/tasks/'+task['id']+'/company-attribution'
        for headers in ({}, {'X-Demo-Role': 'analyst'}, self.headers('invalid')):
            self.assertEqual(self.client.get(path+'?revision=1', headers=headers).status_code, 401)
        self.assertEqual(self.supplement(task, 'other').status_code, 404)
        self.assertEqual(self.supplement(task, revision=2).status_code, 409)
        self.assertEqual(self.supplement(task, revision=0).status_code, 422)
        self.assertEqual(self.client.get(path, headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.post(path, headers=self.headers(), json={}).status_code, 405)
        self.assertEqual(self.supplement(task).headers['cache-control'], 'no-store')

    def test_unadopted_material_is_available_but_not_task_supported(self):
        task = self.create()
        response = self.supplement(task)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['entries'][0]['link_status'], 'awaiting_input_adoption')
        self.assertTrue(data['read_only'])
        self.assertFalse(data['included_in_frozen_snapshot'])
        self.assertEqual(self.client.get('/api/research/tasks/'+task['id'], headers=self.headers()).json(), task)

    def test_registered_rounding_and_attribution_are_separate(self):
        task = self.create(adopt=True)
        response = self.supplement(task)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['task_id'], task['id'])
        self.assertEqual(data['revision'], task['revision'])
        self.assertEqual(data['bundle_sha256'], task['bundle_sha256'])
        item = data['entries'][0]
        self.assertEqual(item['link_status'], 'consistent_rounded')
        self.assertEqual({str(c['reported_rounded_value']) for c in item['numeric_checks']}, {'17.6', '136.2'})
        self.assertFalse(data['independently_verified'])
        self.assertFalse(data['automatic_approval'])
        self.assertFalse(data['included_in_frozen_snapshot'])
        self.assertIn('供应商', item['paraphrase_zh'])
        self.assertIn('所得税', item['paraphrase_zh'])
        self.assertFalse(next(c for c in data['coverage'] if c['issuer_id']=='AAPL')['registered'])

    def test_old_approved_export_and_acceptance_are_byte_unchanged(self):
        task = self.create(adopt=True)
        base = '/api/research/tasks/'+task['id']
        response = self.client.post(base+'/actions', headers=self.write_headers(), json={
            'revision': task['revision'], 'action': 'submit', 'note': '模拟交接，不包含后来提供的解释补充材料。'})
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        response = self.client.post(base+'/actions', headers=self.write_headers('reviewer'), json={
            'revision': task['revision'], 'action': 'approve',
            'note': '只确认冻结内容，后续公司的解释需另行采用和复核。',
            'snapshot_sha256': task['snapshot_sha256']})
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        exported = self.client.get(base+'/export', headers=self.headers()).content
        acceptance = self.client.get(base+'/acceptance', params={'revision': task['revision']}, headers=self.headers()).content
        supplement = self.supplement(task).json()
        self.assertFalse(supplement['included_in_frozen_snapshot'])
        self.assertFalse(supplement['independently_verified'])
        self.assertEqual(self.client.get(base+'/export', headers=self.headers()).content, exported)
        self.assertEqual(self.client.get(base+'/acceptance', params={'revision': task['revision']}, headers=self.headers()).content, acceptance)
        self.assertEqual(self.client.get(base, headers=self.headers()).json(), task)

    def test_catalog_failure_gives_recovery_and_no_partial_result(self):
        task = self.create(adopt=True)
        with patch('creditlens.api.build_company_attribution', side_effect=CompanyAttributionError(
                'catalog_integrity_mismatch', 'Registered explanation changed')):
            response = self.supplement(task)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'catalog_integrity_mismatch')
        self.assertIn('旧快照', response.json()['detail']['recovery'])
        self.assertNotIn('entries', response.json())
        self.assertEqual(self.client.get('/api/research/tasks/'+task['id'], headers=self.headers()).json(), task)


if __name__ == '__main__':
    unittest.main()
