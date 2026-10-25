"""Read-only verification of saved complete OCR against a currency diagnostic.

No PDF/image is read, no download or OCR is performed, and no file is written.
This checks preserved JSON and exact rule replay, not the original model run,
source authenticity, image correspondence or a new independent evaluation.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.currency_diagnostic import currency_diagnostic_view
from creditlens.currency_evidence import crosscheck_currency_fact
from creditlens.diligence import digest
from creditlens.document_transfer import file_hash
from creditlens.issuer_evaluation import IssuerPack, check_fact, read_json, require
from creditlens.visual import OcrPage
from creditlens.word_evidence import WordPage


RAW_JSON_LIMIT = 25_000_000
DEFAULT_REPORT = "artifacts/currency-diagnostic-report.json"


def remap_parent_indices(value, indices: list[int]):
    """Translate only parent references; word/year-word indices stay local."""
    require(type(indices) is list and all(type(i) is int and i >= 0 for i in indices)
            and len(indices) == len(set(indices)), "Invalid compact-to-full parent mapping")
    def mapped(index):
        require(type(index) is int and 0 <= index < len(indices), "Invalid compact parent reference")
        return indices[index]
    def visit(item):
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                if key in {"line_index", "current_header_index"}:
                    result[key] = mapped(child)
                elif key == "candidate_parent_indices":
                    require(type(child) is list, "Candidate parent references must be a list")
                    result[key] = [mapped(index) for index in child]
                else:
                    result[key] = visit(child)
            return result
        return item
    return visit(value)


def verify(root: Path, raw_dir: Path | None = None, *, report_path: Path | None = None) -> dict:
    """Verify a released source and candidate diagnostic using four saved JSONs.

    raw_dir is the parent containing registered document-id subdirectories.
    A report override permits review of an unpublished local diagnostic; it does
    not mark that candidate as an approved or published currency release.
    """
    root = Path(root).resolve()
    raw_dir = (Path(raw_dir) if raw_dir is not None else root/".run/reproductions/issuer-evaluation").resolve()
    report_path = Path(report_path) if report_path is not None else root/DEFAULT_REPORT
    diagnostic_raw, diagnostic_data = read_json(report_path)
    view = currency_diagnostic_view(root, diagnostic_data)
    diagnostic, source = view["report"], view["source"]
    registrations = {d["id"]: d for d in source["manifest"]["documents"]}
    new_documents = {d["id"]: d for d in diagnostic["documents"]}
    file_hashes, canonical_hashes, pages_checked, facts_checked = {}, {}, [], []
    public_parent_count = 0

    def path_for(document_id: str, filename: str) -> Path:
        path = (raw_dir/document_id/filename).resolve()
        require(path.is_relative_to(raw_dir), "Saved OCR path escaped the selected directory")
        return path

    for document in source["report"]["documents"]:
        document_id = document["id"]
        registration = registrations[document_id]
        pack_path = (root/registration["pack_path"]).resolve()
        require(pack_path.is_relative_to((root/"data/examples").resolve()), "Reference pack escaped examples")
        pack_raw, pack_data = read_json(pack_path, 5_000_000)
        require(sha256(pack_raw).hexdigest() == registration["pack_sha256"], "Reference pack changed during raw verification")
        pack = IssuerPack.model_validate(pack_data)
        facts = {fact.id: fact for fact in pack.facts}
        old_results = {item["fact_id"]: item for item in document["facts"]}
        new_results = {item["fact_id"]: item for item in new_documents[document_id]["facts"]}
        checked_ids = set()
        for page in document["pages"]:
            number = page["page"]
            word_name, line_name = f"ocr-words-{number}.json", f"ocr-lines-{number}.json"
            word_raw, word_data = read_json(path_for(document_id, word_name), RAW_JSON_LIMIT)
            line_raw, line_data = read_json(path_for(document_id, line_name), RAW_JSON_LIMIT)
            words, lines = WordPage.model_validate(word_data), OcrPage.model_validate(line_data)
            word_model, line_model = words.model_dump(mode="json"), lines.model_dump(mode="json")
            word_hash, line_hash = digest(word_data), digest(line_data)
            # Check raw JSON semantics as well as the typed projection: a string
            # "74", boolean, or int/float rewrite must not hide in coercion.
            require(word_data == word_model and line_data == line_model and
                    word_hash == digest(word_model) == page["full_word_ocr_sha256"] and
                    line_hash == digest(line_model) == page["full_line_ocr_sha256"],
                    "Complete saved OCR canonical hash or typed semantics differs")
            require(words.line_page() == lines and digest(words.line_page().model_dump(mode="json")) == line_hash,
                    "Complete word OCR does not project exactly to line OCR")
            require((words.page, words.width, words.height) == (number, page["width"], page["height"]) and
                    (lines.page, lines.width, lines.height) == (number, page["width"], page["height"]) and
                    type(page["ocr_line_count"]) is int and len(words.lines) == len(lines.spans) == page["ocr_line_count"],
                    "Saved OCR geometry or complete line count differs")
            indices = [entry["source_line_index"] for entry in page["evidence_lines"]]
            require(all(type(i) is int and 0 <= i < len(words.lines) for i in indices) and
                    indices == sorted(set(indices)), "Published parent source indices are invalid or repeated")
            for index, entry in zip(indices, page["evidence_lines"]):
                expected = {key: entry[key] for key in ("line", "words")}
                actual = words.lines[index].model_dump(mode="json")
                require(actual == expected and digest(actual) == digest(expected),
                        "Published parent/word evidence differs from its complete-page source index")
            for fact_id in page["fact_ids"]:
                require(fact_id in facts and fact_id not in checked_ids, "Missing or repeated fact in complete OCR replay")
                old_actual = check_fact(facts[fact_id], words, diagnostic["min_confidence"])
                old_expected = remap_parent_indices({key: old_results[fact_id][key]
                                  for key in ("fact_id", "page", "line", "word", "date")}, indices)
                require(old_actual == old_expected and digest(old_actual) == digest(old_expected),
                        "Complete OCR no longer reproduces the frozen line/word/date result: " + fact_id)
                actual = crosscheck_currency_fact(facts[fact_id], words, min_confidence=diagnostic["min_confidence"])
                expected = remap_parent_indices(new_results[fact_id]["currency"], indices)
                require(actual == expected and digest(actual) == digest(expected),
                        "Complete OCR and public currency diagnostic differ: " + fact_id)
                unchanged = ([c for c in actual["checks"] if c["field"] != "amount"] ==
                             [c for c in old_actual["date"]["checks"] if c["field"] != "amount"] and
                             actual["date_association"] == old_actual["date"]["date_association"])
                require(unchanged and new_results[fact_id]["non_amount_unchanged"] is True and
                        actual["automatic_approval"] is False, "Currency replay changed unrelated financial evidence")
                facts_checked.append({"document_id": document_id, "fact_id": fact_id, "page": number,
                                      "legacy_date_status": old_actual["date"]["status"], "currency_status": actual["status"],
                                      "association_applied": actual["currency_association"]["applied"],
                                      "exact_after_parent_index_mapping": True, "non_amount_unchanged": True})
                checked_ids.add(fact_id)
            for name, raw, canonical in ((word_name, word_raw, word_hash), (line_name, line_raw, line_hash)):
                file_hashes[f"{document_id}/{name}"] = sha256(raw).hexdigest()
                canonical_hashes[f"{document_id}/{name}"] = canonical
            public_parent_count += len(indices)
            pages_checked.append({"document_id": document_id, "page": number,
                                  "complete_parent_lines": len(words.lines), "published_parent_lines": len(indices)})
        require(checked_ids == set(facts), "Not every registered fact was replayed against complete OCR")
    require(len(facts_checked) == source["report"]["summary"]["facts"] == diagnostic["summary"]["facts"],
            "Complete OCR fact inventory differs from the source or diagnostic")
    return {"schema_version": 1, "operation": "verify_preserved_currency_ocr_without_pdf_images_or_inference",
            "status": "verified", "diagnostic_report_sha256": sha256(diagnostic_raw).hexdigest(),
            "source_report_sha256": diagnostic["source"]["report_sha256"],
            "source_manifest_sha256": diagnostic["source"]["manifest_sha256"],
            "source_release_sha256": diagnostic["source"]["release_sha256"],
            "verification_implementation_sha256": file_hash(Path(__file__)),
            "full_ocr_canonical_hashes_verified": True, "word_to_line_projection_verified": True,
            "published_parent_membership_verified": True, "published_parent_lines_checked": public_parent_count,
            "full_ocr_rule_replay_verified": True, "facts_checked": len(facts_checked),
            "pages": pages_checked, "facts": facts_checked,
            "raw_ocr_file_sha256": file_hashes, "full_ocr_canonical_sha256": canonical_hashes,
            "pdf_and_image_bytes_verified": False, "model_inference_performed": False,
            "original_model_execution_verified": False, "source_authenticity_verified": False,
            "candidate_report_is_not_a_release_approval": True,
            "synthetic_controls_are_separate_from_raw_ocr_samples": True,
            "scope": "Exact agreement of preserved complete OCR JSON, reviewed public parent membership, and current deterministic rules. No PDF/image read, model execution, source authentication or additional independent evaluation sample."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, help="Parent of registered document-id folders containing saved OCR JSON")
    parser.add_argument("--report", type=Path, help="Currency diagnostic JSON; may be an unpublished local candidate")
    args = parser.parse_args(argv)
    try:
        result = verify(ROOT, args.raw_dir, report_path=args.report)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc).splitlines()[0],
                          "scope": "Saved OCR consistency only; PDF/images and original execution are not verified"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
