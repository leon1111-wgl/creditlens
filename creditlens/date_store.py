"""Read a reviewed date-rule diagnostic together with its verified source."""
from hashlib import sha256
import json
from pathlib import Path

from .date_diagnostic import date_diagnostic_view
from .visual_store import VisualArtifactError

REPORT = "artifacts/date-diagnostic-report.json"
RELEASE = "configs/date-diagnostic-artifact.json"


def date_snapshot(root: Path) -> dict:
    root = Path(root)
    release = json.loads((root / RELEASE).read_text(encoding="utf-8"))
    if (not isinstance(release, dict) or
            set(release) != {"schema_version", "files"} or
            release.get("schema_version") != 1 or
            not isinstance(release.get("files"), dict) or
            set(release.get("files", {})) != {REPORT}):
        raise VisualArtifactError("Date diagnostic release registration invalid")
    raw = (root / REPORT).read_bytes()
    if sha256(raw).hexdigest() != release["files"][REPORT]:
        raise VisualArtifactError("Date diagnostic differs from its reviewed release")
    # The view recomputes the exact bytes checked here against the same source
    # snapshot that it returns, keeping the old and new UI results together.
    return date_diagnostic_view(root, json.loads(raw))
