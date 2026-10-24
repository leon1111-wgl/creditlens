from datetime import date
from pathlib import Path
import unittest
from creditlens.diligence import CasePack
from scripts.export_factors import snapshot


class FactorTests(unittest.TestCase):
    def setUp(self):
        self.pack = CasePack.model_validate_json((Path(__file__).resolve().parents[1] / "data/examples/msft-fy2025.json").read_text(encoding="utf-8"))

    def test_no_future_features_or_scenario_as_observation(self):
        old = snapshot(self.pack, date(2025, 7, 29))
        self.assertEqual(old["factors"], [])
        self.assertEqual(old["source_documents"], [])
        current = snapshot(self.pack, date(2025, 8, 1))
        self.assertEqual(len(current["factors"]), 5)
        self.assertNotIn("stress_fcf", [f["id"] for f in current["factors"]])
        self.assertIsNone(current["outcome_label"])
        self.assertFalse(current["usable_for_prediction_training"])
        self.assertTrue(all(f["fact_ids"] for f in current["factors"]))
