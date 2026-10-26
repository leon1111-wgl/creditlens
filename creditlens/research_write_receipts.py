"""Transactional receipts for research and explanation update/action retries.

Callers authorize and load the current tenant-scoped object before replay. All
operations here use the caller's transaction; no independent connection commits.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from fastapi import HTTPException

WRITE_RECEIPT_VERSION = "research-write-receipt-1"


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf8")).hexdigest()


def _error(code, status=409):
    messages = {
        "invalid_write_receipt_key": "A nonblank write idempotency key of 8 to 128 characters is required",
        "write_receipt_conflict": "This key is already bound to a different request, principal, object or action in this tenant",
        "write_receipt_integrity_failed": "Stored write receipt failed its integrity check; no response was replayed",
        "write_receipt_storage_failed": "Write receipt could not be saved; the business transaction was rolled back",
    }
    raise HTTPException(status, {"code": code, "message": messages[code]})


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS research_write_receipts (
        tenant TEXT NOT NULL, request_key TEXT NOT NULL,
        binding TEXT NOT NULL, binding_sha256 TEXT NOT NULL,
        response TEXT NOT NULL, response_sha256 TEXT NOT NULL,
        receipt_sha256 TEXT NOT NULL, PRIMARY KEY(tenant, request_key))""")


def bind(key, actor, entity_type, entity_id, action, request):
    if key is None:
        return None
    if type(key) is not str or not 8 <= len(key) <= 128 or not key.strip():
        _error("invalid_write_receipt_key", 422)
    return {"schema_version": 1, "receipt_version": WRITE_RECEIPT_VERSION,
            "tenant_id": actor.tenant_id, "user_id": actor.user_id, "role": actor.role,
            "entity_type": entity_type, "entity_id": entity_id, "action": action,
            "request": request}


def _receipt_sha(tenant, key, binding_sha, response_sha):
    return _sha({"schema_version": 1, "receipt_version": WRITE_RECEIPT_VERSION,
                 "tenant_id": tenant, "request_key": key,
                 "binding_sha256": binding_sha, "response_sha256": response_sha})


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate stored receipt key")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("Non-finite stored receipt value")


def _checked_response(response, binding, current):
    """Bind a historical response to the object's still-retained event prefix."""
    if (type(response) is not dict or type(response["revision"]) is not int
            or response["revision"] != binding["request"]["revision"]+1
            or response["id"] != binding["entity_id"] or response["tenant_id"] != binding["tenant_id"]
            or response["store_version"] != current["store_version"]
            or response["automatic_approval"] is not False
            or type(current["revision"]) is not int or current["revision"] < response["revision"]):
        raise ValueError("Receipt response identity mismatch")
    events = response["events"]
    if (type(events) is not list or not events
            or _sha(events) != _sha(current["events"][:len(events)])
            or events[-1]["revision"] != response["revision"]
            or events[-1]["action"] != binding["action"]
            or events[-1]["actor"] != binding["user_id"]
            or events[-1]["role"] != binding["role"]
            or events[-1]["status"] != response["status"]
            or events[-1]["snapshot_id"] != response["snapshot_id"]
            or events[-1]["snapshot_sha256"] != response["snapshot_sha256"]):
        raise ValueError("Receipt response event mismatch")
    statuses = {"update": {"draft", "returned"}, "submit": {"in_review"},
                "approve": {"reviewed"}, "return": {"returned"}}
    if response["status"] not in statuses[binding["action"]]:
        raise ValueError("Receipt response status mismatch")


def replay(db, key, binding, current):
    """Return the original response, never reconstruct it as the latest view."""
    if binding is None:
        return None
    row = db.execute("SELECT binding,binding_sha256,response,response_sha256,receipt_sha256 "
                     "FROM research_write_receipts WHERE tenant=? AND request_key=?",
                     (binding["tenant_id"], key)).fetchone()
    if row is None:
        return None
    try:
        saved = json.loads(row[0], object_pairs_hook=_pairs, parse_constant=_reject_constant)
        response = json.loads(row[2], object_pairs_hook=_pairs, parse_constant=_reject_constant)
        if (_sha(saved) != row[1] or _sha(response) != row[3]
                or _receipt_sha(binding["tenant_id"], key, row[1], row[3]) != row[4]):
            raise ValueError("Receipt hash mismatch")
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        _error("write_receipt_integrity_failed")
    if _sha(saved) != _sha(binding):
        _error("write_receipt_conflict")
    try:
        _checked_response(response, binding, current)
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError, OverflowError):
        _error("write_receipt_integrity_failed")
    return response


def remember(db, key, binding, response, current):
    """Insert only after the business response is complete, in the same txn."""
    if binding is None:
        return response
    try:
        _checked_response(response, binding, current)
        binding_sha, response_sha = _sha(binding), _sha(response)
        db.execute("INSERT INTO research_write_receipts VALUES (?,?,?,?,?,?,?)",
                   (binding["tenant_id"], key, _json(binding), binding_sha, _json(response), response_sha,
                    _receipt_sha(binding["tenant_id"], key, binding_sha, response_sha)))
    except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError, OverflowError, sqlite3.Error):
        _error("write_receipt_storage_failed", 503)
    return response
