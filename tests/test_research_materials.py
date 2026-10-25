from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import tempfile
import unittest

from creditlens.diligence import digest
from creditlens.research_materials import (
    BUNDLE_ID, MATERIAL_MODE, MaterialsError, _annual_change,
    build_comparison, build_material_bundle, require_material_mode,
)


ROOT = Path(__file__).resolve().parents[1]


class MaterialBundleTests(unittest.TestCase):
    def setUp(self):
        self.bundle = build_material_bundle(ROOT)

    def decisions(self, excluded=()):
        return [{"observation_id": o["id"], "decision": "rejected" if o["id"] in excluded else "adopted", "note": "Reviewed the registered statement line."}
                for o in self.bundle["observations"]]

    def test_real_eight_values_keep_global_identity_and_signed_raw_amounts(self):
        obs = self.bundle["observations"]
        self.assertEqual(len(obs), 8)
        self.assertEqual(len({o["id"] for o in obs}), 8)
        actual = {(o["issuer_id"], o["metric"], o["year"]): o["normalized_value"] for o in obs}
        self.assertEqual(actual, {
            ("MSFT", "cfo", 2024): "118548", ("MSFT", "cfo", 2025): "136162",
            ("MSFT", "capex", 2024): "44477", ("MSFT", "capex", 2025): "64551",
            ("AAPL", "cfo", 2024): "118254", ("AAPL", "cfo", 2025): "111482",
            ("AAPL", "capex", 2024): "9447", ("AAPL", "capex", 2025): "12715",
        })
        for o in obs:
            self.assertEqual(o["id"], f"{o['issuer_id']}:{o['source']['sha256']}:{o['fact_id']}")
            self.assertEqual(o["source_version_id"], f"{o['issuer_id']}:{o['source']['sha256']}")
            self.assertEqual(o["period"]["days"], (date.fromisoformat(o["period"]["end"]) - date.fromisoformat(o["period"]["start"])).days + 1)
            if o["metric"] == "capex":
                self.assertEqual(o["raw_signed_value"], "-" + o["normalized_value"])
                self.assertEqual(o["transformation"], "absolute_cash_outflow")
                self.assertTrue(o["raw_value"].startswith("("))

    def test_bundle_is_deterministic_and_does_not_claim_raw_or_pit_verification(self):
        self.assertEqual(digest(self.bundle), digest(build_material_bundle(ROOT)))
        self.assertEqual(self.bundle["material_mode"], MATERIAL_MODE)
        for source in self.bundle["source_versions"]:
            self.assertTrue(source["server_trusted"])
            self.assertTrue(source["registered_source_hash_verified"])
            self.assertFalse(source["raw_pdf_rehashed"])
            self.assertFalse(source["historical_version_verified"])
        self.assertFalse(self.bundle["contract"]["ranking_allowed"])

    def test_scope_evidence_binds_new_manual_mapping_to_statement_and_source(self):
        for observation in self.bundle["observations"]:
            b = observation["basis"]
            self.assertEqual(b["operations_scope"], "reported_consolidated_statement_total")
            self.assertEqual(b["operations_partition"], "unknown")
            for evidence in b["scope_evidence"]:
                self.assertEqual(evidence["source_sha256"], observation["source"]["sha256"])
                self.assertEqual(evidence["value"], b[evidence["field"]])
            line = next(e for e in b["scope_evidence"] if e["field"] == "operations_scope")
            self.assertEqual(line["page"], observation["source"]["page"])
            self.assertEqual(line["quote"], observation["source"]["location"]["row_label"])

    def test_no_adoption_is_no_calculation(self):
        result = build_comparison(self.bundle, [])
        self.assertEqual(len(result["unassessed_observation_ids"]), 8)
        for row in result["annual_changes"]:
            self.assertEqual(row["status"], "blocked")
            self.assertIsNone(row["absolute_change"])
            self.assertEqual(row["input_ids"], [])

    def test_actual_adopted_annual_changes_and_refused_cross_company_ranks(self):
        result = build_comparison(self.bundle, self.decisions())
        actual = {(r["issuer_id"], r["metric"]): r["absolute_change"] for r in result["annual_changes"]}
        self.assertEqual(actual, {("MSFT", "cfo"): "17614", ("MSFT", "capex"): "20074", ("AAPL", "cfo"): "-6772", ("AAPL", "capex"): "3268"})
        for row in result["annual_changes"]:
            self.assertEqual(row["status"], "computed")
            self.assertEqual(row["growth_rate_status"], "computed")
            self.assertEqual(len(row["input_ids"]), 2)
            self.assertEqual(bool(row["warnings"]), row["issuer_id"] == "MSFT")
        for row in result["cross_company"]:
            self.assertEqual(row["status"], "side_by_side_only")
            self.assertIsNone(row["ranking"])
            self.assertIn("actual_period_mismatch", [x["code"] for x in row["reasons"]])
        self.assertFalse(result["prediction"])
        self.assertFalse(result["automatic_approval"])

    def test_rejected_fact_cannot_enter_annual_calculation(self):
        excluded = self.bundle["observations"][0]["id"]
        result = build_comparison(self.bundle, self.decisions([excluded]))
        affected = next(r for r in result["annual_changes"] if excluded in r["candidate_observation_ids"])
        self.assertEqual(affected["status"], "blocked")
        self.assertNotIn(excluded, affected["input_ids"])
        self.assertEqual(result["rejected_observation_ids"], [excluded])

    def test_decision_order_does_not_change_result(self):
        self.assertEqual(build_comparison(self.bundle, self.decisions()), build_comparison(self.bundle, list(reversed(self.decisions()))))

    def test_unknown_duplicate_and_invalid_decisions_rejected(self):
        one = self.decisions()[0]
        bads = [
            [one, one], [{**one, "observation_id": "cfo-2025"}],
            [{**one, "decision": "pending"}], [{**one, "issuer_id": "AAPL"}],
            [{**one, "note": " "}], [{**one, "note": "x" + " " * 2000}],
            [{**one, "note": None}], [{**one, "observation_id": ["bad"]}],
            {"observation_id": one["observation_id"]},
        ]
        for value in bads:
            with self.subTest(value=str(value)[:100]), self.assertRaises(MaterialsError):
                build_comparison(self.bundle, value)

    def test_tampered_observation_source_company_value_and_contract_are_rejected(self):
        for key, value in (("issuer_id", "AAPL"), ("company", "Other Co"), ("unit", "USD"), ("normalized_value", "1")):
            bad = deepcopy(self.bundle)
            bad["observations"][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(MaterialsError, "untrusted_bundle"):
                build_comparison(bad, [])
        for mutate in (
            lambda b: b["source_versions"][0].update(url="https://example.com/evil.pdf"),
            lambda b: b["contract"].update(ranking_allowed=True),
            lambda b: b["observations"].append(deepcopy(b["observations"][0])),
            lambda b: b["observations"][0]["basis"].update(operations_scope="continuing"),
        ):
            bad = deepcopy(self.bundle)
            mutate(bad)
            with self.assertRaises(MaterialsError):
                build_comparison(bad, [])

    def test_historical_mode_is_refused_even_before_any_adoption(self):
        bad = deepcopy(self.bundle)
        bad["material_mode"] = "historical_as_of"
        with self.assertRaisesRegex(MaterialsError, "historical_version_unverified"):
            build_comparison(bad, [])
        with self.assertRaises(MaterialsError):
            require_material_mode("historical_as_of")

    def test_unknown_bundle_path_or_url_never_selected(self):
        for name in ("../data/examples/msft-fy2025-pdf.json", "https://example.com/pack", "MSFT", None):
            with self.subTest(name=name), self.assertRaises(MaterialsError):
                build_material_bundle(ROOT, name)

    def test_semantic_hash_accepts_crlf_but_refuses_changed_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "data/examples").mkdir(parents=True)
            for source in self.bundle["source_versions"]:
                target = base / source["pack_path"]
                target.write_bytes((ROOT / source["pack_path"]).read_text(encoding="utf-8").replace("\n", "\r\n").encode())
            self.assertEqual(build_material_bundle(base), self.bundle)
            target = base / self.bundle["source_versions"][0]["pack_path"]
            data = json.loads(target.read_text(encoding="utf-8"))
            data["company"] = "wrong company"
            target.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(MaterialsError, "material_hash_mismatch"):
                build_material_bundle(base)

    def test_missing_registered_material_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(MaterialsError, "invalid_registered_material"):
                build_material_bundle(Path(tmp))

    def test_source_link_cannot_be_replaced_under_same_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "data/examples").mkdir(parents=True)
            for source in self.bundle["source_versions"]:
                data = json.loads((ROOT / source["pack_path"]).read_text(encoding="utf-8"))
                data["sources"][0]["url"] = "https://example.com/unregistered.pdf"
                (base / source["pack_path"]).write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(MaterialsError, "material_hash_mismatch"):
                build_material_bundle(base)


