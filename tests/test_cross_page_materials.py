from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens import cross_page_materials as module
from creditlens.diligence import CasePack, digest
from creditlens.research_materials import build_material_bundle
from scripts import evaluate_cross_page_evidence as replay


ROOT = Path(__file__).resolve().parents[1]


class CrossPageMaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for entry in module._SOURCE_EXPECTATIONS.values():
            path = self.root/entry["pack_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT/entry["pack_path"]).read_bytes())

    def test_four_tasks_twelve_roles_eight_amounts_and_two_shared_scope_pages(self):
        catalog = module.build_cross_page_materials(self.root)
        self.assertEqual((len(catalog["tasks"]), len(catalog["cards"])), (4, 12))
        cards = {c["id"]: c for c in catalog["cards"]}
        self.assertEqual(len(cards), 12)
        self.assertEqual(sum(c["role"] != "consolidation_scope" for c in cards.values()), 8)
        scope_locations = {(c["report_sha256"], c["location"]["page"]) for c in cards.values() if c["role"] == "consolidation_scope"}
        self.assertEqual(len(scope_locations), 2)
        for task in catalog["tasks"]:
            self.assertEqual(set(task["required_role_ids"]), {"cfo", "revenue", "consolidation_scope"})
            for role, card_id in task["required_role_ids"].items():
                card = cards[card_id]
                self.assertEqual(card["role"], role)
                for key in ("issuer_id", "report_sha256", "year", "period", "financial_scope"):
                    self.assertEqual(card[key], task[key])
                self.assertEqual(card["id"], f"{task['issuer_id']}:{task['report_sha256']}:{role}-{task['year']}")

    def test_fixed_real_amounts_and_fiscal_periods(self):
        catalog = module.build_cross_page_materials(self.root)
        values = {(c["issuer_id"], c["year"], c["role"]): c["normalized_value"] for c in catalog["cards"] if c["role"] != "consolidation_scope"}
        self.assertEqual(values, {
            ("MSFT", 2024, "cfo"): "118548", ("MSFT", 2025, "cfo"): "136162",
            ("MSFT", 2024, "revenue"): "245122", ("MSFT", 2025, "revenue"): "281724",
            ("AAPL", 2024, "cfo"): "118254", ("AAPL", 2025, "cfo"): "111482",
            ("AAPL", 2024, "revenue"): "391035", ("AAPL", 2025, "revenue"): "416161"})
        periods = {t["id"]: t["period"] for t in catalog["tasks"]}
        self.assertEqual(periods["aapl-cfo-revenue-2025"], {"start": "2024-09-29", "end": "2025-09-27"})
        self.assertEqual(periods["msft-cfo-revenue-2025"], {"start": "2024-07-01", "end": "2025-06-30"})

    def test_scope_has_no_fabricated_boxes_or_human_verification(self):
        catalog = module.build_cross_page_materials(self.root)
        for card in catalog["cards"]:
            self.assertFalse(card["provenance"]["independent_human_verified"])
            self.assertFalse(card["provenance"]["model_output_verified"])
            self.assertFalse(card["availability"]["historical_verified"])
            if card["role"] == "consolidation_scope":
                for key in ("amount_bbox", "year_bbox", "unit_bbox", "page_width", "page_height", "printed_page"):
                    self.assertIsNone(card["location"][key])
                self.assertIn("pending", card["location"]["locator"])
                self.assertEqual(card["value_status"], "text_evidence")
                self.assertIsNone(card["unit"])
                self.assertIsNone(card["raw_value"])

    def test_digest_is_deterministic_and_covers_materials(self):
        catalog = module.build_cross_page_materials(self.root)
        self.assertEqual(catalog, module.build_cross_page_materials(self.root))
        claimed = catalog.pop("material_sha256")
        self.assertEqual(claimed, digest(catalog))
        catalog["cards"][0]["location"]["amount_bbox"]["x0"] += 1
        self.assertNotEqual(claimed, digest(catalog))

    def test_changed_pack_or_missing_file_fail_closed(self):
        path = self.root/module._SOURCE_EXPECTATIONS["MSFT"]["pack_path"]
        original = path.read_bytes()
        data = json.loads(original)
        data["facts"][0]["value"] = "1"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(module.CrossPageMaterialsError):
            module.build_cross_page_materials(self.root)
        path.write_bytes(original)
        path.unlink()
        with self.assertRaises(module.CrossPageMaterialsError):
            module.build_cross_page_materials(self.root)

    def test_duplicate_json_key_is_not_silently_accepted(self):
        path = self.root/module._SOURCE_EXPECTATIONS["MSFT"]["pack_path"]
        data = json.loads(path.read_text(encoding="utf-8"))
        # A duplicate equal key survives an ordinary JSON loader without changing
        # semantic digest; the new boundary must still reject it.
        path.write_text('{"schema_version":1,' + json.dumps(data)[1:], encoding="utf-8")
        with self.assertRaises(module.CrossPageMaterialsError) as caught:
            module.build_cross_page_materials(self.root)
        self.assertEqual(caught.exception.code, "source_pack_unavailable")

    def test_mismatched_scope_anchor_rejected_even_if_source_pack_unchanged(self):
        bundle = build_material_bundle(self.root)
        bundle["observations"][0]["basis"]["scope_evidence"][0]["page"] += 1
        with patch.object(module, "build_material_bundle", return_value=bundle):
            with self.assertRaises(module.CrossPageMaterialsError) as caught:
                module.build_cross_page_materials(self.root)
        self.assertEqual(caught.exception.code, "scope_registry_changed")

    def test_bundle_change_between_reads_is_rejected(self):
        first = build_material_bundle(self.root)
        changed = deepcopy(first)
        changed["limitations"].append("synthetic read race")
        with patch.object(module, "build_material_bundle", side_effect=[first, changed]):
            with self.assertRaises(module.CrossPageMaterialsError) as caught:
                module.build_cross_page_materials(self.root)
        self.assertEqual(caught.exception.code, "source_changed_during_read")

    def test_period_and_unit_guards_not_just_hash_checks(self):
        path = self.root/module._SOURCE_EXPECTATIONS["MSFT"]["pack_path"]
        real_read = module._read_pack
        for field, value in (("unit", "CNY_million"), ("period_start", "2023-07-02")):
            with self.subTest(field=field):
                data = json.loads(path.read_text(encoding="utf-8"))
                next(f for f in data["facts"] if f["id"] == "revenue-2024")[field] = value
                altered = CasePack.model_validate(data)
                def fixture_read(root, source, expected):
                    return altered if source["issuer_id"] == "MSFT" else real_read(root, source, expected)
                with patch.object(module, "_read_pack", side_effect=fixture_read):
                    with self.assertRaises(module.CrossPageMaterialsError) as caught:
                        module.build_cross_page_materials(self.root)
                self.assertEqual(caught.exception.code, "fact_basis_mismatch")

    def test_replay_keeps_all_fixed_controls_and_independent_expected_values(self):
        report = replay.build_report(ROOT)
        self.assertEqual(report["summary"], {"cases": 22, "passed": 22, "failed": 0,
                         "registered_disclosure_cases": 4, "synthetic_controls": 18, "computed": 5, "refused": 17})
        self.assertEqual(report["scope"]["independent_human_gold"], 0)
        self.assertEqual(report["scope"]["model_calls"], 0)
        self.assertFalse(report["scope"]["model_accuracy_measured"])
        actual = {c["task_id"]: c["result"]["calculation"]["result"]["value"] for c in report["cases"] if c["operation"] == "complete"}
        self.assertEqual(actual, replay.FIXED_PERCENTAGES)

    def test_replay_detects_result_mutation_even_when_summary_unchanged(self):
        report = replay.build_report(ROOT)
        report["cases"][0]["result"]["calculation"]["result"]["value"] = "48.000000"
        with self.assertRaises(ValueError):
            replay.validate_report(ROOT, report)

    def test_replay_detects_scope_and_source_hash_mutation(self):
        report = replay.build_report(ROOT)
        for kind in ("scope", "source"):
            with self.subTest(kind=kind):
                changed = deepcopy(report)
                if kind == "scope":
                    changed["scope"]["independent_human_gold"] = 4
                else:
                    changed["source_bindings"][0]["report_sha256"] = "0" * 64
                with self.assertRaises(ValueError):
                    replay.validate_report(ROOT, changed)

    def test_synthetic_mutations_never_modify_registered_catalog(self):
        catalog = module.build_cross_page_materials(ROOT)
        original = deepcopy(catalog)
        for case in replay.cases():
            replay._prepare(catalog, case)
        self.assertEqual(catalog, original)

    def test_cli_creates_new_run_json_and_refuses_overwrite_or_public_path(self):
        fake_report = {"summary": {"cases": 22}}
        with patch.object(replay, "ROOT", self.root), patch.object(replay, "build_report", return_value=fake_report), \
             patch.object(replay, "validate_report", return_value=fake_report):
            replay.run(Path("new.json"))
            target = self.root/".run/reproductions/new.json"
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), fake_report)
            with self.assertRaises(ValueError):
                replay.run(Path("new.json"))
            with self.assertRaises(ValueError):
                replay.run(self.root/"artifacts/wrong.json")
            self.assertFalse((self.root/"artifacts/wrong.json").exists())


if __name__ == "__main__":
    unittest.main()
