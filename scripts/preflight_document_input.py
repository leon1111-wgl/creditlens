"""Prepare one explicitly supplied raw OCR page; never overwrite reviewed data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.date_diagnostic import read_json
from creditlens.document_input import prepare_document_input, input_error
from scripts.evaluate_currency_diagnostic import output_path


def run(input_path: Path, output: Path, *, root: Path = ROOT, manifest_path: Path | None = None) -> dict:
    selected = Path(output)
    if not selected.is_absolute() and selected.parent == Path("."):
        selected = root/".run/reproductions"/selected
    target = output_path(root, selected)
    raw, value = read_json(input_path, limit=8_000_000)
    result = prepare_document_input(root, value, manifest_path=manifest_path)
    from hashlib import sha256
    result["raw_input_sha256"] = sha256(raw).hexdigest()
    # Serialize and flush completely before exposing the result name.
    encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    target = output_path(root, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".document-input-", suffix=".tmp", dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Hard-link publication is atomic and refuses an existing destination.
        # If the filesystem does not support it, fail rather than overwrite.
        os.link(temporary, target)
    finally:
        if temporary.resolve().parent == target.parent.resolve():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A completed result remains valid; an orphan .tmp is not loaded.
    return {"ok": True, "status": result["status"], "output": str(target),
            "summary": result["ocr"]["summary"], "queryable": False, "automatic_approval": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New JSON name under .run/reproductions")
    parser.add_argument("--manifest", type=Path, help="Registered transfer manifest inside configs")
    args = parser.parse_args(argv)
    try:
        result = run(args.input, args.output, manifest_path=args.manifest)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": input_error(exc), "queryable": False}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
