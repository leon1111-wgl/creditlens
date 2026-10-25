from copy import deepcopy
from hashlib import sha256
from importlib.util import find_spec
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from pydantic import ValidationError

from creditlens.diligence import digest
from creditlens.visual_robustness import (RobustnessConfig, STATES, Variant, compare_conditions,
                                         compact_fact, summarize, transform_image, verified_image)
from scripts.evaluate_visual_robustness import create_engine, result_to_page

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT/"configs/visual-robustness.json").read_text(encoding="utf-8"))


class RobustnessContractTests(unittest.TestCase):
    def test_cpu_profile_rejects_silent_thread_clamping_before_loading_model(self):
        profile = {"engine": "onnxruntime", "execution_provider": "CPUExecutionProvider", "use_cls": False,
                   "intra_op_num_threads": 4, "inter_op_num_threads": 1}
        for changed in ({"intra_op_num_threads": 8}, {"intra_op_num_threads": 0}, {"inter_op_num_threads": 2}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                create_engine(profile | changed)

    def test_exactly_one_clean_baseline_and_unique_page_condition_ids(self):
        for mutate in (
            lambda c: c.update(frozen_before_inference=False),
            lambda c: c["pages"].append(deepcopy(c["pages"][0])),
            lambda c: c["variants"].append(deepcopy(c["variants"][0])),
            lambda c: c["variants"].reverse(),
            lambda c: c["variants"].append({"id": "second_clean", "operation": "identity"}),
        ):
            config = deepcopy(CONFIG)
            mutate(config)
            with self.subTest(config=config), self.assertRaises(ValidationError):
                RobustnessConfig.model_validate(config)

    def test_transform_parameters_reject_unsupported_geometry_and_ambiguous_values(self):
        for variant in (
            {"id": "rotate", "operation": "rotate", "angle": 10},
            {"id": "crop", "operation": "crop"},
            {"id": "clean", "operation": "identity", "radius": 0.7},
            {"id": "blur", "operation": "gaussian_blur"},
            {"id": "blur", "operation": "gaussian_blur", "radius": float("nan")},
            {"id": "jpeg", "operation": "jpeg", "quality": 60.5},
            {"id": "jpeg", "operation": "jpeg", "quality": True},
            {"id": "contrast", "operation": "contrast", "factor": 0},
            {"id": "../outside", "operation": "identity"},
        ):
            with self.subTest(variant=variant), self.assertRaises(ValidationError):
                Variant.model_validate(variant)

    def test_summary_and_transitions_reject_missing_duplicate_or_invalid_facts(self):
        clean = [{"fact_id": "cash", "status": "consistent"}, {"fact_id": "debt", "status": "insufficient"}]
        for variant in (clean[:1], clean+clean[:1], clean+[{"fact_id": "extra", "status": "consistent"}],
                        [{"fact_id": "cash", "status": "approved"}, clean[1]]):
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                compare_conditions(clean, variant)

    def test_transition_counts_keep_regressions_and_apparent_improvements_separate(self):
        clean = [{"fact_id": "cash", "status": "consistent"}, {"fact_id": "debt", "status": "insufficient"},
                 {"fact_id": "capex", "status": "conflict"}]
        changed = [{"fact_id": "cash", "status": "insufficient"}, {"fact_id": "debt", "status": "consistent"},
                   {"fact_id": "capex", "status": "conflict"}]
        comparison = compare_conditions(clean, changed)
        self.assertEqual(comparison["fact_count"], 3)
        self.assertEqual(sum(comparison["transitions"].values()), 3)
        self.assertEqual(comparison["status_changed"], 2)
        self.assertEqual(comparison["consistent_to_nonconsistent"], 1)
        self.assertEqual(comparison["nonconsistent_to_consistent"], 1)

    def test_ocr_output_length_mismatch_stops_instead_of_truncating_evidence(self):
        page = {"page": 1, "width": 100, "height": 200, "image_width": 200, "image_height": 400}
        empty = result_to_page(SimpleNamespace(boxes=None), page)
        self.assertEqual(empty.spans, [])
        self.assertEqual((empty.width, empty.height), (100, 200))
        with self.assertRaises(ValueError):
            result_to_page(SimpleNamespace(boxes=[1], txts=[], scores=[]), page)

    def test_public_result_retains_every_check_and_failed_observation(self):
        fact = {"fact_id": "cash", "label": "cash", "page": 1, "status": "insufficient", "automatic_approval": False,
                "checks": [{"field": "amount", "status": "consistent", "reason": "same_displayed_content",
                            "expected": "10", "region": {}, "observations": [{"text": "10"}]},
                           {"field": "unit", "status": "insufficient", "reason": "low_ocr_confidence",
                            "expected": "millions", "region": {"x0": 1}, "observations": [{"text": "milllons"}]}]}
        output = compact_fact(fact)
        self.assertEqual(len(output["checks"]), 2)
        self.assertNotIn("observations", output["checks"][0])
        self.assertEqual(output["checks"][1]["observations"], fact["checks"][1]["observations"])
        self.assertFalse(output["automatic_approval"])

    def test_published_run_contains_every_page_condition_fact_and_field(self):
        report = json.loads((ROOT/"artifacts/visual-robustness-report.json").read_text(encoding="utf-8"))
        config = RobustnessConfig.model_validate(CONFIG)
        self.assertEqual(report["config_sha256"], digest(config.model_dump(mode="json")))
        self.assertEqual(report["environment"]["providers"], {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]})
        self.assertEqual({(d["id"], d["page"]) for d in report["documents"]}, {(p.document_id, p.page) for p in config.pages})
        variants = [v.id for v in config.variants]
        total = 0
        for doc in report["documents"]:
            self.assertEqual([c["id"] for c in doc["conditions"]], variants)
            clean = doc["conditions"][0]
            self.assertEqual(clean["image_sha256"], doc["source_image_sha256"])
            expected_fields = {f["fact_id"]: [c["field"] for c in f["checks"]] for f in clean["facts"]}
            for condition in doc["conditions"]:
                total += len(condition["facts"])
                self.assertEqual({f["fact_id"] for f in condition["facts"]}, set(doc["registered_fact_ids"]))
                self.assertEqual(summarize(condition["facts"]), condition["summary"])
                self.assertEqual(compare_conditions(clean["facts"], condition["facts"]), condition["compared_with_clean"])
                self.assertEqual(condition["synthetic_degradation"], condition["id"] != "clean")
                for fact in condition["facts"]:
                    self.assertFalse(fact["automatic_approval"])
                    self.assertEqual([c["field"] for c in fact["checks"]], expected_fields[fact["fact_id"]])
                    if fact["status"] == "consistent":
                        self.assertTrue(all(c["status"] == "consistent" for c in fact["checks"]))
        self.assertEqual(total, 80)
        for row in report["summary_by_variant"]:
            self.assertEqual(row["fact_count"], 20)
            self.assertEqual(sum(row["transitions_from_clean"].values()), 20)
            expected = {s: sum(next(c for c in d["conditions"] if c["id"] == row["id"])["summary"][s]
                               for d in report["documents"]) for s in STATES}
            self.assertEqual(row["summary"], expected)


