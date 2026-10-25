"""Compare three local candidate reports; never replace the frozen release."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.reproduction import KINDS, default_candidates, exit_code, review_reproduction
from creditlens.experiment_paths import validate_write_path


def export_summary(root: Path, output: Path, summary: dict, candidates: dict[str, Path]):
    """Create a new local summary only; disallow release/input overwrite."""
    path = validate_write_path(root, output)
    allowed = (root/".run").resolve()
    preview = (root/".run/document-ai").resolve()
    if not path.is_relative_to(allowed) or path in {p.resolve() for p in candidates.values()}:
        raise ValueError("Summary output must be a new file under the repository .run directory")
    if path.is_relative_to(preview) or preview.is_relative_to(path):
        raise ValueError("Summary output cannot use the registered document preview directory")
    if path.suffix.lower() != ".json" or path.exists():
        raise ValueError("Summary output must be a new JSON file")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for kind, path in default_candidates(ROOT).items():
        parser.add_argument("--"+kind, type=Path, default=path)
    parser.add_argument("--output", type=Path, help="Optional new JSON path under .run; existing files are never overwritten")
    args = parser.parse_args(argv)
    candidates = {kind: getattr(args, kind) for kind in KINDS}
    summary = review_reproduction(ROOT, candidates)
    if args.output:
        try:
            export_summary(ROOT, args.output, summary, candidates)
        except (ValueError, OSError):
            print(json.dumps({"status": "invalid", "error": "Summary output rejected; no release files modified"}))
            return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return exit_code(summary)


if __name__ == "__main__":
    sys.exit(main())
