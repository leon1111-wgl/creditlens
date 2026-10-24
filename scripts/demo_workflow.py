"""Reproduce the review workflow using explicitly simulated identities."""
from datetime import date
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import CasePack
from creditlens.workflow import Actor, WorkflowService, render_memo

pack = CasePack.model_validate_json((ROOT / "data/examples/msft-fy2025.json").read_text(encoding="utf-8"))
with tempfile.TemporaryDirectory() as temp:
    svc = WorkflowService(Path(temp) / "workflow.sqlite")
    analyst, reviewer = Actor("simulated-analyst", "demo", "analyst"), Actor("simulated-reviewer", "demo", "reviewer")
    data = svc.create(analyst, pack, date(2025, 8, 1), 20, "reproducible-demo")
    for issue in data["analysis"]["issues"]:
        data = svc.update(data["id"], analyst, data["revision"], "disposition", issue_id=issue["id"],
                          disposition="accepted_limitation", note="演示记录：已核对官网表格，现金可用性、租赁和资本承诺需补充原文；本演示不作授信结论。")
    data = svc.update(data["id"], analyst, data["revision"], "submit")
    data = svc.update(data["id"], reviewer, data["revision"], "approve",
                      note="自动化演示身份：确认规则计算和来源引用流程运行，尚未进行真实银行分析员与复核员验收。")
    (ROOT / "artifacts/workflow-demo.json").write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    (ROOT / "artifacts/review-memo.md").write_text(render_memo(data), encoding="utf-8")
    print(json.dumps({"status": data["status"], "revision": data["revision"], "events": len(data["events"]),
                      "event_integrity_valid": data["event_integrity_valid"], "identities": "simulated"}))