class AnnualCalculationBoundaryTests(unittest.TestCase):
    """Synthetic rule boundaries; never exposed as registered disclosures."""
    def pair(self, issuer="MSFT", metric="cfo"):
        return deepcopy([o for o in build_material_bundle(ROOT)["observations"] if o["issuer_id"] == issuer and o["metric"] == metric])

    def calc(self, pair):
        return _annual_change(pair[0], pair[1], {x["id"] for x in pair})

    def test_zero_and_negative_base_have_absolute_difference_but_no_growth(self):
        for base in ("0", "-10"):
            pair = self.pair()
            pair[0]["normalized_value"], pair[1]["normalized_value"] = base, "5"
            result = self.calc(pair)
            self.assertEqual(result["status"], "computed")
            self.assertEqual(result["absolute_change"], "5" if base == "0" else "15")
            self.assertIsNone(result["growth_rate"])
            self.assertEqual(result["growth_rate_status"], "not_computed_nonpositive_base")

    def test_negative_current_is_not_silently_made_positive(self):
        pair = self.pair()
        pair[0]["normalized_value"], pair[1]["normalized_value"] = "10", "-5"
        result = self.calc(pair)
        self.assertEqual(result["absolute_change"], "-15")
        self.assertEqual(result["growth_rate"], "-1.500000")

    def test_same_year_label_does_not_allow_misaligned_or_nonannual_period(self):
        for patch in ({"start": "2024-07-02", "days": 364}, {"days": 366}, {"kind": "quarter"}, {"fiscal_calendar": "unknown"}):
            pair = self.pair()
            pair[1]["period"].update(patch)
            with self.subTest(patch=patch):
                self.assertEqual(self.calc(pair)["status"], "blocked")
        pair = self.pair()
        pair[1]["year"] = 2026
        self.assertEqual(self.calc(pair)["status"], "blocked")

    def test_52_and_53_weeks_cannot_be_annualized_away(self):
        pair = self.pair("AAPL")
        pair[1]["period"].update(end="2025-10-04", days=371)
        result = self.calc(pair)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("annual_length_mismatch", [x["code"] for x in result["reasons"]])

    def test_unknown_or_missing_scope_evidence_blocks_even_if_same(self):
        pair = self.pair()
        for observation in pair:
            observation["basis"]["operations_scope"] = "unknown"
        self.assertEqual(self.calc(pair)["status"], "blocked")
        pair = self.pair()
        pair[1]["basis"]["scope_evidence"] = []
        self.assertEqual(self.calc(pair)["status"], "blocked")

    def test_different_units_issuer_scope_source_or_components_block(self):
        for key, value in (("issuer_id", "AAPL"), ("unit", "USD"), ("source_version_id", "new-version"), ("transformation", "other")):
            pair = self.pair()
            pair[1][key] = value
            with self.subTest(key=key):
                self.assertEqual(self.calc(pair)["status"], "blocked")
        pair = self.pair(metric="capex")
        pair[1]["basis"]["capex_components"] = "ppe_and_intangibles"
        self.assertEqual(self.calc(pair)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
