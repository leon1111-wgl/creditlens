"""Registered, reproducible input diagnostics for the read-only workbench."""
from hashlib import sha256
from pathlib import Path

from .date_diagnostic import read_json
from .input_diagnostic import input_diagnostic_view

REPORT="artifacts/document-input-checks.json"
RELEASE="configs/document-input-artifact.json"


def input_snapshot(root:Path):
    root=Path(root)
    if not (root/RELEASE).is_file() or not (root/REPORT).is_file():
        raise FileNotFoundError("Input diagnostic release is unavailable")
    _,release=read_json(root/RELEASE,limit=100_000)
    if (set(release)!={"schema_version","files"} or type(release.get("schema_version")) is not int
            or release['schema_version']!=1 or not isinstance(release['files'],dict)
            or set(release['files'])!={REPORT}):
        raise ValueError("Invalid input diagnostic release")
    raw,report=read_json(root/REPORT)
    if sha256(raw).hexdigest()!=release['files'][REPORT]:
        raise ValueError("Input diagnostic differs from reviewed release")
    return input_diagnostic_view(root,report)
