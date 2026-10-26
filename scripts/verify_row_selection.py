"""Check a saved row-selection response without downloading or changing a task."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from creditlens.row_selection_verification import (
    MAX_INPUT_BYTES, RowSelectionVerificationError, RowSelectionSourceError, load_and_verify_row_selection,
)
from creditlens.row_selection_verification_experiment import (
    build_row_selection_verification_report, load_row_selection_verification_snapshot,
)
from scripts.evaluate_row_disambiguation import output_path

EXIT_MATCHED = 0
EXIT_MISMATCH = 1
EXIT_INPUT = 2
EXIT_SOURCE = 3
EXIT_TOO_LARGE = 4


def read_input(path: Path) -> bytes:
    """Exactly one bounded read: no full-file load before the size guard."""
    path = Path(path)
    if str(path).startswith(("\\\\", "//")) or "://" in str(path):
        raise RowSelectionVerificationError("nonlocal_input", "input", "Use an existing local JSON file")
    try:
        if not path.is_file():
            raise OSError("Not a regular file")
        with path.open("rb") as stream:
            return stream.read(MAX_INPUT_BYTES+1)
    except OSError as exc:
        raise RowSelectionVerificationError("input_file_unavailable", "input", "Local input is unavailable") from exc


def verify_file(path: Path, *, root=ROOT):
    return load_and_verify_row_selection(read_input(path), Path(root))


def _write(root, target, payload):
    target = output_path(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--input", type=Path, help="Existing row-selection JSON response, at most 128 KiB")
    modes.add_argument("--run-controls", action="store_true", help="Rebuild all eight saved-shape checks and separate synthetic controls")
    modes.add_argument("--verify-saved", action="store_true", help="Replay the small public verification report")
    parser.add_argument("--output", type=Path, help="Optional new JSON directly inside .run/reproductions")
    args = parser.parse_args(argv)
    target = None
    try:
        if args.output is not None:
            target = output_path(ROOT, args.output)
    except (ValueError, OSError):
        print(json.dumps({"schema_version": 1, "status": "input_error", "code": "invalid_output_path", "path": "output"}))
        return EXIT_INPUT
    try:
        if args.run_controls:
            payload = build_row_selection_verification_report(ROOT)
            result = {"schema_version": 1, "status": "controls_passed", "report_sha256": sha256(
                (json.dumps(payload,ensure_ascii=False,separators=(",", ":"),allow_nan=False)+"\n").encode("utf8")).hexdigest(),
                "registered_paths": payload["registered_paths"]["summary"], "synthetic_controls": payload["synthetic_controls"]["summary"],
                "model_accuracy_measured": False}
            status = EXIT_MATCHED
        elif args.verify_saved:
            snapshot = load_row_selection_verification_snapshot(ROOT); payload = snapshot
            result = {"schema_version": 1, "status": "saved_checks_replayed", "report_sha256": snapshot["report_sha256"],
                "registered_paths": snapshot["report"]["registered_paths"]["summary"],
                "synthetic_controls": snapshot["report"]["synthetic_controls"]["summary"], "full_ocr_replayed": False}
            status = EXIT_MATCHED
        else:
            payload = result = verify_file(args.input, root=ROOT)
            status = EXIT_MATCHED if result["status"] == "matched" else EXIT_MISMATCH
    except RowSelectionVerificationError as exc:
        payload = result = {"schema_version": 1, "status": "input_error", "code": exc.code, "path": exc.path,
                            "http_status": exc.http_status, "automatic_approval": False}
        status = EXIT_TOO_LARGE if exc.http_status == 413 else EXIT_INPUT
    except RowSelectionSourceError:
        payload = result = {"schema_version": 1, "status": "source_unavailable", "code": "trusted_snapshot_unavailable",
                            "path": "snapshot", "http_status": 409, "automatic_approval": False}
        status = EXIT_SOURCE
    except (ValueError, OSError):
        payload = result = {"schema_version": 1, "status": "verification_unavailable", "code": "verification_not_completed",
                            "path": "verification", "automatic_approval": False}
        status = EXIT_SOURCE
    if target is not None:
        try: _write(ROOT, target, payload)
        except (ValueError, OSError):
            print(json.dumps({"schema_version": 1, "status": "output_error", "code": "output_not_created", "path": "output"}))
            return EXIT_INPUT
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
