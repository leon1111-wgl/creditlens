"""XBRL grounding benchmark: key derivation, OCR-only page reading and fact scoring.

Synthetic OCR spans only; no network, rendering or model. The real run is the
Actions workflow; these tests pin the rules that were committed before it.
"""
import json
from pathlib import Path
import unittest

from creditlens.xbrl_grounding import (
    PageReading, candidate_order, header_year, key_entry, key_magnitude, latest_annual_report,
    score_fact, statement_pages, summarize, unit_header,
)
from scripts.xbrl_grounding import paginate

ROOT = Path(__file__).resolve().parents[1]


def span(text, x0, top, x1, bottom, confidence=0.99):
    return {"text": text, "confidence": confidence, "bbox": {"x0": x0, "top": top, "x1": x1, "bottom": bottom}}


def box(x0, top, x1, bottom):
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


CONTEXTS = {"fy25": {"period": {"start": "2025-01-01", "end": "2025-12-31"}, "dims": []},
            "fy24": {"period": {"start": "2024-01-01", "end": "2024-12-31"}, "dims": []},
            "i25": {"period": {"instant": "2025-12-31"}, "dims": [["us-gaap:StatementBusinessSegmentsAxis", "x:CloudMember"]]}}
UNITS = {"usd": "iso4217:USD", "usdps": "iso4217:USD/xbrli:shares"}


def fact(text, b, *, context="fy25", scale="6", sign=None, fmt="ixt:num-dot-decimal", open_=False, close=False, unit="usd"):
    return {"seq": 1, "concept": "us-gaap:Revenues", "context_ref": context, "unit_ref": unit, "decimals": "-6",
            "scale": scale, "sign": sign, "format": fmt, "text": text, "printed_open": open_, "printed_close": close,
            "box": b, "page": 1}


def statement_page(cells):
    """A two-column income statement: unit header, year header, rows of label + two numbers."""
    spans = [span("CONSOLIDATED STATEMENTS OF OPERATIONS", 150, 40, 460, 52),
             span("(In millions, except per share amounts)", 200, 56, 410, 66),
             span("Year Ended December 31,", 380, 80, 540, 90),
             span("2025", 400, 94, 424, 104), span("2024", 490, 94, 514, 104)]
    return spans + cells


class KeyTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(str(key_magnitude("1,234", "ixt:num-dot-decimal")), "1234")
        self.assertEqual(str(key_magnitude("12.50", "ixt:numdotdecimal")), "12.50")
        self.assertEqual(str(key_magnitude("—", "ixt:fixed-zero")), "0")
        self.assertIsNone(key_magnitude("no", "ixt-sec:numwordsen"))
        self.assertIsNone(key_magnitude("1,2a4", None))

    def test_printed_sign_and_xbrl_sign_are_kept_apart(self):
        # Printed as a deduction "(500)" but tagged as a positive value (presentation negation).
        k = key_entry(fact("500", box(0, 0, 1, 1), open_=True, close=True), CONTEXTS, UNITS)
        self.assertEqual((k["printed_value"], k["xbrl_value"]), ("-500000000", "500000000"))
        # Tagged with sign="-" and printed in parentheses: both negative.
        k = key_entry(fact("500", box(0, 0, 1, 1), sign="-", open_=True, close=True), CONTEXTS, UNITS)
        self.assertEqual((k["printed_value"], k["xbrl_value"]), ("-500000000", "-500000000"))
        self.assertEqual(k["year"], 2025)
        self.assertEqual(key_entry(fact("1", box(0, 0, 1, 1), context="i25"), CONTEXTS, UNITS)["dimensions"],
                         [["us-gaap:StatementBusinessSegmentsAxis", "x:CloudMember"]])

    def test_unsupported_format_is_not_scored(self):
        k = key_entry(fact("none", box(0, 0, 1, 1), fmt="ixt-sec:numwordsen"), CONTEXTS, UNITS)
        self.assertFalse(k["scorable"])
        result = score_fact(fact("none", box(400, 120, 424, 130), fmt="ixt-sec:numwordsen"), k, PageReading(statement_page([])))
        self.assertEqual((result["outcome"], result["reason"]), ("not_scored", "unsupported_format"))


