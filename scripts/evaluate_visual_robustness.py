"""Offline CPU OCR on frozen clean pages and synthetic image degradations.

All conditions, including failures, are published. Full page images and complete
OCR outputs remain in .run/. This is a development stress experiment, not real
scan validation or a generic extraction benchmark.
"""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path
import argparse
import json
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from creditlens.diligence import CasePack, digest
from creditlens.visual import OcrPage, VISUAL_RULE_VERSION, crosscheck_fact
from creditlens.visual_robustness import (RobustnessConfig, STATES, compare_conditions,
                                         compact_fact, summarize, transform_image, verified_image)
from creditlens.visual_store import REGISTERED_PACKS


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def create_engine(profile):
    if (profile["engine"], profile["execution_provider"], profile["use_cls"]) != ("onnxruntime", "CPUExecutionProvider", False):
        raise ValueError("This experiment supports only the pinned CPU profile without orientation classification")
    if not 1 <= profile["intra_op_num_threads"] <= 4 or profile["inter_op_num_threads"] != 1:
        raise ValueError("The profile must use 1-4 intra-op threads and one inter-op thread; no silent clamping")
    import rapidocr
    from rapidocr import RapidOCR, EngineType, ModelType, OCRVersion
    params = {"EngineConfig.onnxruntime.intra_op_num_threads": profile["intra_op_num_threads"],
              "EngineConfig.onnxruntime.inter_op_num_threads": profile["inter_op_num_threads"],
              "EngineConfig.onnxruntime.use_cuda": False, "EngineConfig.onnxruntime.use_dml": False,
              "Global.text_score": 0.0, "Global.use_cls": False,
              "Det.limit_side_len": profile["det_limit_side_len"]}
    for model in profile["models"]:
        path = Path(rapidocr.__file__).parent/"models"/model["filename"]
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != model["sha256"]:
            raise ValueError("Installed weight does not match the frozen model: "+model["filename"])
        for key, val in {"model_path": str(path), "engine_type": EngineType.ONNXRUNTIME,
                         "ocr_version": OCRVersion.PPOCRV6, "model_type": ModelType.SMALL}.items():
            params[model["stage"]+"."+key] = val
    return RapidOCR(params=params)


def result_to_page(result, page):
    spans = []
    if result.boxes is not None:
        if not (len(result.boxes) == len(result.txts) == len(result.scores)):
            raise ValueError("OCR output lengths disagree")
        for text, score, quad in zip(result.txts, result.scores, result.boxes):
            spans.append({"text": text, "confidence": float(score), "bbox": {
                "x0": max(0, float(quad[:, 0].min())*page["width"]/page["image_width"]),
                "top": max(0, float(quad[:, 1].min())*page["height"]/page["image_height"]),
                "x1": min(page["width"], float(quad[:, 0].max())*page["width"]/page["image_width"]),
                "bottom": min(page["height"], float(quad[:, 1].max())*page["height"]/page["image_height"])}})
    return OcrPage(page=page["page"], width=page["width"], height=page["height"], spans=spans)


