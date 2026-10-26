"""Replay discarded successful write responses in an isolated synthetic workflow.

The simulation deliberately ignores the first return value; it does not disrupt
a real network. Official registered materials are reused without downloading.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from creditlens.diligence import digest
from creditlens.research_task_store import ResearchTaskStore
from creditlens.explanation_review_store import ExplanationReviewStore
from creditlens.workflow import Actor
from scripts.evaluate_row_disambiguation import output_path

REPORT = "artifacts/research-write-retry.json"
NOTE = "Synthetic workflow check; financial causes remain independently unverified."
IMPLEMENTATION_FILES = ("creditlens/research_write_receipts.py", "creditlens/research_task_store.py",
    "creditlens/explanation_review_store.py", "creditlens/explanation_evidence.py", "creditlens/company_attribution.py",
    "creditlens/research_materials.py", "creditlens/cash_flow_sources.py", "creditlens/cash_flow_bridge.py",
    "creditlens/diligence.py", "scripts/replay_research_write_retry.py")


def research_update(task, suffix=""):
    return {"revision": task["revision"], "decisions": [{"observation_id": o["id"], "decision": "adopted", "note": NOTE}
        for o in task["bundle"]["observations"]], "hypotheses": [],
        "coverage_note": "Registered current comparative periods only; no historical availability claim."+suffix,
        "bridge_review_note": "Accounting reconciliation only, without independent causal proof."}


def explanation_update(review, decision="needs_evidence"):
    return {"revision": review["revision"], "decisions": [{"entry_id": e["id"], "decision": decision, "note": NOTE}
        for e in review["evidence"]["attribution"]["entries"]],
        "coverage_note": "Company statement only; independent evidence of causes is still missing."}


def action_body(record, action):
    return {"revision": record["revision"], "action": action, "note": NOTE,
            "snapshot_sha256": record["snapshot"]["sha256"] if record["snapshot"] else None}


def metrics(record):
    return {"revision": record["revision"], "status": record["status"], "events": len(record["events"]), "snapshots": len(record["snapshots"])}


def build_replay(root: Path) -> dict:
    root = Path(root).resolve()
    analyst = Actor("synthetic-author", "synthetic-write-retry", "analyst")
    editor = Actor("synthetic-supplement-author", analyst.tenant_id, "analyst")
    reviewer = Actor("synthetic-reviewer", analyst.tenant_id, "reviewer")
    viewer = Actor("synthetic-viewer", analyst.tenant_id, "viewer")
    other = Actor("synthetic-other-tenant", "synthetic-unrelated", "analyst")
    checks, operations = [], []
    counter = iter(range(1, 100))
    def check(name, actual, expected):
        passed = digest(actual) == digest(expected)
        checks.append({"id": name, "actual": actual, "expected": expected, "passed": passed})
        if not passed: raise ValueError("Write retry replay check failed: "+name)
    def refusal(name, call, expected):
        try: call()
        except HTTPException as exc: check(name, exc.status_code, expected)
        else: raise ValueError("Expected refusal did not occur: "+name)
    def perform(name, call, current):
        first = call()  # Commit completes; caller then acts as if this return was lost.
        before = current(); replay = call(); after = current()
        check(name+"_same_response", digest(replay), digest(first))
        check(name+"_no_repeat_mutation", digest(after), digest(before))
        operations.append({"id": name, "simulated_fault": "discarded_successful_return_after_commit",
            "response_sha256": digest(first), "replayed_response_sha256": digest(replay), "current": metrics(after)})
        return first

    with tempfile.TemporaryDirectory(prefix="creditlens-write-retry-") as directory, \
            patch("creditlens.research_task_store.uuid.uuid4", side_effect=lambda: uuid.UUID(int=next(counter))), \
            patch.object(ResearchTaskStore, "now", return_value="2026-10-26T08:00:00+00:00"):
        db_path = Path(directory)/"synthetic-research.sqlite"
        research = ResearchTaskStore(db_path, root); supplement = ExplanationReviewStore(research)
        request = {"question": "How do annual cash flow changes relate to the registered company statement?",
            "selection_reason": "Synthetic software replay of registered official facts; no customer or human annotation.",
            "include_cash_flow_bridge": True}
        task = research.create(analyst, request, "synthetic-parent-create")
        get_parent = lambda: research.get(task["id"], viewer)
        update1 = research_update(task)

        def fail_receipt_once(name, call, current):
            before = current()
            with research.db(write=True) as db:
                db.execute("CREATE TRIGGER synthetic_receipt_failure BEFORE INSERT ON research_write_receipts BEGIN SELECT RAISE(ABORT, 'synthetic receipt storage failure'); END")
            failure_status = None
            try: call()
            except HTTPException as exc: failure_status = exc.status_code
            except sqlite3.IntegrityError: failure_status = "uncaught_sqlite_error"
            finally:
                with research.db(write=True) as db: db.execute("DROP TRIGGER synthetic_receipt_failure")
            check(name+"_failure_observed", failure_status, 503)
            check(name+"_business_rolled_back", digest(current()), digest(before))

        save1 = lambda: research.update(task["id"], analyst, **update1, idempotency_key="synthetic-parent-save-one")
        fail_receipt_once("parent_receipt_failure", save1, get_parent)
        task = perform("parent_save", save1, get_parent)
        saved_response = deepcopy(task)
        refusal("viewer_cannot_replay_write", lambda: research.update(task["id"], viewer, **update1,
            idempotency_key="synthetic-parent-save-one"), 403)
        refusal("cross_tenant_cannot_replay_write", lambda: research.update(task["id"], other, **update1,
            idempotency_key="synthetic-parent-save-one"), 404)
        refusal("same_key_different_principal", lambda: research.update(task["id"], editor, **update1,
            idempotency_key="synthetic-parent-save-one"), 409)
        refusal("same_key_different_body", lambda: research.update(task["id"], analyst, **{**update1,"coverage_note":NOTE},
            idempotency_key="synthetic-parent-save-one"), 409)
        submit1 = action_body(task, "submit")
        with patch.object(research, "_material_bundle", side_effect=HTTPException(409,"Synthetic source unavailable")):
            check("old_save_replays_while_source_unavailable", digest(save1()), digest(saved_response))
            refusal("new_submit_stops_on_source_failure", lambda: research.act(task["id"], analyst, **submit1,
                idempotency_key="synthetic-parent-source-blocked"), 409)
            check("source_failure_did_not_modify_draft", digest(get_parent()), digest(task))
        # The earlier source failure committed neither business data nor a receipt.
        submit_call = lambda: research.act(task["id"], analyst, **submit1, idempotency_key="synthetic-parent-source-blocked")
        task = perform("parent_submit", submit_call, get_parent)
        old_submit = deepcopy(task)
        with patch.object(research, "_material_bundle", side_effect=HTTPException(409,"Synthetic source unavailable")):
            check("old_submit_replays_while_source_unavailable", digest(submit_call()), digest(old_submit))
        self_reviewer = Actor(analyst.user_id, analyst.tenant_id, "reviewer")
        refusal("principal_cannot_review_own_task", lambda: research.act(task["id"], self_reviewer,
            **action_body(task,"approve"),idempotency_key="synthetic-parent-self-review"),403)
        returned_body = action_body(task,"return")
        task = perform("parent_return", lambda: research.act(task["id"],reviewer,**returned_body,
            idempotency_key="synthetic-parent-return-one"),get_parent)
        old_parent_id=task["snapshot"]["id"]; old_parent_export=research.export(task["id"],viewer,old_parent_id)
        update2=research_update(task," Revised coverage in a new snapshot.")
        task=perform("parent_resave",lambda:research.update(task["id"],analyst,**update2,
            idempotency_key="synthetic-parent-save-two"),get_parent)
        submit2=action_body(task,"submit")
        task=perform("parent_resubmit",lambda:research.act(task["id"],analyst,**submit2,
            idempotency_key="synthetic-parent-submit-two"),get_parent)
        approval=action_body(task,"approve")
        task=perform("parent_approve",lambda:research.act(task["id"],reviewer,**approval,
            idempotency_key="synthetic-parent-approve"),get_parent)
        parent_final=research.export(task["id"],viewer)
        check("parent_old_export_unchanged",digest(research.export(task["id"],viewer,old_parent_id)),digest(old_parent_export))
        check("parent_old_save_response_not_upgraded",digest(save1()),digest(saved_response))
        check("parent_old_submit_response_not_upgraded",digest(submit_call()),digest(old_submit))
        check("parent_current_revision_not_rewound",metrics(get_parent()),{"revision":7,"status":"reviewed","events":7,"snapshots":2})

        review=supplement.create(task["id"],editor,{"parent_revision":task["revision"],
            "parent_snapshot_sha256":task["snapshot_sha256"]},"synthetic-child-create")
        get_child=lambda:supplement.get(review["id"],viewer)
        child_update1=explanation_update(review)
        child_save=lambda:supplement.update(review["id"],editor,**child_update1,idempotency_key="synthetic-child-save-one")
        fail_receipt_once("child_receipt_failure",child_save,get_child)
        review=perform("child_save",child_save,get_child); saved_child=deepcopy(review)
        refusal("cross_entity_key_conflict",lambda:supplement.update(review["id"],editor,**child_update1,
            idempotency_key="synthetic-parent-save-one"),409)
        child_submit1=action_body(review,"submit")
        with patch.object(supplement,"_build_evidence",side_effect=HTTPException(409,"Synthetic registration unavailable")):
            check("old_child_save_replays_while_source_unavailable",digest(child_save()),digest(saved_child))
            refusal("new_child_submit_stops_on_source_failure",lambda:supplement.act(review["id"],editor,**child_submit1,
                idempotency_key="synthetic-child-source-blocked"),409)
            check("child_source_failure_did_not_modify_draft",digest(get_child()),digest(review))
        child_submit_call=lambda:supplement.act(review["id"],editor,**child_submit1,idempotency_key="synthetic-child-source-blocked")
        review=perform("child_submit",child_submit_call,get_child); submitted_child=deepcopy(review)
        child_return=action_body(review,"return")
        review=perform("child_return",lambda:supplement.act(review["id"],reviewer,**child_return,
            idempotency_key="synthetic-child-return-one"),get_child)
        old_child_id=review["snapshot"]["id"]; old_child_export=supplement.export(review["id"],viewer,old_child_id)
        child_update2=explanation_update(review,"adopt_as_company_statement")
        review=perform("child_resave",lambda:supplement.update(review["id"],editor,**child_update2,
            idempotency_key="synthetic-child-save-two"),get_child)
        child_submit2=action_body(review,"submit")
        review=perform("child_resubmit",lambda:supplement.act(review["id"],editor,**child_submit2,
            idempotency_key="synthetic-child-submit-two"),get_child)
        child_approve=action_body(review,"approve")
        review=perform("child_approve",lambda:supplement.act(review["id"],reviewer,**child_approve,
            idempotency_key="synthetic-child-approve"),get_child)
        check("child_old_export_unchanged",digest(supplement.export(review["id"],viewer,old_child_id)),digest(old_child_export))
        check("child_old_save_response_not_upgraded",digest(child_save()),digest(saved_child))
        check("child_old_submit_response_not_upgraded",digest(child_submit_call()),digest(submitted_child))
        check("child_current_revision_not_rewound",metrics(get_child()),{"revision":7,"status":"reviewed","events":7,"snapshots":2})
        check("parent_export_unchanged_by_child",digest(research.export(task["id"],viewer)),digest(parent_final))
        check("approval_does_not_prove_causes",review["independently_verified"],False)

        before=get_parent()
        with research.db(write=True) as db:
            stored=db.execute("SELECT response FROM research_write_receipts WHERE tenant=? AND request_key=?",
                (analyst.tenant_id,"synthetic-parent-save-one")).fetchone()[0]
            db.execute("UPDATE research_write_receipts SET response='{}' WHERE tenant=? AND request_key=?",
                (analyst.tenant_id,"synthetic-parent-save-one"))
        refusal("corrupt_receipt_fails_closed",save1,409)
        check("corrupt_receipt_does_not_repeat_business_write",digest(get_parent()),digest(before))
        with research.db(write=True) as db:
            db.execute("UPDATE research_write_receipts SET response=? WHERE tenant=? AND request_key=?",
                (stored,analyst.tenant_id,"synthetic-parent-save-one"))
            count=db.execute("SELECT count(*) FROM research_write_receipts").fetchone()[0]
        check("exactly_one_receipt_per_successful_write",count,12)
        rebuilt=ResearchTaskStore(db_path,root); rebuilt_child=ExplanationReviewStore(rebuilt)
        check("reconstructed_store_replays_parent",digest(rebuilt.update(task["id"],analyst,**update1,
            idempotency_key="synthetic-parent-save-one")),digest(saved_response))
        check("reconstructed_store_replays_child",digest(rebuilt_child.act(review["id"],editor,**child_submit1,
            idempotency_key="synthetic-child-source-blocked")),digest(submitted_child))
        snapshots={"parent_previous":digest(old_parent_export),"parent_current":digest(parent_final),
            "child_previous":digest(old_child_export),"child_current":digest(supplement.export(review["id"],viewer))}
        sources={"bundle_sha256":task["bundle_sha256"],"cash_flow_bridge_sha256":task["cash_flow_bridge_sha256"],
            "explanation_evidence_sha256":review["evidence_sha256"]}
    code_hashes={name:sha256((root/name).read_text(encoding="utf8").replace("\r\n","\n").replace("\r","\n").encode("utf8")).hexdigest()
                 for name in IMPLEMENTATION_FILES}
    return {"schema_version":1,"experiment":"research-write-retry-1","source_bindings":sources,
        "implementation_hash_basis":"python_utf8_lf","implementation_sha256":code_hashes,
        "operations":operations,"checks":checks,"summary":{"checks":len(checks),"passed":sum(c["passed"] for c in checks),
            "failed":sum(not c["passed"] for c in checks),"successful_write_intents":12,"persisted_receipts":12,
            "research_snapshots":2,"explanation_snapshots":2},"export_sha256":snapshots,
        "faults":[
            {"type":"discarded_successful_return_after_commit","mechanism":"Caller intentionally discards the completed return value; no live network interruption"},
            {"type":"receipt_insert_failure","mechanism":"Temporary SQLite BEFORE INSERT trigger aborts receipt storage"},
            {"type":"source_registration_unavailable","mechanism":"Temporary source builder raises a fixed 409; registered files are unchanged"},
            {"type":"receipt_content_corruption","mechanism":"Temporary database receipt response changed without its checksum, then restored"}],
        "scope":{"faults":"synthetic_controls_only","principals":"synthetic",
            "official_materials":"registered_current_disclosures","real_network_disruption":False,
            "fixed_ids_and_clock":"temporary_replay_only","main_database_modified":False,"raw_ocr_used":False,
            "new_ocr_calls":0,"model_calls":0,"network_calls":0,"independent_human_gold":0,"automatic_approval":False},
        "limitations":["只验证已提交写入的回执恢复，不承诺未提交请求已经受理，也不模拟后台队列恢复。",
            "旧响应可能早于当前版本；重放旧响应不会回退服务端，也不能在界面上冒充最新状态。",
            "哈希检测存储不一致，不抵抗能同时改写业务、回执及所有摘要的数据库管理员。",
            "身份和故障均为模拟，官方财务材料复用现有登记；不证明真实网络故障、用户效率或金融因果。"]}


def validate_replay(root:Path, report:dict)->dict:
    if digest(build_replay(root)) != digest(report): raise ValueError("Research write replay differs")
    return deepcopy(report)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,help="New JSON directly inside .run/reproductions")
    parser.add_argument("--verify-saved",action="store_true")
    args=parser.parse_args(argv)
    if args.verify_saved and args.output: parser.error("--verify-saved cannot write output")
    try:
        target=output_path(ROOT,args.output) if args.output else None
        report=build_replay(ROOT)
        if args.verify_saved:
            saved=json.loads((ROOT/REPORT).read_text(encoding="utf8"))
            if digest(saved)!=digest(report): raise ValueError("Saved replay differs")
        if target:
            target=output_path(ROOT,target); target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf8",newline="\n") as stream:
                json.dump(report,stream,ensure_ascii=False,separators=(",",":"),allow_nan=False); stream.write("\n")
        print(json.dumps({"verified":True,"summary":report["summary"],"real_network_disruption":False},ensure_ascii=False))
        return 0
    except (ValueError,OSError,HTTPException,sqlite3.Error):
        print(json.dumps({"verified":False,"code":"write_retry_replay_failed"})); return 1


if __name__=="__main__":raise SystemExit(main())
