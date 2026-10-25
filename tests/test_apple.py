"""Synthetic parser/control failures are separate from the official development fixture."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest
from pydantic import ValidationError

from creditlens.document import Token, EvidenceError, extract_stacked_table
from creditlens.diligence import CasePack, analyze_pack
from scripts.build_apple_pack import compare_reference
from test_document import words

ROOT = Path(__file__).resolve().parents[1]
PACK = json.loads((ROOT/"data/examples/aapl-fy2025-pdf.json").read_text(encoding="utf-8"))
REFERENCE = json.loads((ROOT/"data/reference/aapl-fy2025-reference.json").read_text(encoding="utf-8"))


def sample():
    return (words("STATEMENT", 10, 10)+words("(In millions)", 10, 30)
            +words("September 27,", 281, 50)+words("September 28,", 381, 50)
            +[Token("2025", 300, 62, 316, 70), Token("2024", 400, 62, 416, 70)]
            +words("Current liabilities:", 10, 85)+words("Term debt", 10, 105)
            +[Token("1,200", 320, 105, 340, 113), Token("(100)", 420, 105, 440, 113)]
            +words("Total current liabilities", 10, 130)
            +words("Non-current liabilities:", 10, 160)+words("Term debt", 10, 185)
            +[Token("8,000", 320, 185, 340, 193), Token("9,000", 420, 185, 440, 193)]
            +words("Total non-current liabilities", 10, 210))


def read(tokens, scoped=True):
    metric = {"label": "Term debt", "section": ["Current liabilities:", "Total current liabilities"]} if scoped else "Term debt"
    return extract_stacked_table(tokens, page=1, width=600, height=800, title="STATEMENT", unit_header="(In millions)",
         years=[2025, 2024], date_headers=["September 27,", "September 28,"], metrics={"current_debt": metric})


def analyze(payload=None):
    return analyze_pack(CasePack.model_validate(payload or PACK), date(2025, 11, 1), 20)


class StackedDocumentTests(unittest.TestCase):
    def test_centered_headers_and_scoped_labels_preserve_sign(self):
        result = read(sample())
        self.assertEqual([r["value"] for r in result], ["1200", "-100"])
        self.assertEqual(result[0]["pdf_location"]["period_header"], "September 27, 2025")
        self.assertEqual(result[0]["pdf_location"]["section"]["start_label"], "Current liabilities:")

    def test_repeated_label_without_section_is_refused(self):
        with self.assertRaises(EvidenceError):
            read(sample(), scoped=False)

    def test_missing_section_boundary_or_duplicate_row_is_refused(self):
        duplicate = [replace(t, top=t.top+12, bottom=t.bottom+12) for t in sample() if t.top == 105]
        for tokens in ([t for t in sample() if t.top != 85], [t for t in sample() if t.top != 130], sample()+duplicate):
            with self.subTest(tokens=len(tokens)), self.assertRaises(EvidenceError):
                read(tokens)

    def test_wrong_date_year_unit_and_column_fail_closed(self):
        variants = [
            [replace(t, text="26,") if t.text == "27," else t for t in sample()],
            [replace(t, text="2025") if t.text == "2024" else t for t in sample()],
            [t for t in sample() if t.top != 30],
            [replace(t, x0=t.x0+50, x1=t.x1+50) if t.text == "1,200" else t for t in sample()],
            [replace(t, text="O.12") if t.text == "1,200" else t for t in sample()],
        ]
        for tokens in variants:
            with self.subTest(tokens=tokens[-1]), self.assertRaises(EvidenceError):
                read(tokens)

    def test_imported_section_must_contain_row_and_stay_on_page(self):
        for kind in ("outside", "wrong_section"):
            payload = deepcopy(PACK)
            loc = next(f["pdf_location"] for f in payload["facts"] if f["metric"] == "current_debt")
            if kind == "outside":
                loc["section"]["start_bbox"]["x1"] = 9999
            else:
                loc["section"]["end_bbox"].update(top=1, bottom=2)
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                CasePack.model_validate(payload)

    def test_official_reference_all_22_cells_and_basis(self):
        report = compare_reference(CasePack.model_validate(PACK), REFERENCE)
        self.assertEqual((report["compared"], report["matched"], report["missing_pdf_fact_ids"]), (22, 22, []))

    def test_reference_mismatch_is_reported_without_repair(self):
        payload = deepcopy(PACK)
        payload["facts"][0].update(value="1", raw_value="1")
        report = compare_reference(CasePack.model_validate(payload), REFERENCE)
        self.assertEqual(report["matched"], 21)
        self.assertIn("value", report["checks"][0]["different_fields"])
        self.assertEqual(report["checks"][0]["pdf_value"], "1")


class MultiIssuerControlsTests(unittest.TestCase):
    def test_liquidity_uses_four_original_cells_without_long_term_assets(self):
        result = analyze()
        metrics = {m["id"]: m for m in result["metrics"]}
        self.assertEqual(Decimal(metrics["liquidity"]["value"]), Decimal(54697)/Decimal(20329))
        self.assertEqual(metrics["liquidity"]["fact_ids"], ["cash_equivalents-2025", "short_investments-2025", "short_debt-2025", "current_debt-2025"])
        self.assertEqual(metrics["fcf"]["value"], "98767")
        self.assertEqual(metrics["stress_fcf"]["value"], "76470.6")
        self.assertEqual(result["blocking_count"], 0)

    def test_incomplete_conflicted_or_mixed_unit_components_block_liquidity(self):
        for kind in ("missing", "conflict", "units", "old_period"):
            payload = deepcopy(PACK)
            fact = next(f for f in payload["facts"] if f["id"] == "short_investments-2025")
            if kind == "missing":
                payload["facts"].remove(fact)
            elif kind == "conflict":
                payload["facts"].append({**deepcopy(fact), "id": "conflicting-component"})
            elif kind == "units":
                fact["unit"] = "USD"
            else:
                fact["period_end"] = "2025-06-30"
            result = analyze(payload)
            with self.subTest(kind=kind):
                self.assertNotIn("liquidity", [m["id"] for m in result["metrics"]])
                self.assertGreater(result["blocking_count"], 0)

    def test_aggregate_and_components_cannot_be_double_counted_or_cherry_picked(self):
        payload = deepcopy(PACK)
        fact = deepcopy(next(f for f in payload["facts"] if f["id"] == "cash_equivalents-2025"))
        fact.update(id="aggregate-2025", metric="liquid_assets", value="54697", raw_value="54,697")
        fact.pop("pdf_location")
        payload["facts"].append(fact)
        result = analyze(payload)
        self.assertIn("ambiguous_liquidity_basis", [i["id"] for i in result["issues"]])
        self.assertNotIn("liquidity", [m["id"] for m in result["metrics"]])

    def test_52_week_comparison_requires_explicit_calendar(self):
        self.assertEqual(len(analyze()["comparison"]), 11)
        payload = deepcopy(PACK)
        payload.pop("fiscal_calendar")
        self.assertEqual(analyze(payload)["comparison"], [])

    def test_unequal_week_counts_do_not_create_unqualified_flow_growth(self):
        payload = deepcopy(PACK)
        fact = next(f for f in payload["facts"] if f["id"] == "cfo-2024")
        fact["period_start"] = "2023-09-24"  # Synthetic 53-week comparison.
        self.assertNotIn("cfo", [m["metric"] for m in analyze(payload)["comparison"]])

    def test_apple_filing_cutoff_blocks_all_22_facts(self):
        result = analyze_pack(CasePack.model_validate(PACK), date(2025, 10, 30))
        self.assertEqual(result["metrics"], [])
        self.assertEqual(result["excluded_future_facts"], 22)

    def test_negative_liquidity_component_rejected(self):
        payload = deepcopy(PACK)
        next(f for f in payload["facts"] if f["id"] == "cash_equivalents-2025").update(value="-1", raw_value="(1)")
        with self.assertRaises(ValidationError):
            CasePack.model_validate(payload)
