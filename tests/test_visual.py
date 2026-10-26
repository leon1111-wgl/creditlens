from copy import deepcopy
from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from creditlens.api import create_app
from creditlens.diligence import CasePack, digest
from creditlens.visual import OcrPage, crosscheck_fact
from creditlens.visual_store import VisualStore, VisualArtifactError

ROOT = Path(__file__).resolve().parents[1]
PACK = CasePack.model_validate_json((ROOT/"data/examples/msft-fy2025-pdf.json").read_text(encoding="utf-8"))
REPORT = json.loads((ROOT/"artifacts/document-ai-report.json").read_text(encoding="utf-8"))


def sample_fact():
    return next(f for f in PACK.facts if f.id == "capex-2025")


def synthetic_page(fact):
    """Controlled OCR responses for rule tests, not real model performance."""
    loc = fact.pdf_location
    fields = [(loc.amount_bbox, fact.raw_value), (loc.year_bbox, str(loc.year)),
              (loc.period_bbox, loc.period_header), (loc.unit_bbox, loc.unit_header),
              (loc.row_label_bbox, loc.row_label)]
    return {"page": loc.page, "width": loc.page_width, "height": loc.page_height,
            "spans": [{"text": text, "confidence": 0.99, "bbox": box.model_dump()} for box, text in fields]}


