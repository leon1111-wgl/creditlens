import json
import unittest
import httpx
from creditlens.core import ingest_finqa
from creditlens.planner import LocalPlanner
from test_core import RECORD


class PlannerTests(unittest.TestCase):
    def plan(self, **changes):
        return {"status": "propose_calculation", "steps": [{"op": "divide", "left": "cell:table_1:1", "right": "cell:table_1:2"}],
                "evidence_ids": ["table_1"], "explanation": "Divide source payments by transactions; review the financial units.", **changes}

    def planner(self, plan, status=200):
        def handler(request):
            body = json.loads(request.content)
            self.assertEqual(body["response_format"]["type"], "json_schema")
            self.assertNotIn('gold_inds', body['messages'][1]['content'])
            self.assertNotIn('program', body['messages'][1]['content'])
            return httpx.Response(status, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(plan)}}]})
        return LocalPlanner("http://127.0.0.1:8000/v1", "test-model", transport=httpx.MockTransport(handler))

    def test_valid_mock_plan_executes_only_source_cells(self):
        result = self.planner(self.plan()).run(ingest_finqa(RECORD), "example volume transactions")
        self.assertEqual(result["calculation"]["value"], "127.4")
        self.assertEqual(result["status"], "needs_analyst_review")

    def test_hallucinated_reference_rejected(self):
        with self.assertRaises(ValueError):
            self.planner(self.plan(evidence_ids=["fabricated"])).run(ingest_finqa(RECORD), "example volume transactions")

    def test_unsupported_tool_cannot_execute(self):
        with self.assertRaises(ValueError):
            self.planner(self.plan(steps=[{"op": "exec", "left": "code", "right": "code"}])).run(ingest_finqa(RECORD), "example volume transactions")

    def test_future_filter_prevents_model_call(self):
        def forbidden(request):
            raise AssertionError("No model call expected")
        planner = LocalPlanner("http://localhost:8000/v1", "test", transport=httpx.MockTransport(forbidden))
        result = planner.run(ingest_finqa(RECORD), "example", as_of="2025-01-01")
        self.assertFalse(result["model_called"])

    def test_remote_endpoint_rejected(self):
        for url in ["https://paid.example/v1", "http://169.254.169.254", "http://user:pass@localhost", "http://localhost/?x=y"]:
            with self.assertRaises(ValueError):
                LocalPlanner(url, "test")

    def test_malformed_plan_and_transport_errors_fail_closed(self):
        with self.assertRaises(ValueError):
            self.planner({"credit_approved": True}).run(ingest_finqa(RECORD), "example volume")
        with self.assertRaises(httpx.HTTPStatusError):
            self.planner(self.plan(), 500).run(ingest_finqa(RECORD), "example volume")


if __name__ == "__main__":
    unittest.main()
