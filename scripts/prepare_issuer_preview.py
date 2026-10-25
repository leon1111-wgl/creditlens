"""Copy only the already reviewed and hash-matching local issuer page images."""
from hashlib import sha256
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.issuer_store import issuer_snapshot


def prepare(root: Path, source_dir: Path) -> dict:
    root, source_dir = Path(root).resolve(), Path(source_dir).resolve()
    view = issuer_snapshot(root)
    destination = (root / ".run/document-transfer/issuer-evaluation/raw").resolve()
    if not destination.is_relative_to(root):
        raise ValueError("Preview directory escapes the project")
    planned = []
    preserved = 0
    for document in view["report"]["documents"]:
        for page in document["pages"]:
            relative = Path(document["id"]) / f"page-{page['page']}.png"
            source, target = (source_dir/relative).resolve(), (destination/relative).resolve()
            if not source.is_relative_to(source_dir) or not target.is_relative_to(destination):
                raise ValueError("Preview path escaped its registered directory")
            raw = source.read_bytes()
            if sha256(raw).hexdigest() != page["image_sha256"]:
                raise ValueError("Source page image differs from the reviewed image")
            if target.exists():
                if target.read_bytes() != raw:
                    raise ValueError("Existing issuer preview differs; refusing to replace it")
                preserved += 1
            else:
                planned.append((target, raw))
    # Check every input before any write. This is a local copying utility, not
    # a sandbox against another process modifying paths concurrently.
    for target, raw in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
    return {"created": len(planned), "preserved": preserved}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT/".run/reproductions/issuer-evaluation")
    args = parser.parse_args()
    print(prepare(ROOT, args.source_dir))
