"""Historical exports and present registration are separate HTTP results."""
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from creditlens.api import create_app, ROOT
from creditlens.preparation_examples import preparation_examples
from creditlens.preparation_applicability import assess_preparation_applicability


class PreparationApplicabilityApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        principals = [{'key_sha256':sha256(key.encode()).hexdigest(),'user_id':key,'tenant_id':tenant,'role':role}
            for key,tenant,role in [('author','one','analyst'),('reader','one','viewer'),('other','two','viewer')]]
        self.app = create_app(audit_db=self.root/'workflow.sqlite',auth_mode='api_key',principals=principals)
        self.client = self.enterContext(TestClient(self.app))
        self.headers = {'Authorization':'Bearer author','Idempotency-Key':'new-case'}
        value = preparation_examples(ROOT)['examples']['complete']
        response = self.client.post('/api/document-preparation/tasks',json=value,headers=self.headers)
        self.assertEqual(response.status_code,201,response.text)
        self.job = response.json()
        self.url = '/api/document-preparation/tasks/'+self.job['id']

    def test_matching_registration_does_not_change_export(self):
        before = self.client.get(self.url+'/export',headers=self.headers).content
        response = self.client.get(self.url+'/applicability',headers={'Authorization':'Bearer reader'})
        self.assertEqual(response.status_code,200,response.text)
        result = response.json()
        self.assertEqual(result['verification']['status'],'internally_consistent')
        self.assertEqual(result['applicability']['status'],'matching_registration')
        self.assertTrue(result['applicability']['ready_for_content_review'])
        self.assertFalse(result['queryable'])
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.client.get(self.url+'/export',headers=self.headers).content,before)

    def test_current_unavailable_still_has_readable_history(self):
        old = self.client.get(self.url+'/export',headers=self.headers).content
        with patch('creditlens.preparation_routes.assess_preparation_applicability',
                   side_effect=lambda _root,verified:assess_preparation_applicability(self.root,verified)):
            response = self.client.get(self.url+'/applicability',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['applicability']['status'],'registration_unavailable')
        self.assertEqual(self.client.get(self.url+'/export',headers=self.headers).content,old)

    def test_authorization_precedes_registration_inspection(self):
        with patch('creditlens.preparation_routes.assess_preparation_applicability') as inspect:
            self.assertEqual(self.client.get(self.url+'/applicability').status_code,401)
            self.assertEqual(self.client.get(self.url+'/applicability',headers={'Authorization':'Bearer other'}).status_code,404)
            self.assertEqual(self.client.get(self.url+'/applicability?attempt_id=unknown',headers=self.headers).status_code,404)
            inspect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
