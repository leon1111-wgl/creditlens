"""Synthetic issuer contracts and mocked image-only invocation, not OCR scores."""
import builtins
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from creditlens import issuer_evaluation as issuer
from creditlens.diligence import digest
from creditlens.document_transfer import file_hash
from creditlens.word_evidence import WordLine, WordPage
from scripts import evaluate_issuer as runner
from tests import test_date_evidence as dates
from tests import test_document_transfer as fixtures


class IssuerEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TransferTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        for relative in issuer.REQUIRED_FILES:
            dest = self.root/relative; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixtures.ROOT/relative, dest)
        fact, page = dates.controlled()
        extra = dates.span("unrelated footer", dates.box(1, 1, 30, 10))
        page["lines"].insert(0, {"line": extra, "words": [deepcopy(extra)]})
        self.page = WordPage.model_validate(page)
        source = self.fixture.pack["sources"][0]
        source = {key: source[key] for key in ("id", "title", "url", "sha256", "underlying_filing_date")}
        source.update(id=fact.source_id, wrapper_filing_date="2025-09-01",
            wrapper_filing_evidence_url="https://example.org/wrapper-index",
            exact_asset_publication_date=None, first_available_at=None,
            source_date_basis="exact_asset_publication_unknown", publication_evidence_url=None,
            publication_note="Wrapper event is known; exact current asset publication is not established",
            revision_note="Synthetic revision status; no historical byte snapshot is asserted",
            retrieved_at="2026-01-01T00:00:00Z", historical_snapshot_verified=False, point_in_time_eligible=False)
        self.pack = {**self.fixture.pack, "purpose": "frozen_issuer_evaluation", "sources": [source],
                     "facts": [fact.model_dump(mode="json")]}
        self.manifest = deepcopy(self.fixture.manifest)
        self.manifest.update(suite_version=issuer.VERSION, experiment_id="independent-issuer", frozen_at="2026-01-02T00:00:00Z",
            files={name: file_hash(self.root/name) for name in issuer.REQUIRED_FILES}, rule_versions=issuer.RULES.copy(),
            execution_packages={name: "fixture-version" for name in issuer.EXECUTION_PACKAGES},
            render={"engine": "pypdfium2", "version": "fixture-version", "pillow_version": "fixture-version",
                    "scale": 2.0, "rotation": 0, "image_format": "PNG"})
        doc = self.manifest["documents"][0]
        doc["pdf"] = {key: doc["pdf"][key] for key in ("url", "sha256", "filename", "allowed_hosts")}
        doc["pages"] = [{"page": self.page.page, "width": self.page.width, "height": self.page.height,
                         "image_width": 1000, "image_height": 1400,
                         "image_sha256": sha256(self.fixture.image).hexdigest(), "fact_ids": [fact.id]}]
        self.manifest_path = self.root/"configs/issuer-evaluation.json"
        self.save()
        self.local = self.root/".run/reproductions/issuer-evaluation"
        self.output = self.root/".run/reproductions/issuer-evaluation-report.json"

    def save(self):
        doc = self.manifest["documents"][0]
        path = self.root/doc["pack_path"]
        self.fixture.write(path, self.pack)
        doc["pack_sha256"] = sha256(path.read_bytes()).hexdigest()
        self.fixture.write(self.manifest_path, self.manifest)

    def report(self, page=None):
        frozen, packs = issuer.load_manifest(self.root)
        doc = frozen.documents[0]; pack = packs[doc.id]; page = page or self.page
        evidence, outcomes = issuer.compact_page(page, pack.facts, frozen.profile["min_confidence"])
        groups = issuer.date_groups(doc.id, doc.pdf.sha256, pack.facts, outcomes)
        compact = WordPage(page=page.page, width=page.width, height=page.height,
                           lines=[WordLine.model_validate({k: line[k] for k in ("line", "words")}) for line in evidence])
        summary = issuer.summarize(outcomes, groups, 1, 1)
        return {"schema_version": 1, "suite_version": issuer.VERSION, "experiment_id": frozen.experiment_id,
                "generated_at": datetime.now(timezone.utc).isoformat(), "manifest_sha256": frozen.snapshot_sha256,
                "rule_versions": issuer.RULES.copy(), "scope": issuer.SCOPE, "automatic_approval": False,
                "environment": {"operation": "full_page_ocr_then_frozen_rule_evaluation", "reference_sent_to_recognizer": False,
                    "providers": {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]},
                    "packages": self.manifest["execution_packages"].copy()},
                "documents": [{"id": doc.id, "company": pack.company, "source": pack.sources[0].model_dump(mode="json"),
                    "pdf": doc.pdf.model_dump(mode="json"), "pack_sha256": doc.pack_sha256, "raw_pdf_sha256": doc.pdf.sha256,
                    "pages": [{**doc.pages[0].model_dump(mode="json"), "full_line_ocr_sha256": digest(page.line_page().model_dump(mode="json")),
                        "full_word_ocr_sha256": digest(page.model_dump(mode="json")), "evidence_sha256": digest(compact.model_dump(mode="json")),
                        "ocr_line_count": len(page.lines), "elapsed_seconds": 0.1, "evidence_lines": evidence}],
                    "facts": outcomes, "date_groups": groups, "summary": summary}], "summary": summary}

    def run_mocked(self, **kwargs):
        doc = self.manifest["documents"][0]
        pdf = self.local/doc["id"]/doc["pdf"]["filename"]
        pdf.parent.mkdir(parents=True, exist_ok=True); pdf.write_bytes(self.fixture.raw_pdf)
        image = Mock(size=(1000, 1400)); image.copy.return_value = image
        image.save.side_effect = lambda buffer, **kw: buffer.write(self.fixture.image)
        bitmap = Mock(); bitmap.to_pil.return_value = image
        pdf_page = Mock(); pdf_page.get_rotation.return_value = 0
        pdf_page.get_width.return_value = 500; pdf_page.get_height.return_value = 700
        pdf_page.render.return_value = bitmap
        class Pdf:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __getitem__(self, index): return pdf_page
        engine = Mock(return_value=SimpleNamespace(boxes=None))
        engine.text_det.session.session.get_providers.return_value = ["CPUExecutionProvider"]
        engine.text_rec.session.session.get_providers.return_value = ["CPUExecutionProvider"]
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "version", return_value="fixture-version"), \
             patch.dict("sys.modules", {"pypdfium2": SimpleNamespace(PdfDocument=Pdf)}), \
             patch.object(runner, "create_engine", return_value=engine), redirect_stdout(StringIO()):
            result = runner.run(**kwargs)
        return result, engine, pdf

    def test_source_filing_event_is_not_exact_asset_publication_or_pit(self):
        source = issuer.IssuerPack.model_validate(self.pack).sources[0]
        self.assertEqual(source.wrapper_filing_date.isoformat(), "2025-09-01")
        self.assertIsNone(source.exact_asset_publication_date)
        self.assertFalse(source.historical_snapshot_verified)
        self.assertFalse(source.point_in_time_eligible)
        changed = deepcopy(self.pack)
        changed["sources"][0].update(exact_asset_publication_date="2025-09-01")
        with self.assertRaises(ValueError):
            issuer.IssuerPack.model_validate(changed)
        changed["sources"][0].update(source_date_basis="exact_asset_publication_verified",
                                    publication_evidence_url="https://example.org/asset-evidence")
        known = issuer.IssuerPack.model_validate(changed).sources[0]
        self.assertIsNotNone(known.exact_asset_publication_date)
        self.assertFalse(known.point_in_time_eligible)
        changed["sources"][0]["point_in_time_eligible"] = True
        with self.assertRaises(ValueError):
            issuer.IssuerPack.model_validate(changed)

    def test_all_executed_dependencies_and_reference_builder_are_frozen(self):
        for relative in ("creditlens/date_evidence.py", "creditlens/issuer_evaluation.py", "scripts/evaluate_issuer.py",
                         "scripts/evaluate_document_transfer.py", "configs/document-ai.json", "scripts/build_test_reference.py"):
            path = self.root/relative; raw = path.read_bytes(); path.write_bytes(raw+b"\n")
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                issuer.load_manifest(self.root)
            path.write_bytes(raw)

    def test_python_lf_is_portable_but_json_and_pack_are_raw_byte_bound(self):
        python = self.root/"creditlens/date_evidence.py"
        python.write_bytes(python.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        issuer.load_manifest(self.root)
        pack = self.root/self.manifest["documents"][0]["pack_path"]
        pack.write_bytes(pack.read_bytes().replace(b"\n", b"\r\n"))
        with self.assertRaises(ValueError):
            issuer.load_manifest(self.root)

    def test_inventory_profile_rule_render_and_previous_issuer_changes_rejected(self):
        original = deepcopy(self.manifest)
        changes = (lambda m: m["documents"][0]["pages"][0].update(fact_ids=[]),
                   lambda m: m["documents"][0]["pages"].append(deepcopy(m["documents"][0]["pages"][0])),
                   lambda m: m["documents"][0]["pages"][0].update(width=501),
                   lambda m: m["documents"][0].update(id="nvda"),
                   lambda m: m["documents"][0].update(pack_path="../private.json"),
                   lambda m: m["files"].pop("creditlens/date_evidence.py"),
                   lambda m: m["rule_versions"].update(date="changed-rule"),
                   lambda m: m["execution_packages"].pop("rapidocr"),
                   lambda m: m["render"].update(scale=3),
                   lambda m: m.update(frozen_at="2020-01-01T00:00:00Z"))
        for change in changes:
            self.manifest = deepcopy(original); change(self.manifest); self.save()
            with self.subTest(change=change), self.assertRaises(ValueError):
                issuer.load_manifest(self.root)

    def test_full_and_compact_checks_and_all_parent_indices_match(self):
        report = self.report()
        result = issuer.validate_issuer_report(self.root, report)
        self.assertEqual(result["summary"]["date"]["consistent"], 1)
        self.assertEqual(result["summary"]["date_association_applied"], 1)
        self.assertEqual(result["summary"]["facts"], 1)
        evidence = result["documents"][0]["pages"][0]["evidence_lines"]
        self.assertNotIn(0, [line["source_line_index"] for line in evidence])
        association = result["documents"][0]["facts"][0]["date"]["date_association"]
        self.assertEqual(association["candidate_parent_indices"], [0])
        self.assertEqual(association["legacy_year"]["parent_lines"][0]["line_index"], 0)

    def test_date_only_numeric_parent_without_words_is_retained(self):
        raw = self.page.model_dump(mode="json")
        # This numeric parent is nearby but outside the year cell, and has no
        # projected words. Old word guards omit it; date-specific selection must not.
        line = dates.span("9", dates.box(151, 50, 157, 60))
        raw["lines"].append({"line": line, "words": []})
        page = WordPage.model_validate(raw)
        report = self.report(page)
        result = issuer.validate_issuer_report(self.root, report)
        evidence = result["documents"][0]["pages"][0]["evidence_lines"]
        self.assertIn(len(page.lines)-1, [entry["source_line_index"] for entry in evidence])
        fact = result["documents"][0]["facts"][0]
        self.assertFalse(fact["date"]["date_association"]["applied"])
        self.assertEqual(fact["date"]["status"], "insufficient")

    def test_unsupported_multiline_date_remains_in_inventory_and_denominator(self):
        raw = self.page.model_dump(mode="json")
        date_line = raw["lines"][1]
        date_line["line"]["text"] = "Jan 26,"
        date_line["line"]["bbox"]["x1"] = 157
        year = date_line["words"].pop()
        raw["lines"].append({"line": deepcopy(year), "words": [deepcopy(year)]})
        report = self.report(WordPage.model_validate(raw))
        issuer.validate_issuer_report(self.root, report)
        self.assertEqual(report["summary"]["facts"], 1)
        self.assertEqual(report["summary"]["date_association_applied"], 0)
        self.assertNotEqual(report["documents"][0]["facts"][0]["date"]["status"], "consistent")

    def test_shared_headers_count_once_and_conflicting_expected_dates_reject(self):
        added = deepcopy(self.pack["facts"][0]); added["id"] = "another-cash"
        self.pack["facts"].append(added)
        self.manifest["documents"][0]["pages"][0]["fact_ids"].append(added["id"])
        self.save()
        report = self.report()
        self.assertEqual(report["summary"]["facts"], 2)
        self.assertEqual(report["summary"]["distinct_date_groups"], 1)
        self.pack["facts"][1]["period_end"] = "2025-01-25"
        self.save()
        with self.assertRaisesRegex(ValueError, "Shared date header"):
            issuer.load_manifest(self.root)

    def test_report_fields_guards_groups_sources_and_totals_are_recomputed(self):
        good = self.report()
        changes = (lambda r: r["summary"].update(facts=2),
                   lambda r: r["summary"]["date"].update(consistent=True),
                   lambda r: r["documents"][0]["facts"][0]["date"].update(status="conflict"),
                   lambda r: r["documents"][0]["facts"][0]["word"]["checks"][0].update(expected="fake"),
                   lambda r: r["documents"][0]["facts"][0]["date"]["date_association"].update(candidate_parent_indices=[]),
                   lambda r: r["documents"][0]["date_groups"][0].update(fact_ids=[]),
                   lambda r: r["documents"][0]["pages"][0].update(fact_ids=["wrong"]),
                   lambda r: r["documents"][0]["pages"][0].update(evidence_sha256="0"*64),
                   lambda r: r["documents"][0]["source"].update(revision_note="Unrelated changed source note"),
                   lambda r: r["environment"].update(reference_sent_to_recognizer=True),
                   lambda r: r["environment"]["providers"].update(detection=["CUDAExecutionProvider"]),
                   lambda r: r["environment"]["packages"].update(Pillow="other"))
        for change in changes:
            bad = deepcopy(good); change(bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                issuer.validate_issuer_report(self.root, bad)

    def test_out_of_page_and_duplicate_source_line_mapping_rejected(self):
        good = self.report()
        for change in (lambda p: p["evidence_lines"][0]["line"]["bbox"].update(x1=9999),
                       lambda p: p["evidence_lines"][1].update(source_line_index=p["evidence_lines"][0]["source_line_index"]),
                       lambda p: p["evidence_lines"][0].update(source_line_index=p["ocr_line_count"])):
            bad = deepcopy(good); change(bad["documents"][0]["pages"][0])
            with self.assertRaises(ValueError):
                issuer.validate_issuer_report(self.root, bad)

    def test_image_only_single_invocation_runs_all_rules_and_preserves_every_fact(self):
        report, engine, pdf = self.run_mocked()
        image = pdf.parent/"page-1.png"
        engine.assert_called_once_with(str(image), return_word_box=True, return_single_char_box=False)
        self.assertEqual(report["summary"]["facts"], 1)
        self.assertEqual(report["summary"]["line"]["insufficient"], 1)
        self.assertEqual(report["summary"]["word"]["insufficient"], 1)
        self.assertEqual(report["summary"]["date"]["insufficient"], 1)
        self.assertTrue(self.output.is_file())
        self.assertTrue((pdf.parent/"ocr-words-1.json").is_file())
        self.assertTrue((pdf.parent/"ocr-lines-1.json").is_file())
        self.assertFalse((self.root/"artifacts/issuer-evaluation-report.json").exists())

    def test_invalid_freeze_paths_existing_files_fail_before_network_or_model(self):
        old_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"rapidocr", "onnxruntime", "pypdfium2", "PIL"}:
                raise AssertionError("Model/render imported before preflight")
            return old_import(name, *args, **kwargs)
        with patch.object(runner, "ROOT", self.root), patch("builtins.__import__", side_effect=guarded), \
             patch.object(runner, "obtain_pdf", side_effect=AssertionError("Download reached")):
            for output in (self.root/"artifacts/new.json", self.root/".run/document-transfer/new.json",
                           self.root/".run/issuer-evaluation/new.json", self.root/".run/transfer-attempts/new.json"):
                with self.assertRaises(ValueError):
                    runner.run(output=output)
            existing = self.local/"testco/page-1.png"
            existing.parent.mkdir(parents=True); existing.write_bytes(b"preserved")
            with self.assertRaises(ValueError):
                runner.run()
            self.assertEqual(existing.read_bytes(), b"preserved")
            (self.root/"creditlens/date_evidence.py").write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.run(local_dir=self.root/".run/fresh")
        self.assertFalse(self.output.exists())

    def test_image_or_renderer_mismatch_stops_before_ocr(self):
        self.manifest["documents"][0]["pages"][0]["image_sha256"] = "0"*64
        self.save()
        with self.assertRaisesRegex(ValueError, "render differs"):
            self.run_mocked()
        self.assertFalse((self.local/"testco/ocr-words-1.json").exists())
        with patch.object(runner, "ROOT", self.root), patch.object(runner, "version", return_value="other"), \
             patch.object(runner, "obtain_pdf", side_effect=AssertionError("Download before render version check")):
            with self.assertRaisesRegex(ValueError, "execution packages differ"):
                runner.run()

    def test_execution_package_freeze_precedes_download_but_public_replay_needs_no_ocr(self):
        report = self.report()
        with patch.object(runner, "ROOT", self.root), \
             patch.object(runner, "version", side_effect=lambda name: "different-ocr" if name == "rapidocr" else "fixture-version"), \
             patch.object(runner, "obtain_pdf", side_effect=AssertionError("Download before OCR version check")):
            with self.assertRaisesRegex(ValueError, "execution packages differ"):
                runner.run()
        report["environment"]["packages"]["rapidocr"] = "different-ocr"
        with self.assertRaisesRegex(ValueError, "package versions differ"):
            issuer.validate_issuer_report(self.root, report)
        report = self.report()
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"rapidocr", "onnxruntime", "numpy", "pypdfium2", "PIL"}:
                raise AssertionError("Public evidence validation imported inference/rendering")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded):
            issuer.validate_issuer_report(self.root, report)

    def test_write_helpers_never_overwrite_existing_bytes(self):
        target = self.root/".run/result.json"
        runner.write_new_json(self.root, target, {"first": True})
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            runner.write_new_json(self.root, target, {"first": False})
        self.assertEqual(target.read_bytes(), before)

    def test_strict_json_and_public_view_report_binding(self):
        path = self.root/"bad.json"
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'[]'):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                issuer.read_json(path)
        report_path = self.root/"artifacts/issuer-evaluation-report.json"
        self.fixture.write(report_path, self.report())
        view = issuer.issuer_view(self.root)
        self.assertEqual(view["report_sha256"], sha256(report_path.read_bytes()).hexdigest())
        self.assertEqual(view["manifest"]["experiment_id"], "independent-issuer")
        self.assertTrue(view["read_only"])


if __name__ == "__main__":
    unittest.main()
