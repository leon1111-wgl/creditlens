"""Read the reviewed evidence-integrity diagnostic without running OCR."""
from hashlib import sha256
from pathlib import Path

from .date_diagnostic import read_json
from .evidence_guard_diagnostic import evidence_guard_diagnostic_view
from .visual_store import VisualArtifactError

REPORT = "artifacts/evidence-guard-report.json"
RELEASE = "configs/evidence-guard-artifact.json"


def evidence_guard_snapshot(root: Path) -> dict:
    root = Path(root)
    if not (root / RELEASE).is_file():
        raise FileNotFoundError("Evidence guard release is not available")
    _, release = read_json(root / RELEASE, limit=100_000)
    if (set(release) != {"schema_version", "files"}
            or type(release.get("schema_version")) is not int
            or release["schema_version"] != 1
            or not isinstance(release.get("files"), dict)
            or set(release["files"]) != {REPORT}):
        raise VisualArtifactError("Evidence guard release registration invalid")
    raw, report = read_json(root / REPORT)
    if sha256(raw).hexdigest() != release["files"][REPORT]:
        raise VisualArtifactError("Evidence guard report differs from its reviewed release")
    return evidence_guard_diagnostic_view(root, report)
