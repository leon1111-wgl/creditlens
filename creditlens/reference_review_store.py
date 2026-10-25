"""Sealed observations and third-principal review; never a human-gold claim."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Annotated
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .workflow import Actor

STORE_VERSION = "reference-review-store-1"
INPUT_FIELDS = ("status", "raw_value", "amount_bbox", "unit_text", "currency_text",
                "section_text", "section_note", "reason")
Revision = Annotated[int, Field(strict=True, ge=1)]
Sha = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=256)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ReferenceReviewCreate(_Request):
    task_id: Identifier
    expected_catalog_sha256: Sha
    previous_review_id: Identifier | None = None
    reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def new_round_reason(self):
        if self.previous_review_id and len(self.reason) < 10:
            raise ValueError("A new round requires a reason of at least ten characters")
        return self


class ReferenceObservationSubmit(_Request):
    revision: Revision
    observation: dict


class ReferenceReviewAction(_Request):
    revision: Revision
    note: str = Field(min_length=10, max_length=2000)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _fail(status, code):
    raise HTTPException(status, {"code": code})


def _validate(model, value):
    try:
        _json(value).encode("utf-8")
        return model.model_validate(value).model_dump(mode="json")
    except (ValidationError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        _fail(422, "invalid_reference_review_request")


class ReferenceReviewStore:
    """A transaction persists each observation once; content is not editable.

    Different user IDs establish only application identity separation, not real
    human independence. Source drift blocks new writes, not historical reads.
    """

    def __init__(self, path: Path, root: Path):
        self.path, self.root = Path(path), Path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reference_reviews (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, task_id TEXT NOT NULL,
                    round INTEGER NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    UNIQUE(tenant,task_id,round));
                CREATE TABLE IF NOT EXISTS reference_review_requests (
                    tenant TEXT NOT NULL, request_key TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL, response TEXT NOT NULL,
                    response_sha256 TEXT NOT NULL, PRIMARY KEY(tenant,request_key));
                CREATE TABLE IF NOT EXISTS reference_review_exports (
                    review_id TEXT PRIMARY KEY, tenant TEXT NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
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

    @staticmethod
    def _authorize(actor, write=False, review=False):
        if (not isinstance(actor, Actor) or actor.role not in {"analyst", "reviewer", "viewer"}
                or any(type(x) is not str or not x.strip() or len(x) > 256
                       for x in (actor.user_id, actor.tenant_id))):
            _fail(401, "invalid_actor")
        if (write and actor.role == "viewer") or (review and actor.role != "reviewer"):
            _fail(403, "reference_role_required")

    @staticmethod
    def _key(value):
        if type(value) is not str or not value.strip() or len(value) > 200:
            _fail(422, "invalid_idempotency_key")
        try:
            value.encode("utf-8")
        except UnicodeError:
            _fail(422, "invalid_idempotency_key")
        return value

    def _catalog(self):
        from .reference_contract import build_reference_catalog, validate_reference_task
        try:
            catalog = build_reference_catalog(self.root)
            _json(catalog)
            if _sha({k: v for k, v in catalog.items() if k != "catalog_sha256"}) != catalog["catalog_sha256"]:
                raise ValueError("Catalog digest mismatch")
            for task in catalog["tasks"]:
                validate_reference_task(task)
            return catalog
        except (ValueError, OSError, KeyError, TypeError):
            _fail(409, "reference_registration_unavailable")

    def _current(self, data):
        catalog = self._catalog()
        tasks = [t for t in catalog["tasks"] if t["id"] == data["task"]["id"]]
        if (catalog["catalog_sha256"] != data["catalog_sha256"] or len(tasks) != 1
                or _sha(tasks[0]) != _sha(data["task"])):
            _fail(409, "reference_registration_changed")
        return catalog

    @staticmethod
    def _observation(task, value):
        from .reference_contract import validate_observation
        try:
            _json(value)
            return validate_observation(task, value)
        except (ValueError, TypeError, KeyError, OverflowError):
            _fail(422, "invalid_reference_observation")

    @staticmethod
    def _comparison(observations):
        from .reference_contract import compare_observations
        return (compare_observations(observations[0]["observation"], observations[1]["observation"])
                if len(observations) == 2 else None)

    def _load(self, db, review_id, actor):
        row = db.execute("SELECT payload,payload_sha256,revision FROM reference_reviews WHERE id=? AND tenant=?",
                         (review_id, actor.tenant_id)).fetchone()
        if row is None:
            _fail(404, "reference_review_not_found")
        try:
            from .reference_contract import validate_reference_task
            data = json.loads(row[0])
            if (_sha(data) != row[1] or data["revision"] != row[2] or data["id"] != review_id
                    or data["tenant_id"] != actor.tenant_id or data["store_version"] != STORE_VERSION
                    or data["human_gold"] is not False or data["independence_not_verified"] is not True
                    or data["automatic_approval"] is not False):
                raise ValueError("Stored record mismatch")
            validate_reference_task(data["task"])
            if data["task_sha256"] != data["task"]["task_sha256"]:
                raise ValueError("Stored task binding mismatch")
            observations = data["observations"]
            if len(observations) > 2 or len({o["submitted_by"] for o in observations}) != len(observations):
                raise ValueError("Stored observer mismatch")
            for slot, item in enumerate(observations, 1):
                observation = item["observation"]
                expected = self._observation(data["task"], {k: observation[k] for k in INPUT_FIELDS})
                if item["slot"] != slot or _sha(expected) != _sha(observation):
                    raise ValueError("Stored observation mismatch")
            comparison = self._comparison(observations)
            if _sha(comparison) != _sha(data["comparison"]):
                raise ValueError("Stored comparison mismatch")
            if data["status"] not in {"closed", "frozen"}:
                expected_status = ("awaiting_first" if not observations else "awaiting_second" if len(observations) == 1
                                   else "agreed" if comparison["status"] == "exact_agreement" else "disagreed")
                if data["status"] != expected_status:
                    raise ValueError("Stored status mismatch")
            if data["status"] == "frozen" and (not comparison or comparison["status"] != "exact_agreement"):
                raise ValueError("Frozen disagreement")
            return data
        except (ValueError, TypeError, KeyError, HTTPException):
            _fail(409, "stored_reference_integrity_failed")

    def _view(self, data, actor):
        visible = len(data["observations"]) == 2
        view = json.loads(_json(data))
        for item in view["observations"]:
            item["visible"] = visible or item["submitted_by"] == actor.user_id
            if not item["visible"]:
                item["observation"] = None
        # No observation hashes, lengths or payload digests appear outside the
        # observation itself. Events deliberately contain no annotation text.
        return view

    @staticmethod
    def _event(data, actor, action, note=""):
        data["events"].append({"sequence": len(data["events"])+1, "action": action,
                               "actor": actor.user_id, "role": actor.role,
                               "at": ReferenceReviewStore.now(), "revision": data["revision"], "note": note})

    @staticmethod
    def _check_revision(data, revision):
        if data["revision"] != revision:
            _fail(409, "reference_revision_changed")

    @staticmethod
    def _save(db, data, previous_revision):
        changed = db.execute("UPDATE reference_reviews SET revision=?,payload=?,payload_sha256=? WHERE id=? AND tenant=? AND revision=?",
                             (data["revision"], _json(data), _sha(data), data["id"], data["tenant_id"], previous_revision)).rowcount
        if changed != 1:
            _fail(409, "reference_revision_changed")

    @staticmethod
    def _replayed(db, actor, key, request_sha):
        row = db.execute("SELECT request_sha256,response,response_sha256 FROM reference_review_requests WHERE tenant=? AND request_key=?",
                         (actor.tenant_id, key)).fetchone()
        if row:
            if row[0] != request_sha:
                _fail(409, "reference_idempotency_conflict")
            response = json.loads(row[1])
            if _sha(response) != row[2]:
                _fail(409, "stored_reference_response_failed")
            return response
        return None

    @staticmethod
    def _remember(db, actor, key, request_sha, response):
        db.execute("INSERT INTO reference_review_requests VALUES (?,?,?,?,?)",
                   (actor.tenant_id, key, request_sha, _json(response), _sha(response)))

    def list(self, actor):
        self._authorize(actor)
        with self.db() as db:
            ids = db.execute("SELECT id FROM reference_reviews WHERE tenant=? ORDER BY rowid DESC LIMIT 200",
                             (actor.tenant_id,)).fetchall()
            rows = [self._load(db, row[0], actor) for row in ids]
        fields = ("id", "revision", "status", "round", "previous_review_id", "created_by", "created_at", "task_sha256")
        return [{**{k: d[k] for k in fields}, "task_id": d["task"]["id"],
                 "query": d["task"]["query"], "submitted_count": len(d["observations"]),
                 "human_gold": False, "independence_not_verified": True} for d in rows]

    def create(self, actor, payload, idempotency_key):
        self._authorize(actor, write=True)
        request = _validate(ReferenceReviewCreate, payload)
        key = self._key(idempotency_key)
        request_sha = _sha({"operation": "create", "actor": actor.user_id, "request": request})
        with self.db(write=True) as db:
            replay = self._replayed(db, actor, key, request_sha)
            if replay is not None:
                return replay
            catalog = self._catalog()
            if catalog["catalog_sha256"] != request["expected_catalog_sha256"]:
                _fail(409, "reference_registration_changed")
            tasks = [t for t in catalog["tasks"] if t["id"] == request["task_id"]]
            if len(tasks) != 1:
                _fail(422, "reference_task_not_registered")
            previous_id = request["previous_review_id"]
            round_number = 1
            if previous_id:
                previous = self._load(db, previous_id, actor)
                if previous["status"] not in {"closed", "frozen"} or previous["task"]["id"] != request["task_id"]:
                    _fail(409, "reference_previous_round_not_terminal")
                round_number = previous["round"]+1
            if db.execute("SELECT 1 FROM reference_reviews WHERE tenant=? AND task_id=? AND round=?",
                          (actor.tenant_id, request["task_id"], round_number)).fetchone():
                _fail(409, "reference_round_already_exists")
            data = {"schema_version": 1, "store_version": STORE_VERSION, "id": uuid.uuid4().hex,
                    "tenant_id": actor.tenant_id, "revision": 1, "status": "awaiting_first", "round": round_number,
                    "previous_review_id": previous_id, "reason": request["reason"], "created_by": actor.user_id,
                    "created_at": self.now(), "catalog_sha256": catalog["catalog_sha256"], "task": tasks[0],
                    "task_sha256": tasks[0]["task_sha256"], "observations": [], "comparison": None,
                    "events": [], "freeze": None, "closure": None,
                    "independence_not_verified": True, "human_gold": False, "automatic_approval": False}
            self._event(data, actor, "created")
            self._current(data)
            db.execute("INSERT INTO reference_reviews VALUES (?,?,?,?,?,?,?)",
                       (data["id"], actor.tenant_id, request["task_id"], round_number, 1, _json(data), _sha(data)))
            response = self._view(data, actor)
            self._remember(db, actor, key, request_sha, response)
            return response

    def get(self, review_id, actor):
        self._authorize(actor)
        with self.db() as db:
            return self._view(self._load(db, review_id, actor), actor)

    def submit(self, review_id, actor, revision, observation, idempotency_key):
        self._authorize(actor, write=True)
        request = _validate(ReferenceObservationSubmit, {"revision": revision, "observation": observation})
        key = self._key(idempotency_key)
        request_sha = _sha({"operation": "submit", "review_id": review_id, "actor": actor.user_id, "request": request})
        with self.db(write=True) as db:
            replay = self._replayed(db, actor, key, request_sha)
            if replay is not None:
                return replay
            data = self._load(db, review_id, actor)
            self._check_revision(data, revision)
            if data["status"] not in {"awaiting_first", "awaiting_second"}:
                _fail(409, "reference_round_sealed")
            if actor.user_id in {o["submitted_by"] for o in data["observations"]}:
                _fail(403, "reference_distinct_observer_required")
            self._current(data)
            normalized = self._observation(data["task"], request["observation"])
            data["observations"].append({"slot": len(data["observations"])+1, "submitted_by": actor.user_id,
                                         "submitted_at": self.now(), "observation": normalized})
            data["comparison"] = self._comparison(data["observations"])
            data["status"] = ("awaiting_second" if len(data["observations"]) == 1 else
                              "agreed" if data["comparison"]["status"] == "exact_agreement" else "disagreed")
            data["revision"] += 1
            self._event(data, actor, "observation_submitted")
            self._current(data)
            self._save(db, data, revision)
            response = self._view(data, actor)
            self._remember(db, actor, key, request_sha, response)
            return response

    def _act(self, review_id, actor, revision, note, action):
        self._authorize(actor, write=True, review=True)
        request = _validate(ReferenceReviewAction, {"revision": revision, "note": note})
        with self.db(write=True) as db:
            data = self._load(db, review_id, actor)
            self._check_revision(data, revision)
            if actor.user_id in {o["submitted_by"] for o in data["observations"]}:
                _fail(403, "reference_independent_reviewer_required")
            if data["status"] in {"frozen", "closed"}:
                _fail(409, "reference_round_sealed")
            if action == "freeze" and data["status"] != "agreed":
                _fail(409, "reference_exact_agreement_required")
            if action == "freeze":
                self._current(data)
            data["revision"] += 1
            data["status"] = "frozen" if action == "freeze" else "closed"
            metadata = {"actor": actor.user_id, "at": self.now(), "note": request["note"], "revision": data["revision"]}
            data["freeze" if action == "freeze" else "closure"] = metadata
            self._event(data, actor, action, request["note"])
            if action == "freeze":
                self._current(data)
            if action == "freeze":
                content = json.loads(_json(data))
                exported = {"schema_version": 1, "export_type": "reference_review_candidate",
                            "review_id": data["id"], "content": content, "content_sha256": _sha(content),
                            "independence_not_verified": True, "human_gold": False, "automatic_approval": False,
                            "scope": "Two recorded observations and third-principal review; not verified human independence or a replacement for existing evaluation references"}
                db.execute("INSERT INTO reference_review_exports VALUES (?,?,?,?)",
                           (data["id"], actor.tenant_id, _json(exported), _sha(exported)))
            self._save(db, data, revision)
            return self._view(data, actor)

    def freeze(self, review_id, actor, revision, note):
        return self._act(review_id, actor, revision, note, "freeze")

    def close(self, review_id, actor, revision, note):
        return self._act(review_id, actor, revision, note, "close")

    def export(self, review_id, actor):
        self._authorize(actor)
        with self.db() as db:
            data = self._load(db, review_id, actor)
            if data["status"] != "frozen":
                _fail(409, "reference_not_frozen")
            row = db.execute("SELECT payload,payload_sha256 FROM reference_review_exports WHERE review_id=? AND tenant=?",
                             (review_id, actor.tenant_id)).fetchone()
            if row is None:
                _fail(409, "reference_export_missing")
            try:
                exported = json.loads(row[0])
                if (_sha(exported) != row[1] or _sha(exported["content"]) != exported["content_sha256"]
                        or _sha(exported["content"]) != _sha(data)):
                    raise ValueError("Frozen export mismatch")
                return exported
            except (ValueError, TypeError, KeyError):
                _fail(409, "reference_export_integrity_failed")
