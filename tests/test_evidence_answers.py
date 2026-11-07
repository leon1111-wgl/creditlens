"""Structured evidence answers (creditlens/evidence_answers.py) and the committed browser pack."""
import json
import unittest
from pathlib import Path

from creditlens.evidence_answers import AnswerError, _instant_change, answer_plan, build_answer_catalog

ROOT = Path(__file__).resolve().parents[1]


def item(issuer, metric, year):
    return {"issuer_id": issuer, "metric": metric, "year": year}


class EvidenceAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_answer_catalog(ROOT)

    def ask(self, kind, *items, unit=None, scope=None):
        plan = {"kind": kind, "items": list(items), "unit": unit}
        if scope:
            plan["scope"] = scope
        return answer_plan(self.catalog, plan)

    def test_catalog_has_evidence_for_every_figure(self):
        self.assertEqual(len(self.catalog["observations"]), 66)
        self.assertEqual(len(self.catalog["percents"]), 12)
        for o in self.catalog["observations"]:
            self.assertIn(o["year"], (2024, 2025))
            self.assertTrue(o["source"]["page"] and o["source"]["amount_bbox"] and o["source"]["unit_header"], o["id"])
            self.assertEqual(o["unit"], "USD_million")
            self.assertIn(o["ocr"]["outcome"], ("matched", "insufficient", "registered_span"))
        for p in self.catalog["percents"]:
            self.assertEqual(len(p["input_ids"]), 2)

    def test_lookup_converts_usd_scale_exactly(self):
        answer = self.ask("lookup", item("MSFT", "revenue", 2025), unit={"currency": "USD", "scale": "hundred_million"})
        self.assertEqual(answer["status"], "answered")
        self.assertEqual(answer["values"][0]["raw_value"], "281,724")
        self.assertEqual(answer["values"][0]["requested"], {"scale": "hundred_million", "value": "2817.24"})
        one = self.ask("lookup", item("MSFT", "short_debt", 2025), unit={"currency": "USD", "scale": "one"})
        self.assertEqual(one["values"][0]["requested"]["value"], "0")

    def test_missing_currency_scope_year_and_line_state_no_value(self):
        cases = [
            (self.ask("lookup", item("MSFT", "revenue", 2025), unit={"currency": "CNY", "scale": "hundred_million"}), "unit_unavailable"),
            (self.ask("lookup", item("MSFT", "revenue", 2025), scope="parent_only"), "scope_unavailable"),
            (self.ask("lookup", item("MSFT", "revenue", 2023)), "not_registered"),
            (self.ask("lookup", item("MSFT", "cash_equivalents", 2025)), "not_registered"),
            (self.ask("lookup", item("AAPL", "liquid_assets", 2025)), "not_registered"),
            (self.ask("lookup", item("AAPL", "gross_margin_pct", 2025), unit={"currency": "USD", "scale": "million"}), "unit_unavailable"),
        ]
        for answer, status in cases:
            self.assertEqual(answer["status"], status)
            self.assertEqual(answer["values"], [])
            self.assertTrue(answer["gaps"])

    def test_changes_use_the_registered_rules(self):
        flow = self.ask("change", item("AAPL", "cfo", 2024), item("AAPL", "cfo", 2025))
        self.assertEqual(flow["status"], "computed")
        self.assertEqual(flow["comparison"]["absolute_change"], "-6772")
        self.assertEqual(flow["comparison"]["growth_rate"], "-0.057267")
        leap = self.ask("change", item("MSFT", "revenue", 2024), item("MSFT", "revenue", 2025))
        self.assertEqual([w["code"] for w in leap["comparison"]["warnings"]], ["leap_year_length_difference"])
        balance = self.ask("change", item("MSFT", "short_debt", 2024), item("MSFT", "short_debt", 2025))
        self.assertEqual((balance["comparison"]["absolute_change"], balance["comparison"]["growth_rate"]), ("-6693", "-1.000000"))
        margin = self.ask("change", item("AAPL", "gross_margin_pct", 2024), item("AAPL", "gross_margin_pct", 2025))
        self.assertEqual(margin["comparison"]["change_pp_6dp"], "0.698814")
        reversed_order = self.ask("change", item("AAPL", "cfo", 2025), item("AAPL", "cfo", 2024))
        self.assertEqual(reversed_order["status"], "blocked")
        self.assertIsNone(reversed_order["comparison"]["absolute_change"])

    def test_cross_company_is_side_by_side_without_difference(self):
        answer = self.ask("cross_company", item("MSFT", "cfo", 2025), item("AAPL", "cfo", 2025))
        self.assertEqual(answer["status"], "side_by_side_only")
        self.assertIsNone(answer["comparison"])
        self.assertIsNone(answer["cross_company"]["difference"])
        self.assertIsNone(answer["ranking"])
        codes = [r["code"] for r in answer["cross_company"]["reasons"]]
        self.assertEqual(codes[:3], ["not_strict_peer_benchmark", "actual_period_mismatch", "annual_calendar_mismatch"])
        same = self.ask("change", item("MSFT", "cfo", 2025), item("AAPL", "cfo", 2025))
        self.assertEqual(same["status"], "side_by_side_only")

    def test_instant_rule_rejects_misaligned_year_ends(self):
        by = {o["id"]: o for o in self.catalog["observations"]}
        previous, current = json.loads(json.dumps(by["AAPL:long_debt:2024"])), json.loads(json.dumps(by["AAPL:long_debt:2025"]))
        current["period"]["end"] = "2025-09-20"
        self.assertEqual([r["code"] for r in _instant_change(previous, current)["reasons"]], ["year_end_mismatch"])

    def test_invalid_plans_are_rejected(self):
        for plan in ({"kind": "lookup", "items": []}, {"kind": "rank", "items": [item("MSFT", "cfo", 2025)]},
                     {"kind": "lookup", "items": [{"issuer_id": "MSFT", "metric": "cfo", "year": "2025"}]},
                     {"kind": "lookup", "items": [item("MSFT", "cfo", 2025)], "unit": {"currency": "USD", "scale": "gazillion"}},
                     {"kind": "lookup", "items": [item("MSFT", "cfo", 2025)], "extra": 1}):
            with self.assertRaises(AnswerError):
                answer_plan(self.catalog, plan)

    def test_committed_browser_pack_is_current_and_has_no_images(self):
        from scripts.build_browser_pack import PACK, render
        text = render(ROOT)
        self.assertEqual(PACK.read_text(encoding="utf-8"), text)
        pack = json.loads(text)
        self.assertNotIn("data:image", text)
        self.assertEqual(set(pack["pages"]), {"MSFT:50", "MSFT:52", "MSFT:53", "AAPL:32", "AAPL:34", "AAPL:36"})


if __name__ == "__main__":
    unittest.main()
