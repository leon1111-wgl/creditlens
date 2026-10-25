"""Read-only check of a date diagnostic against preserved full OCR and inputs.

No model is executed. File hashes establish agreement with the recorded local
inputs, not source authenticity or proof of the original model execution.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.date_diagnostic import read_json
from creditlens.date_evidence import crosscheck_date_fact
from creditlens.date_store import date_snapshot
from creditlens.diligence import digest
from creditlens.document_transfer import TransferPack
from creditlens.visual import OcrPage
from creditlens.word_evidence import WordPage


def _raw_hash(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _remap(value, indices: list[int]):
    """Map compact parent identifiers back to their recorded full-page index."""
    if isinstance(value, list):
        return [_remap(v, indices) for v in value]
    if isinstance(value, dict):
        return {k: indices[v] if k == "line_index" else
                [indices[i] for i in v] if k == "candidate_parent_indices" else
                _remap(v, indices) for k, v in value.items()}
    return value


def verify(root: Path, raw_dir: Path) -> dict:
    root, raw_dir = Path(root).resolve(), Path(raw_dir).resolve()
    view = date_snapshot(root)
    report, source = view["report"], view["source"]
    files, checked = {}, []
    new_docs = {d["id"]: d for d in report["documents"]}
    registrations = {d["id"]: d for d in source["manifest"]["documents"]}

    def path_for(doc_id, filename):
        target = (raw_dir / doc_id / filename).resolve()
        if not target.is_relative_to(raw_dir):
            raise ValueError("Raw evidence path escaped the selected directory")
        return target

    def hash_check(doc_id, filename, expected):
        path = path_for(doc_id, filename)
        actual = _raw_hash(path)
        if actual != expected:
            raise ValueError("Raw file hash differs: " + filename)
        files[f"{doc_id}/{filename}"] = actual
        return path

    for document in source["report"]["documents"]:
        doc_id = document["id"]
        registration = registrations[doc_id]
        hash_check(doc_id, registration["pdf"]["filename"], document["raw_pdf_sha256"])
        pack_path = root / registration["pack_path"]
        raw, value = read_json(pack_path)
        if sha256(raw).hexdigest() != registration["pack_sha256"]:
            raise ValueError("Reference changed while reading raw evidence")
        pack = TransferPack.model_validate(value)
        by_id = {f.id: f for f in pack.facts}
        expected_results = {f["fact_id"]: f["date"] for f in new_docs[doc_id]["facts"]}
        for page in document["pages"]:
            number = page["page"]
            hash_check(doc_id, f"page-{number}.png", page["image_sha256"])
            word_raw, word_data = read_json(path_for(doc_id, f"ocr-words-{number}.json"))
            line_raw, line_data = read_json(path_for(doc_id, f"ocr-lines-{number}.json"))
            words, lines = WordPage.model_validate(word_data), OcrPage.model_validate(line_data)
            if (digest(words.model_dump(mode="json")) != page["full_word_ocr_sha256"] or
                    digest(lines.model_dump(mode="json")) != page["full_line_ocr_sha256"] or
                    words.line_page() != lines):
                raise ValueError("Full OCR differs from the recorded input")
            if (words.page, words.width, words.height) != (number, page["width"], page["height"]):
                raise ValueError("Full OCR page geometry differs")
            for filename, raw in ((f"ocr-words-{number}.json", word_raw), (f"ocr-lines-{number}.json", line_raw)):
                files[f"{doc_id}/{filename}"] = sha256(raw).hexdigest()
            indices = [entry["source_line_index"] for entry in page["evidence_lines"]]
            for i, entry in zip(indices, page["evidence_lines"]):
                if words.lines[i].model_dump(mode="json") != {k: entry[k] for k in ("line", "words")}:
                    raise ValueError("Published parent line does not match full OCR")
            for fid in page["fact_ids"]:
                actual = crosscheck_date_fact(by_id[fid], words, min_confidence=report["min_confidence"])
                if actual != _remap(expected_results[fid], indices):
                    raise ValueError("Full-page and published date results differ: " + fid)
                checked.append({"document_id": doc_id, "fact_id": fid, "page": number,
                                "status": actual["status"], "exact_after_parent_index_mapping": True})
    return {"schema_version": 1, "operation": "verify_preserved_full_ocr_no_inference",
            "model_inference_performed": False, "source_authenticity_verified": False,
            "diagnostic_generated_at": report["generated_at"], "source": report["source"],
            "raw_file_sha256": files, "facts": checked,
            "distinct_date_groups": report["summary"]["distinct_date_groups"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=ROOT/".run/reproductions/document-transfer")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(ROOT, args.raw_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        parser.exit(1, "Raw diagnostic verification failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
