"""App lifecycle recovery controls, distinct from ordinary in-memory retries."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import replay_research_restart as replay


class ResearchRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        cls.report=replay.build_replay(cls.root)

    def test_four_write_classes_after_real_application_reconstruction(self):
        self.assertEqual([o["id"] for o in self.report["operations"]],
            ["research_patch","research_action","explanation_patch","explanation_action"])
        for operation in self.report["operations"]:
            self.assertEqual(operation["first_http_status"],200)
            self.assertEqual(operation["replayed_http_status"],200)
            self.assertEqual(operation["first_response_sha256"],operation["replayed_response_sha256"])
            self.assertEqual(operation["before_state_sha256"],operation["after_state_sha256"])
            self.assertEqual(operation["database_before"],operation["database_after"])
            self.assertGreater(operation["restarted_generation"],1)

    def test_previous_clients_connections_and_stores_are_replaced(self):
        summary=self.report["summary"]
        self.assertEqual((summary["application_instances"],summary["application_rebuilds"]),(13,12))
        for index,item in enumerate(self.report["restarts"],1):
            self.assertEqual((item["previous_generation"],item["current_generation"]),(index,index+1))
            for key in ("previous_client_closed","prior_sql_connection_closed","new_application_instance",
                        "new_research_store_instance","new_explanation_store_instance"):
                self.assertIs(item[key],True)

    def test_old_response_does_not_replace_later_reviewer_state(self):
        self.assertEqual(len(self.report["late_replays"]),4)
        for late in self.report["late_replays"]:
            self.assertLess(late["replayed_revision"],late["current_revision"])
            self.assertEqual((late["current_revision"],late["current_status"]),(4,"reviewed"))
            self.assertEqual(late["database_before"],late["database_after"])
        exports=[c for c in self.report["checks"] if c["id"].endswith("export_unchanged")]
        self.assertEqual(len(exports),2)
        self.assertTrue(all(c["expected"]==c["actual"] for c in exports))

    def test_actual_temporary_source_drift_blocks_new_but_not_completed_writes(self):
        faults=self.report["faults"][:2]
        self.assertEqual([f["id"] for f in faults],["research_source_drift","explanation_source_drift"])
        for fault in faults:
            self.assertEqual(fault["original_sha256"],fault["restored_sha256"])
            self.assertNotEqual(fault["original_sha256"],fault["synthetic_changed_sha256"])
            self.assertEqual((fault["actual_status"],fault["recovery_status"]),(409,200))
            self.assertEqual(fault["before_state_sha256"],fault["failure_after_state_sha256"])
            self.assertEqual(fault["database_before"],fault["database_after_failure"])

    def test_uncommitted_connection_close_and_failed_transaction_leave_no_partial_write(self):
        closed,failed=self.report["faults"][2:]
        self.assertEqual(closed["id"],"closed_uncommitted_connection")
        self.assertEqual(closed["database_before"],closed["database_after_rollback"])
        self.assertEqual(failed["actual_status"],503)
        self.assertEqual(failed["database_before"],failed["database_after_rollback"])
        self.assertEqual(failed["recovery_response_sha256"],failed["post_restart_replay_sha256"])
        counts=self.report["final_database"]["counts"]
        self.assertEqual(counts["research_write_receipts"],8)
        self.assertEqual((counts["research_snapshots"],counts["explanation_review_snapshots"]),(3,2))

    def test_report_is_deterministic_and_checks_are_observed_not_just_totals(self):
        self.assertEqual(replay.validate_replay(self.root,self.report),self.report)
        self.assertTrue(all(check["passed"] for check in self.report["checks"]))
        self.assertEqual(self.report["summary"]["checks"],len(self.report["checks"]))
        self.assertEqual(self.report["summary"]["failed"],0)

    def test_same_counts_do_not_mask_response_or_scope_tampering(self):
        mutations=[]
        changed=deepcopy(self.report);changed["operations"][0]["replayed_response_sha256"]="0"*64;mutations.append(changed)
        changed=deepcopy(self.report);changed["scope"]["real_os_crash"]=True;mutations.append(changed)
        changed=deepcopy(self.report);changed["faults"][0]["actual_status"]=200;mutations.append(changed)
        changed=deepcopy(self.report);changed["restarts"][0]["new_application_instance"]=1;mutations.append(changed)
        for changed in mutations:
            self.assertEqual(changed["summary"],self.report["summary"])
            with self.assertRaises(ValueError):replay.validate_replay(self.root,changed)

    def test_no_op_restart_cannot_be_reported_as_rebuilt_application(self):
        with patch.object(replay.RestartHarness,"restart",return_value=None):
            with self.assertRaisesRegex(ValueError,"application_instances_actually_rebuilt"):
                replay.build_replay(self.root)

    def test_checkout_source_newlines_do_not_change_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for relative in replay.IMPLEMENTATION_FILES+replay.SOURCE_FILES:
                target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes((self.root/relative).read_text(encoding="utf8").replace("\r\n","\n").encode())
            for relative in replay.SOURCE_FILES:
                path=root/relative;path.write_bytes(path.read_bytes().replace(b"\n",b"\r\n"))
            self.assertEqual(replay.validate_replay(root,self.report),self.report)

    def test_source_bindings_change_during_run_fails_closed(self):
        before=replay.bindings(self.root);after=deepcopy(before)
        after["source_file_sha256"][replay.SOURCE_FILES[0]]="0"*64
        with patch.object(replay,"bindings",side_effect=[before,after]):
            with self.assertRaisesRegex(ValueError,"changed during replay"):
                replay.build_replay(self.root)

    def test_scope_does_not_claim_os_crash_or_real_users(self):
        scope=self.report["scope"]
        self.assertTrue(scope["same_python_process"])
        for field in ("real_os_crash","real_network_fault","worker_or_queue_recovery","browser_reload_evaluated","main_database_modified"):
            self.assertIs(scope[field],False)
        self.assertEqual(scope["independent_human_gold"],0)
        self.assertEqual(scope["principals"],"synthetic_api_key_identities")

    def test_safe_output_rejects_overwrite_and_published_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/".run/reproductions").mkdir(parents=True)
            with patch.object(replay,"ROOT",root),patch.object(replay,"build_replay",return_value=self.report),redirect_stdout(io.StringIO()):
                self.assertEqual(replay.main(["--output","restart-local.json"]),0)
                target=root/".run/reproductions/restart-local.json";before=target.read_bytes()
                self.assertEqual(replay.main(["--output","restart-local.json"]),1)
                self.assertEqual(target.read_bytes(),before)
                self.assertEqual(replay.main(["--output",str(root/"artifacts/new.json")]),1)
                self.assertFalse((root/"artifacts/new.json").exists())

    def test_report_reading_rejects_duplicate_nonfinite_and_bad_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"bad.json"
            for raw in (b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":1e400}',b'\xff',b'[]',b' '*1_000_001):
                path.write_bytes(raw)
                with self.subTest(raw=raw[:20]),self.assertRaises(ValueError):replay.read_report(path)


if __name__=="__main__":unittest.main()
