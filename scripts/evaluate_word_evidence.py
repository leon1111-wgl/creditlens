"""Run a full-page, image-only OCR word-location ablation on registered pages.

The saved line baseline is never overwritten. Full OCR outputs stay local;
the published report contains only selected fact evidence and all outcomes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import CasePack, digest
from creditlens.visual import OcrSpan, crosscheck_fact
from creditlens.visual_store import REGISTERED_PACKS, VisualStore
from creditlens.word_evidence import SIGN_GAP_POINTS, WORD_RULE_VERSION, WordLine, WordPage, crosscheck_word_fact


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def adapt_result(result, *, page, width, height, image_width, image_height) -> WordPage:
    """Reference-free adapter for RapidOCR 3.10 nested per-line word_results."""
    def span(text, score, quad):
        if len(quad) != 4 or any(len(point) != 2 for point in quad):
            raise ValueError("Expected a four-point OCR quadrilateral")
        x = [float(point[0])*width/image_width for point in quad]
        y = [float(point[1])*height/image_height for point in quad]
        return OcrSpan(text=text, confidence=float(score), bbox={
            "x0": max(0, min(x)), "x1": min(width, max(x)),
            "top": max(0, min(y)), "bottom": min(height, max(y))})
    if result.boxes is None:
        return WordPage(page=page, width=width, height=height, lines=[])
    if not (len(result.boxes) == len(result.txts) == len(result.scores) == len(result.word_results)):
        raise ValueError("OCR line and word output lengths do not match")
    lines = []
    for text, score, box, words in zip(result.txts, result.scores, result.boxes, result.word_results):
        if words is None:
            words = []
        if any(len(item) != 3 for item in words):
            raise ValueError("Unexpected word_results schema; expected nested text/score/quad tuples")
        lines.append(WordLine(line=span(text, score, box),
                              words=[span(wtext, wscore, wbox) for wtext, wscore, wbox in words]))
    return WordPage(page=page, width=width, height=height, lines=lines)


def counts(facts):
    counter = Counter(f["status"] for f in facts)
    return {key: counter[key] for key in ("consistent", "conflict", "insufficient")}


def run(*, image_root=ROOT/".run/document-ai", local_dir=ROOT/".run/document-ai-words",
        output=ROOT/"artifacts/document-ai-word-report.json"):
    import rapidocr
    from rapidocr import RapidOCR, EngineType, ModelType, OCRVersion
    from PIL import Image

    baseline_path = ROOT/"artifacts/document-ai-report.json"
    baseline = VisualStore(ROOT).report()
    profile = json.loads((ROOT/"configs/document-ai-words.json").read_text(encoding="utf-8"))
    for key, value in baseline["profile"].items():
        if key != "profile_version" and profile.get(key) != value:
            raise ValueError("Word ablation changes a baseline setting: "+key)
    if (profile["word_rule_version"] != WORD_RULE_VERSION or profile["sign_gap_points"] != SIGN_GAP_POINTS or
        profile["confidence_basis"] != "parent_line_score" or profile["return_word_box"] is not True or
        profile["return_single_char_box"] is not False or profile["engine"] != "onnxruntime" or
        profile["execution_provider"] != "CPUExecutionProvider" or profile["use_cls"] is not False):
        raise ValueError("Unsupported word-evidence profile")
    params = {"EngineConfig.onnxruntime.intra_op_num_threads": profile["intra_op_num_threads"],
              "EngineConfig.onnxruntime.inter_op_num_threads": profile["inter_op_num_threads"],
              "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
              "Global.text_score": 0.0, "Global.use_cls": False,
              "Det.limit_side_len": profile["det_limit_side_len"],
              "Global.return_word_box": True, "Global.return_single_char_box": False}
    for model in profile["models"]:
        path = Path(rapidocr.__file__).parent/"models"/model["filename"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            raise ValueError("OCR weight hash mismatch: "+model["filename"])
        for key, value in {"model_path": str(path), "engine_type": EngineType.ONNXRUNTIME,
                           "ocr_version": OCRVersion.PPOCRV6, "model_type": ModelType.SMALL}.items():
            params[model["stage"]+"."+key] = value
    engine = RapidOCR(params=params)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "rule_version": WORD_RULE_VERSION, "profile": profile, "profile_sha256": digest(profile),
              "baseline_report_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
              "baseline_generated_at": baseline["generated_at"], "baseline_summary": baseline["summary"],
              "scope": "Post-hoc native word locations on full-page image-only OCR; same known development facts, not blind accuracy.",
              "environment": {"python": platform.python_version(), "os": platform.system(),
                              "packages": {n: version(n) for n in ("rapidocr", "onnxruntime", "numpy", "Pillow")}},
              "documents": []}
    report["implementation_sha256"] = {
        relative: hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()
        for relative in ("creditlens/word_evidence.py", "scripts/evaluate_word_evidence.py")}
    report["environment"]["rapidocr_source_sha256"] = {
        relative: hashlib.sha256((Path(rapidocr.__file__).parent/relative).read_bytes()).hexdigest()
        for relative in ("main.py", "cal_rec_boxes/main.py", "ch_ppocr_rec/utils.py")}
    all_line, all_word, changes = [], [], []
    for document in baseline["documents"]:
        doc_id = document["id"]
        pack = CasePack.model_validate_json((ROOT/"data/examples"/REGISTERED_PACKS[doc_id]).read_text(encoding="utf-8"))
        saved = {f["fact_id"]: f for f in document["facts"]}
        out = {"id": doc_id, "company": pack.company, "source": document["source"],
               "pack_sha256": document["pack_sha256"], "pages": [], "facts": [], "changes": []}
        doc_line = []
        for page in document["pages"]:
            path = image_root/doc_id/f"page-{page['page']}.png"
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != page["image_sha256"]:
                raise ValueError("Input is not the registered unannotated page image")
            with Image.open(path) as image:
                if image.size != (page["image_width"], page["image_height"]):
                    raise ValueError("Input image dimensions changed")
            started = time.perf_counter()
            # The only model input is the complete hash-verified image.
            result = engine(str(path), return_word_box=True, return_single_char_box=False)
            elapsed = time.perf_counter()-started
            word_page = adapt_result(result, page=page["page"], width=page["width"], height=page["height"],
                                     image_width=page["image_width"], image_height=page["image_height"])
            write_json(local_dir/doc_id/f"ocr-words-{page['page']}.json", word_page.model_dump(mode="json"))
            line_page = word_page.line_page()
            out["pages"].append({**page, "word_ocr_sha256": digest(word_page.model_dump(mode="json")),
                                  "current_line_ocr_sha256": digest(line_page.model_dump(mode="json")),
                                  "line_output_matches_saved_baseline": digest(line_page.model_dump(mode="json")) == page["ocr_sha256"],
                                  "word_tokens": sum(len(entry.words) for entry in word_page.lines),
                                  "word_inference_elapsed_seconds": round(elapsed, 4)})
            for fact in pack.facts:
                if fact.pdf_location.page != page["page"]:
                    continue
                line = crosscheck_fact(fact, line_page, min_confidence=profile["min_confidence"])
                word = crosscheck_word_fact(fact, word_page, min_confidence=profile["min_confidence"])
                word["saved_baseline_status"] = saved[fact.id]["status"]
                word["current_line_status"] = line["status"]
                out["facts"].append(word)
                doc_line.append(line)
                if saved[fact.id]["status"] != word["status"]:
                    change = {"document_id": doc_id, "fact_id": fact.id,
                              "saved_baseline_status": saved[fact.id]["status"],
                              "current_line_status": line["status"], "word_status": word["status"]}
                    out["changes"].append(change)
                    changes.append(change)
            print(json.dumps({"document": doc_id, "page": page["page"], "seconds": round(elapsed, 3),
                              "line_output_unchanged": out["pages"][-1]["line_output_matches_saved_baseline"]}), flush=True)
        out["baseline_summary"] = document["summary"]
        out["current_line_summary"] = counts(doc_line)
        out["word_summary"] = counts(out["facts"])
        all_line += doc_line
        all_word += out["facts"]
        report["documents"].append(out)
    report["environment"]["providers"] = {
        "detection": engine.text_det.session.session.get_providers(),
        "recognition": engine.text_rec.session.session.get_providers()}
    report["current_line_summary"] = counts(all_line)
    report["word_summary"] = counts(all_word)
    report["changes"] = changes
    write_json(output, report)
    print(json.dumps({"baseline": report["baseline_summary"], "same_run_lines": report["current_line_summary"],
                      "words": report["word_summary"], "changed_facts": len(changes)}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-root", type=Path, default=ROOT/".run/document-ai")
    parser.add_argument("--local-dir", type=Path, default=ROOT/".run/document-ai-words")
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts/document-ai-word-report.json")
    args = parser.parse_args()
    run(image_root=args.image_root, local_dir=args.local_dir, output=args.output)
