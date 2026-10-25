"""Read-only verification of a frozen issuer report against complete local OCR.

No model runs and no file is written. Hash agreement does not establish source
authenticity or prove that the recorded model execution actually happened.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.diligence import digest
from creditlens.document_transfer import file_hash
from creditlens.issuer_evaluation import (_validate_loaded_report, check_fact,
                                          load_manifest, read_json, require)
from creditlens.visual import OcrPage
from creditlens.word_evidence import WordPage


def _file_hash(path: Path, limit: int) -> str:
    require(path.is_file() and path.stat().st_size <= limit, "Missing or oversized raw issuer file")
    result, total = sha256(), 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            total += len(chunk)
            require(total <= limit, "Raw issuer file grew beyond its size limit")
            result.update(chunk)
    return result.hexdigest()


def _remap(value, indices: list[int]):
    """Translate every public parent index to its preserved full-page index."""
    def mapped(index):
        require(type(index) is int and 0 <= index < len(indices), "Invalid public parent index")
        return indices[index]
    if isinstance(value, list):
        return [_remap(item, indices) for item in value]
    if isinstance(value, dict):
        return {key: mapped(item) if key == "line_index" else
                [mapped(index) for index in item] if key == "candidate_parent_indices" else
                _remap(item, indices) for key, item in value.items()}
    return value


def verify(root: Path, raw_dir: Path | None = None, *,
           report_path: Path | None = None, manifest_path: Path | None = None) -> dict:
    root = Path(root).resolve()
    raw_dir = Path(raw_dir).resolve() if raw_dir is not None else (root/".run/reproductions/issuer-evaluation").resolve()
    report_path = Path(report_path) if report_path is not None else root/"artifacts/issuer-evaluation-report.json"
    frozen, packs = load_manifest(root, manifest_path)
    report_raw, report_data = read_json(report_path)
    report = _validate_loaded_report(report_data, frozen, packs)
    registrations = {doc.id: doc for doc in frozen.documents}
    files, canonical, checked, page_count = {}, {}, [], 0

    def path_for(document_id, filename):
        path = (raw_dir/document_id/filename).resolve()
        require(path.is_relative_to(raw_dir), "Raw issuer evidence path escaped the selected directory")
        return path

    def hash_check(document_id, filename, expected, limit):
        actual = _file_hash(path_for(document_id, filename), limit)
        require(actual == expected, "Raw issuer file hash differs: " + filename)
        files[f"{document_id}/{filename}"] = actual

    for document in report["documents"]:
        document_id = document["id"]
        registration, pack = registrations[document_id], packs[document_id]
        hash_check(document_id, registration.pdf.filename, document["raw_pdf_sha256"], 60_000_000)
        by_id = {fact.id: fact for fact in pack.facts}
        public = {item["fact_id"]: item for item in document["facts"]}
        for page in document["pages"]:
            number = page["page"]
            hash_check(document_id, f"page-{number}.png", page["image_sha256"], 25_000_000)
            word_name, line_name = f"ocr-words-{number}.json", f"ocr-lines-{number}.json"
            word_raw, word_data = read_json(path_for(document_id, word_name), 25_000_000)
            line_raw, line_data = read_json(path_for(document_id, line_name), 25_000_000)
            words, lines = WordPage.model_validate(word_data), OcrPage.model_validate(line_data)
            word_hash, line_hash = digest(words.model_dump(mode="json")), digest(lines.model_dump(mode="json"))
            require(word_hash == page["full_word_ocr_sha256"] and line_hash == page["full_line_ocr_sha256"],
                    "Complete issuer OCR differs from its recorded canonical hash")
            require(words.line_page() == lines, "Complete word OCR does not project to the recorded line OCR")
            require((words.page, words.width, words.height) == (number, page["width"], page["height"]) and
                    len(words.lines) == page["ocr_line_count"], "Complete issuer OCR page geometry or line count differs")
            files[f"{document_id}/{word_name}"] = sha256(word_raw).hexdigest()
            files[f"{document_id}/{line_name}"] = sha256(line_raw).hexdigest()
            canonical[f"{document_id}/{word_name}"] = word_hash
            canonical[f"{document_id}/{line_name}"] = line_hash
            indices = [entry["source_line_index"] for entry in page["evidence_lines"]]
            for index, entry in zip(indices, page["evidence_lines"]):
                require(0 <= index < len(words.lines), "Published source index is outside complete OCR")
                expected = {key: entry[key] for key in ("line", "words")}
                actual = words.lines[index].model_dump(mode="json")
                require(actual == expected and digest(actual) == digest(expected),
                        "Published complete parent/word evidence does not match its full OCR source index")
            for fact_id in page["fact_ids"]:
                actual = check_fact(by_id[fact_id], words, frozen.profile["min_confidence"])
                expected = _remap({key: public[fact_id][key] for key in ("fact_id", "page", "line", "word", "date")}, indices)
                require(actual == expected and digest(actual) == digest(expected),
                        "Complete OCR and published three-rule results differ: " + fact_id)
                checked.append({"document_id": document_id, "fact_id": fact_id, "page": number,
                                "statuses": {method: actual[method]["status"] for method in ("line", "word", "date")},
                                "exact_after_parent_index_mapping": True})
            page_count += 1
    return {"schema_version": 1, "operation": "verify_complete_issuer_inputs_no_inference",
            "model_inference_performed": False, "original_model_execution_verified": False,
            "source_authenticity_verified": False, "published_parent_membership_verified": True,
            "full_ocr_canonical_hashes_verified": True, "pdf_and_image_raw_hashes_verified": True,
            "experiment_id": report["experiment_id"], "report_generated_at": report["generated_at"],
            "source_report_sha256": sha256(report_raw).hexdigest(), "source_manifest_sha256": frozen.snapshot_sha256,
            "verification_implementation_sha256": file_hash(Path(__file__)),
            "raw_file_sha256": files, "full_ocr_canonical_sha256": canonical,
            "facts": checked, "distinct_pages": page_count,
            "distinct_date_groups": report["summary"]["distinct_date_groups"],
            "scope": "Exact local-file and full-OCR agreement with the frozen report, including public parent membership and three-rule replay. This is not source authentication, proof of original model execution, or an additional independent evaluation sample."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(ROOT, args.raw_dir, report_path=args.report, manifest_path=args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, IndexError) as exc:
        parser.exit(1, "Raw issuer verification failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
