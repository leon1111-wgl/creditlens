"""Read a registered monetary-symbol diagnostic without changing old releases."""
from hashlib import sha256
from pathlib import Path

from .currency_diagnostic import currency_diagnostic_view
from .date_diagnostic import read_json
from .visual_store import VisualArtifactError

REPORT = "artifacts/currency-diagnostic-report.json"
RELEASE = "configs/currency-diagnostic-artifact.json"


def currency_snapshot(root: Path) -> dict:
    root = Path(root)
    if not (root / RELEASE).is_file():
        raise FileNotFoundError("Currency diagnostic release is not available")
    _, release = read_json(root / RELEASE, limit=100_000)
    if (set(release) != {"schema_version", "files"} or
            type(release.get("schema_version")) is not int or release["schema_version"] != 1 or
            not isinstance(release.get("files"), dict) or set(release["files"]) != {REPORT}):
        raise VisualArtifactError("Currency diagnostic release registration invalid")
    raw, report = read_json(root / REPORT)
    if sha256(raw).hexdigest() != release["files"][REPORT]:
        raise VisualArtifactError("Currency diagnostic differs from its reviewed release")
    # Validate the exact parsed bytes above, including recomputation of all
    # real observations and separately labelled synthetic controls.
    return currency_diagnostic_view(root, report)
