"""Saved-response comparison and bounded, side-effect-free local verification."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens import row_selection_verification_experiment as module
from creditlens.row_selection_verification import MAX_INPUT_BYTES, RowSelectionVerificationError
from creditlens.diligence import digest
from scripts import verify_row_selection as runner

ROOT = Path(__file__).resolve().parents[1]


class RowSelectionVerificationExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        previous = module.source
        files = {module.REPORT, previous.CONFIG, previous.REPORT, previous.RELEASE,
            previous.prior.CONFIG, previous.prior.REPORT, previous.prior.RELEASE,
            previous.prior.old.CONFIG, previous.prior.old.REPORT, previous.prior.old.RELEASE,
            "artifacts/document-ai-word-report.json", "configs/table-lookup.json", "configs/table-lookup-artifact.json",
            "artifacts/table-lookup-report.json", "artifacts/document-ai-report.json", "configs/document-ai.json",
            "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json", *module.IMPLEMENTATION_FILES}
        for name in files:
            target = self.root/name; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, target)
        self.report = json.loads((self.root/module.REPORT).read_text(encoding="utf8"))
        self.snapshot = module.source.load_row_disambiguation_snapshot(self.root)
        self.exports = module.registered_exports(self.snapshot)

    def run_cli(self, args):
        out = io.StringIO()
        with patch.object(runner, "ROOT", self.root), redirect_stdout(out):
            code = runner.main(args)
        return code, json.loads(out.getvalue())

    def write_input(self, data):
        path = self.root/"input.json"; path.write_bytes(data); return path

    def test_all_eight_exact_saved_shapes_match_with_no_financial_quality_claim(self):
        loaded = module.load_row_selection_verification_snapshot(self.root)["report"]
        self.assertEqual(loaded["registered_paths"]["summary"], {"cases": 8, "passed": 8, "failed": 0})
        self.assertEqual(len({(c["query_id"],c["row_index"]) for c in loaded["registered_paths"]["cases"]}), 8)
        for case in loaded["registered_paths"]["cases"]:
            result = case["observed"]["verification"]
            self.assertEqual(result["status"], "matched"); self.assertEqual(result["issues"], [])
            self.assertEqual(result["raw_sha256"], case["input_sha256"])
            self.assertFalse(result["scope"]["independent_financial_correctness"])
            self.assertFalse(result["scope"]["human_gold"])
        self.assertFalse(loaded["scope"]["real_http_responses_used"])
        self.assertEqual(loaded["scope"]["independent_human_gold"], 0)

    def test_export_shape_is_unchanged_and_contains_no_verification_additions(self):
        keys = {"schema_version", "report_sha256", "role_report_sha256", "query_id", "document_id", "page", "query", "source",
            "page_metadata", "discovery", "result", "read_only", "persisted", "full_ocr_replayed", "automatic_approval", "selection_basis"}
        for item in self.exports:
            self.assertEqual(set(item["payload"]), keys)
            self.assertTrue(item["payload"]["read_only"])
            self.assertFalse(item["payload"]["persisted"])

    def test_all_synthetic_inputs_are_counted_without_payload_storage(self):
        controls = self.report["synthetic_controls"]
        self.assertEqual(controls["summary"], {"cases": 34, "passed": 34, "failed": 0})
        for item in controls["cases"]:
            self.assertNotIn("raw", item); self.assertNotIn("payload", item)
            self.assertEqual(item["provenance"], "synthetic_development_control")
            self.assertEqual(item["independent_human_confirmation"], "pending")
        self.assertNotIn("SYNTHETIC_UNTRUSTED_CONTEXT", json.dumps(self.report))
        self.assertNotIn("SYNTHETIC_UNTRUSTED_KEY", json.dumps(self.report))
        self.assertNotIn("SYNTHETIC_UNTRUSTED_VALUE", json.dumps(self.report))

    def test_whitespace_equivalence_keeps_raw_and_canonical_hashes_distinct(self):
        payload = self.exports[0]["payload"]
        one = module.observe(module._bytes(payload), self.snapshot)["verification"]
        other = module.observe(json.dumps(payload,indent=2,sort_keys=True).encode(), self.snapshot)["verification"]
        self.assertEqual(other["status"], "matched")
        self.assertEqual(one["canonical_sha256"], other["canonical_sha256"])
        self.assertNotEqual(one["raw_sha256"], other["raw_sha256"])

    def test_modified_self_consistent_values_do_not_replace_trusted_report(self):
        case = next(c for c in self.report["synthetic_controls"]["cases"] if c["id"] == "RV-19")
        self.assertEqual(case["observed"]["status"], "mismatch")
        self.assertIsNone(case["observed"]["verification"]["bound_selection"])

    def test_missing_extra_and_same_value_types_are_mismatches(self):
        cases = {c["id"]: c for c in self.report["synthetic_controls"]["cases"]}
        for key in ("RV-10", "RV-11", "RV-12", "RV-13", "RV-14", "RV-15"):
            self.assertEqual(cases[key]["observed"]["status"], "mismatch")
            self.assertTrue(cases[key]["observed"]["verification"]["issues"])

    def test_no_complete_ocr_read_in_build_or_saved_verification(self):
        with patch.object(module.source.prior.old, "_page", side_effect=AssertionError("No full OCR read")):
            self.assertEqual(module.build_row_selection_verification_report(self.root), self.report)
            module.load_row_selection_verification_snapshot(self.root)
        self.assertFalse((self.root/".run").exists())

    def test_report_replays_all_fields_not_only_summary_counts(self):
        for name in ("status", "input_hash", "source", "scope", "expected", "issues"):
            value = deepcopy(self.report)
            if name == "status": value["registered_paths"]["cases"][0]["observed"]["verification"]["status"] = "mismatch"
            elif name == "input_hash": value["registered_paths"]["cases"][0]["input_sha256"] = "0"*64
            elif name == "source": value["row_report_sha256"] = "0"*64
            elif name == "scope": value["scope"]["independent_human_gold"] = 8
            elif name == "expected": value["synthetic_controls"]["cases"][0]["expected"]["status"] = "matched"
            else: value["synthetic_controls"]["cases"][0]["observed"]["verification"]["issues"] = []
            with self.subTest(name=name), self.assertRaises(module.RowSelectionExperimentError):
                module.validate_row_selection_verification_report(self.root,value)

    def test_control_failure_prevents_verified_report(self):
        actual = module.observe
        def replace(raw, snapshot):
            observed = actual(raw,snapshot)
            if observed["status"] == "mismatch": return {"status": "matched"}
            return observed
        with patch.object(module, "observe", side_effect=replace), self.assertRaises(module.RowSelectionExperimentError):
            module.build_row_selection_verification_report(self.root)

    def test_local_read_is_single_bounded_read(self):
        path = self.write_input(b"{}"); data = io.BytesIO(b"x"*(MAX_INPUT_BYTES+50))
        with patch.object(Path,"open",return_value=data), patch.object(data,"read",wraps=data.read) as read:
            result = runner.read_input(path)
            read.assert_called_once_with(MAX_INPUT_BYTES+1)
        self.assertEqual(len(result),MAX_INPUT_BYTES+1)

    def test_cli_exit_status_distinguishes_match_mismatch_parse_size_and_source(self):
        payload = self.exports[0]["payload"]
        path = self.write_input(module._bytes(payload))
        code,result = self.run_cli(["--input",str(path)])
        self.assertEqual((code,result["status"]),(0,"matched"))
        changed = deepcopy(payload); changed["persisted"] = True; path.write_bytes(module._bytes(changed))
        code,result = self.run_cli(["--input",str(path)])
        self.assertEqual((code,result["status"]),(1,"mismatch"))
        path.write_bytes(b'{"a":1,"a":2}')
        code,result = self.run_cli(["--input",str(path)])
        self.assertEqual((code,result["code"]),(2,"duplicate_key"))
        path.write_bytes(b" "*(MAX_INPUT_BYTES+9))
        code,result = self.run_cli(["--input",str(path)])
        self.assertEqual((code,result["code"]),(4,"input_too_large"))
        path.write_bytes(module._bytes(payload)); (self.root/module.source.RELEASE).unlink()
        code,result = self.run_cli(["--input",str(path)])
        self.assertEqual((code,result["status"]),(3,"source_unavailable"))

    def test_cli_format_errors_are_rejected_before_loading_source(self):
        path = self.write_input(b"\xff")
        with patch("creditlens.row_selection_verification.load_row_disambiguation_snapshot",side_effect=AssertionError("Source must not load")):
            code,result = self.run_cli(["--input",str(path)])
        self.assertEqual((code,result["code"]),(2,"invalid_utf8"))

    def test_cli_does_not_echo_unknown_key_value_or_sensitive_path(self):
        payload = deepcopy(self.exports[0]["payload"])
        payload["PRIVATE_CANARY_KEY"] = "PRIVATE_CANARY_VALUE"
        code,result = self.run_cli(["--input",str(self.write_input(module._bytes(payload)))])
        self.assertEqual(code,1)
        self.assertNotIn("PRIVATE_CANARY",json.dumps(result))
        code,result = self.run_cli(["--input",str(self.root/"PRIVATE_PATH_CANARY.json")])
        self.assertEqual(code,2); self.assertNotIn("PRIVATE_PATH_CANARY",json.dumps(result))

    def test_cli_output_is_new_json_only_and_input_never_changes(self):
        raw=module._bytes(self.exports[0]["payload"]); path=self.write_input(raw)
        code,result=self.run_cli(["--input",str(path),"--output","verification.json"])
        self.assertEqual(code,0); self.assertEqual(path.read_bytes(),raw)
        out=self.root/".run/reproductions/verification.json"
        self.assertEqual(json.loads(out.read_text(encoding="utf8")),result)
        saved=out.read_bytes()
        code,_=self.run_cli(["--input",str(path),"--output","verification.json"])
        self.assertEqual(code,2); self.assertEqual(saved,out.read_bytes())
        for target in (self.root/"artifacts/new.json",self.root/".run/document-ai/new.json",self.root/".run/reproductions/sub/new.json"):
            code,_=self.run_cli(["--input",str(path),"--output",str(target)])
            self.assertEqual(code,2); self.assertFalse(target.exists())

    def test_cli_controls_and_saved_modes_are_small_and_reproducible(self):
        code,result=self.run_cli(["--run-controls","--output","controls.json"])
        self.assertEqual((code,result["status"]),(0,"controls_passed"))
        actual=json.loads((self.root/".run/reproductions/controls.json").read_text(encoding="utf8"))
        self.assertEqual(digest(actual),digest(self.report)); self.assertNotIn("cases",result)
        code,result=self.run_cli(["--verify-saved"])
        self.assertEqual((code,result["status"]),(0,"saved_checks_replayed"))
        self.assertFalse(result["full_ocr_replayed"])

    def test_original_frozen_files_unchanged_by_verification(self):
        files=[self.root/module.source.REPORT,self.root/module.source.RELEASE,self.root/module.source.prior.REPORT]
        before={str(p):sha256(p.read_bytes()).hexdigest() for p in files}
        module.build_row_selection_verification_report(self.root)
        self.assertEqual(before,{str(p):sha256(p.read_bytes()).hexdigest() for p in files})
        self.assertEqual(list(self.root.rglob("*.sqlite")),[])


if __name__ == "__main__": unittest.main()
