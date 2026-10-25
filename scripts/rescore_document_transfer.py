"""Recheck a corrected reference against preserved OCR; never run inference.

The old manifest/report and complete OCR, PDF and image bytes are read-only.
Inference implementation/configuration must be unchanged; reference construction
and reference positions may change. A new private report records that distinction.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import digest
from creditlens.document_transfer import (TransferManifest, TransferPack, TransferReport,
    _validate_loaded_report, compact_page, file_hash, load_manifest, summarize)
from creditlens.experiment_paths import validate_write_path
from creditlens.visual import OcrPage
from creditlens.word_evidence import WordLine, WordPage


def require(value, message):
    if not value:
        raise ValueError(message)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def _finite(value):
    number = float(value)
    require(math.isfinite(number), "Non-finite JSON number")
    return number


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def read_bytes(path: Path, limit: int, *, within: Path | None = None):
    resolved = path.resolve()
    if within is not None:
        require(resolved.is_relative_to(within.resolve()), "Registered input escaped its directory")
    require(resolved.is_file(), "Registered input is missing or not a regular file")
    require(resolved.stat().st_size <= limit, "Registered input exceeds its size limit")
    raw = resolved.read_bytes()
    require(len(raw) <= limit, "Registered input exceeds its size limit")
    return raw


def read_json(path: Path, limit: int, *, within: Path | None = None):
    raw = read_bytes(path, limit, within=within)
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_reject_constant, parse_float=_finite)
    return raw, value


def output_path(root: Path, output: Path, local_dir: Path, inputs: tuple[Path, ...]):
    path = validate_write_path(root, output)
    require(path.suffix.lower() == ".json" and not path.exists(), "Rescored output must be a new JSON file")
    protected = (local_dir.resolve(), (root/".run/document-ai").resolve(), (root/".run/document-transfer").resolve())
    require(not any(path.is_relative_to(p) or p.is_relative_to(path) for p in protected),
            "Rescored output cannot overwrite original OCR or registered previews")
    require(path not in {p.resolve() for p in inputs}, "Rescored output cannot replace an input")
    return path


def load_source(root, report_path, manifest_path, current, current_packs):
    raw_manifest, manifest_value = read_json(manifest_path, 1_000_000, within=root/"configs")
    source = TransferManifest.model_validate(manifest_value)
    source._snapshot_sha256 = sha256(raw_manifest).hexdigest()
    require(source.files == current.files, "Source and current inference/configuration files differ")
    # The current load_manifest already validated these exact implementation hashes.
    # Old reference builders are intentionally not loaded/executed: geometry changed.
    source._profile = current.profile
    require(source.frozen_at <= datetime.now(timezone.utc), "Source freeze time cannot be in the future")
    require([d.id for d in source.documents] == [d.id for d in current.documents], "Transfer documents changed")
    packs = {}
    for old, new in zip(source.documents, current.documents):
        require(old.pages == new.pages, "Page, image or registered fact inventory changed")
        require(old.pdf == new.pdf, "Registered source PDF or publication boundary changed")
        raw_pack, pack_value = read_json(root/old.pack_path, 5_000_000, within=root/"data/examples")
        require(sha256(raw_pack).hexdigest() == old.pack_sha256, "Source reference pack hash changed")
        pack = TransferPack.model_validate(pack_value)
        replacement = current_packs[old.id]
        require(pack.company == replacement.company and pack.entity_id == replacement.entity_id and
                pack.sources == replacement.sources and pack.fiscal_calendar == replacement.fiscal_calendar,
                "Reference issuer, source or calendar changed")
        originals = {f.id: f for f in pack.facts}
        replacements = {f.id: f for f in replacement.facts}
        require(originals.keys() == replacements.keys(), "Reference facts are missing or added")
        for fid, fact in originals.items():
            before, after = fact.model_dump(mode="json"), replacements[fid].model_dump(mode="json")
            old_loc, new_loc = before.pop("pdf_location"), after.pop("pdf_location")
            # A reference-coordinate correction cannot silently change financial answers.
            require(before == after, "Financial values, labels or periods changed during geometry rescoring")
            for key in ("page", "page_width", "page_height", "coordinate_system", "row_label", "year", "period_header", "unit_header"):
                require(old_loc[key] == new_loc[key], "Reference field identity or page changed")
            old_section, new_section = old_loc.get("section"), new_loc.get("section")
            require(bool(old_section) == bool(new_section), "Reference section membership changed")
            if old_section:
                require(all(old_section[key] == new_section[key] for key in ("start_label", "end_label")),
                        "Reference section labels changed")
        packs[old.id] = pack
    raw_report, report_value = read_json(report_path, 10_000_000)
    TransferReport.model_validate(report_value)
    report = _validate_loaded_report(report_value, source, packs)
    return source, packs, report, sha256(raw_report).hexdigest()


def run(*, source_report, source_manifest=None, local_dir=None, manifest=None, output=None):
    old_report_path = Path(source_report)
    old_manifest_path = Path(source_manifest) if source_manifest is not None else ROOT/"configs/document-transfer-v1-invalid.json"
    manifest_path = Path(manifest) if manifest is not None else ROOT/"configs/document-transfer.json"
    directory = Path(local_dir) if local_dir is not None else ROOT/".run/reproductions/document-transfer"
    destination = output_path(ROOT, Path(output) if output is not None else ROOT/".run/reproductions/document-transfer-rescored.json",
                              directory, (old_report_path, old_manifest_path, manifest_path))
    current, packs = load_manifest(ROOT, manifest_path)
    old, old_packs, source, source_hash = load_source(ROOT, old_report_path, old_manifest_path, current, packs)
    report = {key: deepcopy(source[key]) for key in ("schema_version", "suite_version", "rule_versions", "scope", "environment")}
    report.update(experiment_id=current.experiment_id, generated_at=datetime.now(timezone.utc).isoformat(),
                  manifest_sha256=current.snapshot_sha256, documents=[])
    report["environment"].update(operation="rescoring_preserved_ocr",
        inference_generated_at=source["environment"].get("inference_generated_at", source["generated_at"]),
        source_report_sha256=source_hash, source_manifest_sha256=old.snapshot_sha256,
        elapsed_seconds_basis="Copied from the source inference report; no model inference occurs during rescoring.",
        rescore_implementation_sha256=file_hash(Path(__file__)),
        source_reference_code_check="Old reference-builder hashes retained as provenance, not executed; inference files must be identical.")
    all_pairs = []
    for original, registration in zip(source["documents"], current.documents):
        pack, old_pack = packs[registration.id], old_packs[registration.id]
        root_dir = directory/registration.id
        pdf = read_bytes(root_dir/registration.pdf.filename, 60_000_000, within=directory)
        require(sha256(pdf).hexdigest() == registration.pdf.sha256 == original["raw_pdf_sha256"], "Raw PDF hash changed")
        by_id, old_by_id = {f.id: f for f in pack.facts}, {f.id: f for f in old_pack.facts}
        document = {"id": registration.id, "company": pack.company, "source": pack.sources[0].model_dump(mode="json"),
                    "source_publication": registration.pdf.model_dump(mode="json"), "pack_sha256": registration.pack_sha256,
                    "raw_pdf_sha256": registration.pdf.sha256, "pages": [], "facts": []}
        for previous, page in zip(original["pages"], registration.pages):
            image = read_bytes(root_dir/f"page-{page.page}.png", 25_000_000, within=directory)
            require(sha256(image).hexdigest() == page.image_sha256, "Preserved page image hash changed")
            _, raw_word = read_json(root_dir/f"ocr-words-{page.page}.json", 25_000_000, within=directory)
            _, raw_line = read_json(root_dir/f"ocr-lines-{page.page}.json", 25_000_000, within=directory)
            words, line = WordPage.model_validate(raw_word), OcrPage.model_validate(raw_line)
            require((words.page, words.width, words.height) == (page.page, page.width, page.height), "Complete OCR page registration changed")
            require(line == words.line_page(), "Preserved line OCR differs from the word page parent lines")
            require(digest(words.model_dump(mode="json")) == previous["full_word_ocr_sha256"] and
                    digest(line.model_dump(mode="json")) == previous["full_line_ocr_sha256"], "Preserved full OCR hash changed")
            require(len(words.lines) == previous["ocr_line_count"], "Original OCR line count changed")
            old_evidence, old_pairs = compact_page(words, [old_by_id[fid] for fid in page.fact_ids], current.profile["min_confidence"])
            require(old_evidence == previous["evidence_lines"] and old_pairs == [f for f in original["facts"] if f["page"] == page.page],
                    "Complete preserved OCR does not reproduce the source report")
            evidence, pairs = compact_page(words, [by_id[fid] for fid in page.fact_ids], current.profile["min_confidence"])
            compact = WordPage(page=page.page, width=page.width, height=page.height,
                               lines=[WordLine.model_validate({key: item[key] for key in ("line", "words")}) for item in evidence])
            document["pages"].append({**page.model_dump(mode="json"),
                "full_line_ocr_sha256": previous["full_line_ocr_sha256"], "full_word_ocr_sha256": previous["full_word_ocr_sha256"],
                "evidence_sha256": digest(compact.model_dump(mode="json")), "ocr_line_count": previous["ocr_line_count"],
                "elapsed_seconds": previous["elapsed_seconds"], "evidence_lines": evidence})
            document["facts"].extend(pairs)
        document["summary"] = summarize(document["facts"])
        all_pairs.extend(document["facts"])
        report["documents"].append(document)
    report["summary"] = summarize(all_pairs)
    report = _validate_loaded_report(report, current, packs)
    # All inputs and outputs have been checked in memory before the first write.
    destination = output_path(ROOT, destination, directory, (old_report_path, old_manifest_path, manifest_path))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"operation": "rescoring_preserved_ocr", "summary": report["summary"], "output": str(destination)}))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, default=ROOT/"configs/document-transfer-v1-invalid.json")
    parser.add_argument("--local-dir", type=Path, default=ROOT/".run/reproductions/document-transfer")
    parser.add_argument("--manifest", type=Path, default=ROOT/"configs/document-transfer.json")
    parser.add_argument("--output", type=Path, default=ROOT/".run/reproductions/document-transfer-rescored.json")
    args = parser.parse_args(argv)
    try:
        run(source_report=args.source_report, source_manifest=args.source_manifest, local_dir=args.local_dir,
            manifest=args.manifest, output=args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Rescoring failed: "+str(exc).splitlines()[0]+"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
