"""Copy the pinned PP-OCRv5 files for 截图核对 into a built site (GitHub Actions only).

The page loads the OCR weights from its own site, not from Hugging Face directly: Hugging Face's
web application firewall answers automated (headless) browsers with a CAPTCHA page, so a page that
fetched from Hugging Face could not be tested on Actions. This build step downloads the files once
from the pinned Hugging Face revision, checks size and SHA-256 against configs/table-check.json, and
writes them under the site; the page checks the SHA-256 again before running anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--into", required=True, type=Path, help="e.g. site/models/pp-ocrv5")
    args = parser.parse_args(argv)
    if not os.getenv("GITHUB_ACTIONS"):
        raise SystemExit("Downloads model weights; runs on GitHub Actions only (no weights are kept on a developer machine).")
    config = json.loads((ROOT / "configs/table-check.json").read_text(encoding="utf-8"))
    weights = config["weights"]
    args.into.mkdir(parents=True, exist_ok=True)
    for name, spec in weights["files"].items():
        request = Request(weights["base_url"] + name, headers={"User-Agent": "CreditLens site build (https://github.com/leon1111-wgl/creditlens)"})
        with urlopen(request, timeout=120) as response:
            raw = response.read(spec["bytes"] + 1)
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != spec["bytes"] or digest != spec["sha256_hex"]:
            raise SystemExit(f"{name}: size or SHA-256 differs from the pinned file")
        (args.into / name).write_bytes(raw)
        print(json.dumps({"file": name, "bytes": len(raw), "sha256": digest}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
