"""Rebuild local app/store instances and replay writes against isolated SQLite.

This exercises application lifecycles in one Python process, not an OS crash,
network interruption, backup restore or asynchronous worker recovery.
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

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from fastapi.testclient import TestClient
from creditlens import api
from creditlens.diligence import digest
from creditlens.research_task_store import ResearchTaskStore
from scripts.evaluate_row_disambiguation import output_path

VERSION="research-restart-replay-1"
REPORT="artifacts/research-restart-report.json"
NOTE="Synthetic restart validation; financial explanations remain independently unverified."
SOURCE_FILES=("data/examples/msft-fy2025-pdf.json","data/examples/aapl-fy2025-pdf.json",
    "data/research/cash-flow-bridge-v1.json","data/sources/company-attributions.json")
IMPLEMENTATION_FILES=("creditlens/api.py","creditlens/workflow.py","creditlens/research_task_store.py",
    "creditlens/explanation_review_store.py","creditlens/research_write_receipts.py","creditlens/explanation_evidence.py",
    "creditlens/research_acceptance.py","creditlens/research_materials.py","creditlens/company_attribution.py",
    "creditlens/cash_flow_bridge.py","creditlens/cash_flow_sources.py","creditlens/diligence.py",
    "scripts/replay_research_restart.py")
TABLES=("research_tasks","research_snapshots","explanation_reviews","explanation_review_snapshots","research_write_receipts")
USERS=(("author","synthetic-restart-author","analyst"),("editor","synthetic-restart-editor","analyst"),
       ("reviewer","synthetic-restart-reviewer","reviewer"),("viewer","synthetic-restart-viewer","viewer"))


def file_hash(path):
    return sha256(Path(path).read_text(encoding="utf8").replace("\r\n","\n").replace("\r","\n").encode()).hexdigest()


def bindings(root):
    return {"implementation_hash_basis":"python_utf8_lf","source_file_hash_basis":"utf8_lf",
            "implementation_sha256":{p:file_hash(root/p) for p in IMPLEMENTATION_FILES},
            "source_file_sha256":{p:file_hash(root/p) for p in SOURCE_FILES}}


def save_body(record,child=False):
    if child:
        return {"revision":record["revision"],"decisions":[{"entry_id":e["id"],"decision":"needs_evidence","note":NOTE}
            for e in record["evidence"]["attribution"]["entries"]],"coverage_note":"Company statement only; independent causal evidence remains missing."}
    return {"revision":record["revision"],"decisions":[{"observation_id":o["id"],"decision":"adopted","note":NOTE}
        for o in record["bundle"]["observations"]],"hypotheses":[],
        "coverage_note":"Current registered comparative periods only; historical availability has not been established.",
        "bridge_review_note":"Accounting reconciliation only; no independent causal conclusion."}


def action_body(record,action):
    return {"revision":record["revision"],"action":action,"note":NOTE,
            "snapshot_sha256":record["snapshot_sha256"] if record.get("snapshot") else None}


def metrics(record):
    return {"revision":record["revision"],"status":record["status"],"events":len(record["events"]),"snapshots":len(record["snapshots"])}


class RestartHarness:
    """Own only the temporary app, temporary registered copies and temporary DB."""
    def __init__(self,directory):
        self.directory=Path(directory);self.client=None;self.app=None;self.generation=0;self.restarts=[]
        self.principals=[{"key_sha256":sha256(("synthetic-"+key).encode()).hexdigest(),"user_id":user,
            "tenant_id":"synthetic-restart-tenant","role":role} for key,user,role in USERS]
        self.start()

    def start(self):
        self.app=api.create_app(self.directory/"unused-dataset.json",self.directory/"audit.sqlite",
            auth_mode="api_key",principals=self.principals)
        self.client=TestClient(self.app,raise_server_exceptions=False)
        self.client.__enter__();self.generation+=1

    def close(self):
        if self.client is not None:
            self.client.__exit__(None,None,None);self.client.close()
            self.client=None;self.app=None

    def restart(self,label):
        previous_app=self.app;previous_store=self.app.state.research_tasks;previous_child=self.app.state.explanation_reviews
        with previous_store.db() as previous_connection:
            previous_connection.execute("SELECT 1").fetchone()
        try: previous_connection.execute("SELECT 1")
        except sqlite3.ProgrammingError: connection_closed=True
        else: connection_closed=False
        previous_client=self.client
        self.close();self.start()
        proof={"id":label,"previous_generation":self.generation-1,"current_generation":self.generation,
            "previous_client_closed":previous_client.is_closed,"prior_sql_connection_closed":connection_closed,
            "new_application_instance":self.app is not previous_app,
            "new_research_store_instance":self.app.state.research_tasks is not previous_store,
            "new_explanation_store_instance":self.app.state.explanation_reviews is not previous_child}
        if not all(proof[k] for k in proof if k.endswith("closed") or k.endswith("instance")):
            raise ValueError("Application reconstruction did not close and replace resources")
        self.restarts.append(proof)

    def request(self,method,route,body=None,key=None,user="author"):
        headers={"Authorization":"Bearer synthetic-"+user}
        if key is not None:headers["Idempotency-Key"]=key
        return self.client.request(method,route,headers=headers,json=body)

    def get(self,route):
        response=self.request("GET",route,user="viewer")
        if response.status_code!=200:raise ValueError("Temporary record read failed")
        return response

    def database(self):
        with self.app.state.research_tasks.db() as db:
            content={table:[list(row) for row in db.execute("SELECT * FROM "+table+" ORDER BY "+
                     ("tenant,request_key" if table=="research_write_receipts" else "id")).fetchall()] for table in TABLES}
        return {"sha256":digest(content),"counts":{table:len(rows) for table,rows in content.items()}}


def build_replay(root:Path)->dict:
    root=Path(root).resolve();initial_bindings=bindings(root)
    checks=[];operations=[];late_replays=[];faults=[]
    def check(key,actual,expected):
        passed=digest(actual)==digest(expected)
        checks.append({"id":key,"actual":actual,"expected":expected,"passed":passed})
        if not passed:raise ValueError("Restart replay check failed: "+key)
    def successful(response,status=200):
        if response.status_code!=status:raise ValueError("Temporary workflow setup or write failed")
        return response.json()
    counter=iter(range(1,10000))
    with tempfile.TemporaryDirectory(prefix="creditlens-restart-") as directory, \
            patch("creditlens.research_task_store.uuid.uuid4",side_effect=lambda:uuid.UUID(int=next(counter))), \
            patch.object(ResearchTaskStore,"now",return_value="2026-10-26T11:00:00+00:00"):
        temp=Path(directory);sources=temp/"registered"
        for relative in SOURCE_FILES:
            target=sources/relative;target.parent.mkdir(parents=True,exist_ok=True)
            # Stable temporary copies of repository text, not PDF response bytes.
            target.write_bytes((root/relative).read_text(encoding="utf8").replace("\r\n","\n").replace("\r","\n").encode())
        with patch.object(api,"ROOT",sources):
            server=RestartHarness(temp)
            try:
                def create_parent(key):
                    return successful(server.request("POST","/api/research/tasks",{
                        "question":"Can a completed research intent be recovered after rebuilding the local service?",
                        "selection_reason":"Synthetic application-lifecycle control using registered materials.",
                        "include_cash_flow_bridge":True},key),201)
                def create_child(parent,key):
                    return successful(server.request("POST","/api/research/tasks/"+parent["id"]+"/explanation-reviews",{
                        "parent_revision":parent["revision"],"parent_snapshot_sha256":parent["snapshot_sha256"]},key,"editor"),201)
                def replay_after_restart(label,method,route,body,key,user,expected_metrics):
                    first=server.request(method,route,body,key,user);record=successful(first)
                    base=route.removesuffix("/actions")
                    before=server.get(base);db_before=server.database()
                    server.restart(label)
                    replay=server.request(method,route,deepcopy(body),key,user)
                    check(label+"_status",replay.status_code,200)
                    check(label+"_same_http_bytes",sha256(replay.content).hexdigest(),sha256(first.content).hexdigest())
                    after=server.get(base)
                    check(label+"_current_record_unchanged",sha256(after.content).hexdigest(),sha256(before.content).hexdigest())
                    check(label+"_database_unchanged",server.database(),db_before)
                    check(label+"_one_business_transition",metrics(after.json()),expected_metrics)
                    operation={"id":label,"method":method,"route":route,"principal":user,"idempotency_key":key,
                        "body":body,"request_sha256":digest({"method":method,"route":route,"principal":user,"key":key,"body":body}),
                        "first_http_status":first.status_code,"replayed_http_status":replay.status_code,
                        "first_response_sha256":sha256(first.content).hexdigest(),"replayed_response_sha256":sha256(replay.content).hexdigest(),
                        "before_state_sha256":sha256(before.content).hexdigest(),"after_state_sha256":sha256(after.content).hexdigest(),
                        "database_before":db_before,"database_after":server.database(),"current":metrics(after.json()),
                        "restarted_generation":server.generation,"passed":True}
                    operations.append(operation)
                    return record,operation

                parent=create_parent("restart-parent-create");parent_route="/api/research/tasks/"+parent["id"]
                parent,p_save=replay_after_restart("research_patch","PATCH",parent_route,save_body(parent),"restart-parent-save","author",
                    {"revision":2,"status":"draft","events":2,"snapshots":0})
                parent,p_submit=replay_after_restart("research_action","POST",parent_route+"/actions",action_body(parent,"submit"),"restart-parent-submit","author",
                    {"revision":3,"status":"in_review","events":3,"snapshots":1})
                parent=successful(server.request("POST",parent_route+"/actions",action_body(parent,"approve"),user="reviewer"))
                parent_export=server.get(parent_route+"/export").content
                child=create_child(parent,"restart-child-create");child_route="/api/research/explanation-reviews/"+child["id"]
                child,c_save=replay_after_restart("explanation_patch","PATCH",child_route,save_body(child,True),"restart-child-save","editor",
                    {"revision":2,"status":"draft","events":2,"snapshots":0})
                child,c_submit=replay_after_restart("explanation_action","POST",child_route+"/actions",action_body(child,"submit"),"restart-child-submit","editor",
                    {"revision":3,"status":"in_review","events":3,"snapshots":1})
                child=successful(server.request("POST",child_route+"/actions",action_body(child,"approve"),user="reviewer"))
                child_export=server.get(child_route+"/export").content
                server.restart("after_other_principal_approved")
                for operation in operations:
                    route=operation["route"].removesuffix("/actions");current=server.get(route);before=server.database()
                    replay=server.request(operation["method"],operation["route"],operation["body"],operation["idempotency_key"],operation["principal"])
                    check(operation["id"]+"_late_replay_bytes",sha256(replay.content).hexdigest(),operation["first_response_sha256"])
                    check(operation["id"]+"_new_get_not_rewound",server.get(route).content==current.content,True)
                    check(operation["id"]+"_late_database_unchanged",server.database(),before)
                    late_replays.append({"operation_id":operation["id"],"replayed_revision":replay.json()["revision"],
                        "current_revision":current.json()["revision"],"current_status":current.json()["status"],
                        "response_sha256":sha256(replay.content).hexdigest(),"current_get_sha256":sha256(current.content).hexdigest(),
                        "database_before":before,"database_after":server.database(),"passed":True})
                check("late_replays_are_older_than_current_get",all(c["replayed_revision"]<c["current_revision"] for c in late_replays),True)

                # Actual edits affect only copied registered JSON, then are restored.
                for label,is_child,relative,old_operation in (
                    ("research_source_drift",False,SOURCE_FILES[0],p_submit),
                    ("explanation_source_drift",True,SOURCE_FILES[3],c_submit)):
                    record=create_child(parent,label+"-create") if is_child else create_parent(label+"-create")
                    route=("/api/research/explanation-reviews/" if is_child else "/api/research/tasks/")+record["id"]
                    user="editor" if is_child else "author"
                    record=successful(server.request("PATCH",route,save_body(record,is_child),user=user))
                    body=action_body(record,"submit");new_key=label+"-new-submit"
                    before=server.get(route).content;before_db=server.database()
                    path=sources/relative;original=path.read_bytes();changed=json.loads(original.decode("utf8"))
                    if is_child:changed["entries"][0]["paraphrase_zh"]+=" Synthetic changed registration."
                    else:changed["facts"][0]["raw_value"]="999999"
                    changed_raw=json.dumps(changed,ensure_ascii=False,allow_nan=False).encode("utf8");path.write_bytes(changed_raw)
                    server.restart(label)
                    old=server.request(old_operation["method"],old_operation["route"],old_operation["body"],old_operation["idempotency_key"],old_operation["principal"])
                    check(label+"_completed_receipt_readable",sha256(old.content).hexdigest(),old_operation["first_response_sha256"])
                    failed=server.request("POST",route+"/actions",body,new_key,user)
                    check(label+"_new_write_blocked",failed.status_code,409)
                    detail=failed.json()["detail"]
                    check(label+"_failure_is_source_validation",detail.get("code") if isinstance(detail,dict) else detail,
                        "explanation_evidence_unavailable" if is_child else "Cash flow attachment cannot be verified against the task material")
                    failure_after=server.get(route).content;failure_database=server.database()
                    check(label+"_draft_unchanged",sha256(failure_after).hexdigest(),sha256(before).hexdigest())
                    check(label+"_no_receipt_or_snapshot",failure_database,before_db)
                    path.write_bytes(original);server.restart(label+"_restored")
                    restored=server.request("POST",route+"/actions",body,new_key,user);successful(restored)
                    check(label+"_same_pending_request_can_complete",metrics(restored.json()),{"revision":3,"status":"in_review","events":3,"snapshots":1})
                    faults.append({"id":label,"mechanism":"modified_temporary_registered_json_then_rebuilt_application",
                        "source_file":relative,"original_sha256":sha256(original).hexdigest(),"synthetic_changed_sha256":sha256(changed_raw).hexdigest(),
                        "restored_sha256":sha256(path.read_bytes()).hexdigest(),"new_request_sha256":digest({"body":body,"key":new_key}),
                        "expected_status":409,"actual_status":failed.status_code,"recovery_status":restored.status_code,
                        "before_state_sha256":sha256(before).hexdigest(),"failure_after_state_sha256":sha256(failure_after).hexdigest(),
                        "database_before":before_db,"database_after_failure":failure_database,
                        "recovery_response_sha256":sha256(restored.content).hexdigest(),"passed":True})

                # Close an actually open uncommitted SQL connection. SQLite rolls
                # it back; this deliberately does not kill a process or machine.
                pending=create_parent("restart-uncommitted-create");pending_route="/api/research/tasks/"+pending["id"]
                save=save_body(pending);save_key="restart-uncommitted-save"
                before=server.get(pending_route).content;db_before=server.database()
                connection=sqlite3.connect(temp/"research-tasks.sqlite")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("UPDATE research_tasks SET revision=777 WHERE id=?",(pending["id"],))
                connection.execute("INSERT INTO research_write_receipts VALUES (?,?,?,?,?,?,?)",
                    ("synthetic-restart-tenant",save_key,"{}","0"*64,"{}","0"*64,"0"*64))
                check("uncommitted_transaction_was_open",connection.in_transaction,True)
                connection.close();server.restart("closed_uncommitted_connection")
                rollback_db=server.database()
                check("uncommitted_transaction_rolled_back",rollback_db,db_before)
                check("uncommitted_task_readable",sha256(server.get(pending_route).content).hexdigest(),sha256(before).hexdigest())
                saved=server.request("PATCH",pending_route,save,save_key);pending=successful(saved)
                check("uncommitted_key_not_reserved",metrics(pending),{"revision":2,"status":"draft","events":2,"snapshots":0})
                faults.append({"id":"closed_uncommitted_connection","mechanism":"SQLite_BEGIN_without_commit_then_explicit_connection_close",
                    "request_sha256":digest({"body":save,"key":save_key}),"database_before":db_before,
                    "database_after_rollback":rollback_db,"recovery_response_sha256":sha256(saved.content).hexdigest(),"passed":True})

                # A committed trigger induces receipt failure during an actual
                # HTTP submission. Its business transaction must also roll back.
                submit=action_body(pending,"submit");submit_key="restart-transaction-submit"
                before=server.get(pending_route).content;db_before=server.database()
                with server.app.state.research_tasks.db(write=True) as connection:
                    connection.execute("CREATE TRIGGER restart_receipt_failure BEFORE INSERT ON research_write_receipts BEGIN SELECT RAISE(ABORT,'synthetic restart failure'); END")
                failed=server.request("POST",pending_route+"/actions",submit,submit_key)
                check("receipt_failure_http_status",failed.status_code,503)
                check("receipt_failure_code",failed.json()["detail"]["code"],"write_receipt_storage_failed")
                server.restart("receipt_transaction_rolled_back")
                rollback_db=server.database()
                check("receipt_failure_rebuilt_database_unchanged",rollback_db,db_before)
                check("receipt_failure_rebuilt_task_unchanged",sha256(server.get(pending_route).content).hexdigest(),sha256(before).hexdigest())
                with server.app.state.research_tasks.db(write=True) as connection:connection.execute("DROP TRIGGER restart_receipt_failure")
                restored=server.request("POST",pending_route+"/actions",submit,submit_key);successful(restored)
                server.restart("receipt_recovery_committed")
                repeated=server.request("POST",pending_route+"/actions",submit,submit_key)
                check("receipt_failure_recovery_survives_restart",sha256(repeated.content).hexdigest(),sha256(restored.content).hexdigest())
                check("receipt_failure_one_recovered_snapshot",metrics(server.get(pending_route).json()),{"revision":3,"status":"in_review","events":3,"snapshots":1})
                faults.append({"id":"receipt_transaction_rolled_back","mechanism":"temporary_SQLite_trigger_aborts_receipt_insert_then_application_rebuilt",
                    "request_sha256":digest({"body":submit,"key":submit_key}),"expected_status":503,"actual_status":failed.status_code,
                    "database_before":db_before,"database_after_rollback":rollback_db,"recovery_response_sha256":sha256(restored.content).hexdigest(),
                    "post_restart_replay_sha256":sha256(repeated.content).hexdigest(),"passed":True})
                check("original_parent_export_unchanged",sha256(server.get(parent_route+"/export").content).hexdigest(),sha256(parent_export).hexdigest())
                check("original_supplement_export_unchanged",sha256(server.get(child_route+"/export").content).hexdigest(),sha256(child_export).hexdigest())
                final_db=server.database()
                check("one_receipt_per_completed_keyed_intent",final_db["counts"]["research_write_receipts"],8)
                check("research_snapshot_inventory",final_db["counts"]["research_snapshots"],3)
                check("supplement_snapshot_inventory",final_db["counts"]["explanation_review_snapshots"],2)
                export_hashes={"research":sha256(parent_export).hexdigest(),"explanation":sha256(child_export).hexdigest()}
                generations=server.generation;restarts=deepcopy(server.restarts)
                check("application_instances_actually_rebuilt",[generations,len(restarts)],[13,12])
            finally:server.close()
    if bindings(root)!=initial_bindings:raise ValueError("Implementation or source changed during replay")
    return {"schema_version":1,"experiment_version":VERSION,**initial_bindings,
        "operations":operations,"late_replays":late_replays,"faults":faults,"restarts":restarts,"checks":checks,
        "export_sha256":export_hashes,"final_database":final_db,
        "summary":{"write_path_classes":len(operations),"late_replays":len(late_replays),"fault_scenarios":len(faults),
            "application_instances":generations,"application_rebuilds":len(restarts),"successful_keyed_write_intents":8,
            "persisted_receipts":final_db["counts"]["research_write_receipts"],"checks":len(checks),
            "passed":sum(c["passed"] for c in checks),"failed":sum(not c["passed"] for c in checks)},
        "scope":{"principals":"synthetic_api_key_identities","financial_materials":"registered_fixture_only",
            "same_python_process":True,"application_lifespans_rebuilt":True,"database_connections_reopened":True,
            "real_os_crash":False,"real_network_fault":False,"worker_or_queue_recovery":False,"browser_reload_evaluated":False,
            "main_database_modified":False,"independent_human_gold":0,"model_calls":0,"ocr_calls":0,"network_calls":0,
            "temporary_source_files_only":True,"temporary_source_copy_encoding":"utf8_lf",
            "fixed_clock_and_ids":"isolated_replay_only","automatic_approval":False},
        "limitations":["新建应用和Store实例并重新打开SQLite连接，同一Python进程内完成；不等于操作系统或机器崩溃测试。",
            "成功回执保存于业务事务；未提交事务可以回滚，不代表此前请求已经受理。",
            "重放回执可能属于旧版本，必须与当前GET区分；本脚本不验证浏览器刷新逻辑。",
            "来源漂移仅改写临时复制的小型JSON；未改官方原文、主数据库或冻结报告。",
            "全部身份和故障均为合成软件控制，不是客户效果、金融准确率或独立人工标注。"],
        "automatic_approval":False}


def validate_replay(root:Path,report:dict)->dict:
    if type(report) is not dict or digest(build_replay(root))!=digest(report):raise ValueError("Research restart replay differs")
    return deepcopy(report)


def read_report(path):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError("Duplicate report key")
            result[key]=value
        return result
    with Path(path).open("rb") as stream:raw=stream.read(1_000_001)
    if len(raw)>1_000_000:raise ValueError("Restart report exceeds limit")
    report=json.loads(raw.decode("utf8"),object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(ValueError("Nonfinite number")))
    if type(report) is not dict:raise ValueError("Report must be an object")
    json.dumps(report,ensure_ascii=False,allow_nan=False).encode("utf8")
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group()
    group.add_argument("--output",type=Path,help="New JSON directly inside .run/reproductions")
    group.add_argument("--verify-saved",action="store_true")
    args=parser.parse_args(argv)
    try:
        target=output_path(ROOT,args.output) if args.output else None
        report=validate_replay(ROOT,read_report(ROOT/REPORT)) if args.verify_saved else build_replay(ROOT)
        if target is not None:
            target=output_path(ROOT,target);target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf8",newline="\n") as stream:
                json.dump(report,stream,ensure_ascii=False,separators=(",",":"),allow_nan=False);stream.write("\n")
        print(json.dumps({"verified":True,"summary":report["summary"],"real_os_crash":False},ensure_ascii=False));return 0
    except (ValueError,OSError,sqlite3.Error):
        print(json.dumps({"verified":False,"code":"research_restart_replay_failed"}));return 1


if __name__=="__main__":raise SystemExit(main())
