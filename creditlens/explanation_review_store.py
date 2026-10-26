"""Separate review of company statements without changing approved parent work."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Annotated, Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .research_task_store import ResearchTaskStore, _json, _sha
from .workflow import Actor, require
from . import research_write_receipts as write_receipts


STORE_VERSION = "explanation-review-1"
SUPPORT_SCOPE = "company_statement_only"
EXPORT_SCOPE = "Frozen company-statement review and its recorded decisions; approval is not independent causal verification"
Revision = Annotated[int, Field(strict=True, ge=1)]
Sha256 = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExplanationReviewCreate(_Request):
    parent_revision: Revision
    parent_snapshot_sha256: Sha256


class ExplanationReviewDecision(_Request):
    entry_id: str = Field(strict=True, min_length=1, max_length=256)
    decision: Literal["adopt_as_company_statement", "rejected", "needs_evidence"]
    note: str = Field(strict=True, min_length=10, max_length=2000)


class ExplanationReviewUpdate(_Request):
    revision: Revision
    decisions: list[ExplanationReviewDecision] = Field(max_length=100)
    coverage_note: str = Field(strict=True, max_length=4000)

    @model_validator(mode="after")
    def unique_entries(self):
        ids = [item.entry_id for item in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate explanation entry decisions")
        return self


class ExplanationReviewAction(_Request):
    revision: Revision
    action: Literal["submit", "approve", "return"]
    note: str = Field(strict=True, min_length=10, max_length=2000)
    snapshot_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def bind_review_snapshot(self):
        if self.action != "submit" and self.snapshot_sha256 is None:
            raise ValueError("Review actions require a submitted snapshot SHA256")
        return self


def _validate(model, value):
    try:
        return model.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "type": e["type"], "msg": e["msg"]}
                  for e in exc.errors(include_input=False, include_url=False)]
        raise HTTPException(422, {"code": "invalid_explanation_review_request", "errors": errors}) from exc


class ExplanationReviewStore:
    """Use the parent's database transaction, but never update parent tables.

    Identity comes from the existing API Actor. Demo actors are simulated. Hashes
    detect inconsistent stored content, not a database administrator rewriting
    content and every checksum together. Offline reads need no live registry.
    """

    def __init__(self, parent_store: ResearchTaskStore):
        self.parent = parent_store
        self.root = parent_store.root
        with self.parent.db(write=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS explanation_reviews (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, parent_task_id TEXT NOT NULL,
                revision INTEGER NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                request_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                UNIQUE(tenant, request_key))""")
            db.execute("""CREATE TABLE IF NOT EXISTS explanation_review_snapshots (
                id TEXT PRIMARY KEY, attachment_id TEXT NOT NULL, tenant TEXT NOT NULL,
                content TEXT NOT NULL, content_sha256 TEXT NOT NULL, metadata TEXT NOT NULL,
                UNIQUE(attachment_id, id))""")
            db.execute("CREATE INDEX IF NOT EXISTS explanation_reviews_parent ON explanation_reviews(tenant,parent_task_id)")

    def _parent_export(self, db, data):
        snapshot = self.parent._snapshot(db, data, data["snapshot_id"])
        if snapshot["sha256"] != data["snapshot_sha256"]:
            raise HTTPException(409, "Parent task no longer matches its frozen snapshot")
        history = [item for item in data["review_history"] if item["snapshot_id"] == snapshot["id"]]
        return {"schema_version": 1, "task_id": data["id"], "snapshot": snapshot,
                "review": history[-1] if history else None, "review_history": history,
                "editable_draft_included": False, "automatic_approval": False,
                "scope": "Frozen research content and its recorded review; not investment advice or causal validation"}

    def _live_parent(self, db, task_id, actor, revision, snapshot_sha):
        data = self.parent._load(db, task_id, actor)
        if data["status"] != "reviewed":
            raise HTTPException(409, "Approve the parent research snapshot before creating or submitting its supplement")
        if data["revision"] != revision or data["snapshot_sha256"] != snapshot_sha:
            raise HTTPException(409, "Parent revision or snapshot changed; create a newly bound supplement")
        exported = self._parent_export(db, data)
        review = exported["review"]
        if (review is None or review["decision"] != "approve"
                or review["snapshot_sha256"] != snapshot_sha or review["revision"] != revision):
            raise HTTPException(409, "Parent approval does not match the requested snapshot and revision")
        return data, exported

    def _build_evidence(self, parent, exported):
        from .explanation_evidence import build_explanation_evidence, ExplanationEvidenceError
        try:
            evidence = build_explanation_evidence(parent, exported, root=self.root)
            _json(evidence)
            return evidence
        except (ExplanationEvidenceError, ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, {"code": "explanation_evidence_unavailable",
                                      "message": "Registered parent materials or company explanation changed; new evidence cannot be frozen"}) from exc

    @staticmethod
    def _decisions(data, decisions, coverage_note, for_submit=False):
        from .explanation_evidence import validate_explanation_decisions, ExplanationEvidenceError
        try:
            return validate_explanation_decisions(data["evidence"], decisions, coverage_note, for_submit=for_submit)
        except (ExplanationEvidenceError, ValueError, KeyError, TypeError) as exc:
            code = getattr(exc, "code", "invalid_explanation_decisions")
            raise HTTPException(422, {"code": code, "message": "Decisions must use this evidence's entries and permitted company-statement scope; supply complete reasons before submission"}) from exc

    @staticmethod
    def _parent_contributors(data):
        content = data["evidence"]["parent_export"]["snapshot"]["content"]
        return sorted(set(content["contributors"]) | {content["created_by"], content["submitted_by"]})

    def _checked_payload(self, row):
        from .explanation_evidence import validate_explanation_evidence, validate_explanation_decisions, ExplanationEvidenceError
        try:
            data = json.loads(row[0])
            if (_sha(data) != row[1] or data["revision"] != row[2]
                    or data["store_version"] != STORE_VERSION or data["support_scope"] != SUPPORT_SCOPE
                    or data["independently_verified"] is not False or data["automatic_approval"] is not False
                    or _sha(data["evidence"]) != data["evidence_sha256"]):
                raise ValueError("Stored supplement integrity mismatch")
            validate_explanation_evidence(data["evidence"])
            parent = data["evidence"]["parent"]
            if (data["parent_task_id"] != parent["task_id"] or data["tenant_id"] != parent["tenant_id"]
                    or data["parent_revision"] != parent["task_revision"]
                    or data["parent_snapshot_sha256"] != parent["snapshot_sha256"]
                    or data["parent_export_sha256"] != _sha(data["evidence"]["parent_export"])):
                raise ValueError("Stored parent binding mismatch")
            validate_explanation_decisions(data["evidence"], data["decisions"], data["coverage_note"])
            if (type(data["contributors"]) is not list or not data["contributors"]
                    or data["created_by"] not in data["contributors"]
                    or (data["submitted_by"] is not None and data["submitted_by"] not in data["contributors"])):
                raise ValueError("Stored contributor record mismatch")
            return data
        except (ExplanationEvidenceError, KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, "Stored explanation review integrity check failed") from exc

    def _load(self, db, review_id, actor):
        row = db.execute("SELECT payload,payload_sha256,revision FROM explanation_reviews WHERE id=? AND tenant=?",
                         (review_id, actor.tenant_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Explanation review not found")
        data = self._checked_payload(row)
        if data["id"] != review_id or data["tenant_id"] != actor.tenant_id:
            raise HTTPException(409, "Stored explanation review identity mismatch")
        return data

    def _snapshot(self, db, data, snapshot_id):
        row = db.execute("SELECT content,content_sha256,metadata FROM explanation_review_snapshots WHERE id=? AND attachment_id=? AND tenant=?",
                         (snapshot_id, data["id"], data["tenant_id"])).fetchone()
        if row is None:
            raise HTTPException(404, "Snapshot not found for this explanation review")
        try:
            content, metadata = json.loads(row[0]), json.loads(row[2])
            if (_sha(content) != row[1] or content["attachment_id"] != data["id"]
                    or content["tenant_id"] != data["tenant_id"] or metadata["id"] != snapshot_id
                    or metadata["sha256"] != row[1] or metadata["revision"] != content["task_revision"]
                    or metadata["submitted_by"] != content["submitted_by"]
                    or metadata["created_at"] != content["submitted_at"]):
                raise ValueError("Snapshot binding mismatch")
            return {**metadata, "content": content}
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(409, "Stored explanation snapshot integrity check failed") from exc

    def _export(self, db, data, snapshot_id):
        from .explanation_evidence import validate_explanation_export, ExplanationEvidenceError
        if snapshot_id is None:
            raise HTTPException(409, "Submit an explanation snapshot before exporting")
        snapshot = self._snapshot(db, data, snapshot_id)
        if snapshot_id == data["snapshot_id"] and snapshot["sha256"] != data["snapshot_sha256"]:
            raise HTTPException(409, "Explanation review does not match its current submitted snapshot")
        history = [item for item in data["review_history"] if item["snapshot_id"] == snapshot_id]
        result = {"schema_version": 1, "export_type": "explanation_review", "attachment_id": data["id"],
                  "snapshot": snapshot, "review": history[-1] if history else None, "review_history": history,
                  "editable_draft_included": False, "support_scope": SUPPORT_SCOPE,
                  "independently_verified": False, "automatic_approval": False, "scope": EXPORT_SCOPE}
        try:
            validate_explanation_export(result)
        except (ExplanationEvidenceError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(409, "Frozen explanation export failed its offline integrity contract") from exc
        return result

    def _view(self, db, data):
        current = self._export(db, data, data["snapshot_id"]) if data["snapshot_id"] else None
        snapshots = []
        for row in db.execute("SELECT id FROM explanation_review_snapshots WHERE attachment_id=? AND tenant=? ORDER BY rowid",
                              (data["id"], data["tenant_id"])):
            snapshot = current["snapshot"] if current and row[0] == current["snapshot"]["id"] else self._export(db, data, row[0])["snapshot"]
            snapshots.append({key: snapshot[key] for key in ("id", "sha256", "created_at", "submitted_by", "revision")})
        return {**data, "parent_contributors": self._parent_contributors(data),
                "snapshot": current["snapshot"] if current else None, "snapshots": snapshots}

    def _event(self, data, actor, action, note):
        data["events"].append({"sequence": len(data["events"]) + 1, "revision": data["revision"],
                               "at": self.parent.now(), "actor": actor.user_id, "role": actor.role,
                               "action": action, "note": note, "status": data["status"],
                               "snapshot_id": data["snapshot_id"], "snapshot_sha256": data["snapshot_sha256"]})

    def _save(self, db, data, revision):
        data["updated_at"] = self.parent.now()
        result = db.execute("UPDATE explanation_reviews SET revision=?,payload=?,payload_sha256=? WHERE id=? AND tenant=? AND revision=?",
                            (data["revision"], _json(data), _sha(data), data["id"], data["tenant_id"], revision))
        if result.rowcount != 1:
            raise HTTPException(409, "Explanation revision changed; refresh before editing")

    @staticmethod
    def _editable(data):
        if data["status"] not in {"draft", "returned"}:
            raise HTTPException(409, "Submitted or reviewed explanation cannot be edited")

    def list(self, task_id: str, actor: Actor) -> list:
        self.parent._reader(actor)
        with self.parent.db() as db:
            self.parent._load(db, task_id, actor)
            rows = db.execute("SELECT id FROM explanation_reviews WHERE tenant=? AND parent_task_id=? ORDER BY rowid DESC LIMIT 100",
                              (actor.tenant_id, task_id)).fetchall()
            records = [self._load(db, row[0], actor) for row in rows]
            if any(record["parent_task_id"] != task_id for record in records):
                raise HTTPException(409, "Stored explanation parent index mismatch")
        keys = ("id", "parent_task_id", "parent_revision", "parent_snapshot_sha256", "status", "revision",
                "created_at", "updated_at", "created_by", "snapshot_sha256", "support_scope")
        return [{key: record[key] for key in keys} for record in records]

    def create(self, task_id: str, actor: Actor, payload: dict, idempotency_key: str) -> dict:
        require(actor, "analyst")
        request = _validate(ExplanationReviewCreate, payload)
        if type(idempotency_key) is not str or not 1 <= len(idempotency_key.strip()) <= 200:
            raise HTTPException(422, "A nonempty idempotency key of at most 200 characters is required")
        request_sha = _sha({"request": request, "actor": actor.user_id, "parent_task_id": task_id})
        with self.parent.db(write=True) as db:
            old = db.execute("SELECT id,request_sha256 FROM explanation_reviews WHERE tenant=? AND request_key=?",
                             (actor.tenant_id, idempotency_key)).fetchone()
            if old:
                if old[1] != request_sha:
                    raise HTTPException(409, "Idempotency key belongs to different content, parent or principal")
                return self._view(db, self._load(db, old[0], actor))
            parent, exported = self._live_parent(db, task_id, actor, request["parent_revision"], request["parent_snapshot_sha256"])
            evidence = self._build_evidence(parent, exported)
            now = self.parent.now()
            data = {"schema_version": 1, "store_version": STORE_VERSION, "id": uuid.uuid4().hex,
                    "tenant_id": actor.tenant_id, "parent_task_id": task_id, **request,
                    "parent_export_sha256": _sha(exported), "evidence": evidence, "evidence_sha256": _sha(evidence),
                    "revision": 1, "status": "draft", "decisions": [], "coverage_note": "",
                    "created_at": now, "updated_at": now, "created_by": actor.user_id, "submitted_by": None,
                    "contributors": [actor.user_id], "snapshot_id": None, "snapshot_sha256": None,
                    "review": None, "review_history": [], "events": [], "support_scope": SUPPORT_SCOPE,
                    "independently_verified": False, "automatic_approval": False}
            self._event(data, actor, "create", "New company-statement supplement; prior parent approval is not inherited")
            db.execute("INSERT INTO explanation_reviews VALUES (?,?,?,?,?,?,?,?)",
                       (data["id"], actor.tenant_id, task_id, 1, _json(data), _sha(data), idempotency_key, request_sha))
            return self._view(db, data)

    def get(self, review_id: str, actor: Actor) -> dict:
        self.parent._reader(actor)
        with self.parent.db() as db:
            return self._view(db, self._load(db, review_id, actor))

    def update(self, review_id: str, actor: Actor, revision: int, decisions: list[dict], coverage_note: str,
               idempotency_key: str | None = None) -> dict:
        require(actor, "analyst")
        request = _validate(ExplanationReviewUpdate, {"revision": revision, "decisions": decisions, "coverage_note": coverage_note})
        with self.parent.db(write=True) as db:
            data = self._load(db, review_id, actor)
            binding = write_receipts.bind(idempotency_key, actor, "explanation_review", review_id, "update", request)
            replayed = write_receipts.replay(db, idempotency_key, binding, data)
            if replayed is not None:
                return replayed
            self.parent._revision(data, request["revision"])
            self._editable(data)
            normalized = self._decisions(data, [{**item, "support_scope": SUPPORT_SCOPE} for item in request["decisions"]], request["coverage_note"])
            data.update(decisions=normalized["decisions"], coverage_note=normalized["coverage_note"])
            data["contributors"] = sorted(set(data["contributors"]) | {actor.user_id})
            data["revision"] += 1
            self._event(data, actor, "update", "Company-statement decisions and coverage updated; no causal approval")
            self._save(db, data, request["revision"])
            return write_receipts.remember(db, idempotency_key, binding, self._view(db, data), data)

    def act(self, review_id: str, actor: Actor, revision: int, action: str, note: str,
            snapshot_sha256: str | None = None, idempotency_key: str | None = None) -> dict:
        request = _validate(ExplanationReviewAction, {"revision": revision, "action": action, "note": note,
                                                    "snapshot_sha256": snapshot_sha256})
        require(actor, "analyst" if request["action"] == "submit" else "reviewer")
        with self.parent.db(write=True) as db:
            data = self._load(db, review_id, actor)
            if idempotency_key is not None and request["action"] != "submit":
                forbidden = set(data["contributors"]) | set(self._parent_contributors(data))
                if actor.user_id in forbidden:
                    raise HTTPException(403, "A parent or supplement contributor cannot review this supplement")
            binding = write_receipts.bind(idempotency_key, actor, "explanation_review", review_id, request["action"], request)
            replayed = write_receipts.replay(db, idempotency_key, binding, data)
            if replayed is not None:
                return replayed
            self.parent._revision(data, request["revision"])
            if request["action"] == "submit":
                self._editable(data)
                if snapshot_sha256 is not None and snapshot_sha256 != data["snapshot_sha256"]:
                    raise HTTPException(409, "Previous explanation snapshot changed; refresh before submitting")
                self._decisions(data, data["decisions"], data["coverage_note"], for_submit=True)
                parent, exported = self._live_parent(db, data["parent_task_id"], actor,
                                                     data["parent_revision"], data["parent_snapshot_sha256"])
                if _sha(exported) != data["parent_export_sha256"]:
                    raise HTTPException(409, "Parent export changed; create a new bound supplement")
                if _sha(self._build_evidence(parent, exported)) != data["evidence_sha256"]:
                    raise HTTPException(409, "Explanation registration or rules changed; create a new supplement")
                now = self.parent.now()
                data["revision"] += 1
                data["submitted_by"] = actor.user_id
                data["contributors"] = sorted(set(data["contributors"]) | {actor.user_id})
                content = {"schema_version": 1, "store_version": STORE_VERSION, "attachment_id": data["id"],
                           "tenant_id": data["tenant_id"], "task_revision": data["revision"],
                           "submitted_by": actor.user_id, "submitted_at": now,
                           **{key: deepcopy(data[key]) for key in (
                               "created_by", "contributors", "parent_task_id", "parent_revision", "parent_snapshot_sha256",
                               "evidence", "evidence_sha256", "decisions", "coverage_note", "support_scope",
                               "independently_verified", "automatic_approval")}}
                metadata = {"id": uuid.uuid4().hex, "sha256": _sha(content), "created_at": now,
                            "submitted_by": actor.user_id, "revision": data["revision"]}
                db.execute("INSERT INTO explanation_review_snapshots VALUES (?,?,?,?,?,?)",
                           (metadata["id"], data["id"], data["tenant_id"], _json(content), metadata["sha256"], _json(metadata)))
                data.update(status="in_review", snapshot_id=metadata["id"], snapshot_sha256=metadata["sha256"], review=None)
            else:
                if data["status"] != "in_review":
                    raise HTTPException(409, "Explanation supplement is not awaiting review")
                snapshot = self._export(db, data, data["snapshot_id"])["snapshot"]
                if snapshot["sha256"] != request["snapshot_sha256"]:
                    raise HTTPException(409, "Submitted explanation snapshot changed; refresh before reviewing")
                forbidden = set(data["contributors"]) | set(self._parent_contributors(data))
                if actor.user_id in forbidden:
                    raise HTTPException(403, "A parent or supplement contributor cannot review this supplement")
                data["revision"] += 1
                data["review"] = {"actor": actor.user_id, "decision": request["action"], "note": request["note"],
                                  "at": self.parent.now(), "revision": data["revision"],
                                  "snapshot_id": snapshot["id"], "snapshot_sha256": snapshot["sha256"]}
                data["review_history"].append(data["review"])
                data["status"] = "reviewed" if request["action"] == "approve" else "returned"
            self._event(data, actor, request["action"], request["note"])
            self._save(db, data, request["revision"])
            return write_receipts.remember(db, idempotency_key, binding, self._view(db, data), data)

    def export(self, review_id: str, actor: Actor, snapshot_id: str | None = None) -> dict:
        self.parent._reader(actor)
        with self.parent.db() as db:
            data = self._load(db, review_id, actor)
            selected = snapshot_id if snapshot_id is not None else data["snapshot_id"]
            return self._export(db, data, selected)
