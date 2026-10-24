from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from creditlens.diligence import CasePack, displayed_amount, analyze_pack, RULE_VERSION
from creditlens.workflow import Actor, WorkflowService, render_memo
from scripts.evaluate_controls import run_controls

PACK = json.loads((Path(__file__).resolve().parents[1] / "data/examples/msft-fy2025.json").read_text(encoding="utf-8"))


class FinancialControlsTests(unittest.TestCase):
    def test_counterfactual_release_suite(self):
        report = run_controls()
        self.assertEqual(report["failed"], 0, [r["id"] for r in report["cases"] if not r["passed"]])
        self.assertEqual(report["probes"], 15)

    def test_amount_notation_preserves_sign_and_precision(self):
        for raw, expected in [(" $ 1,234.500 ", "1234.500"), ("(12,345.06)", "-12345.06"),
                              ("-0.125", "-0.125"), ("￥ 100", "100"), ("0", "0")]:
            with self.subTest(raw=raw):
                self.assertEqual(displayed_amount(raw), Decimal(expected))

    def test_ambiguous_ocr_and_double_signs_fail_closed(self):
        for raw in ["12,34", "1 000", "1e3", "10%", "(1,234", "(-100)", "(+100)", "—", "O.1", ""]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    displayed_amount(raw)

    def test_raw_value_mismatch_is_not_accepted_as_source_evidence(self):
        pack = deepcopy(PACK)
        next(f for f in pack["facts"] if f["id"] == "cfo-2025")["value"] = "10"
        with self.assertRaises(ValidationError):
            CasePack.model_validate(pack)

    def test_negative_cash_flow_cannot_improve_in_downside_scenario(self):
        pack = deepcopy(PACK)
        next(f for f in pack["facts"] if f["id"] == "cfo-2025").update(value="-100", raw_value="(100)")
        result = analyze_pack(CasePack.model_validate(pack), date(2025, 8, 1), 20)
        metrics = {m["id"]: m for m in result["metrics"]}
        self.assertEqual(metrics["fcf"]["value"], "-64651")
        self.assertNotIn("stress_fcf", metrics)
        self.assertIn("undefined_stress_fcf", [i["id"] for i in result["issues"]])

    def test_direct_call_stress_bounds(self):
        pack = CasePack.model_validate(PACK)
        for value in [-1, 81, True, 20.5, "20"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze_pack(pack, date(2025, 8, 1), value)

    def test_conflicted_current_metric_never_appears_in_growth_table(self):
        pack = deepcopy(PACK)
        f = deepcopy(next(f for f in pack["facts"] if f["id"] == "cfo-2025"))
        f.update(id="duplicate-cfo", value="1", raw_value="1")
        pack["facts"].append(f)
        result = analyze_pack(CasePack.model_validate(pack), date(2025, 8, 1))
        self.assertNotIn("cfo", [c["metric"] for c in result["comparison"]])

    def test_rule_upgrade_keeps_history_but_blocks_new_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            service = WorkflowService(Path(temp) / "workflow.sqlite")
            actor = Actor("analyst", "test", "analyst")
            with patch("creditlens.diligence.RULE_VERSION", "legacy-rule"), patch("creditlens.workflow.RULE_VERSION", "legacy-rule"):
                old = service.create(actor, CasePack.model_validate(PACK), date(2025, 8, 1), 20, "legacy-case-001")
            got = service.get(old["id"], actor)
            self.assertTrue(got["event_integrity_valid"])
            self.assertTrue(got["rule_status"]["outdated"])
            self.assertEqual(got["analysis"]["rule_version"], "legacy-rule")
            self.assertEqual(got["rule_status"]["current"], RULE_VERSION)
            with self.assertRaises(HTTPException) as error:
                service.update(old["id"], actor, 1, "disposition", issue_id="scope_review",
                               disposition="verified", note="The older rules must not be silently approved.")
            self.assertEqual(error.exception.status_code, 409)
            self.assertIn("旧规则历史底稿", render_memo(got))
            self.assertEqual(service.get(old["id"], actor)["revision"], 1)
