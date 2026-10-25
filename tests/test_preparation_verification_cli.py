"""Offline reader boundaries; synthetic exports, no model or network."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from creditlens.preparation_verifier import PreparationVerificationError
from scripts.verify_preparation_export import MAX_BYTES, ROOT, main, verify_file


class PreparationVerificationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'export.json'
        self.bundle = json.loads((ROOT/'artifacts/document-preparation-replay.json').read_text(encoding='utf-8'))
        self.value = deepcopy(self.bundle['attempt_exports'][0])

    def write(self, value=None):
        self.path.write_text(json.dumps(self.value if value is None else value), encoding='utf-8')
        return self.path

    def test_single_snapshot_and_explicit_bundle_selection_are_read_only(self):
        self.write()
        before = self.path.read_bytes()
        result = verify_file(self.path)
        self.assertEqual(result['verification']['status'], 'internally_consistent')
        self.assertFalse(result['current_registration_checked'])
        self.assertEqual(self.path.read_bytes(), before)
        self.write(self.bundle)
        self.assertEqual(verify_file(self.path, attempt_index=2)['verification']['revision'], 3)
        for index in (-1, True, 3, '0'):
            with self.assertRaises(PreparationVerificationError):
                verify_file(self.path, attempt_index=index)

    def test_strict_json_rejects_ambiguous_or_nonfinite_inputs(self):
        for raw in (b'{"private_marker":0,"private_marker":1}', b'{"x":NaN}', b'{"x":1e999}',
                    b'\xff', b'['*1500+b']'*1500):
            self.path.write_bytes(raw)
            with self.assertRaises(PreparationVerificationError):
                verify_file(self.path)

    def test_nonlocal_missing_and_oversized_files_rejected(self):
        for path in (Path('https://example.test/export.json'), Path('//server/share/export.json'), self.path):
            with self.assertRaises(PreparationVerificationError):
                verify_file(path)
        self.path.write_bytes(b' '*(MAX_BYTES+1))
        with self.assertRaises(PreparationVerificationError) as error:
            verify_file(self.path)
        self.assertEqual(error.exception.code, 'file_too_large')

    def test_unavailable_current_registration_does_not_invalidate_history(self):
        self.write()
        result = verify_file(self.path, check_current=True, root=Path(self.temp.name))
        self.assertEqual(result['verification']['status'], 'internally_consistent')
        self.assertEqual(result['applicability']['status'], 'registration_unavailable')
        self.assertFalse(result['applicability']['ready_for_content_review'])

    def test_cli_failure_returns_controlled_diagnostic_without_original_input(self):
        self.path.write_text('{"PRIVATE_CLI_CANARY":1,"PRIVATE_CLI_CANARY":2}', encoding='utf-8')
        output = io.StringIO()
        with redirect_stdout(output):
            code = main([str(self.path)])
        self.assertEqual(code, 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value['code'], 'duplicate_json_key')
        self.assertNotIn('PRIVATE_CLI_CANARY', output.getvalue())


if __name__ == '__main__':
    unittest.main()
