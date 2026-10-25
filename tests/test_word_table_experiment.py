from copy import deepcopy
from decimal import localcontext
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import word_table_experiment as module
from creditlens.diligence import digest
from creditlens.word_evidence import WordPage
from scripts import evaluate_word_table_lookup as runner

ROOT=Path(__file__).resolve().parents[1]


class WordTableExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        files={module.CONFIG,module.REPORT,module.RELEASE,"artifacts/document-ai-word-report.json",
               "configs/table-lookup.json","configs/table-lookup-artifact.json","artifacts/table-lookup-report.json",
               "artifacts/document-ai-report.json","configs/document-ai.json",
               "data/examples/msft-fy2025-pdf.json","data/examples/aapl-fy2025-pdf.json",*module.IMPLEMENTATION_FILES}
        for relative in files:
            target=self.root/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,target)
        self.report=json.loads((self.root/module.REPORT).read_text(encoding="utf-8"))

    def test_42_records_40_queries_and_duplicate_query_outputs_equal(self):
        report=module.load_word_table_lookup_snapshot(self.root)["report"]
        self.assertEqual(report["query_inventory"]["registered_cases"],42)
        self.assertEqual(report["query_inventory"]["unique_query_inputs"],40)
        self.assertEqual(len(report["query_inventory"]["duplicated_query_groups"]),2)
        cases={c["id"]:c for d in report["documents"] for c in d["cases"]}
        for group in report["query_inventory"]["duplicated_query_groups"]:
            self.assertEqual(len({cases[i]["extraction_sha256"] for i in group["case_ids"]}),1)

    def test_all_six_parent_projections_equal_previous_registered_inputs(self):
        comparison=self.report["input_comparison"]
        self.assertEqual(len(comparison["pages"]),6)
        self.assertTrue(comparison["all_line_projections_match_previous"])
        self.assertEqual(sum(p["line_count"] for p in comparison["pages"]),725)
        self.assertEqual(sum(p["word_count"] for p in comparison["pages"]),1381)
        for page in comparison["pages"]:
            self.assertEqual(page["line_projection_sha256"],page["previous_line_sha256"])
        self.assertTrue(all(c["line_extraction_matches_previous"] for d in self.report["documents"] for c in d["cases"]))

    def test_three_by_three_transition_matrix_keeps_all_cases(self):
        matrix=self.report["transitions"]
        self.assertEqual(set(matrix),set(module.STATES))
        self.assertTrue(all(set(row)==set(module.STATES) for row in matrix.values()))
        self.assertEqual(sum(n for row in matrix.values() for n in row.values()),42)
        for method in module.METHODS:
            total=self.report["summary"][method]
            self.assertEqual(total["cases"],42)
            self.assertEqual(sum(total[s] for s in module.STATES),42)
        self.assertEqual({s:self.report["summary"]["line"][s] for s in module.STATES},
                         {"matched":26,"wrong":0,"insufficient":16})

    def test_saved_snapshot_and_cli_do_not_read_full_ocr_or_extract(self):
        with patch.object(module,"_page",side_effect=AssertionError("No OCR reads")), \
             patch.object(module,"extract",side_effect=AssertionError("No extraction")):
            self.assertFalse(module.load_word_table_lookup_snapshot(self.root)["full_ocr_replayed"])
            with patch.object(runner,"ROOT",self.root):runner.main(["--verify-saved"])
        self.assertFalse((self.root/".run").exists())

    def test_reference_amount_and_box_do_not_change_extractor(self):
        case=module._controls()[0];page=WordPage.model_validate(case["page"])
        before=module.extract(page,case["query"])
        changed=deepcopy(case["reference"]);changed["signed_amount"]="999"
        changed["amount_bbox"]["x0"]-=100;changed["amount_bbox"]["x1"]-=100
        after=module.extract(page,case["query"])
        self.assertEqual(digest(before),digest(after))
        self.assertEqual(module.score_extraction(before["methods"]["word"],case["reference"])["outcome"],"matched")
        self.assertEqual(module.score_extraction(after["methods"]["word"],changed)["outcome"],"wrong")

    def test_query_cannot_accept_amount_box_or_section(self):
        case=module._controls()[0]
        for key in ("amount","bbox","section"):
            with self.subTest(key=key),self.assertRaises(module.WordTableExperimentError):
                module.extract(WordPage.model_validate(case["page"]),{**case["query"],key:"not permitted"})

    def test_seven_synthetic_controls_separate_and_replayable(self):
        controls=self.report["controls"]
        self.assertEqual(controls["summary"],{"cases":7,"passed":7,"failed":0})
        for case in controls["cases"]:
            self.assertEqual(case["provenance"],"synthetic_developer_control")
            actual=module.extract(WordPage.model_validate(case["input_page"]),case["query"])
            self.assertEqual(digest(actual),case["extraction_sha256"])

    def _example_output(self):
        case=module._controls()[0];page=case["page"]
        result=module.extract(WordPage.model_validate(page),case["query"])
        registration={**page,"line_count":len(page["lines"]),
                      "line_projection_sha256":digest(WordPage.model_validate(page).line_page().model_dump(mode="json"))}
        return case,result,registration

    def test_parent_flags_are_recomputed_from_public_full_parent_words(self):
        case,result,registration=self._example_output()
        parent=result["methods"]["word"]["parent_evidence"][0]
        parent["text_conserved"]=not parent["text_conserved"]
        with self.assertRaises(module.WordTableExperimentError) as caught:
            module._validate_output(result,case["query"],registration,{})
        self.assertEqual(caught.exception.code,"parent_flag_mismatch")

    def test_derived_span_score_and_box_are_recomputed_from_original_words(self):
        for field in ("confidence","bbox"):
            with self.subTest(field=field):
                case,result,registration=self._example_output()
                word=result["methods"]["word"]
                target=word["selected_span_index"]
                for entries in word["evidence"].values():
                    for entry in entries:
                        if entry["span_index"]==target:
                            if field=="confidence":entry["span"]["confidence"]-=.01
                            else:entry["span"]["bbox"]["x0"]+=1
                with self.assertRaises(module.WordTableExperimentError) as caught:
                    module._validate_output(result,case["query"],registration,{})
                self.assertEqual(caught.exception.code,"derived_span_mismatch")

    def test_repeated_reference_index_and_parent_drift_refused(self):
        case,result,registration=self._example_output()
        word=result["methods"]["word"]
        mapping=next(m for m in word["adaptation"]["source_map"] if m["origin"]=="derived_word_group")
        mapping["word_refs"].append(deepcopy(mapping["word_refs"][0]))
        with self.assertRaises(module.WordTableExperimentError):module._validate_output(result,case["query"],registration,{})

    def test_extra_self_reported_proof_fields_are_rejected(self):
        for kind in ("parent","mapping","adaptation","guard"):
            with self.subTest(kind=kind):
                case,result,registration=self._example_output();word=result["methods"]["word"]
                if kind=="parent":target=word["parent_evidence"][0]
                elif kind=="mapping":target=word["adaptation"]["source_map"][0]
                elif kind=="adaptation":target=word["adaptation"]
                else:
                    word["guard_issues"]=[{"code":"word_low_confidence","line_indices":[],"word_refs":[]}]
                    target=word["guard_issues"][0]
                target["verified_true"]=True
                with self.assertRaises(module.WordTableExperimentError):module._validate_output(result,case["query"],registration,{})

    def test_self_consistent_lower_score_cannot_remain_selected(self):
        case,result,registration=self._example_output();word=result["methods"]["word"]
        index=word["selected_span_index"]
        mapping=next(m for m in word["adaptation"]["source_map"] if m["span_index"]==index)
        ref=mapping["word_refs"][0]
        parent=next(p for p in word["parent_evidence"] if p["line_index"]==ref["line_index"])
        parent["words"][ref["word_index"]]["span"]["confidence"]=.2
        for entries in word["evidence"].values():
            for entry in entries:
                if entry["span_index"]==index:entry["span"]["confidence"]=.2
        with self.assertRaises(module.WordTableExperimentError) as caught:
            module._validate_output(result,case["query"],registration,{})
        self.assertEqual(caught.exception.code,"selected_parent_not_qualified")

    def test_guard_word_cannot_point_to_unrelated_public_parent_after_rehash(self):
        for mutation in ("unrelated_parent","duplicate_ref"):
            with self.subTest(mutation=mutation):
                changed=deepcopy(self.report)
                case=next(c for d in changed["documents"] for c in d["cases"] if c["id"]=="msft:short_debt-2025")
                word=case["methods"]["word"]["extraction"]
                issue=next(i for i in word["guard_issues"] if i["code"]=="word_outside_parent")
                if mutation=="unrelated_parent":
                    parent=next(p for p in word["parent_evidence"] if p["line_index"] not in issue["line_indices"] and p["words"])
                    issue["word_refs"]=[{"line_index":parent["line_index"],"word_index":0}]
                else:issue["word_refs"].append(deepcopy(issue["word_refs"][0]))
                case["extraction_sha256"]=digest({**case["extraction_metadata"],"methods":{k:case["methods"][k]["extraction"] for k in module.METHODS}})
                with self.assertRaises(module.WordTableExperimentError) as caught:
                    module.validate_word_table_lookup_report(self.root,changed)
                self.assertEqual(caught.exception.code,"guard_ref_parent_mismatch" if mutation=="unrelated_parent" else "duplicate_guard_word_ref")

    def test_same_count_score_or_transition_mutation_is_rejected(self):
        for field in ("score","transition"):
            with self.subTest(field=field):
                changed=deepcopy(self.report);case=changed["documents"][0]["cases"][0]
                if field=="score":case["methods"]["word"]["score"]["amount_match"]=True
                else:case["transition"]["to"]="wrong"
                # Pick a definitely changed score regardless of actual success.
                if field=="score":case["methods"]["word"]["score"]["outcome"]="wrong"
                with self.assertRaises(module.WordTableExperimentError):module.validate_word_table_lookup_report(self.root,changed)

    def test_release_hash_refuses_changed_saved_evidence(self):
        altered=deepcopy(self.report);altered["limitations"].append("Changed claim")
        (self.root/module.REPORT).write_text(json.dumps(altered),encoding="utf-8")
        with self.assertRaises(module.WordTableExperimentError) as caught:module.load_word_table_lookup_snapshot(self.root)
        self.assertEqual(caught.exception.code,"published_report_changed")

    def test_input_hash_and_previous_image_binding_cannot_drift(self):
        path=self.root/module.CONFIG;original=path.read_bytes()
        for field in ("word_sha256","image_sha256"):
            with self.subTest(field=field):
                value=json.loads(original);value["documents"][0]["pages"][0][field]="0"*64
                path.write_text(json.dumps(value),encoding="utf-8")
                with self.assertRaises(module.WordTableExperimentError):module.load_word_table_lookup_snapshot(self.root)
                path.write_bytes(original)

    def test_missing_ocr_fails_without_download_or_output(self):
        with self.assertRaises(module.WordTableExperimentError):module.build_word_table_lookup_report(self.root,self.root/"missing")
        self.assertFalse((self.root/"missing").exists())

    def test_global_decimal_precision_does_not_change_saved_validation(self):
        with localcontext() as context:
            context.prec=2
            self.assertEqual(module.validate_word_table_lookup_report(self.root,self.report),self.report)

    def test_output_new_run_only_and_existing_report_unchanged(self):
        with patch.object(runner,"ROOT",self.root),patch.object(runner,"build_word_table_lookup_report",return_value=self.report), \
             patch.object(runner,"validate_word_table_lookup_report",return_value=self.report):
            runner.run(output=Path("new.json"))
            with self.assertRaises(ValueError):runner.run(output=Path("new.json"))
            with self.assertRaises(ValueError):runner.run(output=self.root/module.REPORT)


if __name__=="__main__":unittest.main()