def run(source_dir: Path, output: Path, local_dir: Path):
    config = RobustnessConfig.model_validate_json((ROOT/"configs/visual-robustness.json").read_text(encoding="utf-8"))
    source = json.loads((ROOT/"artifacts/document-ai-report.json").read_text(encoding="utf-8"))
    profile = json.loads((ROOT/"configs/document-ai.json").read_text(encoding="utf-8"))
    if digest(source) != config.source_report_sha256 or digest(profile) != config.source_profile_sha256:
        raise ValueError("Baseline report/profile changed; freeze a new separately named experiment")
    if source["rule_version"] != VISUAL_RULE_VERSION:
        raise ValueError("Baseline comparison rules changed")
    # Persist the selected pages and all transformation parameters before inference.
    write_json(local_dir/"frozen-config.json", config.model_dump(mode="json"))
    prepared = []
    for item in config.pages:
        doc = next(d for d in source["documents"] if d["id"] == item.document_id)
        page = next(p for p in doc["pages"] if p["page"] == item.page)
        pack = CasePack.model_validate_json((ROOT/"data/examples"/REGISTERED_PACKS[item.document_id]).read_text(encoding="utf-8"))
        if digest(pack.model_dump(mode="json")) != item.pack_sha256 or item.pack_sha256 != doc["pack_sha256"]:
            raise ValueError("Reference facts changed")
        if page["image_sha256"] != item.image_sha256:
            raise ValueError("Source image registration changed")
        raw = verified_image(source_dir/item.document_id/f"page-{item.page}.png", item.image_sha256,
                             (page["image_width"], page["image_height"]))
        prepared.append((item, doc, page, pack, raw))
    engine = create_engine(profile)
    report = {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
              "experiment": "synthetic_coordinate_preserving_stress", "suite_version": config.suite_version,
              "rule_version": VISUAL_RULE_VERSION, "config": config.model_dump(mode="json"),
              "config_sha256": digest(config.model_dump(mode="json")), "profile": profile,
              "profile_sha256": digest(profile), "environment": {"python": platform.python_version(),
                  "os": platform.system(), "packages": {name: version(name) for name in ("rapidocr", "onnxruntime", "numpy", "Pillow")}},
              "scope": "Fixed known-location development facts on two issuer pages. Synthetic degradations, not real scans or unseen-layout accuracy.",
              "documents": [], "summary_by_variant": []}
    for item, doc, page, pack, raw in prepared:
        facts = [f for f in pack.facts if f.pdf_location.page == item.page]
        baseline_registered = [f for f in doc["facts"] if f["page"] == item.page]
        document = {"id": item.document_id, "company": pack.company, "page": item.page,
                    "source": doc["source"], "pack_sha256": item.pack_sha256,
                    "source_image_sha256": item.image_sha256, "width": page["width"], "height": page["height"],
                    "image_width": page["image_width"], "image_height": page["image_height"],
                    "registered_fact_ids": [f.id for f in facts], "conditions": []}
        clean = None
        for variant in config.variants:
            directory = local_dir/item.document_id/f"page-{item.page}"/variant.id
            directory.mkdir(parents=True, exist_ok=True)
            image = transform_image(raw, variant)
            image_path = directory/"input.png"
            image_path.write_bytes(image)
            started = time.perf_counter()
            result = engine(str(image_path))  # Full image only, no reference facts or regions.
            elapsed = time.perf_counter()-started
            ocr = result_to_page(result, page)
            write_json(directory/"ocr.json", ocr.model_dump(mode="json"))
            checked = [crosscheck_fact(fact, ocr, min_confidence=profile["min_confidence"]) for fact in facts]
            if clean is None:
                clean = checked
            condition = {"id": variant.id, "synthetic_degradation": variant.operation != "identity",
                         "transform": variant.model_dump(mode="json", exclude_none=True),
                         "image_sha256": sha256(image).hexdigest(), "ocr_sha256": digest(ocr.model_dump(mode="json")),
                         "ocr_lines": len(ocr.spans), "elapsed_seconds": round(elapsed, 4),
                         "summary": summarize(checked), "compared_with_clean": compare_conditions(clean, checked),
                         "facts": [compact_fact(f) for f in checked]}
            if variant.operation == "identity":
                condition["compared_with_saved_baseline"] = compare_conditions(baseline_registered, checked)
            document["conditions"].append(condition)
            print(json.dumps({"document": item.document_id, "page": item.page, "condition": variant.id,
                              "summary": condition["summary"], "seconds": round(elapsed, 3)}), flush=True)
        report["documents"].append(document)
    report["environment"]["providers"] = {"detection": engine.text_det.session.session.get_providers(),
                                           "recognition": engine.text_rec.session.session.get_providers()}
    if any(providers != ["CPUExecutionProvider"] for providers in report["environment"]["providers"].values()):
        raise ValueError("Actual inference providers differ from the pinned CPU experiment")
    for variant in config.variants:
        counts, transitions = Counter(), Counter()
        for doc in report["documents"]:
            condition = next(c for c in doc["conditions"] if c["id"] == variant.id)
            counts.update(condition["summary"])
            transitions.update(condition["compared_with_clean"]["transitions"])
        report["summary_by_variant"].append({"id": variant.id, "fact_count": sum(counts.values()),
                                             "summary": {s: counts[s] for s in STATES},
                                             "transitions_from_clean": dict(transitions)})
    write_json(output, report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT/".run/document-ai")
    parser.add_argument("--local-dir", type=Path, default=ROOT/".run/visual-robustness")
    parser.add_argument("--output", type=Path, default=ROOT/"artifacts/visual-robustness-report.json")
    args = parser.parse_args()
    run(args.source_dir, args.output, args.local_dir)
