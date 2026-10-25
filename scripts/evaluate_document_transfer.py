"""Run the frozen new-issuer, known-location full-page CPU experiment.

Only --download can retrieve the exact manifest-registered PDF. No reference
values, labels, boxes or text are supplied to the image recognizer.
"""
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
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import digest
from creditlens.document_transfer import (VERSION, SCOPE, PdfRegistration, check_download_url,
    compact_page, load_manifest, summarize, validate_transfer_report)
from creditlens.experiment_paths import validate_experiment_paths, validate_write_path
from creditlens.visual import VISUAL_RULE_VERSION
from creditlens.word_evidence import WORD_RULE_VERSION, WordLine, WordPage
from scripts.evaluate_visual_robustness import create_engine
from scripts.evaluate_word_evidence import adapt_result

MAX_PDF_BYTES = 60_000_000


def write_json(root: Path, path: Path, value):
    path = validate_write_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")


class RegisteredRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, request, response, code, message, headers, newurl):
        check_download_url(newurl, self.allowed_hosts)
        return super().redirect_request(request, response, code, message, headers, newurl)


def obtain_pdf(root: Path, path: Path, registration: PdfRegistration, *, download=False) -> Path:
    path = validate_write_path(root, path)
    if path.exists():
        if path.stat().st_size > MAX_PDF_BYTES:
            raise ValueError("Registered PDF exceeds the 60 MB limit")
        raw = path.read_bytes()
    else:
        if not download:
            raise FileNotFoundError("Registered local PDF missing; explicitly use --download to fetch its pinned official source")
        url = check_download_url(str(registration.url), registration.allowed_hosts)
        opener = urllib.request.build_opener(RegisteredRedirect(registration.allowed_hosts))
        request = urllib.request.Request(url, headers={"User-Agent": "CreditLens/Research-Document-Check"})
        with opener.open(request, timeout=60) as response:
            check_download_url(response.url, registration.allowed_hosts)
            raw = response.read(MAX_PDF_BYTES+1)
    if len(raw) > MAX_PDF_BYTES or not raw.startswith(b"%PDF-") or hashlib.sha256(raw).hexdigest() != registration.sha256:
        raise ValueError("PDF bytes do not match the frozen official source")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return path


def run(*, manifest=None, output=None, local_dir=None, download=False):
    manifest_path = Path(manifest) if manifest is not None else ROOT/"configs/document-transfer.json"
    paths = validate_experiment_paths(ROOT, Path(output) if output is not None else ROOT/".run/reproductions/document-transfer-report.json",
                                      Path(local_dir) if local_dir is not None else ROOT/".run/reproductions/document-transfer",
                                      read_only_dirs=(ROOT/".run/document-transfer",))
    frozen, packs = load_manifest(ROOT, manifest_path)
    # No image/PDF/OCR imports, downloads or writes occur before this freeze check.
    profile = frozen.profile
    pdf_paths = {doc.id: obtain_pdf(ROOT, paths.local_dir/doc.id/doc.pdf.filename, doc.pdf, download=download)
                 for doc in frozen.documents}
    import pypdfium2 as pdfium
    engine = create_engine(profile)
    report = {"schema_version": 1, "suite_version": VERSION, "experiment_id": frozen.experiment_id,
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "manifest_sha256": frozen.snapshot_sha256,
              "rule_versions": {"line": VISUAL_RULE_VERSION, "word": WORD_RULE_VERSION}, "scope": SCOPE,
              "environment": {"python": platform.python_version(), "os": platform.system(),
                  "packages": {name: version(name) for name in ("rapidocr", "onnxruntime", "numpy", "pypdfium2", "Pillow")}},
              "documents": []}
    all_pairs = []
    for registration in frozen.documents:
        pack = packs[registration.id]
        document = {"id": registration.id, "company": pack.company,
                    "source": pack.sources[0].model_dump(mode="json"),
                    "source_publication": registration.pdf.model_dump(mode="json"),
                    "pack_sha256": registration.pack_sha256, "raw_pdf_sha256": registration.pdf.sha256,
                    "pages": [], "facts": []}
        by_id = {f.id: f for f in pack.facts}
        directory = paths.local_dir/registration.id
        with pdfium.PdfDocument(pdf_paths[registration.id]) as pdf:
            for page_spec in registration.pages:
                page = pdf[page_spec.page-1]
                try:
                    if page.get_rotation() != 0 or (page.get_width(), page.get_height()) != (page_spec.width, page_spec.height):
                        raise ValueError("Transfer PDF rotation or dimensions differ from the frozen reference")
                    bitmap = page.render(scale=profile["render_scale"])
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                image_raw = buffer.getvalue()
                if image.size != (page_spec.image_width, page_spec.image_height) or hashlib.sha256(image_raw).hexdigest() != page_spec.image_sha256:
                    raise ValueError("Rendered full-page image differs from the pre-OCR frozen render")
                image_path = validate_write_path(ROOT, directory/f"page-{page_spec.page}.png")
                image_path.write_bytes(image_raw)
                started = time.perf_counter()
                result = engine(str(image_path), return_word_box=True, return_single_char_box=False)
                elapsed = time.perf_counter()-started
                # Convert only the native output and page dimensions, never a reference value or box.
                word_page = adapt_result(result, page=page_spec.page, width=page_spec.width, height=page_spec.height,
                                         image_width=page_spec.image_width, image_height=page_spec.image_height)
                line_page = word_page.line_page()
                write_json(ROOT, directory/f"ocr-words-{page_spec.page}.json", word_page.model_dump(mode="json"))
                write_json(ROOT, directory/f"ocr-lines-{page_spec.page}.json", line_page.model_dump(mode="json"))
                facts = [by_id[fid] for fid in page_spec.fact_ids]
                evidence, pairs = compact_page(word_page, facts, profile["min_confidence"])
                compact = WordPage(page=word_page.page, width=word_page.width, height=word_page.height,
                                   lines=[WordLine.model_validate({k: e[k] for k in ("line", "words")}) for e in evidence])
                document["pages"].append({**page_spec.model_dump(mode="json"),
                    "full_line_ocr_sha256": digest(line_page.model_dump(mode="json")),
                    "full_word_ocr_sha256": digest(word_page.model_dump(mode="json")),
                    "evidence_sha256": digest(compact.model_dump(mode="json")),
                    "ocr_line_count": len(word_page.lines), "elapsed_seconds": round(elapsed, 4),
                    "evidence_lines": evidence})
                document["facts"].extend(pairs)
                print(json.dumps({"document": registration.id, "page": page_spec.page,
                                  "summary": summarize(pairs), "seconds": round(elapsed, 4)}), flush=True)
        document["summary"] = summarize(document["facts"])
        all_pairs.extend(document["facts"])
        report["documents"].append(document)
    report["environment"]["providers"] = {"detection": engine.text_det.session.session.get_providers(),
                                           "recognition": engine.text_rec.session.session.get_providers()}
    report["summary"] = summarize(all_pairs)
    report = validate_transfer_report(ROOT, report, manifest_path)
    write_json(ROOT, paths.output, report)
    print(json.dumps({"summary": report["summary"], "output": str(paths.output)}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT/"configs/document-transfer.json")
    parser.add_argument("--output", type=Path, default=ROOT/".run/reproductions/document-transfer-report.json")
    parser.add_argument("--local-dir", type=Path, default=ROOT/".run/reproductions/document-transfer")
    parser.add_argument("--download", action="store_true", help="Retrieve only the exact registered official PDF if absent")
    args = parser.parse_args()
    run(manifest=args.manifest, output=args.output, local_dir=args.local_dir, download=args.download)
