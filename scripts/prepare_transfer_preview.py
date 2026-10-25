"""Prepare reviewed local page images without replacing a different preview."""
import argparse
from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.experiment_paths import validate_write_path
from creditlens.transfer_store import transfer_snapshot


def prepare_preview(root: Path, source_dir: Path) -> dict:
    view = transfer_snapshot(root)
    base = (root/".run/document-transfer/raw").resolve()
    sources = Path(source_dir).resolve()
    pending = []
    preserved = 0
    for document in view["report"]["documents"]:
        for page in document["pages"]:
            name = f"page-{page['page']}.png"
            source = (sources/document["id"]/name).resolve()
            if not source.is_relative_to(sources):
                raise ValueError("Source image escapes its selected directory")
            raw = source.read_bytes()
            if sha256(raw).hexdigest() != page["image_sha256"]:
                raise ValueError("Source image differs from the reviewed experiment")
            target = validate_write_path(root, base/document["id"]/name)
            if not target.is_relative_to(base):
                raise ValueError("Preview target escapes its registered directory")
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("Existing preview differs; no images were replaced")
                preserved += 1
            else:
                pending.append((target, raw))
    # Check every source/target before creating any preview. Exclusive creation
    # prevents overwriting a file introduced between this check and the write.
    for target, raw in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
    return {"created": len(pending), "preserved": preserved}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT/".run/reproductions/document-transfer")
    args = parser.parse_args()
    print(prepare_preview(ROOT, args.source_dir))
