import copy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from creditlens.core import Evidence, ResearchCase, calculate, ingest_finqa, parse_number, retrieve
from creditlens.service import ResearchService

RECORD = {
    "id": "EX/2024/page_1.pdf-1", "filename": "EX/2024/page_1.pdf",
    "pre_text": ["Operating cash flow is discussed below."], "post_text": ["Review the units."],
    "table": [["company", "volume billion", "transactions billion"], ["example", "637", "5.0"]],
    "qa": {"question": "example volume per transaction", "answer": "SECRET_ANSWER",
           "program": "SECRET_PROGRAM", "gold_inds": {"table_1": "SECRET_GOLD"},
           "model_input": [["table_1", "SECRET_INPUT"]]},
    "table_retrieved": [{"text": "SECRET_RETRIEVER"}],
}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.case = ingest_finqa(copy.deepcopy(RECORD))

    def test_gold_labels_never_enter_runtime(self):
        serialized = json.dumps([x.to_dict() for x in self.case.evidence])
        self.assertNotIn("SECRET", serialized)
        mutated = copy.deepcopy(RECORD)
        mutated["qa"]["gold_inds"] = {"text_999": "changed"}
        mutated["qa"]["program"] = "eval malicious"
        self.assertEqual(self.case, ingest_finqa(mutated))

    def test_text_indices_include_post_text_offset(self):
        self.assertEqual(self.case.evidence[1].id, "text_1")
        self.assertEqual(self.case.evidence[1].text, "Review the units.")

    def test_unknown_publication_date_is_not_inferred_from_year(self):
        self.assertTrue(all(e.published_at is None for e in self.case.evidence))
        self.assertEqual(retrieve(self.case, "example", as_of="2030-01-01"), [])

    def test_future_and_other_tenant_filtered_before_ranking(self):
        original = self.case.evidence[-1]
        blocks = (
            replace(original, id="allowed", tenant_id="tenant-a", published_at="2024-01-01"),
            replace(original, id="future", tenant_id="tenant-a", published_at="2026-01-01"),
            replace(original, id="secret", tenant_id="tenant-b", published_at="2024-01-01"),
        )
        case = ResearchCase(self.case.id, self.case.report_ref, "", blocks)
        hits = retrieve(case, "volume", tenant_id="tenant-a", as_of="2025-01-01")
        self.assertEqual([h["evidence"]["id"] for h in hits], ["allowed"])

    def test_hash_changes_when_cell_changes(self):
        ev = self.case.evidence[-1]
        self.assertNotEqual(ev.sha256, replace(ev, cells=("example", "638", "5")).sha256)

    def test_empty_query_does_not_fabricate_evidence(self):
        self.assertEqual(retrieve(self.case, "the of"), [])

    def test_arithmetic_is_source_backed(self):
        result = calculate([{"op": "divide", "left": "cell:table_1:1", "right": "cell:table_1:2"}],
                           list(self.case.evidence))
        self.assertEqual(result["value"], "127.4")
        self.assertEqual(result["citations"][0]["id"], "table_1")

    def test_numeric_formats(self):
        self.assertEqual(parse_number("($ 1,200.50)"), Decimal("-1200.50"))
        self.assertEqual(parse_number("12.5%"), Decimal(".125"))
        for invalid in ("-", "NaN", "1e100", "__import__('os')", "12 million", "3 / 4"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_number(invalid)

    def test_arithmetic_rejects_unsafe_and_forward_refs(self):
        for step in [
            {"op": "eval", "left": "cell:table_1:1", "right": "const:1"},
            {"op": "add", "left": "cell:table_999:1", "right": "const:1"},
            {"op": "add", "left": "step:0", "right": "const:1"},
            {"op": "add", "left": "const:999", "right": "const:1"},
            {"op": "add", "left": "cell:table_1:-1", "right": "const:1"},
            {"op": "add", "left": "const:1", "right": "const:100"},
        ]:
            with self.subTest(step=step), self.assertRaises(ValueError):
                calculate([step], list(self.case.evidence))

    def test_zero_denominator_rejected(self):
        evidence = [replace(self.case.evidence[-1], cells=("example", "637", "0"))]
        with self.assertRaisesRegex(ValueError, "zero"):
            calculate([{"op": "divide", "left": "cell:table_1:1", "right": "cell:table_1:2"}], evidence)

    def test_calculator_rechecks_time_filter(self):
        with self.assertRaisesRegex(ValueError, "Unavailable"):
            calculate([{"op": "divide", "left": "cell:table_1:1", "right": "cell:table_1:2"}],
                      list(self.case.evidence), as_of="2025-01-01")

    def test_cross_case_calculation_rejected(self):
        ev = self.case.evidence[-1]
        with self.assertRaisesRegex(ValueError, "Cross-case"):
            calculate([{"op": "add", "left": "cell:table_1:1", "right": "const:1"}],
                      [ev, replace(ev, case_id="OTHER")])


class PersistenceTests(unittest.TestCase):
    def test_report_roundtrip_and_parameterized_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "input.json"
            data.write_text(json.dumps([RECORD]), encoding="utf-8")
            service = ResearchService(data, root / "audit.sqlite")
            self.assertTrue(service.source_url.startswith("urn:sha256:"))
            result = service.analyze(RECORD["id"], "example volume")
            reloaded = ResearchService(data, root / "audit.sqlite").get_report(result["id"])
            self.assertEqual(result, reloaded)
            self.assertIsNone(service.get_report("' OR 1=1 --"))
            self.assertNotIn("SECRET", json.dumps(result))
            self.assertEqual(result["mode"], "retrieval_only")


if __name__ == "__main__":
    unittest.main()
