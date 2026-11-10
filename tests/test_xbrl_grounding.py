"""XBRL grounding benchmark: key derivation, OCR-only page reading and fact scoring.

Synthetic OCR spans only; no network, rendering or model. The real run is the
Actions workflow; these tests pin the rules that were committed before it.
"""
import json
from pathlib import Path
import unittest

from creditlens.xbrl_grounding import (
    PageReading, candidate_order, header_year, key_entry, key_magnitude,
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

    def test_diluted_row_finds_the_heading_above_basic(self):
        # OCR boxes of adjacent lines overlap slightly; the heading sits above two amount rows.
        page = statement_page([span("Earnings per share:", 6, 134, 87, 146.6), span("Basic", 22, 146.1, 50, 160.1),
                               span("7.49", 414, 146.1, 440, 160.1), span("Diluted", 22, 157.6, 56, 172.6),
                               span("7.46", 414, 158.1, 442, 172.1)])
        for text, b in (("7.49", box(414, 146.1, 440, 160.1)), ("7.46", box(414, 158.1, 442, 172.1))):
            f = fact(text, b, scale="0", unit="usdps")
            r = score_fact(f, key_entry(f, CONTEXTS, UNITS), PageReading(page))
            self.assertEqual(r["reason"], "share_or_per_share_row", text)

    def test_score2_heading_scope_ends_at_a_row_back_at_its_indent(self):
        # A wrapped share-capital label above two USD equity rows at the same indent (round-2 defect).
        page = statement_page([
            span("Common stock, $0.001 par value; 110,000,000 shares authorized;", 20, 150, 330, 160),
            span("40,048,410 shares issued and outstanding", 30, 162, 250, 172), span("40", 412, 162, 424, 172),
            span("Additional paid-in capital", 20, 176, 160, 186), span("1,234", 400, 176, 426, 186),
            span("Retained earnings", 20, 190, 120, 200), span("5,678", 400, 190, 426, 200)])
        for text, b, rule2 in (("40", box(412, 162, 424, 172), "share_or_per_share_row"),
                               ("1,234", box(400, 176, 426, 186), None), ("5,678", box(400, 190, 426, 200), None)):
            f = fact(text, b, context="fy25")
            k = key_entry(f, CONTEXTS, UNITS)
            self.assertEqual(score_fact(f, k, PageReading(page))["reason"], "share_or_per_share_row", text)
            r = score_fact(f, k, PageReading(page, "xbrl-grounding-score-2"))
            self.assertEqual(r["reason"], rule2, text)
            if rule2 is None:
                self.assertEqual(r["outcome"], "match", text)

    def test_score2_keeps_indented_and_basic_diluted_per_share_rows(self):
        indented = statement_page([span("Earnings per share:", 6, 134, 87, 146.6), span("Basic", 22, 146.1, 50, 160.1),
                                   span("7.49", 414, 146.1, 440, 160.1), span("Diluted", 22, 157.6, 56, 172.6),
                                   span("7.46", 414, 158.1, 442, 172.1)])
        flush = statement_page([span("Net income per share", 20, 134, 130, 146), span("Basic", 20, 148, 48, 160),
                                span("7.49", 414, 148, 440, 160), span("Diluted", 20, 162, 54, 174),
                                span("7.46", 414, 162, 442, 174)])
        for page, cells in ((indented, (("7.49", box(414, 146.1, 440, 160.1)), ("7.46", box(414, 158.1, 442, 172.1)))),
                            (flush, (("7.49", box(414, 148, 440, 160)), ("7.46", box(414, 162, 442, 174))))):
            for text, b in cells:
                f = fact(text, b, scale="0", unit="usdps")
                r = score_fact(f, key_entry(f, CONTEXTS, UNITS), PageReading(page, "xbrl-grounding-score-2"))
                self.assertEqual(r["reason"], "share_or_per_share_row", text)

    def test_unknown_score_rule_is_refused(self):
        with self.assertRaises(ValueError):
            PageReading([], "xbrl-grounding-score-9")

    def test_opening_balance_belongs_to_the_column_it_opens(self):
        contexts = dict(CONTEXTS, open25={"period": {"instant": "2024-12-31"}, "dims": []})
        on_page = [{"context_ref": "fy25"}, {"context_ref": "fy24"}]
        starts = __import__("creditlens.xbrl_grounding", fromlist=["page_period_starts"]).page_period_starts(on_page, contexts)
        k = key_entry(fact("1", box(0, 0, 1, 1), context="open25"), contexts, UNITS, starts)
        self.assertEqual((k["years"], k["period_role"]), ([2024, 2025], "opening_or_closing_balance"))
        # On a balance sheet (no durations on the page) the instant keeps its own year only.
        self.assertEqual(key_entry(fact("1", box(0, 0, 1, 1), context="open25"), contexts, UNITS, {})["years"], [2024])

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

    def test_rule_c_adds_seen_issuers_and_a_new_salt(self):
        from scripts.xbrl_grounding import heldout_rule
        config = json.loads((ROOT / "configs/xbrl-grounding.json").read_text(encoding="utf-8"))
        b, c = heldout_rule(config, "b"), heldout_rule(config, "c")
        self.assertEqual(b, config["heldout_rule"])
        seen = {1321655, 1780312, 719220, 1287750, 1092699, 914156, 1655050, 21344}
        self.assertTrue(seen <= set(c["exclude"]["ciks"]) and set(b["exclude"]["ciks"]) <= set(c["exclude"]["ciks"]))
        self.assertNotEqual(b["order"]["salt"], c["order"]["salt"])
        self.assertEqual((c["population"], c["accept"], c["take"]), (b["population"], b["accept"], b["take"]))
        self.assertEqual(c["rule_id"], "heldout-2026-11-c")

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


class GroundingRouteTests(unittest.TestCase):
    def test_local_service_serves_the_page_and_only_published_files(self):
        import tempfile
        from fastapi.testclient import TestClient
        from creditlens.api import create_app
        with tempfile.TemporaryDirectory() as tmp, TestClient(create_app(Path(tmp) / "missing.json", Path(tmp) / "audit.sqlite")) as client:
            page = client.get("/grounding")
            self.assertEqual(page.status_code, 200)
            self.assertIn('src="/assets/grounding.js"', page.text)
            self.assertIn("答案键 = 发行人自己的 XBRL 标签", page.text)
            self.assertIn("干净渲染页，不是扫描件", page.text)
            self.assertEqual(client.get("/assets/grounding.js").headers["content-type"].split(";")[0], "text/javascript")
            for name in ("..%2Fapi.py", "index.html", "x.png", "a/b.json"):
                self.assertEqual(client.get("/assets/grounding/" + name).status_code, 404, name)
            index = ROOT / "creditlens/grounding/index.json"
            if index.is_file():
                data = json.loads(index.read_text(encoding="utf-8"))
                self.assertEqual(client.get("/assets/grounding/index.json").json()["summary"], data["summary"])
                image = data["issuers"][0]["pages"][0]["image"]
                self.assertEqual(client.get("/assets/grounding/" + image).headers["content-type"], "image/webp")


if __name__ == "__main__":
    unittest.main()
