"""HTTP integration against the registered official materials, with temporary state."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from creditlens.api import create_app


class ResearchTasksApiTests(unittest.TestCase):
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
                          ('analyst-key', 'alice', 'a', 'analyst'),
                          ('review-key', 'ruth', 'a', 'reviewer'),
                          ('self-key', 'alice', 'a', 'reviewer'),
                          ('viewer-key', 'vin', 'a', 'viewer'),
                          ('other-key', 'bob', 'b', 'analyst'))]
        self.client = TestClient(create_app(root / 'missing.json', root / 'audit.sqlite',
                                           auth_mode='api_key', principals=principals))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def headers(self, key='analyst-key', idem='research-api-test-001'):
        return {'Authorization': 'Bearer ' + key, 'Idempotency-Key': idem}

    def create(self, **changes):
        body = dict(bundle_id='msft-aapl-fy2025', question='What changed in annual operating cash flow?',
                    selection_reason='Compare two official cases, without treating them as a peer benchmark.',
                    material_mode='current_verified_materials', as_of=None)
        body.update(changes)
        return self.client.post('/api/research/tasks', json=body, headers=self.headers())

    def test_cash_flow_attachment_create_edit_submit_and_export(self):
        response = self.create(include_cash_flow_bridge=True)
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        self.assertEqual(len(task['cash_flow_bridge']['statements']), 4)
        body = dict(revision=task['revision'], decisions=[
            dict(observation_id=o['id'], decision='adopted', note='Registered original statement inspected.')
            for o in task['bundle']['observations']], hypotheses=[],
            coverage_note='Current report comparative columns; no historical or causal conclusion.',
            bridge_review_note='Accounting reconciliation only; underlying business causes remain unverified.')
        response = self.client.patch('/api/research/tasks/' + task['id'], json=body, headers=self.write_headers())
        self.assertEqual(response.status_code, 200, response.text)
        response = self.action(response.json(), 'submit')
        self.assertEqual(response.status_code, 200, response.text)
        content = response.json()['snapshot']['content']
        self.assertEqual(content['store_version'], 'research-handoff-2')
        self.assertEqual(content['bridge_review_note'], body['bridge_review_note'])
        response = self.client.get('/api/research/tasks/' + task['id'] + '/export', headers=self.headers('viewer-key'))
        self.assertEqual(response.status_code, 200, response.text)
        from scripts.verify_research_export import verify_export
        self.assertTrue(verify_export(response.json())['ok'])

    def prepared(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        task = response.json()
        body = dict(revision=task['revision'], decisions=[
            dict(observation_id=o['id'], decision='adopted',
                 note='Registered source and period inspected; scope remains statement total.')
            for o in task['bundle']['observations']],
            hypotheses=[dict(id='working-capital', text='Working capital may explain part of the change.',
                             evidence_ids=[task['bundle']['observations'][0]['id']], status='pending',
                             note='Cash flow totals do not establish causality; inspect reconciliation next.')],
            coverage_note='Fiscal dates and calendars differ. Within-company changes only, without ranking.')
        response = self.client.patch('/api/research/tasks/' + task['id'], json=body, headers=self.write_headers())
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, task, action, key='analyst-key', **changes):
        body = dict(revision=task['revision'], action=action,
                    note='Checked source scope; unresolved explanations stay pending.',
                    snapshot_sha256=task['snapshot']['sha256'] if task['snapshot'] else None)
        body.update(changes)
        return self.client.post('/api/research/tasks/' + task['id'] + '/actions',
                                headers=self.write_headers(key), json=body)

    def test_materials_and_tasks_require_real_configured_identity(self):
        for route in ('/api/research/task-materials', '/api/research/tasks'):
            for headers in ({}, {'X-Demo-Role': 'analyst'}, self.headers('bad-key')):
                self.assertEqual(self.client.get(route, headers=headers).status_code, 401)
        response = self.client.get('/api/research/task-materials', headers=self.headers('viewer-key'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['observations']), 8)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        # The shell is public, but carries no saved task or credential.
        shell = self.client.get('/tasks')
        self.assertEqual(shell.status_code, 200)
        self.assertNotIn('analyst-key', shell.text)

    def test_request_contract_idempotency_roles_and_origin(self):
        task = self.create().json()
        self.assertEqual(self.create().json()['id'], task['id'])
        self.assertEqual(self.create(question='A different research question').status_code, 409)
        self.assertEqual(self.create(bundle_id='unregistered').status_code, 422)
        self.assertEqual(self.create(material_mode='historical_as_of', as_of='2025-01-01').status_code, 422)
        self.assertEqual(self.create(as_of='2025-01-01').status_code, 422)
        body = dict(question='A valid new question', selection_reason='Official test cases with explicit limits')
        for key in ('viewer-key', 'review-key'):
            self.assertEqual(self.client.post('/api/research/tasks', json=body,
                                              headers=self.headers(key)).status_code, 403)
        self.assertEqual(self.client.post('/api/research/tasks', json=body,
                                          headers={'Authorization': 'Bearer analyst-key'}).status_code, 422)
        self.assertEqual(self.client.post('/api/research/tasks', json=body,
                                          headers={**self.headers(), 'Origin': 'https://other.invalid'}).status_code, 403)

    def test_research_to_independent_review_export_preserves_content(self):
        task = self.prepared()
        rows = task['comparison']['annual_changes']
        actual = {(r['issuer_id'], r['metric']): r['absolute_change'] for r in rows}
        self.assertEqual(actual, {('MSFT', 'cfo'): '17614', ('MSFT', 'capex'): '20074',
                                  ('AAPL', 'cfo'): '-6772', ('AAPL', 'capex'): '3268'})
        response = self.action(task, 'submit')
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        self.assertEqual(task['status'], 'in_review')
        snapshot = task['snapshot']
        canonical = json.dumps(snapshot['content'], sort_keys=True, ensure_ascii=False,
                               separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), snapshot['sha256'])
        self.assertEqual(self.action(task, 'approve', 'self-key').status_code, 403)
        self.assertEqual(self.action(task, 'approve', 'review-key', snapshot_sha256='0' * 64).status_code, 409)
        response = self.action(task, 'approve', 'review-key')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['status'], 'reviewed')
        path = '/api/research/tasks/' + task['id']
        exported = self.client.get(path + '/export', params={'snapshot_id': snapshot['id']},
                                   headers=self.headers('viewer-key'))
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()['snapshot'], snapshot)
        self.assertEqual(self.client.get(path + '/snapshots/' + snapshot['id'],
                                         headers=self.headers()).json()['snapshot'], snapshot)
        self.assertFalse(exported.json()['automatic_approval'])
        self.assertEqual(snapshot['content']['hypotheses'][0]['status'], 'pending')

    def test_tenant_isolation_including_snapshot_and_export(self):
        task = self.prepared()
        task = self.action(task, 'submit').json()
        path = '/api/research/tasks/' + task['id']
        for route in (path, path + '/export', path + '/snapshots/' + task['snapshot']['id']):
            self.assertEqual(self.client.get(route, headers=self.headers('other-key')).status_code, 404)
        self.assertEqual(self.client.get('/api/research/tasks', headers=self.headers('other-key')).json(), [])
        self.assertEqual(self.action(task, 'return', 'other-key').status_code, 403)

    def test_incomplete_decisions_and_unsaved_revision_cannot_submit(self):
        task = self.create().json()
        self.assertEqual(self.action(task, 'submit').status_code, 422)
        self.assertEqual(self.client.get('/api/research/tasks/' + task['id'] + '/export',
                                         headers=self.headers()).status_code, 409)
        ready = self.prepared()
        self.assertEqual(self.action(ready, 'submit', revision=task['revision']).status_code, 409)
        bad = dict(revision=ready['revision'], decisions=ready['decisions'], coverage_note=ready['coverage_note'],
                   hypotheses=[{**ready['hypotheses'][0], 'status': 'supported'}])
        self.assertEqual(self.client.patch('/api/research/tasks/' + task['id'], headers=self.write_headers(),
                                           json=bad).status_code, 422)


if __name__ == '__main__':
    unittest.main()
