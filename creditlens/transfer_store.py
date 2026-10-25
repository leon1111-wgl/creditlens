"""Serve only reviewed transfer evidence and hash-bound local page images."""
from hashlib import sha256
import json
from pathlib import Path

from .document_transfer import validate_transfer_report
from .visual_store import VisualArtifactError

REPORT = "artifacts/document-transfer-report.json"
MANIFEST = "configs/document-transfer.json"
RELEASE = "configs/document-transfer-artifact.json"


def transfer_snapshot(root: Path) -> dict:
    root = Path(root)
    release = json.loads((root/RELEASE).read_text(encoding="utf-8"))
    if (release.get("schema_version") != 1 or
            set(release.get("files", {})) != {REPORT, MANIFEST}):
        raise VisualArtifactError("Transfer release registration invalid")
    raw = (root/REPORT).read_bytes()
    if sha256(raw).hexdigest() != release["files"][REPORT]:
        raise VisualArtifactError("Transfer report differs from its reviewed release")
    report = json.loads(raw)
    if report.get("manifest_sha256") != release["files"][MANIFEST]:
        raise VisualArtifactError("Transfer report uses a different manifest")
    # Validate the exact report bytes checked above, not a second report read.
    validate_transfer_report(root, report)
    manifest_raw = (root/MANIFEST).read_bytes()
    if sha256(manifest_raw).hexdigest() != report["manifest_sha256"]:
        raise VisualArtifactError("Transfer manifest changed")
    return {"report": report, "manifest": json.loads(manifest_raw), "read_only": True,
            "validation_scope": "Reviewed report hashes, frozen rules and source references, complete registered fact sets and rechecked public evidence. Full OCR and image bytes are validated separately; this is not a signature or proof of model execution."}


def transfer_image(root: Path, document_id: str, page_number: int, *, expected_sha256: str | None = None) -> bytes:
    view = transfer_snapshot(root)
    document = next((d for d in view["report"]["documents"] if d["id"] == document_id), None)
    if document is None:
        raise FileNotFoundError("Document is not registered")
    page = next((p for p in document["pages"] if p["page"] == page_number), None)
    if page is None:
        raise FileNotFoundError("Page is not registered")
    if expected_sha256 is not None and expected_sha256 != page["image_sha256"]:
        raise VisualArtifactError("Page image registration changed; reload the report")
    base = (Path(root)/".run/document-transfer/raw").resolve()
    path = (base/document_id/f"page-{page_number}.png").resolve()
    if not path.is_relative_to(base):
        raise VisualArtifactError("Image path escapes the registered local directory")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != page["image_sha256"]:
        raise VisualArtifactError("Local image differs from the registered transfer image")
    return raw