class VisualRulesTests(unittest.TestCase):
    def check(self, mutate=lambda p: None):
        fact = sample_fact()
        page = synthetic_page(fact)
        mutate(page)
        return crosscheck_fact(fact, OcrPage.model_validate(page))

    def test_all_fields_required_but_match_never_automatically_approves(self):
        result = self.check()
        self.assertEqual(result["status"], "consistent")
        self.assertEqual(len(result["checks"]), 5)
        self.assertFalse(result["automatic_approval"])

    def test_configured_threshold_changes_decision(self):
        fact = sample_fact()
        page = synthetic_page(fact)
        page["spans"][0]["confidence"] = 0.95
        ocr = OcrPage.model_validate(page)
        self.assertEqual(crosscheck_fact(fact, ocr, min_confidence=0.90)["status"], "consistent")
        self.assertEqual(crosscheck_fact(fact, ocr, min_confidence=0.99)["status"], "insufficient")
        for threshold in (float("nan"), -1, 2):
            with self.subTest(threshold=threshold), self.assertRaises(ValueError):
                crosscheck_fact(fact, ocr, min_confidence=threshold)

    def test_missing_parentheses_conflicts_even_with_absolute_cash_outflow(self):
        result = self.check(lambda p: p["spans"][0].update(text="64,551"))
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["checks"][0]["status"], "conflict")

    def test_wrong_year_or_magnitude_blocks_full_fact_agreement(self):
        for index, text in [(1, "2024"), (3, "(In billions)")]:
            with self.subTest(text=text):
                self.assertEqual(self.check(lambda p: p["spans"][index].update(text=text))["status"], "conflict")

    def test_low_confidence_correct_digits_are_insufficient(self):
        result = self.check(lambda p: p["spans"][0].update(confidence=0.3))
        self.assertEqual(result["status"], "insufficient")

    def test_missing_unit_is_not_ignored(self):
        self.assertEqual(self.check(lambda p: p["spans"].pop(3))["status"], "insufficient")

    def test_blank_image_keeps_all_five_checks_missing(self):
        result = self.check(lambda p: p.update(spans=[]))
        self.assertEqual(result["status"], "insufficient")
        self.assertTrue(all(c["reason"] == "no_spatial_evidence" for c in result["checks"]))

    def test_merged_adjacent_column_is_not_resolved_by_expected_substring(self):
        def merge(page):
            page["spans"][0]["text"] = "(64,551) $ 44,477"
            page["spans"][0]["bbox"]["x1"] += 100
        result = self.check(merge)
        self.assertEqual(result["checks"][0]["reason"], "merged_or_misaligned_region")
        self.assertEqual(result["status"], "insufficient")

    def test_two_overlapping_candidates_are_not_cherry_picked(self):
        def duplicate(page):
            other = deepcopy(page["spans"][0])
            other["text"] = "123"
            page["spans"].append(other)
        self.assertEqual(self.check(duplicate)["checks"][0]["reason"], "ambiguous_cell")

    def test_garbled_numeric_characters_are_not_repaired(self):
        result = self.check(lambda p: p["spans"][0].update(text="(64,55I)"))
        self.assertEqual(result["checks"][0]["reason"], "unreadable_numeric_cell")

    def test_same_digits_in_another_column_do_not_count(self):
        def shift(page):
            page["spans"][0]["bbox"]["x0"] += 100
            page["spans"][0]["bbox"]["x1"] += 100
        self.assertEqual(self.check(shift)["checks"][0]["reason"], "no_spatial_evidence")

    def test_wrong_page_or_dimensions_rejected(self):
        for key in ("page", "width", "height"):
            p = synthetic_page(sample_fact())
            p[key] += 1
            with self.subTest(key=key), self.assertRaises(ValueError):
                crosscheck_fact(sample_fact(), OcrPage.model_validate(p))

    def test_invalid_ocr_coordinates_and_confidence_rejected(self):
        for value in (float("nan"), -1, 2):
            p = synthetic_page(sample_fact())
            p["spans"][0]["confidence"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                OcrPage.model_validate(p)
        p = synthetic_page(sample_fact())
        p["spans"][0]["bbox"]["x1"] = p["width"]+1
        with self.assertRaises(ValidationError):
            OcrPage.model_validate(p)

    def test_published_run_keeps_all_facts_including_failures(self):
        self.assertEqual(sum(REPORT["summary"].values()), 42)
        self.assertEqual({d["id"] for d in REPORT["documents"]}, {"msft", "aapl"})
        self.assertEqual(REPORT["environment"]["providers"]["detection"], ["CPUExecutionProvider"])
        self.assertEqual(REPORT["environment"]["providers"]["recognition"], ["CPUExecutionProvider"])
        for doc in REPORT["documents"]:
            self.assertEqual(sum(doc["summary"].values()), len(doc["facts"]))
            for fact in doc["facts"]:
                self.assertFalse(fact["automatic_approval"])
                if fact["status"] == "consistent":
                    self.assertTrue(all(c["status"] == "consistent" for c in fact["checks"]))


class VisualStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/"artifacts").mkdir()
        (self.root/"configs").mkdir()
        (self.root/"configs/document-ai.json").write_bytes((ROOT/"configs/document-ai.json").read_bytes())
        (self.root/"data/examples").mkdir(parents=True)
        for filename in ("msft-fy2025-pdf.json", "aapl-fy2025-pdf.json"):
            (self.root/"data/examples"/filename).write_bytes((ROOT/"data/examples"/filename).read_bytes())
        self.report = deepcopy(REPORT)
        self.raw = b"\x89PNG\r\n\x1a\nsynthetic-preview-bytes"
        self.report["documents"][0]["pages"][0]["image_sha256"] = hashlib.sha256(self.raw).hexdigest()
        self.save()
        self.image_path = self.root/".run/document-ai/msft/page-50.png"
        self.image_path.parent.mkdir(parents=True)
        self.image_path.write_bytes(self.raw)
        self.store = VisualStore(self.root)

    def save(self):
        (self.root/"artifacts/document-ai-report.json").write_text(json.dumps(self.report), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_hash_bound_image_and_missing_preview_status(self):
        self.assertEqual(self.store.image("msft", 50), self.raw)
        view = self.store.view()
        self.assertTrue(view["documents"][0]["pages"][0]["preview_available"])
        self.assertFalse(view["documents"][0]["pages"][1]["preview_available"])

    def test_changed_image_is_rejected(self):
        self.image_path.write_bytes(self.raw+b"changed")
        with self.assertRaises(VisualArtifactError):
            self.store.image("msft", 50)

    def test_fact_pack_change_refuses_stale_ocr_report(self):
        p = self.root/"data/examples/msft-fy2025-pdf.json"
        data = json.loads(p.read_text(encoding="utf-8"));data["title"] += " revised"
        p.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(VisualArtifactError):
            self.store.view()

    def test_unknown_pages_and_paths_are_not_read(self):
        for doc, page in [("../../data", 50), ("msft", 51), ("msft", -1)]:
            with self.subTest(doc=doc, page=page), self.assertRaises(FileNotFoundError):
                self.store.image(doc, page)

    def test_report_references_counts_and_configuration_must_reproduce(self):
        original = deepcopy(self.report)
        mutations = [
            lambda r: r["documents"][0]["facts"][0]["checks"][0].update(expected="999999"),
            lambda r: r["documents"][0].update(facts=[]),
            lambda r: r["documents"][0]["facts"].append(deepcopy(r["documents"][0]["facts"][0])),
            lambda r: r["documents"][0]["facts"][0].update(page=999),
            lambda r: r["documents"][0]["facts"][0].update(automatic_approval=True),
            lambda r: r["documents"][0]["source"].update(url="https://example.org/wrong.pdf"),
            lambda r: r["documents"][0]["summary"].update(consistent=999),
            lambda r: r["summary"].update(consistent=999),
            lambda r: r["profile"].update(min_confidence=0.99),
            lambda r: r.update(profile_sha256="0"*64),
            lambda r: r["documents"][0]["pages"][0].update(width=999),
        ]
        for index, mutate in enumerate(mutations):
            self.report = deepcopy(original)
            mutate(self.report)
            self.save()
            with self.subTest(index=index), self.assertRaises(VisualArtifactError):
                self.store.view()

    def test_duplicate_ocr_detections_remain_valid_ambiguous_evidence(self):
        doc = self.report["documents"][0]
        first = doc["facts"][0]
        number = first["page"]
        spans = {digest(s): s for f in doc["facts"] if f["page"] == number
                 for c in f["checks"] for s in c["observations"]}
        duplicate = deepcopy(first["checks"][0]["observations"][0])
        loc = next(f for f in PACK.facts if f.id == first["fact_id"]).pdf_location
        ocr = OcrPage(page=number, width=loc.page_width, height=loc.page_height,
                      spans=[*spans.values(), duplicate])
        by_id = {f.id: f for f in PACK.facts}
        doc["facts"] = [crosscheck_fact(by_id[f["fact_id"]], ocr) if f["page"] == number else f
                        for f in doc["facts"]]
        self.assertEqual(doc["facts"][0]["checks"][0]["reason"], "ambiguous_cell")
        counts = Counter(f["status"] for f in doc["facts"])
        doc["summary"] = {s: counts[s] for s in ("consistent", "conflict", "insufficient")}
        totals = Counter(f["status"] for d in self.report["documents"] for f in d["facts"])
        self.report["summary"] = {s: totals[s] for s in ("consistent", "conflict", "insufficient")}
        self.save()
        self.assertEqual(self.store.report(), self.report)

    def test_api_authentication_read_only_and_tampered_preview(self):
        principals=[dict(key_sha256=hashlib.sha256(b"test-key").hexdigest(), user_id="v", tenant_id="a", role="viewer")]
        with patch("creditlens.api.ROOT", self.root), TestClient(create_app(self.root/"missing.json", self.root/"audit.sqlite",
                                               auth_mode="api_key", principals=principals)) as client:
            headers={"Authorization":"Bearer test-key"}
            query="?expected_sha256="+hashlib.sha256(self.raw).hexdigest()
            for route in ("/api/documents", "/api/documents/msft/pages/50.png"+query):
                self.assertEqual(client.get(route).status_code, 401)
                self.assertEqual(client.get(route, headers={"X-Demo-Role":"viewer"}).status_code, 401)
                self.assertEqual(client.get(route, headers=headers).status_code, 200)
            response=client.get("/api/documents/msft/pages/50.png"+query,headers=headers)
            self.assertEqual(response.headers["content-type"],"image/png")
            self.assertEqual(response.headers["Cache-Control"],"no-store")
            self.assertEqual(client.get("/api/documents/msft/pages/51.png"+query,headers=headers).status_code,404)
            self.image_path.write_bytes(self.raw+b"changed")
            self.assertEqual(client.get("/api/documents/msft/pages/50.png"+query,headers=headers).status_code,409)
            self.assertEqual(client.post("/api/documents",headers=headers).status_code,405)
            self.assertEqual(client.get("/api/reviews",headers=headers).json(),[])


if __name__ == "__main__":
    unittest.main()
