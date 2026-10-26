"""Separate same-input extraction changes from financial reference scoring."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import role_table_experiment as module
from creditlens.diligence import digest
from creditlens.word_evidence import WordPage
from scripts import evaluate_role_table_lookup as runner

ROOT = Path(__file__).resolve().parents[1]


class RoleTableExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        files = {module.CONFIG, module.REPORT, module.RELEASE, module.old.CONFIG, module.old.REPORT, module.old.RELEASE,
                 "artifacts/document-ai-word-report.json", "configs/table-lookup.json", "configs/table-lookup-artifact.json",
                 "artifacts/table-lookup-report.json", "artifacts/document-ai-report.json", "configs/document-ai.json",
                 "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json", *module.IMPLEMENTATION_FILES}
        for name in files:
            target = self.root/name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, target)
        self.report = json.loads((self.root/module.REPORT).read_text(encoding="utf-8"))

    def cases(self, report=None):
        return [c for d in (report or self.report)["documents"] for c in d["cases"]]

    def rehash(self, case):
        case["extraction_sha256"] = digest({**case["extraction_metadata"], "methods": {k: case["methods"][k]["extraction"] for k in module.METHODS}})

    def test_complete_42_records_and_40_queries_share_outputs(self):
        report = module.load_role_table_lookup_snapshot(self.root)["report"]
        self.assertEqual(report["query_inventory"]["registered_cases"], 42)
        self.assertEqual(report["query_inventory"]["unique_query_inputs"], 40)
        self.assertEqual(len(report["query_inventory"]["duplicated_query_groups"]), 2)
        cases = {c["id"]: c for c in self.cases(report)}
        for group in report["query_inventory"]["duplicated_query_groups"]:
            self.assertEqual(len({cases[key]["extraction_sha256"] for key in group["case_ids"]}), 1)

    def test_old_methods_remain_identical_for_every_record(self):
        old = module.old.load_word_table_lookup_snapshot(self.root)["report"]
        previous = {c["id"]: c for d in old["documents"] for c in d["cases"]}
        for case in self.cases():
            for method in ("line", "word"):
                self.assertEqual(case["methods"][method], previous[case["id"]]["methods"][method])
            self.assertTrue(case["legacy_extractions_match_previous"])
        self.assertEqual(self.report["summary"]["line"]["matched"], 26)
        self.assertEqual(self.report["summary"]["word"]["matched"], 22)

    def test_six_complete_word_inputs_and_prior_projection_are_bound(self):
        comparison = self.report["input_comparison"]
        self.assertTrue(comparison["all_word_inputs_match_previous"])
        self.assertTrue(comparison["all_legacy_extractions_match_previous"])
        self.assertTrue(comparison["previous_line_comparison"]["all_line_projections_match_previous"])
        pages = [p for d in self.report["documents"] for p in d["pages"]]
        self.assertEqual(len(pages), 6)
        self.assertEqual(sum(p["line_count"] for p in pages), 725)
        self.assertEqual(sum(p["word_count"] for p in pages), 1381)

    def test_both_full_transition_matrices_keep_all_42_records(self):
        for source in ("line", "word"):
            matrix = self.report["transitions"][source+"_to_role"]
            self.assertEqual(set(matrix), set(module.STATES))
            self.assertEqual(sum(v for row in matrix.values() for v in row.values()), 42)
            for before in module.STATES:
                for after in module.STATES:
                    self.assertEqual(matrix[before][after], sum(c["methods"][source]["score"]["outcome"] == before
                        and c["methods"]["role"]["score"]["outcome"] == after for c in self.cases()))

    def test_saved_validation_never_reads_complete_ocr(self):
        with patch.object(module.old, "_page", side_effect=AssertionError("No full OCR read")):
            snapshot = module.load_role_table_lookup_snapshot(self.root)
            self.assertFalse(snapshot["full_ocr_replayed"])
            with patch.object(runner, "ROOT", self.root): runner.main(["--verify-saved"])
        self.assertFalse((self.root/".run").exists())

    def test_reference_amount_and_box_only_change_post_extraction_scoring(self):
        spec = module._controls()[0]; page = WordPage.model_validate(spec["input_page"])
        output = module.extract(page, spec["query"])
        former = {"id": "same", "fact_id": "first", "page": 1, "query": spec["query"], "reference": spec["reference"]}
        first = module._case(former, output)
        altered = deepcopy(former); altered["fact_id"] = "different"; altered["reference"]["signed_amount"] = "999"
        altered["reference"]["amount_bbox"] = {"x0": 10., "top": 10., "x1": 20., "bottom": 20.}
        second = module._case(altered, output)
        self.assertEqual(first["extraction_sha256"], second["extraction_sha256"])
        self.assertNotEqual(first["methods"]["role"]["score"], second["methods"]["role"]["score"])
        for field in ("expected_amount", "amount_bbox", "fact_id", "section"):
            with self.assertRaises(module.RoleTableExperimentError): module.extract(page, {**spec["query"], field: "forbidden"})

    def test_synthetic_denominator_and_guard_expected_results_are_fixed(self):
        controls = self.report["controls"]
        self.assertEqual(controls["summary"], {"cases": 15, "passed": 15, "failed": 0})
        self.assertEqual(len({c["id"] for c in controls["cases"]}), 15)
        for case in controls["cases"]:
            self.assertEqual(case["provenance"], "synthetic_development_control")
            self.assertEqual(case["methods"]["role"]["score"]["outcome"], case["expected_role_outcome"])

    def test_anchor_issues_remain_visible_and_amount_issues_block(self):
        cases = {c["id"]: c for c in self.report["controls"]["cases"]}
        for identifier in ("anchor_low_native", "anchor_native_outside"):
            role = cases[identifier]["methods"]["role"]["extraction"]
            self.assertEqual(role["status"], "selected")
            self.assertTrue(role["nonblocking_anchor_issues"])
            self.assertFalse(role["guard_issues"])
        for identifier in ("amount_low_native", "amount_outside_parent", "missing_amount_words", "dropped_parentheses"):
            self.assertEqual(cases[identifier]["methods"]["role"]["extraction"]["status"], "insufficient")

    def test_accounting_prefix_keeps_signed_amount_and_both_boxes(self):
        case = next(c for c in self.report["controls"]["cases"] if c["id"] == "leading_negative")
        role = case["methods"]["role"]["extraction"]
        self.assertEqual(role["normalized_value"], "-200")
        self.assertEqual(role["selected_span_origin"], "derived_accounting_cell")
        association = next(a for a in role["prefix_associations"] if a["span_index"] == role["selected_span_index"])
        self.assertLess(association["display_bbox"]["x0"], association["numeric_bbox"]["x0"])
        self.assertIn("$", association["raw_value"])
        self.assertIn("(", association["raw_value"])

    def test_counts_unchanged_score_or_scope_mutation_is_rejected(self):
        mutations = [lambda r: r["scope"].update(independent_human_gold=42),
                     lambda r: self.cases(r)[0]["methods"]["role"]["score"].update(outcome="wrong"),
                     lambda r: r["transitions"]["word_to_role"]["matched"].update(wrong=4)]
        for mutation in mutations:
            report = deepcopy(self.report); mutation(report)
            with self.assertRaises(module.RoleTableExperimentError): module.validate_role_table_lookup_report(self.root, report)

    def test_legacy_method_mutation_cannot_be_accepted_as_new_baseline(self):
        report = deepcopy(self.report); case = self.cases(report)[0]
        case["methods"]["line"]["extraction"]["unit"]["currency"] = "USD"; self.rehash(case)
        with self.assertRaises(ValueError): module.validate_role_table_lookup_report(self.root, report)

    def test_duplicate_or_missing_case_cannot_keep_same_denominator(self):
        report = deepcopy(self.report); report["documents"][0]["cases"][1] = deepcopy(report["documents"][0]["cases"][0])
        with self.assertRaises(module.RoleTableExperimentError): module.validate_role_table_lookup_report(self.root, report)

    def test_release_hash_and_source_configuration_changes_rejected(self):
        path = self.root/module.REPORT; path.write_bytes(path.read_bytes()+b"\n")
        with self.assertRaisesRegex(module.RoleTableExperimentError, "published_report_changed"):
            module.load_role_table_lookup_snapshot(self.root)
        path = self.root/module.CONFIG; data = json.loads(path.read_text(encoding="utf-8")); data["documents"][0]["pages"][0]["word_sha256"] = "0"*64
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(module.RoleTableExperimentError, "input_inventory_changed"):
            module.validate_role_table_lookup_report(self.root, self.report)

    def test_implementation_version_changes_rejected(self):
        path = self.root/"creditlens/role_table_lookup.py"; path.write_bytes(path.read_bytes()+b"\n# changed\n")
        with self.assertRaisesRegex(module.RoleTableExperimentError, "report_mismatch"):
            module.validate_role_table_lookup_report(self.root, self.report)

    def test_missing_full_inputs_fail_without_download_or_output(self):
        with self.assertRaises(ValueError): runner.run(self.root/"missing", "missing-input.json", root=self.root)
        self.assertFalse((self.root/".run/reproductions/missing-input.json").exists())

    def test_new_output_only_and_no_public_or_preview_paths(self):
        for path in (self.root/module.REPORT, self.root/".run/document-ai/a.json", "../escape.json", "nested/report.json", "report.txt"):
            with self.assertRaises(ValueError): runner.output_path(self.root, path)
        path = runner.output_path(self.root, "new.json"); path.parent.mkdir(parents=True); path.write_text("existing", encoding="utf-8")
        with self.assertRaises(ValueError): runner.output_path(self.root, path)

    def test_local_prefix_boxes_barrier_or_refs_cannot_be_self_reported(self):
        spec = next(c for c in module._controls() if c["id"] == "leading_dollars")
        page = WordPage.model_validate(spec["input_page"])
        registration = {"page": page.page, "width": page.width, "height": page.height, "line_count": len(page.lines),
                        "line_projection_sha256": digest(page.line_page().model_dump(mode="json"))}
        original = module.extract(page, spec["query"])
        mutations = [lambda r: r["prefix_associations"][0]["numeric_bbox"].update(x0=245),
                     lambda r: r["prefix_associations"][0]["display_bbox"].update(x0=230),
                     lambda r: r["prefix_associations"][0].update(left_barrier=0),
                     lambda r: r["prefix_associations"][1].update(prefix_ref=deepcopy(r["prefix_associations"][0]["prefix_ref"]))]
        for mutation in mutations:
            output = deepcopy(original); mutation(output["methods"]["role"])
            with self.assertRaises(module.RoleTableExperimentError): module._validate_output(output, spec["query"], registration, {})

    def test_nonblocking_anchor_warning_cannot_be_hidden(self):
        spec = next(c for c in module._controls() if c["id"] == "anchor_low_native")
        page = WordPage.model_validate(spec["input_page"]); output = module.extract(page, spec["query"])
        output["methods"]["role"]["nonblocking_anchor_issues"] = []
        registration = {"page": page.page, "width": page.width, "height": page.height, "line_count": len(page.lines),
                        "line_projection_sha256": digest(page.line_page().model_dump(mode="json"))}
        with self.assertRaisesRegex(module.RoleTableExperimentError, "anchor_issue_mismatch"):
            module._validate_output(output, spec["query"], registration, {})

    def test_guard_word_references_cannot_point_to_another_parent(self):
        spec = next(c for c in module._controls() if c["id"] == "amount_low_native")
        page = WordPage.model_validate(spec["input_page"]); output = module.extract(page, spec["query"])
        output["methods"]["role"]["guard_issues"][0]["word_refs"] = [{"line_index": 0, "word_index": 0}]
        registration = {"page": page.page, "width": page.width, "height": page.height, "line_count": len(page.lines),
                        "line_projection_sha256": digest(page.line_page().model_dump(mode="json"))}
        with self.assertRaisesRegex(module.RoleTableExperimentError, "invalid_word_ref"):
            module._validate_output(output, spec["query"], registration, {})

    def test_ordinary_group_cannot_drop_symbol_and_keep_matching_amount(self):
        spec = deepcopy(module._controls()[0]); data = spec["input_page"]
        amount = data["lines"][5]; number = deepcopy(amount["words"][0])
        prefix = deepcopy(number); prefix.update(text="$"); prefix["bbox"].update(x0=344, x1=348)
        amount["line"]["text"] = "$200"; amount["line"]["bbox"]["x0"] = 344
        amount["words"] = [prefix, number]
        page = WordPage.model_validate(data); output = module.extract(page, spec["query"])
        role = output["methods"]["role"]; index = role["selected_span_index"]
        self.assertEqual(role["selected_span_origin"], "derived_word_group")
        self.assertEqual(role["raw_value"], "$200")
        mapping = next(entry for entry in role["adaptation"]["source_map"] if entry["span_index"] == index)
        mapping["word_refs"] = [{"line_index": 5, "word_index": 1}]
        role["raw_value"] = "200"
        for entries in role["evidence"].values():
            for entry in entries:
                if entry["span_index"] == index: entry["span"] = deepcopy(number)
        registration = {"page": page.page, "width": page.width, "height": page.height, "line_count": len(page.lines),
                        "line_projection_sha256": digest(page.line_page().model_dump(mode="json"))}
        with self.assertRaisesRegex(module.RoleTableExperimentError, "unassigned_amount_words"):
            module._validate_output(output, spec["query"], registration, {})


if __name__ == "__main__": unittest.main()
