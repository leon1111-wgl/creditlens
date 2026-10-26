"""Public evidence integrity and explicit row choices, without local OCR fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import row_disambiguation_experiment as module
from creditlens.diligence import digest
from creditlens.visual import OcrPage
from scripts import evaluate_row_disambiguation as runner

ROOT = Path(__file__).resolve().parents[1]


class RowDisambiguationExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        files = {module.CONFIG, module.REPORT, module.RELEASE,
            module.prior.CONFIG, module.prior.REPORT, module.prior.RELEASE,
            module.prior.old.CONFIG, module.prior.old.REPORT, module.prior.old.RELEASE,
            "artifacts/document-ai-word-report.json", "configs/table-lookup.json", "configs/table-lookup-artifact.json",
            "artifacts/table-lookup-report.json", "artifacts/document-ai-report.json", "configs/document-ai.json",
            "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json", *module.IMPLEMENTATION_FILES}
        for name in files:
            target = self.root/name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, target)
        self.report = json.loads((self.root/module.REPORT).read_text(encoding="utf8"))

    def reject(self, value):
        with self.assertRaises(module.RowDisambiguationExperimentError):
            module.validate_row_disambiguation_report(self.root, value)

    def refresh_paths(self, query):
        """Keep all public copies equal so mutations reach inner relationship checks."""
        d = query["discovery"]
        query["unselected_result"] = {**deepcopy(d), "selection": None, "extraction": None}
        query["paths"] = [{"row_index": c["row_index"], "result": {**deepcopy(d), "status": c["extraction"]["status"],
            "selection": {"input_sha256": d["input_binding"]["input_sha256"], "row_index": c["row_index"], "explicit": True},
            "extraction": deepcopy(c["extraction"])}} for c in d["candidates"]]

    def test_all_old_duplicate_queries_and_every_row_are_retained(self):
        snapshot = module.load_row_disambiguation_snapshot(self.root)
        summary = snapshot["report"]["summary"]
        self.assertEqual(summary, {"prior_registered_records": 42, "prior_unique_queries": 40, "covered_baseline_records": 6,
            "unique_queries": 4, "candidate_paths": 8, "selected_paths": 8, "insufficient_paths": 0,
            "unselected_queries": 4, "financial_reference_scoring": False})
        self.assertEqual([len(q["baseline_case_ids"]) for q in self.report["queries"]], [1, 1, 2, 2])
        for q in self.report["queries"]:
            self.assertEqual(q["discovery"]["legacy_line_result"]["reason"], "duplicate_row_label")
            self.assertEqual([c["row_index"] for c in q["discovery"]["candidates"]], [p["row_index"] for p in q["paths"]])

    def test_unselected_has_no_value_and_chosen_path_keeps_both_rows(self):
        for q in self.report["queries"]:
            self.assertIsNone(q["unselected_result"]["extraction"])
            self.assertIsNone(q["unselected_result"]["selection"])
            self.assertEqual(q["unselected_result"]["status"], "selection_required")
            for p in q["paths"]:
                self.assertEqual(p["result"]["candidates"], q["discovery"]["candidates"])
                self.assertTrue(p["result"]["selection"]["explicit"])
                self.assertFalse(p["result"]["automatic_approval"])
                self.assertNotIn("score", p["result"])
        self.assertFalse(self.report["scope"]["gold_scoring_performed"])
        self.assertEqual(self.report["scope"]["independent_human_gold"], 0)

    def test_saved_validation_never_reads_complete_ocr(self):
        with patch.object(module.prior.old, "_page", side_effect=AssertionError("No raw input read")):
            snapshot = module.load_row_disambiguation_snapshot(self.root)
            self.assertFalse(snapshot["full_ocr_replayed"])
            with patch.object(runner, "ROOT", self.root): runner.main(["--verify-saved"])
        self.assertFalse((self.root/".run").exists())

    def test_all_six_input_versions_are_registered_but_only_one_page_queried(self):
        pages = [p for d in self.report["input_inventory"] for p in d["pages"]]
        self.assertEqual(len(pages), 6)
        self.assertEqual({(q["document_id"], q["page"]) for q in self.report["queries"]}, {("aapl", 34)})
        for q in self.report["queries"]:
            self.assertEqual(q["discovery"]["input_binding"]["page_sha256"], q["page_metadata"]["line_projection_sha256"])

    def test_reference_and_fact_metadata_cannot_enter_extractor(self):
        control = module._controls()[0]; page = OcrPage.model_validate(control["input_page"])
        for key in ("expected_amount", "amount_bbox", "fact_id", "section"):
            with self.assertRaises(module.RowDisambiguationExperimentError):
                module.extract_paths(page, {**control["query"], key: "forbidden"})

    def test_query_discovery_never_uses_financial_reference(self):
        previous = module.prior.load_role_table_lookup_snapshot(self.root)["report"]
        before = module._query_specs(previous)
        for doc in previous["documents"]:
            for case in doc["cases"]:
                case["reference"] = {"amount": "999999", "section": "invented"}
                case["fact_id"] = "ignored-fact"
        self.assertEqual(before, module._query_specs(previous))

    def test_selection_types_are_not_python_truthiness(self):
        mutations = [
            lambda q: q["paths"][0]["result"]["selection"].__setitem__("explicit", 1),
            lambda q: q["paths"][0]["result"]["selection"].__setitem__("row_index", 13.0),
            lambda q: q["unselected_result"].__setitem__("automatic_approval", 0),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                data = deepcopy(self.report); mutation(data["queries"][0]); self.reject(data)

    def test_no_candidate_query_or_path_can_be_silently_omitted(self):
        for kind in ("query", "candidate", "path", "duplicate_query"):
            data = deepcopy(self.report); q = data["queries"][0]
            if kind == "query": data["queries"].pop()
            elif kind == "candidate":
                q["discovery"]["candidates"].pop(); q["discovery"]["candidate_count"] -= 1; self.refresh_paths(q)
            elif kind == "path": q["paths"].pop()
            else: data["queries"][-1] = deepcopy(data["queries"][0])
            with self.subTest(kind=kind): self.reject(data)

    def test_wrong_selected_row_or_stale_binding_is_rejected(self):
        for field, value in (("row_index", 32), ("input_sha256", "0"*64)):
            data = deepcopy(self.report); data["queries"][0]["paths"][0]["result"]["selection"][field] = value
            self.reject(data)

    def test_locally_inconsistent_raw_amount_is_rejected_even_when_copies_match(self):
        data = deepcopy(self.report); q = data["queries"][0]
        q["discovery"]["candidates"][0]["extraction"]["raw_value"] = "123"
        q["discovery"]["candidates"][0]["extraction"]["normalized_value"] = "123"
        self.refresh_paths(q); self.reject(data)

    def test_visible_context_relationships_and_order_are_checked(self):
        for kind in ("side", "order", "missing"):
            data = deepcopy(self.report); q = data["queries"][0]; context = q["discovery"]["candidates"][0]["context"]
            if kind == "side": context["above"][0] = deepcopy(context["below"][0])
            elif kind == "order": context["above"].reverse()
            else: context["above"].pop()
            self.refresh_paths(q)
            with self.subTest(kind=kind): self.reject(data)

    def test_scope_source_and_summary_claims_cannot_be_upgraded(self):
        for kind in ("source", "summary", "scope", "extra"):
            data = deepcopy(self.report)
            if kind == "source": data["queries"][0]["source"]["sha256"] = "0"*64
            elif kind == "summary": data["summary"]["financial_reference_scoring"] = True
            elif kind == "scope": data["scope"]["independent_human_gold"] = 8
            else: data["queries"][0]["human_verified"] = True
            with self.subTest(kind=kind): self.reject(data)

    def test_synthetic_controls_include_every_expected_refusal_and_positive(self):
        controls = self.report["controls"]
        self.assertEqual(controls["summary"], {"cases": 15, "passed": 15, "failed": 0})
        self.assertEqual(controls, module._run_controls())
        self.assertEqual(len({c["id"] for c in controls["cases"]}), 15)
        self.assertEqual(next(c for c in controls["cases"] if c["id"] == "RD-12")["observed"]["normalized_value"], "-100")
        data = deepcopy(self.report); data["controls"]["cases"][0]["expected"]["status"] = "selected"; self.reject(data)

    def test_failed_control_cannot_be_published_as_verified(self):
        bad = deepcopy(self.report["controls"]); bad["cases"][0]["passed"] = False; bad["summary"] = {"cases": 15, "passed": 14, "failed": 1}
        with patch.object(module, "_run_controls", return_value=bad): self.reject(self.report)

    def test_live_registration_or_implementation_drift_is_rejected(self):
        for name in (module.CONFIG, "data/examples/aapl-fy2025-pdf.json", "creditlens/row_disambiguation.py"):
            path = self.root/name; saved = path.read_bytes()
            if name.startswith("data/examples/"):
                changed = json.loads(saved); changed["facts"][0]["raw_value"] = "999999"
                path.write_text(json.dumps(changed), encoding="utf8")
            else:
                path.write_bytes(saved+b"\n")
            with self.subTest(path=name): self.reject(self.report)
            path.write_bytes(saved)

    def test_saved_report_raw_digest_is_required(self):
        path = self.root/module.REPORT; path.write_bytes(path.read_bytes()+b"\n")
        with self.assertRaises(module.RowDisambiguationExperimentError): module.load_row_disambiguation_snapshot(self.root)

    def test_full_replay_requires_existing_registered_inputs(self):
        with self.assertRaises(ValueError): module.build_row_disambiguation_report(self.root, self.root/"absent")
        self.assertFalse((self.root/"absent").exists())

    def test_new_output_only_and_verify_mode_has_no_write(self):
        for target in (self.root/"artifacts/new.json", self.root/".run/document-ai/a.json", self.root/".run/reproductions/nested/a.json", "bad.txt"):
            with self.subTest(target=target), self.assertRaises(ValueError): runner.output_path(self.root, target)
        output = runner.output_path(self.root, "new.json")
        with patch.object(runner, "build_row_disambiguation_report", return_value=self.report):
            runner.run(output=output, root=self.root)
        self.assertEqual(json.loads(output.read_text(encoding="utf8")), self.report)
        with self.assertRaises(ValueError): runner.run(output=output, root=self.root)
        self.assertEqual(digest(json.loads(output.read_text(encoding="utf8"))), digest(self.report))


if __name__ == "__main__": unittest.main()
