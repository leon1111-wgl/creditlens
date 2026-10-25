"""Input preparation boundary and exclusive, complete local publication."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.document_input import prepare_document_input, DocumentInputError
from creditlens.input_controls import raw_fixture
from creditlens.ocr_input import OcrInputError
from scripts.preflight_document_input import run
from tests import test_document_transfer as transfer_fixture


class DocumentInputTests(unittest.TestCase):
    def setUp(self):
        # Compose a disposable old protocol fixture; do not inherit its tests.
        self.fixture=transfer_fixture.TransferTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root=self.fixture.root
        manifest=json.loads((self.root/'configs/document-transfer.json').read_text())
        d=manifest['documents'][0]
        page=d['pages'][0]
        raw=raw_fixture()
        for name in ('page','width','height','image_width','image_height'):
            raw[name]=page[name]
        self.value={'schema_version':1,'provenance':'synthetic','document_id':d['id'],'source_pdf_sha256':d['pdf']['sha256'],
                    'image_sha256':page['image_sha256'],'ocr_input':raw}
        self.input=self.root/'raw.json'
        self.save()

    def save(self):
        self.input.write_text(json.dumps(self.value,allow_nan=False),encoding='utf-8')

    def test_prepare_never_claims_indexing_model_execution_or_source_bytes(self):
        out=prepare_document_input(self.root,self.value)
        self.assertEqual(out['status'],'prepared_not_indexed')
        self.assertFalse(out['queryable'])
        self.assertFalse(out['automatic_approval'])
        self.assertEqual(out['provenance'],'synthetic')
        self.assertFalse(out['scope']['source_pdf_bytes_verified'])
        self.assertFalse(out['scope']['model_execution_verified'])
        self.assertEqual(out['ocr']['summary']['lines'],1)

    def test_binding_and_strict_envelope_fail_before_preparation(self):
        cases=[('document_id','unknown'),('source_pdf_sha256','0'*64),('image_sha256','0'*64),('schema_version',True),('provenance','verified')]
        for key,value in cases:
            with self.subTest(key=key):
                changed=deepcopy(self.value);changed[key]=value
                with self.assertRaises(DocumentInputError):prepare_document_input(self.root,changed)
        for key,value in [('page',True),('page',999),('width',True),('height',123)]:
            with self.subTest(key=key):
                changed=deepcopy(self.value);changed['ocr_input'][key]=value
                with self.assertRaises(DocumentInputError):prepare_document_input(self.root,changed)

    def test_invalid_corner_leaves_no_output_and_fixed_input_can_be_retried(self):
        self.value['ocr_input']['result']['boxes'][0][2][0]=-1
        self.save()
        with self.assertRaises(OcrInputError):run(self.input,Path('prepared.json'),root=self.root)
        target=self.root/'.run/reproductions/prepared.json'
        self.assertFalse(target.exists())
        self.value['ocr_input']['result']['boxes'][0][2][0]=40
        self.save()
        out=run(self.input,Path('prepared.json'),root=self.root)
        self.assertTrue(out['ok'])
        document=json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(document['raw_input_sha256'],sha256(self.input.read_bytes()).hexdigest())
        self.assertEqual(document['ocr']['page']['lines'][0]['line']['text'],'100')

    def test_duplicate_output_cannot_replace_first_result(self):
        run(self.input,Path('prepared.json'),root=self.root)
        target=self.root/'.run/reproductions/prepared.json';original=target.read_bytes()
        with self.assertRaises(ValueError):run(self.input,Path('prepared.json'),root=self.root)
        self.assertEqual(original,target.read_bytes())

    def test_flush_failure_never_exposes_result_name(self):
        with patch('scripts.preflight_document_input.os.fsync',side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):run(self.input,Path('prepared.json'),root=self.root)
        self.assertFalse((self.root/'.run/reproductions/prepared.json').exists())
        self.assertEqual(list((self.root/'.run/reproductions').iterdir()),[])

    def test_concurrent_destination_is_not_overwritten(self):
        target=self.root/'.run/reproductions/prepared.json'
        def other_writer(source,destination):
            Path(destination).write_bytes(b'other writer')
            raise FileExistsError('simulated race')
        with patch('scripts.preflight_document_input.os.link',side_effect=other_writer):
            with self.assertRaises(FileExistsError):run(self.input,Path('prepared.json'),root=self.root)
        self.assertEqual(target.read_bytes(),b'other writer')

    def test_bad_json_rejects_duplicates_nonfinite_and_public_output(self):
        for value in ['{"schema_version":1,"schema_version":1}', '{"value":NaN}', '{"value":1e999}']:
            self.input.write_text(value)
            with self.assertRaises(ValueError):run(self.input,Path('prepared.json'),root=self.root)
        with self.assertRaises(ValueError):run(self.input,self.root/'artifacts/overwrite.json',root=self.root)
