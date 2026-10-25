"""Verify an unsigned explanation export locally, without a registry or database.

No HTTP request is made. This checks internal data and recorded role separation,
not source authenticity, actual identities, signatures or causal truth.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from creditlens.explanation_evidence import ExplanationEvidenceError, validate_explanation_export


MAX_FILE_BYTES = 4 * 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExplanationEvidenceError("duplicate_json_key", "Repeated JSON object key")
        result[key] = value
    return result


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ExplanationEvidenceError("nonfinite_number", "Nonfinite number")
    return value


def _constant(_text):
    raise ExplanationEvidenceError("nonfinite_number", "NaN and Infinity are not accepted")


def verify_file(path: Path) -> dict:
    path = Path(path)
    if str(path).startswith(("\\\\", "//")) or "://" in str(path):
        raise ExplanationEvidenceError("nonlocal_path", "Expected a local export file")
    try:
        if not path.is_file():
            raise ExplanationEvidenceError("invalid_file", "Input is not a regular file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ExplanationEvidenceError("file_too_large", "Export exceeds 4 MiB")
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ExplanationEvidenceError("file_too_large", "Export exceeds 4 MiB")
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique, parse_float=_float, parse_constant=_constant)
    except ExplanationEvidenceError:
        raise
    except OSError as exc:
        raise ExplanationEvidenceError("invalid_file", "Cannot read the export") from exc
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ExplanationEvidenceError("invalid_json", "Expected bounded UTF-8 JSON") from exc
    return validate_explanation_export(document)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="Local JSON export, maximum 4 MiB")
    args = parser.parse_args(argv)
    try:
        result, status = verify_file(args.export), 0
    except ExplanationEvidenceError as exc:
        result, status = {"ok": False, "code": exc.code, "error": exc.message,
                          "scope": "Unsigned internal consistency only"}, 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return status


if __name__ == "__main__":
    sys.exit(main())
