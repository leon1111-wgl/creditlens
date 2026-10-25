"""Run image-only OCR once per frozen new-issuer page, then three fixed rules."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from creditlens.diligence import digest
from creditlens.experiment_paths import validate_experiment_paths, validate_write_path
from creditlens.issuer_evaluation import (EXECUTION_PACKAGES, RULES, SCOPE, VERSION, compact_page, date_groups,
    load_manifest, require, summarize, validate_issuer_report)
from creditlens.word_evidence import WordLine, WordPage
from scripts.evaluate_document_transfer import obtain_pdf
from scripts.evaluate_visual_robustness import create_engine
from scripts.evaluate_word_evidence import adapt_result


def write_new_bytes(root: Path, path: Path, raw: bytes):
    target = validate_write_path(root, path)
    require(not target.exists(), "Issuer output already exists; use a fresh run directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(raw)


def write_new_json(root: Path, path: Path, value):
    write_new_bytes(root, path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n").encode("utf-8"))


def _paths(output, local_dir):
    paths = validate_experiment_paths(
        ROOT, Path(output) if output is not None else ROOT/".run/reproductions/issuer-evaluation-report.json",
        Path(local_dir) if local_dir is not None else ROOT/".run/reproductions/issuer-evaluation",
        read_only_dirs=tuple(ROOT/name for name in (
            ".run/issuer-evaluation", ".run/transfer-attempts", ".run/reproductions/document-transfer",
            ".run/reproductions/document-ai", ".run/reproductions/document-ai-words", ".run/reproductions/visual-robustness")))
    require(paths.output.suffix.lower() == ".json" and not paths.output.exists(), "Issuer report must be a new JSON file")
    return paths


def load_cpu_models(engine):
    # RapidOCR 3.10 initializes sessions lazily. Load the pinned models without
    # passing any image so providers can be checked before the first inference.
    if engine.text_det is None:
        engine._load_det_model()
    if engine.text_rec is None:
        engine._load_rec_model()
    providers = {"detection": engine.text_det.session.session.get_providers(),
                 "recognition": engine.text_rec.session.session.get_providers()}
    require(providers == {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]},
            "Issuer inference must use only CPU providers")
    return providers


def run(*, manifest=None, output=None, local_dir=None, download=False):
    paths = _paths(output, local_dir)
    manifest_path = Path(manifest) if manifest is not None else ROOT/"configs/issuer-evaluation.json"
    frozen, packs = load_manifest(ROOT, manifest_path)
    # Complete path and freeze preflight precedes all network, render/model imports
    # and writes. A previously downloaded exact PDF may be reused; OCR never is.
    for registration in frozen.documents:
        directory = paths.local_dir/registration.id
        for page in registration.pages:
            for name in (f"page-{page.page}.png", f"ocr-words-{page.page}.json", f"ocr-lines-{page.page}.json"):
                target = validate_write_path(ROOT, directory/name)
                require(not target.exists(), "Issuer derived output already exists; select a fresh local directory")
    packages = {name: version(name) for name in sorted(EXECUTION_PACKAGES)}
    require(packages == frozen.execution_packages, "Installed execution packages differ from the frozen versions")
    pdf_paths = {doc.id: obtain_pdf(ROOT, paths.local_dir/doc.id/doc.pdf.filename, doc.pdf, download=download)
                 for doc in frozen.documents}
    import pypdfium2 as pdfium
    engine = create_engine(frozen.profile)
    providers = load_cpu_models(engine)
    report = {"schema_version": 1, "suite_version": VERSION, "experiment_id": frozen.experiment_id,
              "generated_at": datetime.now(timezone.utc).isoformat(), "manifest_sha256": frozen.snapshot_sha256,
              "rule_versions": RULES.copy(), "scope": SCOPE, "automatic_approval": False,
              "environment": {"operation": "full_page_ocr_then_frozen_rule_evaluation", "reference_sent_to_recognizer": False,
                  "python": platform.python_version(), "os": platform.system(), "packages": packages, "providers": providers},
              "documents": []}
    all_outcomes, all_groups, page_count = [], [], 0
    for registration in frozen.documents:
        pack = packs[registration.id]
        document = {"id": registration.id, "company": pack.company,
                    "source": pack.sources[0].model_dump(mode="json"), "pdf": registration.pdf.model_dump(mode="json"),
                    "pack_sha256": registration.pack_sha256, "raw_pdf_sha256": registration.pdf.sha256,
                    "pages": [], "facts": []}
        by_id = {fact.id: fact for fact in pack.facts}
        directory = paths.local_dir/registration.id
        with pdfium.PdfDocument(pdf_paths[registration.id]) as pdf:
            for spec in registration.pages:
                page = pdf[spec.page-1]
                try:
                    require(page.get_rotation() == frozen.render.rotation and
                            (page.get_width(), page.get_height()) == (spec.width, spec.height),
                            "Issuer PDF rotation or visible dimensions differ from reference")
                    bitmap = page.render(scale=frozen.render.scale)
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                try:
                    buffer = BytesIO(); image.save(buffer, format="PNG"); raw = buffer.getvalue()
                    require(image.size == (spec.image_width, spec.image_height) and
                            hashlib.sha256(raw).hexdigest() == spec.image_sha256,
                            "Full-page render differs from its frozen pre-OCR image")
                finally:
                    image.close()
                image_path = directory/f"page-{spec.page}.png"
                write_new_bytes(ROOT, image_path, raw)
                started = time.perf_counter()
                native = engine(str(image_path), return_word_box=True, return_single_char_box=False)
                elapsed = time.perf_counter()-started
                # Only native model output and geometry enter the adapter.
                words = adapt_result(native, page=spec.page, width=spec.width, height=spec.height,
                                     image_width=spec.image_width, image_height=spec.image_height)
                lines = words.line_page()
                write_new_json(ROOT, directory/f"ocr-words-{spec.page}.json", words.model_dump(mode="json"))
                write_new_json(ROOT, directory/f"ocr-lines-{spec.page}.json", lines.model_dump(mode="json"))
                evidence, outcomes = compact_page(words, [by_id[fid] for fid in spec.fact_ids], frozen.profile["min_confidence"])
                compact = WordPage(page=words.page, width=words.width, height=words.height,
                                   lines=[WordLine.model_validate({key: line[key] for key in ("line", "words")}) for line in evidence])
                document["pages"].append({**spec.model_dump(mode="json"), "full_line_ocr_sha256": digest(lines.model_dump(mode="json")),
                    "full_word_ocr_sha256": digest(words.model_dump(mode="json")), "evidence_sha256": digest(compact.model_dump(mode="json")),
                    "ocr_line_count": len(words.lines), "elapsed_seconds": round(elapsed, 4), "evidence_lines": evidence})
                document["facts"].extend(outcomes)
                print(json.dumps({"document": registration.id, "page": spec.page, "facts": len(outcomes),
                                  "seconds": round(elapsed, 4)}), flush=True)
        document["date_groups"] = date_groups(registration.id, registration.pdf.sha256, pack.facts, document["facts"])
        document["summary"] = summarize(document["facts"], document["date_groups"], len(document["pages"]), 1)
        all_outcomes.extend(document["facts"]); all_groups.extend(document["date_groups"]); page_count += len(document["pages"])
        report["documents"].append(document)
    report["summary"] = summarize(all_outcomes, all_groups, page_count, len(report["documents"]))
    report = validate_issuer_report(ROOT, report, manifest_path)
    write_new_json(ROOT, paths.output, report)
    print(json.dumps({"summary": report["summary"], "output": str(paths.output)}), flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--local-dir", type=Path)
    parser.add_argument("--download", action="store_true", help="Fetch only the exact registered official PDF when missing")
    args = parser.parse_args(argv)
    try:
        run(manifest=args.manifest, output=args.output, local_dir=args.local_dir, download=args.download)
    except (ValueError, OSError) as exc:
        parser.exit(1, "Issuer evaluation failed: " + str(exc).splitlines()[0] + "\n")


if __name__ == "__main__":
    main()
