"""Accounting-control tests; synthetic mutations are not financial evidence."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens.cash_flow_bridge import CashFlowBridgeError, _build_from_bundle, build_cash_flow_bridge


def amount(fact, value):
    fact["signed_value"] = str(value)
    number = Decimal(str(value))
    fact["raw_value"] = "(" + format(number.copy_abs(), "f") + ")" if number < 0 else format(number, "f")


def synthetic_sources():
    sources, statements = [], []
    for issuer, sha in (("A", "a" * 64), ("B", "b" * 64)):
        source_id = f"{issuer}:{sha}"
        sources.append({"id": source_id, "issuer_id": issuer, "sha256": sha, "url": "https://example.invalid/synthetic"})
        for year in (2024, 2025):
            start, end = date(year - 1, 7, 1), date(year, 6, 30)
            values = [("net_income", "net_income", 100 if year == 2024 else 110),
                      ("depreciation", "reconciliation_adjustments", 10 if year == 2024 else 12),
                      ("gain_adjustment", "reconciliation_adjustments", -3 if year == 2024 else -4),
                      ("receivables", "operating_assets_and_liabilities", -5 if year == 2024 else -8),
                      ("other_change", "operating_assets_and_liabilities", 0)]
            facts = []
            for row_key, role, value in values + [("cfo", "cfo_total", sum(value for _, _, value in values))]:
                fact = {"id": f"{issuer}:{sha}:cf-bridge-{row_key}-{year}", "row_key": row_key, "role": role,
                        "label": row_key, "label_en": row_key, "source_sha256": sha, "page": 1,
                        "bbox": {"x0": 1, "top": 1, "x1": 5, "bottom": 5}, "location": {"page": 1}}
                amount(fact, value)
                facts.append(fact)
            statements.append({"id": f"{issuer}-{year}", "issuer_id": issuer, "company": "Synthetic " + issuer,
                               "year": year, "source_id": source_id, "source_sha256": sha,
                               "reported_cfo_observation_id": f"{issuer}:{sha}:cfo-{year}",
                               "total_fact_id": facts[-1]["id"], "expected_row_keys": [key for key, _, _ in values],
                               "unit": "USD_million", "scope": "reported_consolidated_statement_total",
                               "period": {"start": str(start), "end": str(end), "days": (end - start).days + 1,
                                          "fiscal_calendar": "calendar_year", "kind": "annual"}, "facts": facts})
    return {"schema_version": 1, "id": "synthetic-cash-flow", "title": "Synthetic only",
            "source_versions": sources, "statements": statements, "contract": {}, "limitations": ["Synthetic only"]}


def change_period(statement, start, end, calendar):
    statement["period"] = {"start": start, "end": end, "days": (date.fromisoformat(end) - date.fromisoformat(start)).days + 1,
                           "fiscal_calendar": calendar, "kind": "annual"}


class CashFlowBridgeTests(unittest.TestCase):
    def setUp(self):
        self.bundle = synthetic_sources()

    def statement(self, year=2025):
        return next(s for s in self.bundle["statements"] if s["issuer_id"] == "A" and s["year"] == year)

    def result(self, year=2025):
        return next(s for s in _build_from_bundle(self.bundle)["statements"] if s["issuer_id"] == "A" and s["year"] == year)

    def changes(self):
        return next(c for c in _build_from_bundle(self.bundle)["changes"] if c["issuer_id"] == "A")

    def assert_blocked(self, code):
        result = self.result()
        self.assertEqual(result["status"], "blocked")
        self.assertIn(code, [r["code"] for r in result["reasons"]])
        self.assertEqual(self.changes()["status"], "blocked")
        return result

    def test_signed_identity_and_every_row_change_reconcile(self):
        previous, current, changes = self.result(2024), self.result(), self.changes()
        self.assertEqual((previous["computed_cfo"], current["reported_cfo"], current["residual"]), ("102", "110", "0"))
        self.assertEqual(current["status"], "reconciled")
        self.assertEqual(len(current["rows"]), 5)
        self.assertNotIn("cfo", [row["row_key"] for row in current["rows"]])
        self.assertEqual(changes["reported_cfo_change"], "8")
        self.assertEqual(changes["contribution_total"], "8")
        self.assertEqual(changes["residual"], "0")
        self.assertEqual({g["group"]: g["contribution"] for g in changes["groups"]},
                         {"net_income": "10", "reconciliation_adjustments": "1", "operating_assets_and_liabilities": "-3"})
        self.assertEqual(next(r for r in changes["rows"] if r["row_key"] == "gain_adjustment")["contribution"], "-1")
        self.assertEqual(len(changes["input_ids"]), 12)
        self.assertFalse(changes["automatic_approval"])

    def test_fixed_inventory_blocks_missing_zero_and_cancelling_rows(self):
        current = self.statement()
        current["facts"] = [f for f in current["facts"] if f["row_key"] != "other_change"]
        result = self.assert_blocked("row_inventory_mismatch")
        self.assertIsNone(result["computed_cfo"])
        self.bundle = synthetic_sources()
        for statement in self.bundle["statements"]:
            for key, value in (("offset_one", 7), ("offset_two", -7)):
                fact = deepcopy(statement["facts"][-2])
                fact.update(row_key=key, label=key, label_en=key,
                            id=f"{statement['issuer_id']}:{statement['source_sha256']}:cf-bridge-{key}-{statement['year']}")
                amount(fact, value)
                statement["facts"].insert(-1, fact)
                statement["expected_row_keys"].append(key)
        self.assertEqual(self.result()["status"], "reconciled")
        current = self.statement()
        current["facts"] = [f for f in current["facts"] if not f["row_key"].startswith("offset_")]
        self.assert_blocked("row_inventory_mismatch")

    def test_raw_sign_mismatch_and_wrong_signed_total_are_visible(self):
        current = self.statement()
        current["facts"][3]["signed_value"] = "8"
        self.assert_blocked("invalid_signed_amount")
        self.bundle = synthetic_sources()
        amount(self.statement()["facts"][3], 8)
        result = self.assert_blocked("nonzero_reconciliation_residual")
        self.assertEqual(result["computed_cfo"], "126")
        self.assertEqual(result["reported_cfo"], "110")
        self.assertEqual(result["residual"], "16")

    def test_missing_nonfinite_ambiguous_and_nonstring_amounts_fail_closed(self):
        for value in (None, "NaN", "Infinity", "-Infinity", "1e2", 1.0, True, "9" * 37):
            with self.subTest(value=value):
                self.bundle = synthetic_sources()
                self.statement()["facts"][0]["signed_value"] = value
                self.assert_blocked("invalid_signed_amount")
        self.bundle = synthetic_sources()
        self.statement()["facts"][0]["raw_value"] = "—"
        self.assert_blocked("invalid_signed_amount")
        self.bundle = synthetic_sources()
        self.statement()["facts"][0]["signed_value"] = float("nan")
        with self.assertRaises(CashFlowBridgeError):
            _build_from_bundle(self.bundle)

    def test_unknown_role_duplicate_key_and_total_double_count_rejected(self):
        self.statement()["facts"][1]["role"] = "made_up_adjustment"
        self.assert_blocked("unknown_role")
        self.bundle = synthetic_sources()
        self.statement()["facts"].append(deepcopy(self.statement()["facts"][0]))
        self.assert_blocked("duplicate_or_invalid_row")
        self.bundle = synthetic_sources()
        self.statement()["expected_row_keys"].append("cfo")
        self.assert_blocked("invalid_row_inventory")
        self.bundle = synthetic_sources()
        self.statement()["facts"][-1]["role"] = "reconciliation_adjustments"
        self.assert_blocked("invalid_total")

    def test_unit_period_scope_and_fact_context_mismatch_block(self):
        for mutation, code in (({"unit": "USD"}, "unit_mismatch"), ({"scope": "continuing_only"}, "scope_mismatch")):
            self.bundle = synthetic_sources()
            self.statement().update(mutation)
            self.assert_blocked(code)
        self.bundle = synthetic_sources()
        self.statement()["period"]["days"] -= 1
        self.assert_blocked("invalid_period")
        for field, value in (("unit", "EUR_million"), ("period", {"start": "1900-01-01"}), ("source_id", "other-source")):
            self.bundle = synthetic_sources()
            self.statement()["facts"][1][field] = value
            self.assert_blocked(f"row_{field}_mismatch")

    def test_cross_source_row_and_year_file_mismatch_block(self):
        self.statement()["facts"][0]["source_sha256"] = "b" * 64
        self.assert_blocked("fact_source_mismatch")
        self.bundle = synthetic_sources()
        self.statement()["source_sha256"] = "c" * 64
        self.assert_blocked("source_mismatch")
        self.bundle = synthetic_sources()
        self.statement()["source_id"] = "B:" + "b" * 64
        self.assert_blocked("source_mismatch")
        self.bundle = synthetic_sources()
        self.statement()["reported_cfo_observation_id"] = "cfo-2025"
        self.assert_blocked("cfo_observation_mismatch")

    def test_reclassified_or_relabelled_rows_are_not_automatically_paired(self):
        for field, value in (("role", "operating_assets_and_liabilities"), ("label", "A revised definition"),
                             ("label_en", "A revised definition")):
            with self.subTest(field=field):
                self.bundle = synthetic_sources()
                self.statement()["facts"][2][field] = value
                self.assertEqual(self.result()["status"], "reconciled")
                changes = self.changes()
                self.assertEqual(changes["status"], "blocked")
                self.assertIn("row_classification_changed", [r["code"] for r in changes["reasons"]])
                self.assertEqual(changes["rows"], [])

    def test_each_year_can_reconcile_but_row_set_change_blocks_decomposition(self):
        current = self.statement()
        current["expected_row_keys"][-1] = "new_zero_row"
        fact = current["facts"][-2]
        fact.update(row_key="new_zero_row", id=f"A:{'a'*64}:cf-bridge-new_zero_row-2025", label="new_zero_row", label_en="new_zero_row")
        self.assertEqual(self.result()["status"], "reconciled")
        changes = self.changes()
        self.assertEqual(changes["status"], "blocked")
        self.assertIn("row_set_changed", [r["code"] for r in changes["reasons"]])

    def test_period_order_and_52_53_week_mismatch_block_changes(self):
        change_period(self.statement(), "2025-07-01", "2026-06-30", "calendar_year")
        self.assertEqual(self.result()["status"], "reconciled")
        self.assertIn("nonconsecutive_periods", [r["code"] for r in self.changes()["reasons"]])
        self.bundle = synthetic_sources()
        change_period(self.statement(2024), "2023-01-01", "2024-01-06", "week_52_53")
        change_period(self.statement(), "2024-01-07", "2025-01-04", "week_52_53")
        self.assertEqual(self.result()["status"], "reconciled")
        self.assertIn("week_year_length_mismatch", [r["code"] for r in self.changes()["reasons"]])

    def test_negative_and_zero_cfo_do_not_generate_contribution_percentages(self):
        for statement in self.bundle["statements"]:
            amount(statement["facts"][0], -100 if statement["year"] == 2024 else 0)
            total = sum(Decimal(f["signed_value"]) for f in statement["facts"] if f["role"] != "cfo_total")
            amount(statement["facts"][-1], total)
        result = _build_from_bundle(self.bundle)
        self.assertTrue(all(s["status"] == "reconciled" for s in result["statements"]))
        self.assertTrue(all(c["status"] == "reconciled" for c in result["changes"]))
        for change in result["changes"]:
            self.assertNotIn("growth_rate", change)
            for row in change["rows"]:
                self.assertNotIn("percentage", row)
                self.assertNotIn("share", row)

    def test_missing_or_duplicate_statement_inventory_fails_closed(self):
        for mutate in (lambda b: b["statements"].pop(),
                       lambda b: b["statements"].__setitem__(1, deepcopy(b["statements"][0])),
                       lambda b: b["source_versions"].append(deepcopy(b["source_versions"][0]))):
            bundle = synthetic_sources()
            mutate(bundle)
            with self.assertRaises(CashFlowBridgeError):
                _build_from_bundle(bundle)

    def test_immutable_input_and_deterministic_result(self):
        before = deepcopy(self.bundle)
        first, second = _build_from_bundle(self.bundle), _build_from_bundle(self.bundle)
        self.assertEqual(first, second)
        self.assertEqual(self.bundle, before)
        first["source_bundle"]["statements"].clear()
        first["statements"][0]["rows"].clear()
        self.assertEqual(_build_from_bundle(self.bundle), second)

    def test_decimal_arithmetic_is_exact_even_under_low_global_precision(self):
        for statement in self.bundle["statements"]:
            amount(statement["facts"][0], "123456789012345678901234567890.123456")
            amount(statement["facts"][2], "-123456789012345678901234567890.123456")
            with localcontext() as context:
                context.prec = 80
                total = sum(Decimal(f["signed_value"]) for f in statement["facts"] if f["role"] != "cfo_total")
            amount(statement["facts"][-1], total)
        with localcontext() as context:
            context.prec = 2
            result = _build_from_bundle(self.bundle)
        self.assertTrue(all(statement["status"] == "reconciled" for statement in result["statements"]))
        self.assertTrue(all(change["status"] == "reconciled" for change in result["changes"]))
        self.assertEqual(result["changes"][0]["contribution_total"], "-1")


class RegisteredCashFlowBridgeTests(unittest.TestCase):
    """Frozen public reference arithmetic, with no OCR or network access."""

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_registered_totals_changes_and_source_bindings(self):
        from creditlens.research_materials import build_material_bundle
        first = build_cash_flow_bridge(self.root)
        self.assertEqual(first, build_cash_flow_bridge(self.root))
        self.assertEqual(sum(len(s["facts"]) for s in first["source_bundle"]["statements"]), 52)
        totals = {(s["issuer_id"], s["year"]): s["computed_cfo"] for s in first["statements"]}
        self.assertEqual(totals, {("MSFT", 2024): "118548", ("MSFT", 2025): "136162",
                                  ("AAPL", 2024): "118254", ("AAPL", 2025): "111482"})
        self.assertTrue(all(s["status"] == "reconciled" and s["residual"] == "0" for s in first["statements"]))
        changes = {c["issuer_id"]: c for c in first["changes"]}
        self.assertEqual([g["contribution"] for g in changes["MSFT"]["groups"]], ["13696", "11092", "-7174"])
        self.assertEqual([g["contribution"] for g in changes["AAPL"]["groups"]], ["18274", "3605", "-28651"])
        self.assertEqual(changes["MSFT"]["reported_cfo_change"], "17614")
        self.assertEqual(changes["AAPL"]["reported_cfo_change"], "-6772")
        self.assertEqual(len(changes["MSFT"]["rows"]), 14)
        self.assertEqual(len(changes["AAPL"]["rows"]), 10)
        observations = {o["id"]: o for o in build_material_bundle(self.root)["observations"]}
        for statement in first["statements"]:
            old = observations[statement["reported_cfo_observation_id"]]
            self.assertEqual(statement["reported_cfo"], old["normalized_value"])
            self.assertEqual(statement["source_sha256"], old["source"]["sha256"])
            self.assertEqual(statement["period"], old["period"])

    def test_changed_missing_or_refrozen_inventory_is_not_silently_accepted(self):
        from creditlens.cash_flow_sources import CashFlowSourcesError, DATASET_PATH
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (DATASET_PATH, "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json"):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / relative, destination)
            source = root / DATASET_PATH
            pristine = json.loads(source.read_text(encoding="utf-8"))
            self.assertEqual(build_cash_flow_bridge(root), build_cash_flow_bridge(self.root))
            # Even editing both the expected inventory and facts cannot establish a new registered source.
            changed = deepcopy(pristine)
            statement = changed["statements"][0]
            missing_key = statement["expected_row_keys"].pop()
            statement["facts"] = [f for f in statement["facts"] if f["row_key"] != missing_key]
            source.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(CashFlowSourcesError) as caught:
                build_cash_flow_bridge(root)
            self.assertEqual(caught.exception.code, "source_hash_mismatch")
            source.unlink()
            with self.assertRaises(CashFlowSourcesError):
                build_cash_flow_bridge(root)

    def test_existing_research_cfo_must_still_match(self):
        from creditlens.cash_flow_sources import load_cash_flow_sources
        from creditlens.research_materials import build_material_bundle
        source = load_cash_flow_sources(self.root)
        material = build_material_bundle(self.root)
        cfo = next(o for o in material["observations"] if o["metric"] == "cfo")
        cfo["normalized_value"] = "999999"
        with patch("creditlens.cash_flow_sources.load_cash_flow_sources", return_value=source), \
                patch("creditlens.research_materials.build_material_bundle", return_value=material):
            with self.assertRaises(CashFlowBridgeError) as caught:
                build_cash_flow_bridge(self.root)
        self.assertEqual(caught.exception.code, "existing_cfo_mismatch")


if __name__ == "__main__":
    unittest.main()
