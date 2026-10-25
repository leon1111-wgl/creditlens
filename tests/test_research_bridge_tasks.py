"""Cash-flow attachment handoff controls; not model or financial-effect evaluation."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from creditlens.research_task_store import ResearchTaskStore, _sha
from creditlens.workflow import Actor
from scripts.verify_research_export import verify_export, ExportError

ROOT = Path(__file__).resolve().parents[1]


class ResearchBridgeTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ResearchTaskStore(Path(self.tmp.name) / 'state.sqlite', ROOT)
        self.analyst = Actor('analyst', 'team', 'analyst')
        self.reviewer = Actor('reviewer', 'team', 'reviewer')
        self.payload = dict(bundle_id='msft-aapl-fy2025', question='Which statement rows explain the cash flow reconciliation?',
                            selection_reason='Two official annual reports; accounting decomposition only.',
                            material_mode='current_verified_materials', as_of=None)

    def create(self, bridge=True, key='attachment-create-1'):
        return self.store.create(self.analyst, dict(self.payload, include_cash_flow_bridge=bridge), key)

    def edit(self, task, note='Registered statement arithmetic checked; operating causes remain unverified.', reject=False):
        decisions = [dict(observation_id=o['id'], decision='rejected' if reject and o['metric']=='cfo' else 'adopted',
                          note='Registered evidence inspected; this is a scoped demonstration decision.')
                     for o in task['bundle']['observations']]
        return self.store.update(task['id'], self.analyst, task['revision'], decisions, [],
                                 'Current materials only; no historical investment claim or causal conclusion.', note)

    def submit(self, task):
        return self.store.act(task['id'], self.analyst, task['revision'], 'submit', 'Submit scoped accounting research')

    def http(self, code, fn, *args):
        with self.assertRaises(HTTPException) as caught:
            fn(*args)
        self.assertEqual(caught.exception.status_code, code, caught.exception.detail)

    def test_attachment_binds_four_totals_and_freezes_all_signed_evidence(self):
        task = self.create()
        bridge = task['cash_flow_bridge']
        self.assertEqual(task['store_version'], 'research-handoff-2')
        self.assertEqual(len(bridge['statements']), 4)
        self.assertEqual(sum(len(s['facts']) for s in bridge['source_bundle']['statements']), 52)
        self.assertTrue(all(s['residual']=='0' and s['status']=='reconciled' for s in bridge['statements']))
        submitted = self.submit(self.edit(task))
        self.assertEqual(submitted['snapshot']['content']['cash_flow_bridge'], bridge)
        approved = self.store.act(task['id'], self.reviewer, submitted['revision'], 'approve',
                                  'Confirmed accounting scope, without certifying causal claims.', submitted['snapshot']['sha256'])
        exported = self.store.export(task['id'], self.reviewer)
        self.assertTrue(verify_export(exported)['ok'])
        self.assertEqual(approved['status'], 'reviewed')

    def test_note_required_and_omitted_update_preserves_existing_note(self):
        task = self.edit(self.create(), '')
        self.http(422, self.submit, task)
        task = self.edit(task)
        note = task['bridge_review_note']
        updated = self.store.update(task['id'], self.analyst, task['revision'], task['decisions'], [], task['coverage_note'])
        self.assertEqual(updated['bridge_review_note'], note)
        self.assertEqual(self.submit(updated)['status'], 'in_review')

    def test_rejected_totals_are_not_implicitly_adopted_by_attachment(self):
        task = self.edit(self.create(), reject=True)
        rejected = {d['observation_id'] for d in task['decisions'] if d['decision']=='rejected'}
        self.assertEqual(len(rejected), 4)
        self.assertTrue(rejected.isdisjoint(task['comparison']['adopted_observation_ids']))
        self.assertEqual(len(task['cash_flow_bridge']['statements']), 4)
        self.assertEqual(self.submit(task)['status'], 'in_review')

    def test_changed_rules_prevent_submit_but_frozen_export_does_not_reload(self):
        task = self.edit(self.create())
        changed = deepcopy(task['cash_flow_bridge']); changed['rule_version'] += '-changed'
        with patch.object(self.store, '_cash_flow_bridge', return_value=changed):
            self.http(409, self.submit, task)
        submitted = self.submit(task)
        before = self.store.export(task['id'], self.reviewer)
        with patch.object(self.store, '_cash_flow_bridge', side_effect=RuntimeError('live source unavailable')):
            self.assertEqual(self.store.export(task['id'], self.reviewer), before)
            self.assertEqual(self.store.get(task['id'], self.reviewer)['snapshot'], submitted['snapshot'])

    def test_legacy_default_and_false_share_idempotency_and_no_new_content_fields(self):
        old = self.store.create(self.analyst, self.payload, 'legacy-request-1')
        retry = self.create(False, 'legacy-request-1')
        self.assertEqual(old, retry)
        self.assertNotIn('cash_flow_bridge', old)
        self.http(422, self.edit, old)
        old = self.edit(old, None)
        exported = self.store.export(self.submit(old)['id'], self.reviewer)
        self.assertTrue(verify_export(exported)['ok'])
        self.assertNotIn('bridge_review_note', exported['snapshot']['content'])

    def test_attachment_stays_inside_tenant_and_independent_review_boundary(self):
        task = self.submit(self.edit(self.create()))
        self.http(404, self.store.export, task['id'], Actor('other', 'other', 'reviewer'))
        self.http(403, self.store.act, task['id'], Actor('analyst', 'team', 'reviewer'), task['revision'],
                  'approve', 'Try self review', task['snapshot']['sha256'])

    def test_nested_attachment_and_source_hashes_checked_after_outer_rehash(self):
        task = self.submit(self.edit(self.create()))
        original = self.store.export(task['id'], self.reviewer)
        for source_hash in (False, True):
            exported = deepcopy(original); content = exported['snapshot']['content']
            if source_hash:
                content['cash_flow_bridge']['source_bundle_sha256'] = '0'*64
                content['cash_flow_bridge_sha256'] = _sha(content['cash_flow_bridge'])
            else:
                content['cash_flow_bridge']['rule_version'] += '-changed'
            exported['snapshot']['sha256'] = _sha(content)
            with self.assertRaises(ExportError) as caught:
                verify_export(exported)
            self.assertEqual(caught.exception.code, 'bridge_source_hash_mismatch' if source_hash else 'bridge_hash_mismatch')

    def test_malformed_store_version_reports_structured_failure(self):
        document = json.loads((ROOT/'artifacts/research-task-demo.json').read_text(encoding='utf-8'))
        for bad in ([], {}, None, 2):
            broken = deepcopy(document); broken['snapshot']['content']['store_version'] = bad
            with self.subTest(bad=bad), self.assertRaises(ExportError):
                verify_export(broken)
