"""Independent fixed expectations, failure channels and exact replay controls."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.diligence import digest
from creditlens.research_query import build_research_query
from scripts import evaluate_research_queries as experiment


class ResearchQueryExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        cls.report=experiment.build_report(cls.root)

    def case(self,key):
        return deepcopy(next(c for c in self.report["cases"] if c["id"]==key))

    def test_entire_fixed_inventory_and_separate_provenance(self):
        summary=self.report["summary"]
        self.assertEqual((summary["cases"],summary["passed"],summary["failed"]),(32,32,0))
        self.assertEqual((summary["registered_material_projections"],summary["synthetic_controls"]),(10,22))
        self.assertEqual(summary["statuses"],{"evidence_available":6,"computed":4,"insufficient":3,"blocked":7,
            "not_registered":3,"unsupported_scope":2,"error":7})
        self.assertEqual({c["id"] for c in self.report["cases"]},{f"RQ-{i:02d}" for i in range(1,33)})
        self.assertEqual(self.report["scope"]["independent_human_gold"],0)
        self.assertFalse(self.report["scope"]["natural_language_understanding_evaluated"])
        self.assertFalse(self.report["scope"]["model_accuracy_evaluated"])
        self.assertTrue(all(c["provenance"]["human_confirmation"]=="pending" for c in self.report["cases"]))

    def test_fixed_numeric_expectations_preserve_negative_outflow_and_units(self):
        card=self.case("RQ-03")["result"]["selected_evidence"][0]["card"]
        self.assertEqual([card[k] for k in ("raw_value","raw_signed_value","normalized_value","unit")],
            ["(64,551)","-64551","64551","USD_million"])
        for key,amount in (("RQ-05","17614"),("RQ-06","-6772"),("RQ-07","20074"),("RQ-08","3268")):
            case=self.case(key)
            self.assertEqual(case["result"]["calculation"]["absolute_change"],amount)
            self.assertEqual(case["expected"]["absolute_change"],amount)
        self.assertEqual(self.case("RQ-08")["result"]["calculation"]["growth_rate"],"0.345930")

    def test_no_all_eight_cards_serve_as_answer_to_single_lookup(self):
        for key in ("RQ-01","RQ-02","RQ-03","RQ-04","RQ-11"):
            result=self.case(key)["result"]
            self.assertEqual(len(result["selected_evidence"]),1)
            self.assertEqual(len(result["input_binding"]["selected_observation_ids"]),1)
            self.assertIsNone(result["calculation"])
            self.assertIsNone(result["operating_evidence"])

    def test_unadopted_lookup_is_locatable_but_comparison_and_explanation_blocked(self):
        self.assertFalse(self.case("RQ-11")["result"]["selected_evidence"][0]["card"]["usable_for_calculation"])
        self.assertEqual(self.case("RQ-12")["result"]["status"],"blocked")
        operating=self.case("RQ-23")["result"]["operating_evidence"]
        self.assertIsNone(operating["accounting_decomposition"]["calculation"])
        self.assertEqual(operating["company_statements"][0]["link_status"],"awaiting_input_adoption")

    def test_company_statement_and_missing_registration_do_not_prove_cause(self):
        msft=self.case("RQ-09")["result"]["operating_evidence"]
        aapl=self.case("RQ-10")["result"]["operating_evidence"]
        self.assertEqual(len(msft["company_statements"]),1)
        self.assertEqual(msft["company_statements"][0]["link_status"],"consistent_rounded")
        self.assertEqual(aapl["company_statements"],[])
        self.assertEqual(aapl["company_coverage"][0]["registered"],False)
        for result in (msft,aapl):
            self.assertEqual(result["independent_verification"]["status"],"missing")
            self.assertIsNone(result["causal_conclusion"])

    def test_hypothesis_outside_selected_scope_is_retained_as_unverified(self):
        hypothesis=self.case("RQ-25")["result"]["operating_evidence"]["candidate_hypotheses"][0]
        self.assertEqual(hypothesis["status"],"unverified_candidate")
        self.assertFalse(hypothesis["all_references_selected"])
        self.assertEqual(len(hypothesis["out_of_query_evidence_ids"]),1)

    def test_free_title_frozen_exports_and_current_tasks_unchanged(self):
        self.assertEqual(self.case("RQ-03")["result"],self.case("RQ-24")["result"])
        self.assertEqual(len(self.report["invariants"]),3)
        for invariant in self.report["invariants"]:
            self.assertTrue(invariant["passed"])
            self.assertEqual(invariant["before_sha256"],invariant["after_sha256"])

    def test_wrong_numeric_and_citation_fail_independent_channels(self):
        def candidate(task,request):
            result=build_research_query(task,request)
            if request["task_kind"]=="metric_lookup" and request["observations"]==[experiment.selector("MSFT")]:
                result["selected_evidence"][0]["card"]["normalized_value"]="136163"
            if request["task_kind"]=="metric_lookup" and request["observations"]==[experiment.selector("AAPL",year=2024)]:
                result["selected_evidence"][0]["card"]["source"]["page"]=999
            result["result_sha256"]=digest({k:v for k,v in result.items() if k!="result_sha256"})
            return result
        report=experiment.build_report(self.root,evaluator=candidate)
        self.assertGreater(report["summary"]["channels"]["numeric"]["failed"],0)
        self.assertGreater(report["summary"]["channels"]["citation"]["failed"],0)
        self.assertTrue(report["scope"]["candidate_override_used"])
        with self.assertRaises(ValueError): experiment.validate_report(self.root,report)

    def test_unexpected_exception_is_not_counted_as_correct_refusal(self):
        def fail(_task,_request): raise RuntimeError("must not be echoed as a legitimate error")
        report=experiment.build_report(self.root,evaluator=fail)
        self.assertEqual(report["summary"]["failed"],32)
        self.assertTrue(all(c["error"]["code"]=="unexpected_exception" for c in report["cases"]))

    def test_all_fields_replayed_even_if_summary_is_unchanged(self):
        self.assertEqual(experiment.validate_report(self.root,self.report),self.report)
        mutations=[]
        changed=deepcopy(self.report);changed["cases"][0]["result"]["selected_evidence"][0]["card"]["source"]["page"]=999;mutations.append(changed)
        changed=deepcopy(self.report);changed["cases"][0]["query"]["observations"][0]["year"]=2024;mutations.append(changed)
        changed=deepcopy(self.report);changed["scope"]["independent_human_gold"]=32;mutations.append(changed)
        changed=deepcopy(self.report);changed["invariants"][1]["after_sha256"]="0"*64;mutations.append(changed)
        changed=deepcopy(self.report);changed["implementation_sha256"]["creditlens/research_query.py"]="0"*64;mutations.append(changed)
        for changed in mutations:
            self.assertEqual(changed["summary"],self.report["summary"])
            with self.assertRaises(ValueError): experiment.validate_report(self.root,changed)

    def test_hash_change_during_replay_fails_closed(self):
        first=experiment._hashes(self.root)
        second=deepcopy(first);second[1][experiment.SOURCE_FILES[0]]="0"*64
        with patch.object(experiment,"_hashes",side_effect=[first,second]):
            with self.assertRaisesRegex(ValueError,"changed during replay"):
                experiment.build_report(self.root)

    def test_default_candidate_failure_cannot_be_saved_as_verified(self):
        import creditlens.research_query as core
        def broken(task,request):
            result=build_research_query(task,request)
            result["automatic_approval"]=True
            return result
        with patch.object(core,"build_research_query",side_effect=broken):
            with self.assertRaisesRegex(ValueError,"checks failed"):
                experiment.build_report(self.root)

    def test_strict_bounded_report_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"input.json"
            for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":1e400}',b'[]',b'\xff'):
                path.write_bytes(raw)
                with self.subTest(raw=raw),self.assertRaises(ValueError): experiment.read_report(path)
            with patch.object(experiment,"MAX_REPORT_BYTES",10):
                path.write_bytes(b" "*11)
                with self.assertRaisesRegex(ValueError,"size limit"): experiment.read_report(path)

    def test_cli_exclusive_output_and_protected_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/".run/reproductions").mkdir(parents=True)
            with patch.object(experiment,"ROOT",root),patch.object(experiment,"build_report",return_value=self.report),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(experiment.main(["--output","new.json"]),0)
                target=root/".run/reproductions/new.json";before=target.read_bytes()
                self.assertEqual(experiment.main(["--output","new.json"]),1)
                self.assertEqual(target.read_bytes(),before)
                self.assertEqual(experiment.main(["--output",str(root/"artifacts/forbidden.json")]),1)
                self.assertFalse((root/"artifacts/forbidden.json").exists())

    def test_cli_saved_report_is_full_replay_not_summary_check(self):
        with patch.object(experiment,"read_report",return_value=self.report),redirect_stdout(io.StringIO()):
            self.assertEqual(experiment.main(["--verify-saved"]),0)
        changed=deepcopy(self.report);changed["automatic_approval"]=True
        with patch.object(experiment,"read_report",return_value=changed),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(experiment.main(["--verify-saved"]),1)


if __name__=="__main__": unittest.main()