class ReadingTests(unittest.TestCase):
    def test_header_grammar(self):
        self.assertEqual(header_year("December 31, 2025"), 2025)
        self.assertEqual(header_year("Dec. 28, 2024"), 2024)
        self.assertEqual(header_year("Year Ended December 31, 2023"), 2023)
        self.assertEqual(header_year("Fiscal 2025"), 2025)
        self.assertIsNone(header_year("Senior notes due 2030"))
        self.assertEqual(unit_header("(Dollars in thousands, except per share data)"), {"scale": 3, "qualified": True})
        self.assertEqual(unit_header("($ in millions)"), {"scale": 6, "qualified": False})
        self.assertIsNone(unit_header("Millions of dollars"))

    def score(self, text_on_page, key_fact, extra=()):
        page = statement_page([span("Revenue", 60, 120, 120, 130), *extra, text_on_page])
        k = key_entry(key_fact, CONTEXTS, UNITS)
        return score_fact(key_fact, k, PageReading(page))

    def test_match_with_parentheses_scale_and_column(self):
        r = self.score(span("(1,234)", 396, 120, 428, 130), fact("1,234", box(399, 120, 425, 130), sign="-", open_=True, close=True))
        self.assertEqual(r["outcome"], "match", r)
        self.assertEqual(r["read"]["scale"], 6)
        self.assertEqual(r["read"]["year"], 2025)

    def test_lost_parentheses_is_a_sign_conflict(self):
        r = self.score(span("1,234", 399, 120, 425, 130), fact("1,234", box(399, 120, 425, 130), open_=True, close=True))
        self.assertEqual((r["outcome"], r["reason"]), ("conflict", "sign"))

    def test_digit_misread_is_a_conflict(self):
        r = self.score(span("1,284", 399, 120, 425, 130), fact("1,234", box(399, 120, 425, 130)))
        self.assertEqual((r["outcome"], r["reason"]), ("conflict", "digits"))

    def test_wrong_column_is_a_period_conflict(self):
        # The key says FY2024, the number sits under the 2025 header.
        r = self.score(span("1,234", 399, 120, 425, 130), fact("1,234", box(399, 120, 425, 130), context="fy24"))
        self.assertEqual((r["outcome"], r["reason"]), ("conflict", "period"))

    def test_scale_conflict_when_header_differs_from_tag(self):
        r = self.score(span("1,234", 399, 120, 425, 130), fact("1,234", box(399, 120, 425, 130), scale="3"))
        self.assertEqual((r["outcome"], r["reason"]), ("conflict", "scale"))

    def test_merged_dollar_sign_abstains(self):
        r = self.score(span("$ 1,234", 380, 120, 425, 130), fact("1,234", box(399, 120, 425, 130)))
        self.assertEqual((r["outcome"], r["reason"]), ("abstain", "merged_or_misaligned_region"))
        self.assertTrue(r["box_hit"])
        self.assertTrue(r["diagnostic"]["digits_equal"])

    def test_low_confidence_two_spans_and_no_span(self):
        f = fact("1,234", box(399, 120, 425, 130))
        self.assertEqual(self.score(span("1,234", 399, 120, 425, 130, .5), f)["reason"], "low_ocr_confidence")
        r = self.score(span("1,2", 399, 120, 411, 130), f, extra=[span("34", 412, 120, 425, 130)])
        self.assertEqual(r["reason"], "ambiguous_cell")
        r = self.score(span("Revenue total", 60, 160, 120, 170), f)
        self.assertEqual((r["reason"], r["box_hit"]), ("no_spatial_evidence", False))

    def test_detached_parenthesis_abstains(self):
        r = self.score(span("1,234", 399, 120, 425, 130), fact("1,234", box(399, 120, 425, 130)), extra=[span(")", 427, 120, 430, 130)])
        self.assertEqual(r["reason"], "detached_numeric_fragment_near_cell")

    def test_dash_zero_abstains_as_unreadable(self):
        r = self.score(span("—", 405, 120, 418, 130), fact("—", box(405, 120, 418, 130), fmt="ixt:fixed-zero"))
        self.assertEqual((r["outcome"], r["reason"]), ("abstain", "unreadable_numeric_cell"))

    def test_per_share_rows_abstain_on_scale(self):
        page = statement_page([span("Earnings per share:", 60, 150, 160, 160), span("Basic", 70, 164, 100, 174),
                               span("6.11", 402, 164, 422, 174)])
        f = fact("6.11", box(402, 164, 422, 174), scale="0", unit="usdps")
        r = score_fact(f, key_entry(f, CONTEXTS, UNITS), PageReading(page))
        self.assertEqual((r["outcome"], r["reason"]), ("abstain", "share_or_per_share_row"))
        self.assertTrue(r["checks"]["digits"] and r["checks"]["period"])

    def test_missing_unit_header_abstains(self):
        page = [span("2025", 400, 94, 424, 104), span("2024", 490, 94, 514, 104), span("1,234", 399, 120, 425, 130)]
        f = fact("1,234", box(399, 120, 425, 130))
        self.assertEqual(score_fact(f, key_entry(f, CONTEXTS, UNITS), PageReading(page))["reason"], "unit_missing")

    def test_duplicate_header_year_and_outside_columns(self):
        page = statement_page([span("2025", 300, 94, 324, 104), span("1,234", 399, 120, 425, 130)])
        f = fact("1,234", box(399, 120, 425, 130))
        self.assertEqual(score_fact(f, key_entry(f, CONTEXTS, UNITS), PageReading(page))["reason"], "duplicate_header_year")
        page = statement_page([span("1,234", 140, 120, 166, 130)])
        f = fact("1,234", box(140, 120, 166, 130))
        self.assertEqual(score_fact(f, key_entry(f, CONTEXTS, UNITS), PageReading(page))["reason"], "outside_header_columns")


