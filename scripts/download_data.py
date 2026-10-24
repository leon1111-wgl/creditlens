"""Download a pinned public FinQA development split and record provenance."""
import hashlib
import json
from pathlib import Path
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
REVISION = "0f16e2867befa6840783e58be38c9efb9229d742"
URL = f"https://raw.githubusercontent.com/czyssrs/FinQA/{REVISION}/dataset/dev.json"
# Git blob hash reported by GitHub for the pinned file.
EXPECTED_BLOB_SHA1 = "970f239f591c782b17df420c0d343b780737f3da"


def main():
    directory = ROOT / "data/raw"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "finqa-dev.json"
    if path.exists():
        content = path.read_bytes()
    else:
        request = urllib.request.Request(URL, headers={"User-Agent": "CreditLens-research/0.1"})
        with urllib.request.urlopen(request, timeout=90) as response:
            content = response.read(20_000_001)
    if len(content) > 20_000_000:
        raise ValueError("Unexpected dataset size")
    blob_hash = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
    if blob_hash != EXPECTED_BLOB_SHA1:
        raise ValueError("Dataset checksum mismatch; file has not been accepted")
    records = json.loads(content)
    if not isinstance(records, list) or len(records) != 883:
        raise ValueError("Unexpected development split")
    for record in records:
        if not {"id", "table", "pre_text", "post_text", "qa"} <= record.keys():
            raise ValueError("Dataset schema mismatch")
    if not path.exists():
        temp = path.with_suffix(".tmp")
        temp.write_bytes(content)
        temp.replace(path)
    manifest = {
        "dataset": "FinQA", "split": "dev", "records": len(records),
        "source_url": URL, "revision": REVISION,
        "git_blob_sha1": blob_hash, "sha256": hashlib.sha256(content).hexdigest(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "license_note": "Upstream repo MIT; underlying company filings may have separate rights.",
        "use": "Local research evaluation; gold fields are excluded from runtime ingestion.",
    }
    (ROOT / "data/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
