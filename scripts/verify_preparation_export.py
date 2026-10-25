"""Check an unsigned preparation export locally; optionally compare registration.

No original PDF, page image, OCR body, database or remote service is accessed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from creditlens.preparation_verifier import PreparationVerificationError, verify_preparation_export
from creditlens.preparation_applicability import assess_preparation_applicability

MAX_BYTES = 4 * 1024 * 1024


def _fail(code):
    raise PreparationVerificationError(code, "$", "Local export could not be verified")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _float(text):
    result = float(text)
    if not math.isfinite(result):
        _fail("nonfinite_number")
    return result


def _constant(_value):
    _fail("nonfinite_number")


def verify_file(path: Path, *, check_current=False, root=ROOT, attempt_index=None):
    path = Path(path)
    if str(path).startswith(("\\\\", "//")) or "://" in str(path):
        _fail("nonlocal_path")
    try:
        if not path.is_file():
            _fail("invalid_file")
        if path.stat().st_size > MAX_BYTES:
            _fail("file_too_large")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            _fail("file_too_large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                           parse_float=_float, parse_constant=_constant)
    except PreparationVerificationError:
        raise
    except OSError:
        _fail("invalid_file")
    except (ValueError, UnicodeError, RecursionError):
        _fail("invalid_json")
    if attempt_index is not None:
        # Explicitly select an export from the shipped synthetic replay bundle.
        # This does not validate the bundle's narrative or other exports.
        if (type(attempt_index) is not int or attempt_index < 0 or type(value) is not dict
                or type(value.get("attempt_exports")) is not list
                or attempt_index >= len(value["attempt_exports"])):
            _fail("invalid_attempt_selection")
        value = value["attempt_exports"][attempt_index]
    verified = verify_preparation_export(value)
    result = {"schema_version": 1, "verification": verified,
              "current_registration_checked": bool(check_current)}
    if check_current:
        result["applicability"] = assess_preparation_applicability(Path(root), verified)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--attempt-index", type=int, help="Explicit zero-based item in a replay bundle")
    parser.add_argument("--check-current", action="store_true", help="Compare local registered metadata too")
    args = parser.parse_args(argv)
    try:
        result = verify_file(args.export, check_current=args.check_current, attempt_index=args.attempt_index)
        status = 0
    except PreparationVerificationError as exc:
        result = {"schema_version": 1, "status": "unsupported_export" if exc.code == 'unsupported_version' else "invalid_export", "code": exc.code,
                  "path": exc.path, "authenticated": False, "queryable": False}
        status = 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
