from copy import deepcopy
from datetime import date
from pathlib import Path
import unittest

from pydantic import ValidationError

from creditlens.temporal import TemporalPack, quarterly_snapshot
from scripts.build_temporal_pack import parse_cash_flow
from scripts.export_quarters import render_snapshot


ROOT = Path(__file__).resolve().parents[1]


class TemporalTests(unittest.TestCase):
    def setUp(self):
        self.pack = TemporalPack.model_validate_json((ROOT / "data/examples/msft-fy2025-temporal.json").read_text(encoding="utf-8"))
        self.data = self.pack.model_dump(mode="json")

    def snapshot(self, data=None, cutoff="2025-07-31"):
        return quarterly_snapshot(TemporalPack.model_validate(data or self.data), date.fromisoformat(cutoff))

    def row(self, result, quarter, metric="cfo"):
        return next(r for r in result["quarters"] if r["quarter"] == quarter and r["metric"] == metric)

    def fact(self, suffix):
        return next(f for f in self.data["facts"] if f["id"].endswith(suffix))

    def test_official_quarters_and_derived_fourth_quarter(self):
        result = self.snapshot()
        self.assertEqual(len(self.data["facts"]), 18)
        self.assertEqual([self.row(result, q)["value"] for q in range(1, 5)], ["34180", "22291", "37044", "42647"])
        self.assertEqual(self.row(result, 4, "capex")["value"], "17079")
        self.assertEqual(self.row(result, 4, "net_income")["value"], "27233")
        self.assertEqual(sum(r["status"] == "reported" for r in result["quarters"]), 9)
        fourth = self.row(result, 4)
        self.assertEqual(fourth["status"], "derived_requires_review")
        self.assertFalse(fourth["eligible_as_observed"])
        self.assertEqual(len(fourth["fact_ids"]), 2)
        self.assertFalse(result["usable_for_prediction_training"])

    def test_publication_day_excluded_and_next_day_available(self):
        same_day = self.snapshot(cutoff="2025-01-29")
        next_day = self.snapshot(cutoff="2025-01-30")
        self.assertIsNone(self.row(same_day, 2)["value"])
        self.assertEqual(self.row(next_day, 2)["value"], "22291")
        self.assertEqual(self.row(next_day, 2)["available_from"], "2025-01-30")
        self.assertEqual(len(same_day["evidence"]["sources"]), 1)
        self.assertTrue(all("q1" in f["id"] for f in same_day["evidence"]["facts"]))

    def test_future_values_do_not_change_earlier_snapshot(self):
        earlier = self.snapshot(cutoff="2025-01-30")
        fact = self.fact("q4-ytd-cfo")
        fact.update(value="999999", raw_value="999,999")
        self.assertEqual(earlier, self.snapshot(cutoff="2025-01-30"))

    def test_missing_predecessor_cannot_be_filled_as_zero(self):
        self.data["facts"] = [f for f in self.data["facts"] if not f["id"].endswith("q3-ytd-cfo")]
        fourth = self.row(self.snapshot(), 4)
        self.assertIsNone(fourth["value"])
        self.assertEqual(fourth["status"], "missing")

    def test_zero_is_a_reported_observation(self):
        self.fact("q1-quarter-cfo").update(value="0", raw_value="0")
        first = self.row(self.snapshot(cutoff="2024-10-31"), 1)
        self.assertEqual(first["value"], "0")
        self.assertTrue(first["eligible_as_observed"])

    def test_direct_quarter_conflicts_with_cumulative_difference(self):
        self.fact("q2-ytd-cfo").update(value="56472", raw_value="56,472")
        second = self.row(self.snapshot(), 2)
        self.assertEqual(second["status"], "blocked")
        self.assertIsNone(second["value"])
        self.assertIn("quarter_vs_cumulative_mismatch", second["checks"])

    def test_mixed_units_or_accounting_basis_block_arithmetic(self):
        for key, value in (("unit", "USD"), ("basis_id", "restated_scope")):
            with self.subTest(key=key):
                altered = deepcopy(self.data)
                next(f for f in altered["facts"] if f["id"].endswith("q4-ytd-cfo"))[key] = value
                fourth = self.row(self.snapshot(altered), 4)
                self.assertEqual(fourth["status"], "blocked")
                self.assertIsNone(fourth["value"])

    def test_duplicate_versions_require_resolution_even_with_equal_values(self):
        duplicate = deepcopy(self.fact("q2-quarter-cfo"))
        duplicate["id"] += "-revision"
        self.data["facts"].append(duplicate)
        self.assertIn("ambiguous_versions", self.row(self.snapshot(), 2)["checks"])

    def test_estimated_value_cannot_masquerade_as_reported(self):
        self.fact("q1-quarter-cfo")["observation_kind"] = "estimated"
        with self.assertRaises(ValidationError):
            TemporalPack.model_validate(self.data)

    def test_irregular_or_cross_year_period_refused(self):
        for start in ("2024-07-02", "2023-07-01"):
            with self.subTest(start=start):
                altered = deepcopy(self.data)
                altered["facts"][0]["period_start"] = start
                with self.assertRaises(ValidationError):
                    TemporalPack.model_validate(altered)

    def test_week_calendar_requires_a_different_adapter(self):
        self.data["fiscal_calendar"] = "week_52_53"
        with self.assertRaises(ValidationError):
            TemporalPack.model_validate(self.data)

    def test_period_after_publication_refused(self):
        self.data["sources"][0]["published_at"] = "2024-09-29"
        with self.assertRaises(ValidationError):
            TemporalPack.model_validate(self.data)

    def test_negative_capex_difference_refused(self):
        self.fact("q4-ytd-capex").update(value="1", raw_value="(1)")
        fourth = self.row(self.snapshot(), 4, "capex")
        self.assertIsNone(fourth["value"])
        self.assertIn("negative_derived_capex", fourth["checks"])

    def test_no_available_sources_produces_missing_not_fabricated_values(self):
        result = self.snapshot(cutoff="2024-10-29")
        self.assertEqual(result["evidence"], {"facts": [], "sources": []})
        self.assertTrue(all(r["value"] is None and r["status"] == "missing" for r in result["quarters"]))

    def test_report_preserves_currency_and_magnitude(self):
        # Synthetic denomination change tests presentation, not an FX conversion.
        for unit, label in (("CNY_million", "百万元人民币"), ("USD", "美元")):
            altered = deepcopy(self.data)
            for fact in altered["facts"]:
                fact["unit"] = unit
            pack = TemporalPack.model_validate(altered)
            report = render_snapshot(pack, quarterly_snapshot(pack, date(2025, 7, 31)))
            self.assertIn(f"| 34180 | {label} |", report)
            self.assertNotIn("百万美元", report)


