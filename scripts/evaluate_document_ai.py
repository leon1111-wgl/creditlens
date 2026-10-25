"""Run real CPU OCR on full official PDF pages, then cross-check known facts.

No reference values, labels or boxes are supplied to the OCR model. Page
selection and subsequent comparisons use existing development annotations.
Raw PDFs, full OCR text and page images remain local and are not published.
"""
from collections import Counter
from datetime import datetime, timezone
import argparse
import hashlib
from io import BytesIO
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import CasePack, digest
from creditlens.experiment_paths import default_local_dir, default_report_path, validate_experiment_paths, validate_write_path
from creditlens.visual import OcrPage, VISUAL_RULE_VERSION, crosscheck_fact
from scripts.build_pdf_pack import verified_pdf

DOCUMENTS = {"msft": "msft-fy2025-pdf.json", "aapl": "aapl-fy2025-pdf.json"}


def write_json(path, payload):
    path = validate_write_path(ROOT, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def save_page_image(path, image, *, expected_sha256=None):
    path = validate_write_path(ROOT, path)
    if path.is_relative_to((ROOT/".run/document-ai").resolve()) and expected_sha256 is None:
        raise ValueError("Registered document previews require their frozen image hash")
    rendered = BytesIO()
    image.save(rendered, format="PNG")
    raw = rendered.getvalue()
    if expected_sha256 is not None:
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Rendered document preview differs from its frozen registration; use a separate reproduction --local-dir")
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("Existing document preview differs; use a separate reproduction --local-dir")
            return  # Preserve an already verified preview, including its mtime.
    path.write_bytes(raw)


def run(*, download=False, output=None, local_dir=None):
    paths = validate_experiment_paths(ROOT, output if output is not None else default_report_path(ROOT, "document-ai"),
                                      local_dir if local_dir is not None else default_local_dir(ROOT, "document-ai"),
                                      allow_registered_preview=True)
    output, local_dir = paths.output, paths.local_dir
    preview_root = (ROOT/".run/document-ai").resolve()
    registered_images = None
    if local_dir.is_relative_to(preview_root):
        if local_dir != preview_root:
            raise ValueError("Registered document previews require the exact .run/document-ai directory")
        frozen = json.loads((ROOT/"artifacts/document-ai-report.json").read_text(encoding="utf-8"))
        registered_images = {(d["id"], p["page"]): p["image_sha256"]
                             for d in frozen["documents"] for p in d["pages"]}
    import pypdfium2 as pdfium
    import rapidocr
    from rapidocr import RapidOCR, EngineType, ModelType, OCRVersion
    profile = json.loads((ROOT/"configs/document-ai.json").read_text(encoding="utf-8"))
    params = {"EngineConfig.onnxruntime.intra_op_num_threads": profile["intra_op_num_threads"],
              "EngineConfig.onnxruntime.inter_op_num_threads": profile["inter_op_num_threads"],
              "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
              "Global.text_score": 0.0, "Global.use_cls": False,
              "Det.limit_side_len": profile["det_limit_side_len"]}
    for model in profile["models"]:
        path = Path(rapidocr.__file__).parent/"models"/model["filename"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            raise ValueError("Installed OCR weight does not match pinned profile: "+model["filename"])
        for key, val in {"model_path": str(path), "engine_type": EngineType.ONNXRUNTIME,
                         "ocr_version": OCRVersion.PPOCRV6, "model_type": ModelType.SMALL}.items():
            params[model["stage"]+"."+key] = val
    engine = RapidOCR(params=params)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "rule_version": VISUAL_RULE_VERSION, "profile": profile, "profile_sha256": digest(profile),
              "environment": {"python": platform.python_version(), "os": platform.system(),
                              "packages": {name: version(name) for name in ["rapidocr", "onnxruntime", "numpy", "pypdfium2", "Pillow"]}},
              "scope": "Known financial-fact regions, after full-page image-only OCR; development examples, not blind extraction accuracy or authenticity.",
              "documents": []}
    for doc_id, pack_file in DOCUMENTS.items():
        pack = CasePack.model_validate_json((ROOT/"data/examples"/pack_file).read_text(encoding="utf-8"))
        source = pack.sources[0]
        pdf_path = ROOT/"data/raw"/f"{doc_id}-fy2025-10k.pdf"
        receipt = verified_pdf(pdf_path, download, url=str(source.url), sha256=source.sha256)
        document = {"id": doc_id, "company": pack.company, "source": source.model_dump(mode="json"),
                    "retrieved_at": receipt["retrieved_at"], "pack_sha256": digest(pack.model_dump(mode="json")),
                    "pages": [], "facts": []}
        directory = local_dir/doc_id
        directory.mkdir(parents=True, exist_ok=True)
        with pdfium.PdfDocument(pdf_path) as pdf:
            for page_number in sorted({f.pdf_location.page for f in pack.facts}):
                page = pdf[page_number-1]
                try:
                    if page.get_rotation() != 0:
                        raise ValueError("Rotated pages require a separate profile")
                    width, height = page.get_width(), page.get_height()
                    bitmap = page.render(scale=profile["render_scale"])
                    try:
                        image = bitmap.to_pil().copy()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                image_path = validate_write_path(ROOT, directory/f"page-{page_number}.png")
                expected_image = None
                if registered_images is not None:
                    expected_image = registered_images.get((doc_id, page_number))
                    if expected_image is None:
                        raise ValueError("Page is not registered for the document preview directory")
                save_page_image(image_path, image, expected_sha256=expected_image)
                # Full unannotated image only. Do not pass known facts or crop boxes.
                started = time.perf_counter()
                result = engine(str(image_path))
                elapsed = time.perf_counter()-started
                observations = []
                if result.boxes is not None:
                    for text, score, quad in zip(result.txts, result.scores, result.boxes):
                        observations.append({"text": text, "confidence": float(score), "bbox": {
                            "x0": max(0, float(quad[:, 0].min())*width/image.width),
                            "top": max(0, float(quad[:, 1].min())*height/image.height),
                            "x1": min(width, float(quad[:, 0].max())*width/image.width),
                            "bottom": min(height, float(quad[:, 1].max())*height/image.height)}})
                ocr = OcrPage(page=page_number, width=width, height=height, spans=observations)
                write_json(directory/f"ocr-{page_number}.json", ocr.model_dump(mode="json"))
                document["pages"].append({"page": page_number, "width": width, "height": height,
                                          "image_width": image.width, "image_height": image.height,
                                          "image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                                          "ocr_sha256": digest(ocr.model_dump(mode="json")),
                                          "ocr_lines": len(observations), "elapsed_seconds": round(elapsed, 4)})
                # Compare only after full-page recognition has completed.
                document["facts"] += [crosscheck_fact(f, ocr, min_confidence=profile["min_confidence"])
                                      for f in pack.facts if f.pdf_location.page == page_number]
                print(json.dumps({"document": doc_id, "page": page_number, "ocr_lines": len(observations),
                                  "seconds": round(elapsed, 3)}), flush=True)
        counts = Counter(f["status"] for f in document["facts"])
        document["summary"] = {state: counts[state] for state in ["consistent", "conflict", "insufficient"]}
        report["documents"].append(document)
    # Record actual active providers, not only requested configuration.
    report["environment"]["providers"] = {
        "detection": engine.text_det.session.session.get_providers(),
        "recognition": engine.text_rec.session.session.get_providers()}
    totals = Counter(f["status"] for d in report["documents"] for f in d["facts"])
    report["summary"] = {state: totals[state] for state in ["consistent", "conflict", "insufficient"]}
    write_json(output, report)
    print(json.dumps({"summary": report["summary"], "output": str(output)}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download only registered official PDFs if missing")
    parser.add_argument("--output", type=Path, default=default_report_path(ROOT, "document-ai"))
    parser.add_argument("--local-dir", type=Path, default=default_local_dir(ROOT, "document-ai"),
                        help="Local full-page images/OCR; defaults to an isolated reproduction directory")
    args = parser.parse_args()
    run(download=args.download, output=args.output, local_dir=args.local_dir)
