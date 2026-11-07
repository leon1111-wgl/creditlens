"""The shared presentation assets must be served without exposing project files."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from creditlens.api import create_app


class WorkspaceAssetTests(unittest.TestCase):
    def test_shared_assets_are_served_with_correct_types(self):
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(create_app(audit_db=Path(directory) / 'audit.sqlite')) as client:
                for name, content_type in [('workspace.css', 'text/css'),
                                           ('workspace.js', 'javascript')]:
                    with self.subTest(name=name):
                        response = client.get('/assets/' + name)
                        self.assertEqual(response.status_code, 200)
                        self.assertIn(content_type, response.headers['content-type'])
                        self.assertEqual(response.headers['x-content-type-options'], 'nosniff')
                for path in ['/assets/api.py', '/assets/requirements.lock',
                             '/assets/%2e%2e/api.py']:
                    self.assertEqual(client.get(path).status_code, 404)


if __name__ == '__main__':
    unittest.main()
