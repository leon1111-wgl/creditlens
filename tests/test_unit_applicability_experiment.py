"""Independent compatibility inventory, synthetic risks and saved-report guards."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens import unit_applicability_experiment as experiment
from scripts import evaluate_unit_applicability as cli


class UnitApplicabilityExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        cls.report=experiment.build_unit_applicability_report(cls.root)
        cls.snapshot={"schema_version":1,"report":cls.report,"report_sha256":"a"*64,
            **{k:cls.report[k] for k in ("role_report_sha256","table_report_sha256","row_report_sha256")},"read_only":True}

    def test_registered_records_queries_and_paths_are_separate_denominators(self):
        summary=self.report["summary"]
        self.assertEqual((summary["registered_records"],summary["unique_query_inputs"]),(42,40))
        self.assertEqual((summary["automatic_method_checks"],summary["explicit_selection_paths"],summary["compatibility_checks"]),(168,8,176))
        self.assertEqual(len({c["id"] for c in self.report["cases"]}),42)
        self.assertEqual(len({(s["query_id"],s["row_index"]) for s in self.report["selections"]}),8)
        self.assertEqual(len({s["query_id"] for s in self.report["selections"]}),4)
        self.assertEqual(self.report["scope"]["independent_human_gold"],0)
        self.assertFalse(self.report["scope"]["model_accuracy_measured"])

    def test_legacy_status_and_units_preserved_in_all_176_checks(self):
        records=[m for c in self.report["cases"] for m in c["methods"].values()]+self.report["selections"]
        self.assertEqual(len(records),176)
        for record in records:
            result=record["unit_check"]
            self.assertEqual(result["original_candidate"]["status"],record["legacy_status"])
            self.assertEqual(result["original_candidate"]["unit"],record["legacy_unit"])
            if record["legacy_status"]!="selected":self.assertEqual(result["status"],"not_selected")
            self.assertEqual(len(record["legacy_extraction_sha256"]),64)

    def test_fixed_synthetic_cases_cover_five_methods_without_reference_values(self):
        controls=self.report["controls"]
        self.assertEqual(controls["summary"]["cases"],20)
        self.assertEqual(controls["summary"]["method_checks"],100)
        self.assertEqual((controls["summary"]["passed"],controls["summary"]["failed"]),(20,0))
        for case in controls["cases"]:
            self.assertEqual(set(case["methods"]),set(experiment.CONTROL_METHODS))
            self.assertEqual(case["provenance"],"synthetic_control")
            self.assertFalse(case["independent_human_verified"])
            self.assertTrue(all(method["passed"] for method in case["methods"].values()))

    def test_prefix_risks_block_every_previously_selected_method(self):
        for key in ("UA-08","UA-09","UA-10","UA-11","UA-12"):
            case=next(c for c in self.report["controls"]["cases"] if c["id"]==key)
            for method in case["methods"].values():
                self.assertEqual(method["legacy_status"],"selected")
                self.assertEqual(method["unit_check"]["status"],"blocked")
                self.assertIsNone(method["unit_check"]["scale"])
                self.assertIsNone(method["unit_check"]["scaled_value"])

    def test_negative_zero_and_full_known_exceptions_have_positive_controls(self):
        for key,value in (("UA-04","-100000000"),("UA-05","100000000"),("UA-06","100000000"),("UA-07","100000000"),("UA-19","0")):
            case=next(c for c in self.report["controls"]["cases"] if c["id"]==key)
            self.assertEqual(case["expected"]["scaled_value"],value)
            self.assertTrue(all(m["unit_check"]["scaled_value"]==value for m in case["methods"].values()))

    def test_unit_ready_does_not_establish_currency_period_or_financial_scope(self):
        records=[m for c in self.report["cases"] for m in c["methods"].values()]+self.report["selections"]
        for record in records:
            scope=record["unit_check"]["scope"]
            for key in ("currency","financial_period","financial_scope"):self.assertEqual(scope[key],"unknown")
            self.assertFalse(scope["full_page_replayed"])
            self.assertFalse(record["unit_check"]["automatic_approval"])

    def test_candidate_and_legacy_reports_are_not_mutated(self):
        before={name:sha256((self.root/path).read_bytes()).hexdigest() for name,path in experiment.SOURCE_PATHS.items()}
        _,query,methods=experiment._control_inputs(experiment.control_specs()[0])
        original=deepcopy(methods)
        for method in methods.values():experiment._unit(query["row_label"],method)
        self.assertEqual(methods,original)
        self.assertEqual(before,{name:sha256((self.root/path).read_bytes()).hexdigest() for name,path in experiment.SOURCE_PATHS.items()})

    def test_saved_report_full_replay_and_same_count_mutations_rejected(self):
        self.assertEqual(experiment.validate_unit_applicability_report(self.root,self.report),self.report)
        variants=[]
        altered=deepcopy(self.report);altered["cases"][0]["methods"]["role"]["unit_check"]["scaled_value"]="1";variants.append(altered)
        altered=deepcopy(self.report);altered["cases"][1]=deepcopy(altered["cases"][0]);variants.append(altered)
        altered=deepcopy(self.report);altered["scope"]["full_ocr_replayed"]=True;variants.append(altered)
        altered=deepcopy(self.report);altered["selections"][0]["row_index"]=float(altered["selections"][0]["row_index"]);variants.append(altered)
        altered=deepcopy(self.report);altered["controls"]["cases"][0]["independent_human_verified"]=True;variants.append(altered)
        for altered in variants:
            self.assertEqual(altered["summary"],self.report["summary"])
            with self.assertRaises(experiment.UnitApplicabilityExperimentError):
                experiment.validate_unit_applicability_report(self.root,altered)

    def test_failed_synthetic_guard_cannot_generate_a_verified_report(self):
        original=experiment.inspect_unit_applicability
        def unsafe(**kwargs):
            result=original(**kwargs)
            if kwargs["extraction"]["unit"]["raw_text"]=="(In millions except revenue in billions)":
                result.update(status="unit_ready",scale="million",scaled_value="100000000")
            return result
        with patch.object(experiment,"inspect_unit_applicability",side_effect=unsafe):
            with self.assertRaises(experiment.UnitApplicabilityExperimentError) as failure:
                experiment.build_unit_applicability_report(self.root)
        self.assertEqual(failure.exception.code,"control_failure")

    def test_case_export_has_explicit_method_and_bound_report_hashes(self):
        case=self.report["cases"][0]
        for method in experiment.METHODS:
            result=experiment.get_unit_case(self.snapshot,case["id"],method)
            self.assertEqual(result["target"],{"kind":"automatic","case_id":case["id"],"method":method,
                **{k:case[k] for k in ("document_id","page","query")}})
            self.assertEqual(result["report_sha256"],self.snapshot["report_sha256"])
            self.assertEqual(result["unit_check"],case["methods"][method]["unit_check"])
        result["unit_check"]["status"]="tampered"
        self.assertNotEqual(case["methods"][method]["unit_check"]["status"],"tampered")
        for method in (None,True,"layout","selection"):
            with self.assertRaises(experiment.UnitApplicabilityExperimentError):experiment.get_unit_case(self.snapshot,case["id"],method)
        with self.assertRaises(experiment.UnitApplicabilityExperimentError):experiment.get_unit_case(self.snapshot,"unknown","role")

    def test_explicit_selection_export_never_defaults_or_accepts_bool_float_index(self):
        selection=self.report["selections"][0]
        result=experiment.get_unit_selection(self.snapshot,selection["query_id"],selection["row_index"])
        self.assertEqual(result["target"]["kind"],"explicit_selection")
        self.assertEqual(result["legacy_extraction_sha256"],selection["legacy_extraction_sha256"])
        self.assertEqual(result["unit_check"],selection["unit_check"])
        for index in (True,float(selection["row_index"]),-1,999):
            with self.assertRaises(experiment.UnitApplicabilityExperimentError):experiment.get_unit_selection(self.snapshot,selection["query_id"],index)
        with self.assertRaises(experiment.UnitApplicabilityExperimentError):experiment.get_unit_selection(self.snapshot,"unknown",selection["row_index"])

    def test_strict_bounded_json_rejects_duplicate_nan_overflow_and_invalid_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"bad.json"
            for data in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e400}',b'\xff',b'[]'):
                path.write_bytes(data)
                with self.assertRaises(experiment.UnitApplicabilityExperimentError):experiment._read(path)
            path.write_bytes(b" "*11)
            with self.assertRaises(experiment.UnitApplicabilityExperimentError):experiment._read(path,10)

    def test_implementation_drift_during_read_fails_closed(self):
        sources=experiment._sources(self.root);first=experiment._hashes(self.root);last=deepcopy(first)
        last["creditlens/unit_applicability.py"]="0"*64
        with patch.object(experiment,"_hashes",side_effect=[first,last]):
            with self.assertRaises(experiment.UnitApplicabilityExperimentError) as failure:experiment._build(self.root,sources)
        self.assertEqual(failure.exception.code,"implementation_changed_during_read")

    def test_published_snapshot_and_store_load_real_small_report_without_ocr(self):
        store=experiment.UnitApplicabilityStore(self.root)
        snapshot=store.snapshot()
        self.assertEqual(snapshot["report"],self.report)
        self.assertEqual(snapshot["report_sha256"],sha256((self.root/experiment.REPORT).read_bytes()).hexdigest())
        self.assertFalse(snapshot["full_ocr_replayed"])
        first=self.report["cases"][0];selection=self.report["selections"][0]
        self.assertEqual(store.case(first["id"],"role"),experiment.get_unit_case(snapshot,first["id"],"role"))
        self.assertEqual(store.selection(selection["query_id"],selection["row_index"]),experiment.get_unit_selection(snapshot,selection["query_id"],selection["row_index"]))
        with redirect_stdout(io.StringIO()):self.assertEqual(cli.main(["--verify-saved"]),0)

    def test_source_snapshot_hash_must_still_match_saved_report_bytes(self):
        sources=experiment._sources(self.root)
        sources["table"]["report_sha256"]="0"*64
        with self.assertRaises(experiment.UnitApplicabilityExperimentError) as failure:
            experiment._build(self.root,sources)
        self.assertEqual(failure.exception.code,"baseline_changed_during_read")

    def test_safe_cli_output_does_not_overwrite_existing_or_published_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/".run/reproductions").mkdir(parents=True)
            with patch.object(cli,"ROOT",root),patch.object(cli,"build_unit_applicability_report",return_value=self.report),redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--output","unit-local.json"]),0)
                path=root/".run/reproductions/unit-local.json";before=path.read_bytes()
                self.assertEqual(cli.main(["--output","unit-local.json"]),1)
                self.assertEqual(path.read_bytes(),before)
                self.assertEqual(cli.main(["--output",str(root/"artifacts/forbidden.json")]),1)
                self.assertFalse((root/"artifacts/forbidden.json").exists())


if __name__=="__main__":unittest.main()
