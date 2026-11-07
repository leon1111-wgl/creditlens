"""Build (or check) creditlens/ask-pack.json, the data the in-browser answer engine reads.

The pack holds the registered observations of creditlens.evidence_answers plus the
saved OCR spans of each cited page, so the tab can draw a 重建示意 preview. It never
contains a page image. `--check` fails when the committed pack differs from a rebuild.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.evidence_answers import build_answer_pack  # noqa: E402
from scripts.export_static_demo import PageSketches  # noqa: E402

PACK = ROOT/"creditlens/ask-pack.json"
REPORTS = ("artifacts/table-lookup-report.json", "artifacts/word-table-lookup-report.json",
           "artifacts/role-table-lookup-report.json")


def page_spans(root: Path = ROOT) -> dict:
    """Saved OCR spans per cited page, deduplicated like the static demo's page sketches."""
    sketches = PageSketches()
    for rel in REPORTS:
        report = json.loads((root/rel).read_text(encoding="utf-8"))
        for document in report["documents"]:
            pages = {p["page"]: p for p in document["pages"]}
            for case in document["cases"]:
                meta = pages[case["page"]]
                sketches.add(document["id"].upper(), case["page"], meta["width"], meta["height"], case)
    margin = json.loads((root/"artifacts/margin-bridge-report.json").read_text(encoding="utf-8"))
    for case in margin["cases"]:
        page = case["page"]
        sketches.add(case["document_id"].upper(), page["page"], page["width"], page["height"], case)
    out = {}
    for (document_id, page), entry in sorted(sketches.pages.items()):
        spans = PageSketches._drawable(entry["spans"].values())
        out[f"{document_id}:{page}"] = {
            "width": round(entry["width"], 2), "height": round(entry["height"], 2),
            "spans": [[text, *[round(v, 1) for v in box]] for text, box in spans],
        }
    return out


def render(root: Path = ROOT) -> str:
    pack = build_answer_pack(root, page_spans(root))
    return json.dumps(pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed pack is stale")
    args = parser.parse_args(argv)
    text = render()
    if args.check:
        current = PACK.read_text(encoding="utf-8") if PACK.exists() else ""
        if current != text:
            print("ask-pack.json is stale: run python scripts/build_browser_pack.py", file=sys.stderr)
            return 1
        print(json.dumps({"status": "pack_current", "bytes": len(text.encode())}))
        return 0
    PACK.write_text(text, encoding="utf-8")
    print(json.dumps({"status": "pack_written", "path": str(PACK.relative_to(ROOT)), "bytes": len(text.encode())}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
