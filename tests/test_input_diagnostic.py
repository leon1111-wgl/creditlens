from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app
from creditlens.input_diagnostic import IMPLEMENTATION_FILES, build_input_diagnostic, input_diagnostic_view
from creditlens.input_store import REPORT,RELEASE,input_snapshot
from scripts import evaluate_document_inputs as runner
from tests import test_document_transfer as fixture

ROOT=Path(__file__).resolve().parents[1]


class InputDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.TransferTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.root=self.fixture.root
        for name in IMPLEMENTATION_FILES:
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,path)
        self.report=build_input_diagnostic(self.root)
        self.save(self.report)

    def save(self,report):
        raw=(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode('utf-8')
        (self.root/REPORT).parent.mkdir(parents=True,exist_ok=True)
        (self.root/REPORT).write_bytes(raw)
        (self.root/RELEASE).write_text(json.dumps({'schema_version':1,'files':{REPORT:sha256(raw).hexdigest()}}))

    def test_original_counterexamples_and_scope_are_visible(self):
        result=input_snapshot(self.root)
        self.assertTrue(result['read_only'])
        by_id={c['id']:c for c in result['report']['raw_controls']}
        for identity in ('nan_corner','orphan_text'):
            self.assertTrue(by_id[identity]['legacy']['accepted'])
            self.assertFalse(by_id[identity]['current']['accepted'])
            self.assertTrue(by_id[identity]['passed'])
        self.assertFalse(result['report']['scope']['original_raw_ocr_checked'])
        self.assertEqual(result['report']['scope']['independent_human_gold_count'],0)
        self.assertFalse(result['report']['asset_controls'][1]['current']['accepted'])

    def test_rehashed_semantic_tampering_is_rejected(self):
        for change in ('expected','path','scope','inventory'):
            changed=deepcopy(self.report)
            if change=='expected':changed['raw_controls'][0]['expected_accepted']=False
            if change=='path':changed['raw_controls'][6]['current']['error']['path']='another.field'
            if change=='scope':changed['scope']['original_raw_ocr_checked']=True
            if change=='inventory':changed['inventory']['counts']['unique_pdf_assets']+=1
            self.save(changed)
            with self.subTest(change=change),self.assertRaises(ValueError):input_snapshot(self.root)

    def test_dependency_or_source_drift_is_rejected(self):
        for name in ('creditlens/ocr_input.py','data/examples/independent-transfer.json'):
            path=self.root/name;original=path.read_bytes();path.write_bytes(original+b'\n')
            with self.subTest(name=name),self.assertRaises(ValueError):input_snapshot(self.root)
            path.write_bytes(original)

    def test_unexpected_runtime_error_does_not_pass_a_negative_control(self):
        with patch('creditlens.input_diagnostic.inspect_raw_ocr',side_effect=RuntimeError('unexpected')):
            with self.assertRaises(RuntimeError):build_input_diagnostic(self.root)

    def test_failed_controls_are_saved_in_candidate_and_not_hidden(self):
        failed=deepcopy(self.report)
        failed['raw_controls'][0]['passed']=False;failed['summary']['raw_passed']-=1
        with patch.object(runner,'ROOT',self.root),patch.object(runner,'build_input_diagnostic',return_value=failed),patch.object(runner,'input_diagnostic_view'):
            with self.assertRaisesRegex(ValueError,'complete candidate'):runner.run(Path('failed.json'))
        saved=json.loads((self.root/'.run/reproductions/failed.json').read_text(encoding='utf-8'))
        self.assertFalse(saved['raw_controls'][0]['passed'])
        self.assertEqual(saved['summary']['raw_cases'],25)
        self.assertEqual(json.loads((self.root/REPORT).read_text(encoding='utf-8')),self.report)

    def test_authenticated_api_read_only_and_error_states(self):
        key='input-check-key';headers={'Authorization':'Bearer '+key}
        principal={'key_sha256':sha256(key.encode()).hexdigest(),'user_id':'viewer','tenant_id':'public','role':'viewer'}
        with patch('creditlens.api.ROOT',self.root),TestClient(create_app(self.root/'missing.json',self.root/'audit.sqlite',auth_mode='api_key',principals=[principal])) as client:
            path='/api/document-input-checks'
            self.assertEqual(client.get(path).status_code,401)
            response=client.get(path,headers=headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json(),input_snapshot(self.root))
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(client.post(path,headers=headers).status_code,405)
            (self.root/REPORT).write_text('{}')
            self.assertEqual(client.get(path,headers=headers).status_code,409)
            (self.root/REPORT).unlink()
            self.assertEqual(client.get(path,headers=headers).status_code,503)
