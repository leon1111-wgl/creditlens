"""Tenant-scoped research handoffs; immutable snapshots, no model approvals."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .workflow import Actor, require


STORE_VERSION = "research-handoff-1"
Revision = Annotated[int, Field(strict=True, ge=1)]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=256)]
Reason = Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
Sha256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchTaskCreate(_Request):
    bundle_id: Literal["msft-aapl-fy2025"] = "msft-aapl-fy2025"
    question: str = Field(strict=True, min_length=5, max_length=2000)
    selection_reason: str = Field(strict=True, min_length=5, max_length=2000)
    material_mode: Literal["current_verified_materials", "historical_as_of"] = "current_verified_materials"
    as_of: date | None = None

    @model_validator(mode="after")
    def current_is_not_historical(self):
        if self.material_mode == "current_verified_materials" and self.as_of is not None:
            raise ValueError("Current materials cannot have a historical as_of date")
        return self


class ResearchDecision(_Request):
    observation_id: Identifier
    decision: Literal["adopted", "rejected"]
    note: Reason


class ResearchHypothesis(_Request):
    id: str = Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    text: str = Field(strict=True, min_length=1, max_length=4000)
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=8)
    status: Literal["pending", "not_assessed"] = "pending"
    note: str = Field(default="", strict=True, max_length=4000)

    @model_validator(mode="after")
    def unique_evidence(self):
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("Duplicate hypothesis evidence references")
        return self


class ResearchTaskUpdate(_Request):
    revision: Revision
    decisions: list[ResearchDecision] = Field(max_length=8)
    hypotheses: list[ResearchHypothesis] = Field(max_length=3)
    coverage_note: str = Field(strict=True, max_length=4000)

    @model_validator(mode="after")
    def unique_records(self):
        for records, key in ((self.decisions, "observation_id"), (self.hypotheses, "id")):
            ids = [getattr(record, key) for record in records]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate research records")
        return self


class ResearchTaskAction(_Request):
    revision: Revision
    action: Literal["submit", "approve", "return"]
    note: Reason
    snapshot_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def reviewer_must_bind_snapshot(self):
        if self.action != "submit" and self.snapshot_sha256 is None:
            raise ValueError("Review actions require the submitted snapshot SHA256")
        return self


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _validate(model, value):
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        # Do not echo submitted free text, secrets or opaque validation objects.
        errors = [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                  for e in exc.errors(include_input=False, include_url=False)]
        raise HTTPException(422, {"code": "invalid_research_request", "errors": errors}) from exc


class ResearchTaskStore:
    """Local SQLite persistence; demo principals are not real authentication.

    Material bundles are created server-side, copied at creation, and checked
    against the registered material again before submission. Existing snapshots
    remain readable without live source files. Hashes detect inconsistent storage,
    not an administrator who can rewrite both content and its hashes.
    """

    def __init__(self, path: Path, root: Path):
        self.path, self.root = Path(path), Path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_tasks (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    request_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    UNIQUE(tenant, request_key));
                CREATE TABLE IF NOT EXISTS research_snapshots (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, tenant TEXT NOT NULL,
                    content TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                    metadata TEXT NOT NULL, UNIQUE(task_id, id));
            """)

    @contextmanager
    def db(self, write=False):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=10)
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise HTTPException(409, "Research storage is busy; retry with the same revision") from exc
            raise HTTPException(503, "Research storage is unavailable") from exc
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _reader(actor):
        if actor.role not in {"viewer", "analyst", "reviewer"}:
            raise HTTPException(403, "Unknown research role")

    @staticmethod
    def _observation_ids(bundle):
        try:
            observations = bundle["observations"]
            ids = [item["id"] for item in observations]
            if len(ids) != 8 or len(set(ids)) != 8 or not all(isinstance(i, str) and i for i in ids):
                raise ValueError("The registered bundle must contain eight unique observations")
            return set(ids)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, "Registered research material inventory is invalid") from exc

    def _material_bundle(self, bundle_id):
        # Deferred import permits the independent store and materials modules to
        # be tested separately; no client can provide a replacement bundle.
        from .research_materials import MaterialsError, build_material_bundle
        try:
            bundle = build_material_bundle(self.root, bundle_id)
            if bundle.get("id") != bundle_id or bundle.get("material_mode") != "current_verified_materials":
                raise ValueError("Unexpected material registration")
            self._observation_ids(bundle)
            _json(bundle)
            return bundle
        except (MaterialsError, OSError, ValueError, TypeError) as exc:
            raise HTTPException(409, "Registered research materials are unavailable or changed") from exc

    @staticmethod
    def _comparison(bundle, decisions):
        from .research_materials import MaterialsError, build_comparison
        try:
            result = build_comparison(bundle, decisions)
            _json(result)
            return result
        except (MaterialsError, ValueError, TypeError) as exc:
            raise HTTPException(409, "Research comparison cannot be verified from the registered material") from exc

    @staticmethod
    def _checked_payload(row):
        try:
            data = json.loads(row[0])
            if _sha(data) != row[1] or data["revision"] != row[2]:
                raise ValueError("Task integrity mismatch")
            if _sha(data["bundle"]) != data["bundle_sha256"]:
                raise ValueError("Material integrity mismatch")
            return data
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, "Stored research task integrity check failed") from exc

    def _load(self, db, task_id, actor):
        row = db.execute("SELECT payload,payload_sha256,revision FROM research_tasks WHERE id=? AND tenant=?",
                         (task_id, actor.tenant_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Research task not found")
        data = self._checked_payload(row)
        if data["id"] != task_id or data["tenant_id"] != actor.tenant_id:
            raise HTTPException(409, "Stored research task identity mismatch")
        return data

    def _snapshot(self, db, data, snapshot_id):
        row = db.execute("SELECT content,content_sha256,metadata FROM research_snapshots WHERE id=? AND task_id=? AND tenant=?",
                         (snapshot_id, data["id"], data["tenant_id"])).fetchone()
        if row is None:
            raise HTTPException(404, "Research snapshot not found for this task")
        try:
            content, metadata = json.loads(row[0]), json.loads(row[2])
            if (_sha(content) != row[1] or content["task_id"] != data["id"]
                    or content["tenant_id"] != data["tenant_id"]
                    or metadata["id"] != snapshot_id or metadata["sha256"] != row[1]
                    or metadata["revision"] != content["task_revision"]
                    or metadata["submitted_by"] != content["submitted_by"]
                    or metadata["created_at"] != content["submitted_at"]):
                raise ValueError("Snapshot integrity mismatch")
            return {**metadata, "content": content}
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, "Stored research snapshot integrity check failed") from exc

    def _view(self, db, data):
        snapshot = self._snapshot(db, data, data["snapshot_id"]) if data["snapshot_id"] else None
        if snapshot is not None and snapshot["sha256"] != data["snapshot_sha256"]:
            raise HTTPException(409, "Task does not match its submitted snapshot")
        snapshots = []
        for row in db.execute("SELECT id FROM research_snapshots WHERE task_id=? AND tenant=? ORDER BY rowid",
                              (data["id"], data["tenant_id"])):
            item = snapshot if snapshot is not None and row[0] == snapshot["id"] else self._snapshot(db, data, row[0])
            snapshots.append({key: item[key] for key in ("id", "sha256", "created_at", "submitted_by", "revision")})
        return {**data, "snapshot": snapshot, "snapshots": snapshots}

    def _event(self, data, actor, action, note):
        data["events"].append({"sequence": len(data["events"]) + 1, "revision": data["revision"],
                               "at": self.now(), "actor": actor.user_id, "role": actor.role,
                               "action": action, "note": note, "status": data["status"],
                               "snapshot_id": data["snapshot_id"], "snapshot_sha256": data["snapshot_sha256"]})

    def _save(self, db, data, old_revision):
        data["updated_at"] = self.now()
        result = db.execute("UPDATE research_tasks SET revision=?,payload=?,payload_sha256=? WHERE id=? AND tenant=? AND revision=?",
                            (data["revision"], _json(data), _sha(data), data["id"], data["tenant_id"], old_revision))
        if result.rowcount != 1:
            raise HTTPException(409, "Revision changed; refresh before editing")

    @staticmethod
    def _revision(data, expected):
        if data["revision"] != expected:
            raise HTTPException(409, "Revision changed; refresh before editing")

    @staticmethod
    def _editable(data):
        if data["status"] not in {"draft", "returned"}:
            raise HTTPException(409, "Submitted or reviewed research cannot be edited")

    def list(self, actor: Actor) -> list:
        self._reader(actor)
        with self.db() as db:
            rows = db.execute("SELECT payload,payload_sha256,revision FROM research_tasks WHERE tenant=? ORDER BY rowid DESC LIMIT 100",
                              (actor.tenant_id,)).fetchall()
            records = [self._checked_payload(row) for row in rows]
        keys = ("id", "question", "bundle_id", "status", "revision", "created_at", "updated_at", "created_by", "snapshot_sha256")
        return [{key: item[key] for key in keys} for item in records]

    def create(self, actor: Actor, payload: dict, idempotency_key: str) -> dict:
        require(actor, "analyst")
        request = _validate(ResearchTaskCreate, payload)
        if request["material_mode"] == "historical_as_of":
            raise HTTPException(422, {"code": "historical_version_unverified", "message": "These materials do not prove historical availability of the file versions"})
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key.strip()) <= 200:
            raise HTTPException(422, "A nonempty idempotency key of at most 200 characters is required")
        request_sha = _sha({"request": request, "actor": actor.user_id})
        with self.db(write=True) as db:
            old = db.execute("SELECT id,request_sha256 FROM research_tasks WHERE tenant=? AND request_key=?",
                             (actor.tenant_id, idempotency_key)).fetchone()
            if old:
                if old[1] != request_sha:
                    raise HTTPException(409, "Idempotency key was used with a different request or principal")
                return self._view(db, self._load(db, old[0], actor))
            bundle = self._material_bundle(request["bundle_id"])
            now = self.now()
            data = {"schema_version": 1, "store_version": STORE_VERSION, "id": uuid.uuid4().hex,
                    "tenant_id": actor.tenant_id, "revision": 1, "status": "draft", **request,
                    "created_at": now, "updated_at": now, "created_by": actor.user_id, "submitted_by": None,
                    "materials_frozen_at": now, "bundle": bundle, "bundle_sha256": _sha(bundle),
                    "decisions": [], "hypotheses": [], "coverage_note": "", "comparison": self._comparison(bundle, []),
                    "hypothesis_scope": "Associated material only; no causal or model validation",
                    "contributors": [actor.user_id], "snapshot_id": None, "snapshot_sha256": None,
                    "review": None, "review_history": [], "events": [], "automatic_approval": False}
            self._event(data, actor, "create", "Registered current materials captured; historical availability is unverified")
            db.execute("INSERT INTO research_tasks VALUES (?,?,?,?,?,?,?)",
                       (data["id"], actor.tenant_id, data["revision"], _json(data), _sha(data), idempotency_key, request_sha))
            return self._view(db, data)

    def get(self, task_id: str, actor: Actor) -> dict:
        self._reader(actor)
        with self.db() as db:
            return self._view(db, self._load(db, task_id, actor))

    def update(self, task_id: str, actor: Actor, revision: int, decisions: list[dict],
               hypotheses: list[dict], coverage_note: str) -> dict:
        require(actor, "analyst")
        request = _validate(ResearchTaskUpdate, {"revision": revision, "decisions": decisions,
                                              "hypotheses": hypotheses, "coverage_note": coverage_note})
        with self.db(write=True) as db:
            data = self._load(db, task_id, actor)
            self._revision(data, request["revision"])
            self._editable(data)
            valid_ids = self._observation_ids(data["bundle"])
            referenced = {d["observation_id"] for d in request["decisions"]}
            referenced.update(e for hypothesis in request["hypotheses"] for e in hypothesis["evidence_ids"])
            if not referenced <= valid_ids:
                raise HTTPException(422, "Research references must belong to this task's registered observations")
            data.update({key: request[key] for key in ("decisions", "hypotheses", "coverage_note")})
            data["comparison"] = self._comparison(data["bundle"], data["decisions"])
            data["contributors"] = sorted(set(data["contributors"]) | {actor.user_id})
            data["revision"] += 1
            self._event(data, actor, "update", "Decisions, associated hypotheses and material coverage updated")
            self._save(db, data, request["revision"])
            return self._view(db, data)

    def act(self, task_id: str, actor: Actor, revision: int, action: str, note: str,
            snapshot_sha256: str | None = None) -> dict:
        request = _validate(ResearchTaskAction, {"revision": revision, "action": action, "note": note,
                                               "snapshot_sha256": snapshot_sha256})
        action = request["action"]
        snapshot_sha256 = request["snapshot_sha256"]
        require(actor, "analyst" if request["action"] == "submit" else "reviewer")
        with self.db(write=True) as db:
            data = self._load(db, task_id, actor)
            self._revision(data, request["revision"])
            if action == "submit":
                self._editable(data)
                if snapshot_sha256 is not None and snapshot_sha256 != data["snapshot_sha256"]:
                    raise HTTPException(409, "Previous snapshot changed; refresh before submitting")
                ids = self._observation_ids(data["bundle"])
                if {d["observation_id"] for d in data["decisions"]} != ids:
                    raise HTTPException(422, "Adopt or reject every observation with a reason before submission")
                if len(data["coverage_note"].strip()) < 10:
                    raise HTTPException(422, "Explain material coverage and limitations in at least 10 characters")
                if _sha(self._material_bundle(data["bundle_id"])) != data["bundle_sha256"]:
                    raise HTTPException(409, "Registered materials changed; create a new task without rewriting this one")
                comparison = self._comparison(data["bundle"], data["decisions"])
                if comparison != data["comparison"]:
                    raise HTTPException(409, "Comparison rules or results changed; refresh the editable task before submitting")
                now = self.now()
                data["revision"] += 1
                data["submitted_by"] = actor.user_id
                data["contributors"] = sorted(set(data["contributors"]) | {actor.user_id})
                content_keys = ("question", "selection_reason", "material_mode", "as_of", "bundle_id", "bundle",
                                "bundle_sha256", "materials_frozen_at", "decisions", "hypotheses", "hypothesis_scope",
                                "coverage_note", "comparison", "created_by", "contributors", "automatic_approval")
                content = {"schema_version": 1, "store_version": STORE_VERSION, "task_id": data["id"],
                           "tenant_id": data["tenant_id"], "task_revision": data["revision"],
                           "submitted_by": actor.user_id, "submitted_at": now,
                           **{key: data[key] for key in content_keys}}
                metadata = {"id": uuid.uuid4().hex, "sha256": _sha(content), "created_at": now,
                            "submitted_by": actor.user_id, "revision": data["revision"]}
                db.execute("INSERT INTO research_snapshots VALUES (?,?,?,?,?,?)",
                           (metadata["id"], data["id"], data["tenant_id"], _json(content), metadata["sha256"], _json(metadata)))
                data.update(status="in_review", snapshot_id=metadata["id"], snapshot_sha256=metadata["sha256"], review=None)
            else:
                if data["status"] != "in_review":
                    raise HTTPException(409, "Research task is not awaiting review")
                snapshot = self._view(db, data)["snapshot"]
                if snapshot is None or snapshot["sha256"] != snapshot_sha256:
                    raise HTTPException(409, "Submitted snapshot changed; refresh before reviewing")
                if actor.user_id in data["contributors"]:
                    raise HTTPException(403, "A principal who created, edited or submitted this task cannot review it")
                data["revision"] += 1
                data["review"] = {"actor": actor.user_id, "decision": action, "note": request["note"],
                                  "at": self.now(), "revision": data["revision"], "snapshot_id": snapshot["id"],
                                  "snapshot_sha256": snapshot["sha256"]}
                data["review_history"].append(data["review"])
                data["status"] = "reviewed" if action == "approve" else "returned"
            self._event(data, actor, action, request["note"])
            self._save(db, data, request["revision"])
            return self._view(db, data)

    def get_snapshot(self, task_id: str, snapshot_id: str, actor: Actor) -> dict:
        self._reader(actor)
        with self.db() as db:
            data = self._load(db, task_id, actor)
            snapshot = self._snapshot(db, data, snapshot_id)
            history = [item for item in data["review_history"] if item["snapshot_id"] == snapshot_id]
            return {"task_id": task_id, "snapshot": snapshot, "review": history[-1] if history else None,
                    "review_history": history, "automatic_approval": False}

    def export(self, task_id: str, actor: Actor, snapshot_id: str | None = None) -> dict:
        self._reader(actor)
        with self.db() as db:
            data = self._load(db, task_id, actor)
            selected = snapshot_id if snapshot_id is not None else data["snapshot_id"]
            if selected is None:
                raise HTTPException(409, "Submit a complete research snapshot before exporting a handoff")
            snapshot = self._snapshot(db, data, selected)
            if selected == data["snapshot_id"] and snapshot["sha256"] != data["snapshot_sha256"]:
                raise HTTPException(409, "Task does not match its submitted snapshot")
            history = [item for item in data["review_history"] if item["snapshot_id"] == snapshot["id"]]
            return {"schema_version": 1, "task_id": task_id, "snapshot": snapshot,
                    "review": history[-1] if history else None, "review_history": history,
                    "editable_draft_included": False, "automatic_approval": False,
                    "scope": "Frozen research content and its recorded review; not investment advice or causal validation"}