class QuarterlyAdapterTests(unittest.TestCase):
    def setUp(self):
        # Synthetic HTML to exercise structural changes; not an official data row.
        self.html = '''<table>
        <tr><th>(In millions) (Unaudited)</th><th>Three Months Ended September 30,</th></tr>
        <tr><th>2024</th><th>2023</th></tr>
        <tr><td>us-gaap:NetIncomeLoss</td><td>Net income</td><td>10</td><td>8</td></tr>
        <tr><td>us-gaap:NetCashProvidedByUsedInOperatingActivities</td><td>Net cash from operations</td><td>12</td><td>9</td></tr>
        <tr><td>us-gaap:PaymentsToAcquirePropertyPlantAndEquipment</td><td>Additions to property and equipment</td><td>(3)</td><td>(2)</td></tr>
        </table>'''

    def test_only_current_year_is_emitted_and_outflow_is_explicit(self):
        facts = parse_cash_flow(self.html, 1)
        self.assertEqual(len(facts), 3)
        self.assertTrue(all(f["period_start"] == "2024-07-01" for f in facts))
        capex = next(f for f in facts if f["metric"] == "capex")
        self.assertEqual((capex["value"], capex["raw_value"], capex["transformation"]), ("3", "(3)", "absolute_cash_outflow"))

    def test_changed_header_or_duplicated_table_is_refused(self):
        variants = [self.html.replace("In millions", "In thousands"),
                    self.html.replace("Three Months", "Six Months"),
                    self.html.replace("<th>2024</th><th>2023</th>", "<th>2023</th><th>2024</th>"),
                    self.html + self.html]
        for html in variants:
            with self.subTest(html=html[:100]), self.assertRaises(ValueError):
                parse_cash_flow(html, 1)


if __name__ == "__main__":
    unittest.main()
