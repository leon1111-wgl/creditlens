"""Fictional workflow replay checks, with actual registered public disclosure facts."""
from copy import deepcopy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi import HTTPException

from scripts import replay_research_scenarios as runner
from scripts.verify_research_export import verify_export


ROOT = Path(__file__).resolve().parents[1]


class ResearchScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.db = cls.directory/"replay.sqlite"
        cls.exports = cls.directory/"exports"
        cls.manifest = runner.replay(ROOT, cls.db, cls.exports)

    def export(self, scenario_index, snapshot_index=-1):
        relative = self.manifest["artifacts"][scenario_index]["snapshots"][snapshot_index]["export_path"]
        return json.loads((self.exports/relative).read_text(encoding="utf-8"))

    def test_three_expected_states_and_snapshot_counts(self):
        self.assertEqual([a["outcome"]["status"] for a in self.manifest["artifacts"]], ["reviewed", "reviewed", "returned"])
        self.assertEqual([len(a["snapshots"]) for a in self.manifest["artifacts"]], [1, 2, 1])
        self.assertTrue(self.manifest["expected_return_is_successful_replay"])

    def test_profit_cash_divergence_is_derived_from_real_registered_attachment(self):
        exported = self.export(0)
        content = exported["snapshot"]["content"]
        self.assertEqual(content["store_version"], "research-handoff-2")
        self.assertEqual(sum(len(s["facts"]) for s in content["cash_flow_bridge"]["source_bundle"]["statements"]), 52)
        diagnostic = self.manifest["artifacts"][0]["outcome"]["direction_diagnostic"]
        self.assertEqual(diagnostic["net_income_change"], "18274")
        self.assertEqual(diagnostic["cfo_change"], "-6772")
        self.assertTrue(all(h["status"] == "pending" for h in content["hypotheses"]))
        self.assertFalse(content["cash_flow_bridge"]["prediction"])

    def test_returned_first_snapshot_and_corrected_second_are_both_preserved(self):
        before, after = self.export(1, 0), self.export(1, 1)
        self.assertNotEqual(before["snapshot"]["id"], after["snapshot"]["id"])
        self.assertNotEqual(before["snapshot"]["sha256"], after["snapshot"]["sha256"])
        self.assertEqual(before["review"]["decision"], "return")
        self.assertEqual(after["review"]["decision"], "approve")
        self.assertLess(before["snapshot"]["revision"], after["snapshot"]["revision"])
        self.assertNotEqual(before["snapshot"]["content"]["coverage_note"], after["snapshot"]["content"]["coverage_note"])
        for exported in (before, after):
            self.assertTrue(verify_export(exported)["ok"])
            self.assertTrue(all(x["ranking"] is None for x in exported["snapshot"]["content"]["comparison"]["cross_company"]))

    def test_rejected_fact_is_preserved_and_comparison_remains_blocked(self):
        exported = self.export(2)
        content = exported["snapshot"]["content"]
        self.assertEqual(content["store_version"], "research-handoff-1")
        rejected = [d for d in content["decisions"] if d["decision"] == "rejected"]
        self.assertEqual(len(rejected), 1)
        observation = next(o for o in content["bundle"]["observations"] if o["id"] == rejected[0]["observation_id"])
        self.assertEqual((observation["issuer_id"], observation["metric"], observation["year"]), ("AAPL", "capex", 2025))
        self.assertEqual(observation["raw_value"], "(12,715)")
        blocked = next(x for x in content["comparison"]["annual_changes"] if x["issuer_id"] == "AAPL" and x["metric"] == "capex")
        self.assertEqual(blocked["status"], "blocked")
        self.assertIsNone(blocked["absolute_change"])
        self.assertNotIn(observation["id"], blocked["input_ids"])
        self.assertTrue(all(h["status"] == "not_assessed" for h in content["hypotheses"]))
        self.assertEqual(exported["review"]["decision"], "return")

    def test_export_files_hash_and_snapshot_checks_pass(self):
        paths = []
        for artifact in self.manifest["artifacts"]:
            for snapshot in artifact["snapshots"]:
                path = self.exports/snapshot["export_path"]
                paths.append(path)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), snapshot["file_sha256"])
                checked = verify_export(json.loads(path.read_text(encoding="utf-8")))
                self.assertEqual(checked["content_sha256"], snapshot["sha256"])
        self.assertEqual(len(paths), 4)
        self.assertEqual(len(set(paths)), 4)
        self.assertEqual(len(list(self.exports.glob("*.json"))), 4)  # three final exports plus manifest
        self.assertFalse(self.db.is_relative_to(self.exports))

    def test_simulated_provenance_roles_and_no_customer_outcome_claim(self):
        p = self.manifest["provenance"]
        self.assertEqual(p["business_scenarios"], "synthetic")
        self.assertFalse(p["real_client_or_university_collaboration"])
        self.assertFalse(p["measured_customer_outcome"])
        self.assertFalse(self.manifest["database_published"])
        for index, artifact in enumerate(self.manifest["artifacts"]):
            self.assertTrue(artifact["tenant_id"].startswith("synthetic-"))
            content = self.export(index)["snapshot"]["content"]
            self.assertIn("模拟", content["question"])
            self.assertNotIn(self.export(index)["review"]["actor"], content["contributors"])
            self.assertFalse(content["automatic_approval"])

    def test_existing_database_refused_without_changing_files(self):
        before = self.db.read_bytes()
        with self.assertRaisesRegex(ValueError, "must both be new"):
            runner.replay(ROOT, self.db, self.directory/"second-exports")
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse((self.directory/"second-exports").exists())

    def test_existing_export_directory_refused_before_creating_database(self):
        candidate = self.directory/"second.sqlite"
        with self.assertRaisesRegex(ValueError, "must both be new"):
            runner.replay(ROOT, candidate, self.exports)
        self.assertFalse(candidate.exists())

    def test_public_database_or_output_paths_refused_before_store(self):
        with patch.object(runner, "ResearchTaskStore", side_effect=AssertionError("Store must not open")):
            for db, out in ((ROOT/"data/research.sqlite", self.directory/"unused-exports"),
                            (self.directory/"unused.sqlite", ROOT/"artifacts/scenarios"),
                            (self.directory/"unused.sqlite", ROOT/".run/document-transfer/scenarios")):
                with self.subTest(db=str(db), out=str(out)), self.assertRaises(ValueError):
                    runner.replay(ROOT, db, out)

    def test_database_cannot_be_inside_export_directory(self):
        with self.assertRaisesRegex(ValueError, "conflict"):
            runner.replay(ROOT, self.directory/"nested/replay.sqlite", self.directory/"nested")

    def test_unknown_reference_is_rejected_instead_of_inventing_an_amount(self):
        task = {"bundle": self.export(0)["snapshot"]["content"]["bundle"]}
        step = deepcopy(runner.load_scenarios(ROOT)["scenarios"][0]["steps"][0])
        step["hypotheses"][0]["evidence_refs"] = ["AAPL.fake_income.2025"]
        with self.assertRaisesRegex(ValueError, "outside registered facts"):
            runner._update_arguments(task, step)

    def test_cli_material_unavailable_returns_structured_refusal_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            db, out = Path(directory)/"failed.sqlite", Path(directory)/"exports"
            capture = io.StringIO()
            with patch.object(runner.ResearchTaskStore, "create", side_effect=HTTPException(409, "Registered materials changed")), contextlib.redirect_stdout(capture):
                self.assertEqual(runner.main(["--database", str(db), "--output-dir", str(out)]), 1)
            result = json.loads(capture.getvalue())
            self.assertEqual((result["status"], result["http_status"]), ("refused", 409))
            self.assertTrue(db.exists())
            self.assertFalse((out/"manifest.json").exists())
            with self.assertRaisesRegex(ValueError, "must both be new"):
                runner.replay(ROOT, db, out)


if __name__ == "__main__":
    unittest.main()
