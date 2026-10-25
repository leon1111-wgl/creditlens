"""Tenant-scoped credit review workflow with optimistic revisions and event receipts."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import secrets
import sqlite3
import uuid

from fastapi import HTTPException, Request
from .diligence import CasePack, analyze_pack, digest, RULE_VERSION


@dataclass(frozen=True)
class Actor:
    user_id: str
    tenant_id: str
    role: str


class AccessControl:
    """Demo identities are explicitly simulated; API-key mode fails closed."""
    def __init__(self, mode: str | None = None, principals: list | None = None):
        self.mode = mode or os.getenv("CREDITLENS_AUTH_MODE", "demo")
        if self.mode not in {"demo", "api_key"}:
            raise ValueError("CREDITLENS_AUTH_MODE must be demo or api_key")
        self.principals = principals if principals is not None else json.loads(os.getenv("CREDITLENS_PRINCIPALS", "[]"))
        if self.mode == "api_key" and not self.principals:
            raise ValueError("API-key mode requires configured principals")
        for p in self.principals:
            if (set(p) != {"key_sha256", "user_id", "tenant_id", "role"}
                    or p["role"] not in {"viewer", "analyst", "reviewer"}
                    or len(p["key_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in p["key_sha256"])
                    or not p["user_id"] or not p["tenant_id"]):
                raise ValueError("Invalid principal configuration")
        if len({p["key_sha256"] for p in self.principals}) != len(self.principals):
            raise ValueError("Duplicate API key digest")

    def resolve(self, request: Request) -> Actor:
        if self.mode == "demo":
            role = request.headers.get("X-Demo-Role", "analyst")
            if role not in {"viewer", "analyst", "reviewer"}:
                raise HTTPException(401, "Unknown demo identity")
            return Actor("demo-" + role, "public-demo", role)
        token = request.headers.get("Authorization", "")
        if not token.startswith("Bearer "):
            raise HTTPException(401, "Bearer token required")
        hashed = hashlib.sha256(token[7:].encode()).hexdigest()
        for principal in self.principals:
            if secrets.compare_digest(principal["key_sha256"], hashed):
                return Actor(principal["user_id"], principal["tenant_id"], principal["role"])
        raise HTTPException(401, "Invalid API key")


def require(actor: Actor, role: str):
    if actor.role != role:
        raise HTTPException(403, "This action requires role: " + role)


class WorkflowService:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
                    UNIQUE(tenant, request_key));
                CREATE TABLE IF NOT EXISTS events (
                    case_id TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(case_id, sequence));
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    def _load(self, db, case_id, actor):
        row = db.execute("SELECT payload FROM reviews WHERE id=? AND tenant=?", (case_id, actor.tenant_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Review case not found")
        return json.loads(row[0])

    def _event(self, db, data, actor, action, note):
        row = db.execute("SELECT sequence,payload FROM events WHERE case_id=? ORDER BY sequence DESC LIMIT 1", (data["id"],)).fetchone()
        event = {"sequence": row[0]+1 if row else 1, "case_id": data["id"], "tenant": actor.tenant_id,
                 "actor": actor.user_id, "role": actor.role, "action": action, "note": note,
                 "created_at": self.now(), "revision": data["revision"],
                 "snapshot_sha256": digest(data), "previous_hash": json.loads(row[1])["hash"] if row else "0"*64}
        event["hash"] = digest(event)
        db.execute("INSERT INTO events VALUES (?,?,?)", (data["id"], event["sequence"], json.dumps(event, ensure_ascii=False)))

    def create(self, actor, pack: CasePack, as_of: date, stress_pct: int, request_key: str, source_status="user_supplied"):
        require(actor, "analyst")
        request_hash = digest({"pack": pack.model_dump(mode="json"), "as_of": str(as_of), "stress": stress_pct, "actor": actor.user_id})
        with self.db(write=True) as db:
            row = db.execute("SELECT payload,request_hash FROM reviews WHERE tenant=? AND request_key=?", (actor.tenant_id, request_key)).fetchone()
            if row:
                if row[1] != request_hash:
                    raise HTTPException(409, "Idempotency key was already used with a different request")
                return json.loads(row[0])
            data = {"id": uuid.uuid4().hex, "tenant": actor.tenant_id, "revision": 1, "status": "draft",
                    "created_at": self.now(), "created_by": actor.user_id,
                    "pack": pack.model_dump(mode="json"), "pack_sha256": digest(pack.model_dump(mode="json")),
                    "source_status": source_status, "analysis": analyze_pack(pack, as_of, stress_pct),
                    "dispositions": {}, "review": None}
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?)",
                       (data["id"], actor.tenant_id, 1, json.dumps(data, ensure_ascii=False), request_key, request_hash))
            self._event(db, data, actor, "created", "Source pack and rule output captured")
        return data

    def list(self, actor):
        with self.db() as db:
            rows = db.execute("SELECT payload FROM reviews WHERE tenant=? ORDER BY rowid DESC LIMIT 100", (actor.tenant_id,)).fetchall()
        return [{"id": d["id"], "company": d["pack"]["company"], "title": d["pack"]["title"],
                 "status": d["status"], "revision": d["revision"], "created_at": d["created_at"],
                 "issue_count": len(d["analysis"]["issues"])} for d in (json.loads(r[0]) for r in rows)]

    def get(self, case_id, actor):
        with self.db() as db:
            data = self._load(db, case_id, actor)
            events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE case_id=? ORDER BY sequence", (case_id,))]
        return {**data, "events": events, "event_integrity_valid": self.integrity(data, events),
                "rule_status": {"recorded": data["analysis"].get("rule_version"), "current": RULE_VERSION,
                                "outdated": data["analysis"].get("rule_version") != RULE_VERSION}}

    @staticmethod
    def integrity(data, events):
        previous = "0"*64
        valid = True
        for sequence, event in enumerate(events, 1):
            unsigned = {k: v for k, v in event.items() if k != "hash"}
            valid &= event["previous_hash"] == previous and event["hash"] == digest(unsigned) and event["sequence"] == sequence
            previous = event["hash"]
        valid &= bool(events) and events[-1]["snapshot_sha256"] == digest(data)
        return bool(valid)

    def update(self, case_id, actor, revision, action, *, issue_id=None, disposition=None, note=""):
        require(actor, "reviewer" if action in {"approve", "return"} else "analyst")
        with self.db(write=True) as db:
            data = self._load(db, case_id, actor)
            events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE case_id=? ORDER BY sequence", (case_id,))]
            if not self.integrity(data, events):
                raise HTTPException(409, "Event or snapshot integrity failed; review actions are blocked")
            if data["analysis"].get("rule_version") != RULE_VERSION:
                raise HTTPException(409, "Recorded rules are outdated; create a new review case from the verified source pack")
            if data["revision"] != revision:
                raise HTTPException(409, "Revision changed. Refresh before editing.")
            if action == "disposition":
                if data["status"] not in {"draft", "returned"}:
                    raise HTTPException(409, "Cannot edit a submitted or reviewed snapshot")
                issue = next((i for i in data["analysis"]["issues"] if i["id"] == issue_id), None)
                if issue is None:
                    raise HTTPException(404, "Issue not found")
                if issue["severity"] == "blocking":
                    raise HTTPException(422, "Blocking data errors require a corrected source pack and a new review case")
                data["dispositions"][issue_id] = {"status": disposition, "note": note, "actor": actor.user_id}
            elif action == "submit":
                if data["status"] not in {"draft", "returned"}:
                    raise HTTPException(409, "Case is not editable")
                if data["analysis"]["blocking_count"] or any(i["id"] not in data["dispositions"] for i in data["analysis"]["issues"]):
                    raise HTTPException(422, "Resolve every review item before submission; blocking errors cannot be waived")
                data["status"] = "in_review"
                data["submitted_by"] = actor.user_id
                data["review"] = None
            elif action in {"approve", "return"}:
                if data["status"] != "in_review":
                    raise HTTPException(409, "Case has not been submitted for review")
                if actor.user_id in {data["created_by"], data.get("submitted_by")} or any(
                        actor.user_id == d["actor"] for d in data["dispositions"].values()):
                    raise HTTPException(403, "A separate reviewer must review this case")
                data["status"] = "reviewed" if action == "approve" else "returned"
                data["review"] = {"actor": actor.user_id, "decision": action, "note": note, "at": self.now()}
            else:
                raise HTTPException(422, "Unknown action")
            data["revision"] += 1
            changed = db.execute("UPDATE reviews SET revision=?,payload=? WHERE id=? AND tenant=? AND revision=?",
                                 (data["revision"], json.dumps(data, ensure_ascii=False), case_id, actor.tenant_id, revision)).rowcount
            if changed != 1:
                raise HTTPException(409, "Concurrent update")
            self._event(db, data, actor, action, note)
        return self.get(case_id, actor)


def render_memo(data):
    """Generated from the immutable fact pack and rule output, without an LLM."""
    def clean(value):
        return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|")
    lines = ["# " + clean(data["pack"]["company"]) + " 信用研究复核底稿", "",
             "状态：" + data["status"] + "；版本：" + str(data["revision"]),
             "截止日期：" + data["analysis"]["as_of"], "",
             "计算规则：" + str(data["analysis"].get("rule_version", "unknown")) + "；当前规则：" + RULE_VERSION,
             "旧规则历史底稿：请按当前规则重新建案核查。" if data["analysis"].get("rule_version") != RULE_VERSION else "规则版本检查：当前版本。", "",
             "本底稿由确定性规则生成；复核确认仅表示底稿检查完成，不表示授信审批或投资建议。", "",
             "## 指标与计算", "", "| 指标 | 值 | 单位 | 公式 | 事实引用 |", "|---|---:|---|---|---|"]
    for m in data["analysis"]["metrics"]:
        lines.append("| " + " | ".join(clean(m[k]) for k in ["title", "value", "unit", "formula"]) + " | " + ", ".join(m["fact_ids"]) + " |")
    lines += ["", "## 待核查事项与处理", ""]
    for issue in data["analysis"]["issues"]:
        lines += ["### " + clean(issue["title"]), "", clean(issue["detail"]), "",
                  "处理：" + clean(data["dispositions"].get(issue["id"], {}).get("note", "尚未处理")), ""]
    lines += ["## 来源", ""]
    for s in data["pack"]["sources"]:
        lines += ["- " + clean(s["title"]) + "：" + str(s["url"]), "  披露日：" + s["published_at"] + "；采集 SHA256：" + s["sha256"]]
    located = [f for f in data["pack"]["facts"] if f.get("pdf_location")]
    if located:
        lines += ["", "## PDF 原页定位", "", "坐标为页面左上角起算的 PDF point；定位与一致性检查不证明真实性。", "",
                  "| 事实 | 原始显示值 | 页码 | 金额框 x0, top, x1, bottom | 年份 | 行标签 |", "|---|---|---:|---|---:|---|"]
        for f in located:
            loc = f["pdf_location"]
            coords = ", ".join(str(loc["amount_bbox"][k]) for k in ("x0", "top", "x1", "bottom"))
            lines.append("| " + " | ".join(clean(v) for v in (f["id"], f["raw_value"], loc["page"], coords,
                                                               loc["year"], loc["row_label"])) + " |")
    lines += ["", "数据包 SHA256：" + data["pack_sha256"], "",
              "事件哈希链检查：" + str(data.get("event_integrity_valid", "not_checked")),
              "本地哈希链不抵御拥有数据库修改权限的管理员重新计算整个链。", ""]
    return "\n".join(lines)
