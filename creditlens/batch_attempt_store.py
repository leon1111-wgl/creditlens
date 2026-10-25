"""Synchronous, tenant-scoped preflight attempts; raw OCR is never persisted.

Committed attempts survive store reconstruction. This is not a background queue
and makes no promise about requests interrupted before their transaction commits.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Annotated, Literal
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .workflow import Actor

STORE_VERSION = "document-batch-attempts-1"
MAX_REQUEST_BYTES = 5_000_000
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=256)]
Count = Annotated[int, Field(ge=0)]
Page = Annotated[int, Field(ge=1, le=20000)]
Status = Literal["blocked", "prepared_not_indexed"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Binding(_Strict):
    manifest_sha256: Sha
    inventory_sha256: Sha
    document_id: Text
    source_pdf_sha256: Sha
    pack_sha256: Sha
    pack_canonical_sha256: Sha
    registered_document_sha256: Sha


class _Error(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")
    # The frozen page validator uses the fixed union path document_id/ocr_input.
    path: str = Field(pattern=r"^[A-Za-z0-9_.$/\[\]-]{1,512}$")
    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")


class _Counts(_Strict):
    lines: Count
    words: Count
    empty: bool


class _Registration(_Strict):
    document_id: Text
    source_pdf_sha256: Sha
    image_sha256: Sha
    page: Page
    fact_ids: list[Text] = Field(max_length=100)


class _PageResult(_Strict):
    input_index: Count
    page: Page | None
    input_sha256: Sha
    input_hash_status: Literal["canonical_json"]
    status: Status
    structure_status: Literal["not_checked", "failed", "passed"]
    summary: _Counts | None
    registration: _Registration | None
    errors: list[_Error] = Field(max_length=100)


class _Summary(_Strict):
    expected_page_count: Count
    submitted_page_count: Count
    unique_submitted_page_count: Count
    unique_expected_page_count: Count
    structure_passed_page_count: Count
    prepared_page_count: Count
    blocked_page_count: Count
    empty_recognition_page_count: Count


class _Versions(_Strict):
    batch: Text
    document_input: Text
    ocr_input: Text
    asset_preflight: Text


class _Scope(_Strict):
    expected_pages_basis: Literal["all_registered_evidence_pages_not_entire_pdf"]
    same_inventory_checked: bool
    raw_ocr_returned: bool
    normalized_ocr_returned: bool
    source_pdf_bytes_verified: bool
    source_image_bytes_verified: bool
    model_execution_verified: bool
    financial_content_verified: bool
    cross_page_tables_understood: bool


class _Report(_Strict):
    schema_version: int
    rule_version: Literal["registered-document-batch-1"]
    status: Status
    document_id: Text
    provenance: Literal["synthetic", "caller_declared_model_output"]
    input_sha256: Sha
    input_hash_status: Literal["canonical_json"]
    material_binding: _Binding
    expected_pages: list[Page] = Field(min_length=1, max_length=32)
    missing_pages: list[Page] = Field(max_length=32)
    duplicate_pages: list[Page] = Field(max_length=32)
    unexpected_pages: list[Page] = Field(max_length=32)
    page_results: list[_PageResult] = Field(max_length=32)
    errors: list[_Error] = Field(max_length=100)
    summary: _Summary
    versions: _Versions
    scope: _Scope
    limitations: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(max_length=20)
    queryable: bool
    automatic_approval: bool
    database_written: bool

    @model_validator(mode="after")
    def bounded_claims(self):
        if self.schema_version != 1 or self.queryable or self.automatic_approval or self.database_written:
            raise ValueError("Invalid preparation scope")
        flags = self.scope.model_dump()
        if any(v is not False for k,v in flags.items() if k not in {"expected_pages_basis", "same_inventory_checked"}):
            raise ValueError("Unverified scope must remain false")
        if self.material_binding.document_id != self.document_id or self.versions.batch != self.rule_version:
            raise ValueError("Report identity mismatch")
        return self


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _http(status, code):
    raise HTTPException(status, {"code": code})


def _json_values(value, ancestors=None, depth=0):
    if depth > 80:
        return False
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in (dict, list):
        return False
    ancestors = set() if ancestors is None else ancestors
    if id(value) in ancestors:
        return False
    ancestors.add(id(value))
    try:
        if type(value) is dict:
            return all(type(k) is str and _json_values(v, ancestors, depth+1) for k,v in value.items())
        return all(_json_values(v, ancestors, depth+1) for v in value)
    finally:
        ancestors.remove(id(value))


class BatchAttemptStore:
    """Only small verified core summaries, hashes and attempt metadata reach SQLite."""
    def __init__(self, path: Path, root: Path):
        self.path, self.root = Path(path), Path(root)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db(write=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS batch_jobs (
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batch_attempts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, tenant TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    UNIQUE(task_id, revision));
                CREATE TABLE IF NOT EXISTS batch_requests (
                    tenant TEXT NOT NULL, key_sha256 TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    response TEXT NOT NULL, response_sha256 TEXT NOT NULL, PRIMARY KEY(tenant,key_sha256));
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
            code = "batch_storage_busy" if any(s in str(exc).lower() for s in ("locked", "busy")) else "batch_storage_unavailable"
            raise HTTPException(409 if code.endswith("busy") else 503, {"code": code}) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, {"code": "batch_write_conflict"}) from exc
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def now():
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _actor(actor: Actor, write=False):
        if not isinstance(actor, Actor) or actor.role not in {"analyst", "viewer", "reviewer"}:
            _http(403, "unknown_batch_role")
        if any(type(v) is not str or not v or len(v) > 256 or any(ord(c) < 32 for c in v) for v in (actor.user_id, actor.tenant_id)):
            _http(403, "invalid_batch_identity")
        if write and actor.role != "analyst":
            _http(403, "analyst_required")

    @staticmethod
    def _id(value):
        if type(value) is not str or not re.fullmatch(r"[0-9a-f]{32}", value):
            _http(404, "batch_not_found")
        return value

    @staticmethod
    def _key(value):
        if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            _http(422, "invalid_idempotency_key")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _payload(value):
        if (type(value) is not dict or set(value) != {"schema_version", "document_id", "provenance", "pages"}
                or type(value.get("schema_version")) is not int or value["schema_version"] != 1
                or type(value.get("document_id")) is not str or not 0 < len(value["document_id"]) <= 200
                or type(value.get("provenance")) is not str or value["provenance"] not in {"synthetic", "caller_declared_model_output"}
                or type(value.get("pages")) is not list or len(value["pages"]) > 32):
            _http(422, "invalid_batch_request")
        try:
            if not _json_values(value):
                _http(422, "noncanonical_batch_request")
            raw = _json(value).encode("utf-8")
            if len(raw) > MAX_REQUEST_BYTES:
                _http(422, "batch_request_too_large")
            return json.loads(raw), hashlib.sha256(raw).hexdigest()
        except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as exc:
            raise HTTPException(422, {"code": "noncanonical_batch_request"}) from exc

    def _inspect(self, payload):
        from .batch_input import BatchInputError, inspect_document_batch
        try:
            return inspect_document_batch(self.root, payload)
        except BatchInputError as exc:
            status = 409 if exc.code in {"material_registration_failed", "unsupported_registered_page_count"} else 422
            # Codes and schema paths come from code, never the exception's text.
            raise HTTPException(status, {"code": exc.code}) from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(503, {"code": "batch_inspection_unavailable"}) from exc

    @staticmethod
    def _report(value, payload=None, input_hash=None):
        try:
            report = _Report.model_validate(value).model_dump(mode="json")
            if payload is not None and (report["input_sha256"] != input_hash or report["document_id"] != payload["document_id"]
                    or report["provenance"] != payload["provenance"] or len(report["page_results"]) != len(payload["pages"])):
                raise ValueError("Core report does not bind the request")
            if len(_json(report).encode("utf-8")) > 500_000:
                raise ValueError("Summary exceeds storage limit")
            if not report["scope"]["same_inventory_checked"]:
                _http(409, "batch_material_changed")
            return report
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise HTTPException(503, {"code": "invalid_batch_core_report"}) from exc

    @staticmethod
    def _decoded(raw, expected):
        try:
            value = json.loads(raw)
            if _sha(value) != expected:
                raise ValueError("Stored digest mismatch")
            return value
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise HTTPException(409, {"code": "batch_storage_integrity_failed"}) from exc

    def _load(self, db, task_id, actor):
        self._id(task_id)
        row = db.execute("SELECT payload,payload_sha256,revision FROM batch_jobs WHERE id=? AND tenant=?", (task_id,actor.tenant_id)).fetchone()
        if row is None:
            _http(404, "batch_not_found")
        data = self._decoded(row[0], row[1])
        if data.get("id") != task_id or data.get("tenant") != actor.tenant_id or data.get("revision") != row[2]:
            _http(409, "batch_storage_integrity_failed")
        return data

    def _attempts(self, db, job):
        rows = db.execute("SELECT payload,payload_sha256 FROM batch_attempts WHERE task_id=? AND tenant=? ORDER BY revision", (job["id"],job["tenant"])).fetchall()
        values = [self._decoded(*row) for row in rows]
        if len(values) != job["revision"]:
            _http(409, "batch_storage_integrity_failed")
        for index, attempt in enumerate(values, 1):
            if (attempt.get("task_id") != job["id"] or attempt.get("tenant") != job["tenant"] or attempt.get("revision") != index
                    or _sha(attempt.get("report")) != attempt.get("report_sha256")
                    or attempt.get("input_sha256") != attempt["report"].get("input_sha256")
                    or attempt["report"].get("material_binding") != job["material_binding"]):
                _http(409, "batch_storage_integrity_failed")
        if values[-1]["report"]["status"] != job["status"]:
            _http(409, "batch_storage_integrity_failed")
        return values

    def _view(self, db, job):
        attempts = self._attempts(db, job)
        return {**job, "latest_attempt": attempts[-1], "attempts": [{"id": a["id"], "revision": a["revision"],
                    "created_at": a["created_at"], "actor": a["actor"], "status": a["report"]["status"],
                    "input_sha256": a["input_sha256"], "report_sha256": a["report_sha256"]} for a in attempts]}

    def _replay(self, db, actor, key, request_hash):
        row = db.execute("SELECT request_sha256,response,response_sha256 FROM batch_requests WHERE tenant=? AND key_sha256=?", (actor.tenant_id,key)).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            _http(409, "idempotency_conflict")
        result = self._decoded(row[1], row[2])
        if result.get("tenant") != actor.tenant_id:
            _http(409, "batch_storage_integrity_failed")
        return result

    @staticmethod
    def _remember(db, actor, key, request_hash, response):
        db.execute("INSERT INTO batch_requests VALUES(?,?,?,?,?)", (actor.tenant_id,key,request_hash,_json(response),_sha(response)))

    def _append(self, db, job, actor, report):
        attempt = {"id": uuid.uuid4().hex, "task_id": job["id"], "tenant": job["tenant"], "revision": job["revision"],
                   "created_at": job["updated_at"], "actor": actor.user_id, "input_sha256": report["input_sha256"],
                   "report": report, "report_sha256": _sha(report)}
        db.execute("INSERT INTO batch_attempts VALUES(?,?,?,?,?,?)", (attempt["id"],job["id"],job["tenant"],job["revision"],_json(attempt),_sha(attempt)))

    def create(self, actor: Actor, payload: dict, idempotency_key: str) -> dict:
        self._actor(actor, write=True)
        key = self._key(idempotency_key)
        payload, input_hash = self._payload(payload)
        request_hash = _sha({"operation":"create","actor":actor.user_id,"input_sha256":input_hash})
        with self.db(write=True) as db:
            replay = self._replay(db, actor, key, request_hash)
            if replay is not None:
                return replay
            report = self._report(self._inspect(payload), payload, input_hash)
            now = self.now()
            job = {"id":uuid.uuid4().hex,"tenant":actor.tenant_id,"revision":1,"status":report["status"],
                   "created_by":actor.user_id,"created_at":now,"updated_at":now,"document_id":report["document_id"],
                   "provenance":report["provenance"],"material_binding":report["material_binding"],
                   "material_binding_sha256":_sha(report["material_binding"]),"store_version":STORE_VERSION,
                   "attempt_summary_persisted":True,"persistence_scope":"attempt_summaries_only_no_raw_ocr",
                   "attempt_metadata_persisted":True,"raw_ocr_persisted":False,
                   "queryable":False,"automatic_approval":False}
            db.execute("INSERT INTO batch_jobs VALUES(?,?,?,?,?)", (job["id"],job["tenant"],job["revision"],_json(job),_sha(job)))
            self._append(db, job, actor, report)
            response = self._view(db, job)
            self._remember(db, actor, key, request_hash, response)
            return response

    def retry(self, actor: Actor, task_id: str, payload: dict, revision: int, idempotency_key: str) -> dict:
        self._actor(actor, write=True)
        self._id(task_id)
        if type(revision) is not int or revision < 1:
            _http(422, "invalid_batch_revision")
        key = self._key(idempotency_key)
        payload, input_hash = self._payload(payload)
        request_hash = _sha({"operation":"retry","actor":actor.user_id,"task_id":task_id,"revision":revision,"input_sha256":input_hash})
        with self.db(write=True) as db:
            job = self._load(db, task_id, actor)
            replay = self._replay(db, actor, key, request_hash)
            if replay is not None:
                return replay
            self._attempts(db, job)
            if job["revision"] != revision:
                _http(409, "stale_batch_revision")
            if job["status"] != "blocked":
                _http(409, "batch_is_terminal")
            if payload["document_id"] != job["document_id"] or payload["provenance"] != job["provenance"]:
                _http(409, "batch_identity_changed")
            report = self._report(self._inspect(payload), payload, input_hash)
            if report["material_binding"] != job["material_binding"] or _sha(report["material_binding"]) != job["material_binding_sha256"]:
                _http(409, "batch_material_changed")
            job.update(revision=revision+1,status=report["status"],updated_at=self.now())
            changed = db.execute("UPDATE batch_jobs SET revision=?,payload=?,payload_sha256=? WHERE id=? AND tenant=? AND revision=?",
                                 (job["revision"],_json(job),_sha(job),task_id,actor.tenant_id,revision)).rowcount
            if changed != 1:
                _http(409, "stale_batch_revision")
            self._append(db, job, actor, report)
            response = self._view(db, job)
            self._remember(db, actor, key, request_hash, response)
            return response

    def get(self, task_id: str, actor: Actor) -> dict:
        self._actor(actor)
        with self.db() as db:
            return self._view(db, self._load(db, task_id, actor))

    def list(self, actor: Actor) -> list[dict]:
        self._actor(actor)
        with self.db() as db:
            ids = db.execute("SELECT id FROM batch_jobs WHERE tenant=? ORDER BY rowid DESC", (actor.tenant_id,)).fetchall()
            return [{key: view[key] for key in ("id","revision","status","created_by","created_at","updated_at","document_id","provenance","queryable")}
                    for (task_id,) in ids for view in [self._view(db,self._load(db,task_id,actor))]]

    def export(self, task_id: str, actor: Actor, attempt_id: str | None = None) -> dict:
        self._actor(actor)
        if attempt_id is not None:
            self._id(attempt_id)
        with self.db() as db:
            job = self._load(db, task_id, actor)
            attempts = self._attempts(db, job)
            selected = attempts[-1] if attempt_id is None else next((a for a in attempts if a["id"] == attempt_id), None)
            if selected is None:
                _http(404, "batch_attempt_not_found")
            return {"schema_version":1,"export_type":"document_batch_attempt","store_version":STORE_VERSION,
                    "job":{k:job[k] for k in ("id","tenant","document_id","provenance","material_binding","material_binding_sha256","created_by","created_at")},
                    "attempt":selected,"attempt_summary_persisted":True,"queryable":False,"automatic_approval":False,
                    "attempt_metadata_persisted":True,"raw_ocr_persisted":False,
                    "persistence_scope":"attempt_summaries_only_no_raw_ocr",
                    "scope":"Committed explicit preflight attempt only; not financial ingestion, approval, background processing or pre-commit crash recovery."}