@unittest.skipUnless(find_spec("PIL"), "Optional image transforms require requirements-pdf.lock")
class RobustnessImageTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        image = Image.new("RGB", (24, 32), "white")
        for x in range(7, 13):
            for y in range(6, 26):
                image.putpixel((x, y), (0, 0, 0))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        self.raw = buffer.getvalue()

    def test_transforms_are_repeatable_preserve_size_and_keep_identity_exact(self):
        from PIL import Image
        config = RobustnessConfig.model_validate(CONFIG)
        for variant in config.variants:
            transformed = transform_image(self.raw, variant)
            self.assertEqual(transformed, transform_image(self.raw, variant))
            with Image.open(BytesIO(transformed)) as image:
                self.assertEqual(image.size, (24, 32))
            if variant.operation == "identity":
                self.assertEqual(transformed, self.raw)
            else:
                self.assertNotEqual(transformed, self.raw)

    def test_changed_source_hash_or_dimensions_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"page.png"
            path.write_bytes(self.raw)
            checksum = sha256(self.raw).hexdigest()
            self.assertEqual(verified_image(path, checksum, (24, 32)), self.raw)
            for wrong_hash, wrong_size in [("0"*64, (24, 32)), (checksum, (32, 24))]:
                with self.subTest(wrong_hash=wrong_hash, wrong_size=wrong_size), self.assertRaises(ValueError):
                    verified_image(path, wrong_hash, wrong_size)


if __name__ == "__main__":
    unittest.main()
