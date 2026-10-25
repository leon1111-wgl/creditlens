"""Deliver a reviewed new-issuer run without changing development diagnostics."""
from hashlib import sha256
from pathlib import Path

from .issuer_evaluation import read_json, validate_issuer_report
from .visual_store import VisualArtifactError

REPORT = "artifacts/issuer-evaluation-report.json"
MANIFEST = "configs/issuer-evaluation.json"
RELEASE = "configs/issuer-evaluation-artifact.json"


def issuer_snapshot(root: Path) -> dict:
    root = Path(root)
    # Keep missing release distinct from an invalid release for the API.
    if not (root / RELEASE).is_file():
        raise FileNotFoundError("Issuer release is not available")
    _, release = read_json(root / RELEASE, 100_000)
    if (not isinstance(release, dict) or release.get("schema_version") != 1 or
            set(release) != {"schema_version", "files"} or
            not isinstance(release.get("files"), dict) or
            set(release["files"]) != {REPORT, MANIFEST}):
        raise VisualArtifactError("Issuer release registration invalid")
    raw, report = read_json(root / REPORT)
    manifest_raw, manifest = read_json(root / MANIFEST, 1_000_000)
    if (sha256(raw).hexdigest() != release["files"][REPORT] or
            sha256(manifest_raw).hexdigest() != release["files"][MANIFEST]):
        raise VisualArtifactError("Issuer evidence differs from its reviewed release")
    if report.get("manifest_sha256") != release["files"][MANIFEST]:
        raise VisualArtifactError("Issuer report uses a different frozen manifest")
    validate_issuer_report(root, report, root / MANIFEST)
    if (root / MANIFEST).read_bytes() != manifest_raw:
        raise VisualArtifactError("Issuer manifest changed while reading the release")
    return {"report": report, "manifest": manifest, "read_only": True,
            "validation_scope": "Reviewed release hashes, frozen rules and source references, complete fact inventory and recomputed public evidence. Original PDF/image bytes and full OCR are verified separately; this is a known-location issuer check, not blind extraction or authenticity proof."}


def issuer_image(root: Path, document_id: str, page_number: int, *, expected_sha256: str) -> bytes:
    view = issuer_snapshot(root)
    document = next((d for d in view["report"]["documents"] if d["id"] == document_id), None)
    page = next((p for p in document["pages"] if p["page"] == page_number), None) if document else None
    if page is None:
        raise FileNotFoundError("Issuer page is not registered")
    if expected_sha256 != page["image_sha256"]:
        raise VisualArtifactError("Issuer page registration changed; reload the result")
    base = (Path(root) / ".run/document-transfer/issuer-evaluation/raw").resolve()
    if not base.is_relative_to(Path(root).resolve()):
        raise VisualArtifactError("Issuer preview directory escaped the project")
    path = (base / document_id / f"page-{page_number}.png").resolve()
    if not path.is_relative_to(base):
        raise VisualArtifactError("Issuer image path escaped the local preview directory")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != page["image_sha256"]:
        raise VisualArtifactError("Issuer preview image differs from the registered bytes")
    return raw