class SummaryAndRuleTests(unittest.TestCase):
    def test_summary_counts_and_penalties(self):
        records = [{"outcome": "match", "reason": None, "box_hit": True, "checks": {"digits": True, "sign": True, "scale": True, "period": True}},
                   {"outcome": "conflict", "reason": "sign", "box_hit": True, "checks": {"digits": True, "sign": False, "scale": True, "period": True}},
                   {"outcome": "abstain", "reason": "unit_missing", "box_hit": True, "checks": {"digits": True, "sign": True, "period": True}},
                   {"outcome": "abstain", "reason": "no_spatial_evidence", "box_hit": False, "checks": None},
                   {"outcome": "not_scored", "reason": "unsupported_format", "box_hit": False, "checks": None}]
        s = summarize(records)
        self.assertEqual(s["facts_scored"], 4)
        self.assertEqual(s["outcomes"], {"match": 1, "conflict": 1, "abstain": 2})
        self.assertEqual(s["box_recall"], {"hit": 3, "of": 4, "rate": 0.75})
        self.assertEqual((s["exact_value"]["correct"], s["exact_value"]["read"]), (1, 2))
        self.assertEqual((s["period_column"]["correct"], s["period_column"]["read"]), (3, 3))
        self.assertEqual((s["rs_0"], s["rs_10"]), (0.25, -2.25))

    def test_candidate_order_is_fixed_by_salt(self):
        ciks = [1, 2, 3, 4, 5, 6]
        self.assertEqual(candidate_order(ciks, "a:"), candidate_order(list(reversed(ciks)), "a:"))
        self.assertNotEqual(candidate_order(range(1, 50), "a:"), candidate_order(range(1, 50), "b:"))

    def test_committed_rule_excludes_project_issuers(self):
        config = json.loads((ROOT / "configs/xbrl-grounding.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config["heldout_rule"]["exclude"]["ciks"]), {789019, 320193, 2488, 1045810})
        self.assertLessEqual(config["max_pages_per_issuer"], 4)

    def test_fsds_instance_names_the_primary_document(self):
        from scripts.xbrl_grounding import explicit_candidates, filing_urls, primary_document, sha1_base32
        self.assertEqual(primary_document("abc-20251231_htm.xml"), "abc-20251231.htm")
        self.assertIsNone(primary_document("abc-20251231.xml"))
        c = explicit_candidates("320193:0000320193-25-000079:aapl-20250927.htm")[0]
        self.assertEqual(filing_urls(c["cik"], c["accession"], c["document"])["document_url"],
                         "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm")
        with self.assertRaises(SystemExit):
            explicit_candidates("320193")
        self.assertEqual(sha1_base32(b""), "3I42H3S6NNFQ2MSVX7XZKYAYSCX5QBYJ")  # SHA-1 of empty input, base32

    def test_latest_annual_report_window(self):
        subs = {"filings": {"recent": {"form": ["10-Q", "10-K/A", "10-K", "10-K"], "filingDate": ["2026-05-01", "2026-03-01", "2026-02-20", "2025-02-20"],
                                       "accessionNumber": ["a", "b", "c", "d"], "reportDate": ["", "", "2025-12-31", "2024-12-31"],
                                       "primaryDocument": ["q.htm", "a.htm", "k.htm", "old.htm"], "isInlineXBRL": [1, 1, 1, 1], "size": [1, 1, 1, 1]}}}
        window = {"form": "10-K", "filed_from": "2026-01-01", "filed_before": "2026-11-11"}
        self.assertEqual(latest_annual_report(subs, window)["accession"], "c")
        self.assertIsNone(latest_annual_report(subs, dict(window, filed_from="2026-03-01")))

    def test_statement_pages_and_pagination(self):
        def f(concept, page, dims=False):
            return {"concept": concept, "context_ref": "d" if dims else "c", "page": page, "displayed": True}
        facts = [f("x:Filler", p) for p in (3, 4, 5, 9) for _ in range(12)]
        facts += [f("us-gaap:EarningsPerShareDiluted", 4), f("us-gaap:LiabilitiesAndStockholdersEquity", 3),
                  f("us-gaap:LiabilitiesAndStockholdersEquity", 9), f("us-gaap:NetCashProvidedByUsedInOperatingActivities", 5, dims=True),
                  f("us-gaap:NetCashProvidedByUsedInOperatingActivities", 9)]
        contexts = {"c": {"dims": []}, "d": {"dims": [["a", "b"]]}}
        anchors = {"income": ["us-gaap:EarningsPerShareDiluted"], "balance": ["us-gaap:LiabilitiesAndStockholdersEquity"],
                   "cash_flow": ["us-gaap:NetCashProvidedByUsedInOperatingActivities"]}
        got = statement_pages(facts, contexts, anchors, min_facts=10, max_pages=4)
        self.assertEqual(got["by_statement"], {"income": 4, "balance": 3, "cash_flow": 9})
        self.assertEqual(paginate([100, 101, 500], 900, 50), [(0.0, 100), (100, 500), (500, 900.0)])


if __name__ == "__main__":
    unittest.main()
