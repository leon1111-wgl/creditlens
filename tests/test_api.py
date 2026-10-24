import json
from pathlib import Path
import tempfile
import unittest
from fastapi.testclient import TestClient
from creditlens.api import create_app
from test_core import RECORD


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        path = root / "cases.json"
        path.write_text(json.dumps([RECORD]), encoding="utf-8")
        self.client = TestClient(create_app(path, root / "audit.sqlite"))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_health_and_workbench(self):
        self.assertEqual(self.client.get("/health").json()["cases"], 1)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("CreditLens", response.text)

    def test_api_calculation_roundtrip(self):
        response = self.client.post("/api/analyze", json={
            "case_id": RECORD["id"], "question": "example volume transactions",
            "steps": [{"op": "divide", "left": "cell:table_1:1", "right": "cell:table_1:2"}],
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["calculation"]["value"], "127.4")
        self.assertEqual(self.client.get("/api/reports/" + data["id"]).json(), data)

    def test_missing_case_returns_404(self):
        response = self.client.post("/api/analyze", json={"case_id": "missing", "question": "a"})
        self.assertEqual(response.status_code, 404)

    def test_schema_validation(self):
        for extra in ({"k": 1000}, {"as_of": "2025-02-30"}, {"program": "malicious"}):
            response = self.client.post("/api/analyze", json={
                "case_id": RECORD["id"], "question": "example", **extra})
            self.assertEqual(response.status_code, 422)

    def test_strict_asof_empty(self):
        response = self.client.post("/api/analyze", json={
            "case_id": RECORD["id"], "question": "example", "as_of": "2030-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "insufficient_evidence")

    def test_no_arithmetic_on_unretrieved_evidence(self):
        response = self.client.post("/api/analyze", json={
            "case_id": RECORD["id"], "question": "zzzzzz",
            "steps": [{"op": "divide", "left": "cell:table_1:1", "right": "cell:table_1:2"}]})
        self.assertEqual(response.status_code, 422)

    def test_foreign_host_denied(self):
        self.assertEqual(self.client.get("/health", headers={"Host": "attacker.example"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
