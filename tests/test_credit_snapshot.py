"""Credit snapshot (v0.36.0) helpers: gallery strata, archive checks, FSDS keys for check (a), check (b) comparison."""
from datetime import date
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import credit_snapshot as cs  # noqa: E402


class GalleryRule(unittest.TestCase):
    def test_config_is_the_pre_registered_rule(self):
        config = cs.load_config()
        self.assertEqual(config["definitions_version"], "credit-snapshot-def-1")
        self.assertEqual(len(config["gallery"]["strata"]), 8)
        self.assertEqual(config["gallery"]["take_per_stratum"] * 8, 40)
        self.assertEqual(config["checks"]["a_parser"]["pass_bar"]["recall_all_filings"], 0.995)
        self.assertEqual(sum(s["take"] for s in config["heldout_b"]["samples"]), 10)

    def test_strata_cover_sic_divisions(self):
        strata = cs.load_config()["gallery"]["strata"]
        self.assertEqual(cs.stratum_of("1311", strata)["id"], "mining")
        self.assertEqual(cs.stratum_of("6022", strata)["id"], "finance")
        self.assertEqual(cs.stratum_of("7372", strata)["id"], "services")
        self.assertIsNone(cs.stratum_of("9995", strata))
        self.assertIsNone(cs.stratum_of("", strata))

    def test_dei_values_and_deterministic_gzip(self):
        html = '<ix:nonNumeric contextRef="c" name="dei:DocumentType"><span>10-K</span></ix:nonNumeric><ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="c">0000021344</ix:nonNumeric>'
        self.assertEqual(cs.dei_values(html), {"DocumentType": "10-K", "EntityCentralIndexKey": "0000021344"})
        self.assertEqual(cs.gzip_bytes(b"abc"), cs.gzip_bytes(b"abc"))
        self.assertEqual(gzip.decompress(cs.gzip_bytes(b"abc")), b"abc")


class CheckA(unittest.TestCase):
    def test_fsds_keys(self):
        self.assertEqual(cs.nearest_month_end(date(2025, 9, 27)), date(2025, 9, 30))
        self.assertEqual(cs.nearest_month_end(date(2025, 10, 3)), date(2025, 9, 30))
        self.assertEqual(cs.nearest_month_end(date(2025, 12, 31)), date(2025, 12, 31))
        self.assertEqual(cs.measure_key("iso4217:USD/xbrli:shares"), "USD/shares")
        self.assertEqual(cs.fact_key("Assets", "iso4217:USD", {"instant": "2025-09-27", "dims": []}), ("Assets", "USD", "20250930", 0))
        self.assertEqual(cs.fact_key("Revenues", "iso4217:USD", {"start": "2024-09-29", "end": "2025-09-27", "dims": []})[3], 4)
        self.assertIsNone(cs.fact_key("Revenues", "iso4217:USD", {"start": "2025-01-01", "end": "2025-12-31", "dims": [["a", "b"]]}))

    def test_compare(self):
        computed = {"contexts": {"c": {"instant": "2025-12-31", "dims": []}}, "units": {"u": "iso4217:USD"},
                    "facts": [{"local": "Assets", "ctx": "c", "unit": "u", "value": "104816000000"},
                              {"local": "Liabilities", "ctx": "c", "unit": "u", "value": "70000000000"}]}
        rows = [{"tag": "Assets", "uom": "USD", "ddate": "20251231", "qtrs": "0", "value": "104816000000.0000"},
                {"tag": "Liabilities", "uom": "USD", "ddate": "20251231", "qtrs": "0", "value": "70000000001.0000"},
                {"tag": "Goodwill", "uom": "USD", "ddate": "20251231", "qtrs": "0", "value": "1.0000"}]
        result = cs.compare_fsds(rows, computed)
        self.assertEqual((result["rows"], result["found"], result["agree"]), (3, 2, 1))
        self.assertEqual(len(result["conflicts"]), 1)

    def test_corrected_key_is_post_hoc_and_separate(self):
        computed = {"contexts": {"d": {"start": "2025-01-01", "end": "2025-12-31", "dims": []}, "c": {"instant": "2025-12-31", "dims": []}},
                    "units": {"eps": "iso4217:USD/xbrli:shares", "u": "iso4217:USD"},
                    "facts": [{"local": "EarningsPerShareDiluted", "ctx": "d", "unit": "eps", "value": "3.36"},
                              {"local": "CommitmentsAndContingencies", "ctx": "c", "unit": "u", "value": None}]}
        rows = [{"tag": "EarningsPerShareDiluted", "uom": "USD", "ddate": "20251231", "qtrs": "4", "value": "3.3600"},
                {"tag": "CommitmentsAndContingencies", "uom": "USD", "ddate": "20251231", "qtrs": "0", "value": ""}]
        registered = cs.compare_fsds(rows, computed)
        self.assertEqual((registered["found"], len(registered["missing"])), (0, 2))
        corrected = cs.compare_fsds(rows, computed, "corrected")
        self.assertEqual((corrected["found"], corrected["agree"], corrected["without_value"], len(corrected["missing"])), (1, 1, 1, 0))


class CheckB(unittest.TestCase):
    def test_tolerances(self):
        self.assertTrue(cs.agree("debt_to_ebitda", 2.004, 2.0))
        self.assertFalse(cs.agree("debt_to_ebitda", 2.006, 2.0))
        self.assertTrue(cs.agree("total_debt", "45492000000", 45492000000))
        self.assertFalse(cs.agree("total_debt", "45492000000", "45492000001"))
        self.assertTrue(cs.agree("interest_coverage", None, None))
        self.assertFalse(cs.agree("interest_coverage", None, 3.2))

    def test_compare_report(self):
        snapshot = {"status": "ok", "template": {"kind": "general"}, "meta": {"period_end": "2025-12-31", "months": 12},
                    "inputs": {"debt": {"value": "100"}}, "metrics": {"debt_to_ebitda": {"value": 2.0}}, "ladder": None}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "computed").mkdir()
            (tmp / "computed" / "x.json").write_text(json.dumps({"snapshot": snapshot}))
            key = {"filings": [{"id": "x", "name": "X", "form": "10-K", "template": "general", "period_end": "2025-12-31", "months": 12,
                                "metrics": {"total_debt": 100, "debt_to_ebitda": 2.001}}]}
            (tmp / "key.json").write_text(json.dumps(key))
            cs.cmd_heldout_compare(type("A", (), {"key": str(tmp / "key.json"), "computed": str(tmp / "computed"), "out": str(tmp / "out")})())
            report = json.loads((tmp / "out" / "check-b.json").read_text())
            self.assertTrue(report["filings"][0]["template_agrees"])
            self.assertEqual(report["per_metric"]["total_debt"], {"agree": 1})
            self.assertEqual(report["per_metric"]["debt_to_ebitda"], {"agree": 1})


if __name__ == "__main__":
    unittest.main()
