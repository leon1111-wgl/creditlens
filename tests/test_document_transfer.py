"""Synthetic contract fixtures; no real PDF rendering, OCR or network calls."""
import builtins
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from creditlens.diligence import CasePack, digest
from creditlens.document_transfer import (REQUIRED_FILES, VERSION, SCOPE, PdfRegistration, TransferPack,
    compact_page, file_hash, load_manifest, summarize, transfer_view, validate_transfer_report)
from creditlens.word_evidence import WORD_RULE_VERSION, WordLine, WordPage
from creditlens.visual import VISUAL_RULE_VERSION
from scripts import evaluate_document_transfer as runner

ROOT = Path(__file__).resolve().parents[1]


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in REQUIRED_FILES:
            dest = self.root/relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, dest)
        self.raw_pdf = b"%PDF-synthetic contract fixture, not a usable PDF"
        self.image = b"synthetic image fixture, never a real OCR input"
        reference_builder = self.root/"scripts/build_test_reference.py"
        reference_builder.write_text("# Synthetic test fixture builder\n", encoding="utf-8", newline="\n")
        original = json.loads((ROOT/"data/examples/msft-fy2025-pdf.json").read_text(encoding="utf-8"))
        fact = next(f for f in original["facts"] if f["id"] == "capex-2025")
        source = {"id": fact["source_id"], "title": "Synthetic independent issuer reference", "url": "https://example.org/test.pdf",
                  "sha256": hashlib.sha256(self.raw_pdf).hexdigest(), "wrapper_publication_date": None,
                  "underlying_filing_date": "2025-07-30", "source_date_basis": "wrapper_publication_unknown",
                  "point_in_time_eligible": False}
        self.pack = {"schema_version": 1, "purpose": "known_location_transfer", "entity_id": "independent-fixture",
                     "company": "Independent fixture issuer", "title": "Controlled financial reference",
                     "sources": [source], "facts": [fact]}
        loc = fact["pdf_location"]
        self.manifest = {"schema_version": 1, "suite_version": VERSION, "experiment_id": "independent-transfer",
            "frozen_at": "2020-01-01T00:00:00Z", "selection_reason": "Synthetic fixture for contract validation only",
            "reference_status": "single_annotator_development_reference", "file_hash_basis": "python_utf8_lf_other_raw",
            "files": {relative: file_hash(self.root/relative) for relative in REQUIRED_FILES},
            "reference_files": {"scripts/build_test_reference.py": file_hash(reference_builder)},
            "documents": [{"id": "testco", "pack_path": "data/examples/independent-transfer.json", "pack_sha256": "0"*64,
                "pdf": {"url": source["url"], "sha256": source["sha256"], "filename": "testco-transfer.pdf",
                    "allowed_hosts": ["example.org"], "underlying_filing_date": "2025-07-30", "wrapper_published_at": None,
                    "publication_note": "Wrapper publication is unknown; fixture does not support point-in-time research",
                    "point_in_time_eligible": False},
                "pages": [{"page": loc["page"], "width": loc["page_width"], "height": loc["page_height"],
                    "image_width": int(loc["page_width"]*2), "image_height": int(loc["page_height"]*2),
                    "image_sha256": hashlib.sha256(self.image).hexdigest(), "fact_ids": [fact["id"]]}]}]}
        self.manifest_path = self.root/"configs/document-transfer.json"
        self.save_pack()
        self.save_manifest()

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")

    def save_pack(self):
        path = self.root/self.manifest["documents"][0]["pack_path"]
        self.write(path, self.pack)
        self.manifest["documents"][0]["pack_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    def save_manifest(self):
        self.write(self.manifest_path, self.manifest)

    def page(self):
        pack = TransferPack.model_validate(self.pack)
        fact = pack.facts[0]
        loc = fact.pdf_location
        fields = [(loc.amount_bbox, fact.raw_value), (loc.year_bbox, str(loc.year)),
                  (loc.period_bbox, loc.period_header), (loc.unit_bbox, loc.unit_header),
                  (loc.row_label_bbox, loc.row_label)]
        extra = {"text": "irrelevant footer", "confidence": 0.99, "bbox": {"x0": 1, "top": 1, "x1": 15, "bottom": 5}}
        lines = [{"line": extra, "words": [extra]}]
        for box, text in fields:
            span = {"text": text, "confidence": 0.99, "bbox": box.model_dump()}
            lines.append({"line": deepcopy(span), "words": [deepcopy(span)]})
        return WordPage(page=loc.page, width=loc.page_width, height=loc.page_height, lines=lines), pack.facts

    def report(self, page=None):
        frozen, packs = load_manifest(self.root)
        registration = frozen.documents[0]
        pack = packs[registration.id]
        page = page or self.page()[0]
        evidence, pairs = compact_page(page, pack.facts, 0.9)
        compact = WordPage(page=page.page, width=page.width, height=page.height,
                          lines=[WordLine.model_validate({k: e[k] for k in ("line", "words")}) for e in evidence])
        return {"schema_version": 1, "suite_version": VERSION, "experiment_id": frozen.experiment_id,
            "generated_at": datetime.now(timezone.utc).isoformat(), "scope": SCOPE,
            "manifest_sha256": hashlib.sha256(self.manifest_path.read_bytes()).hexdigest(),
            "rule_versions": {"line": VISUAL_RULE_VERSION, "word": WORD_RULE_VERSION},
            "environment": {"providers": {"detection": ["CPUExecutionProvider"], "recognition": ["CPUExecutionProvider"]}},
            "documents": [{"id": registration.id, "company": pack.company, "source": pack.sources[0].model_dump(mode="json"),
                "source_publication": registration.pdf.model_dump(mode="json"), "pack_sha256": registration.pack_sha256,
                "raw_pdf_sha256": registration.pdf.sha256, "summary": summarize(pairs), "facts": pairs,
                "pages": [{**registration.pages[0].model_dump(mode="json"), "ocr_line_count": len(page.lines),
                    "full_line_ocr_sha256": digest(page.line_page().model_dump(mode="json")),
                    "full_word_ocr_sha256": digest(page.model_dump(mode="json")), "elapsed_seconds": 0.1,
                    "evidence_sha256": digest(compact.model_dump(mode="json")), "evidence_lines": evidence}]}],
            "summary": summarize(pairs)}

    def test_unknown_wrapper_date_is_separate_and_old_financial_entry_cannot_accept_pack(self):
        manifest, packs = load_manifest(self.root)
        self.assertIsNone(packs["testco"].sources[0].wrapper_publication_date)
        self.assertFalse(packs["testco"].sources[0].point_in_time_eligible)
        with self.assertRaises(ValueError):
            CasePack.model_validate(self.pack)
        self.pack["sources"][0]["published_at"] = "2025-07-30"
        with self.assertRaises(ValueError):
            TransferPack.model_validate(self.pack)

    def test_required_code_profile_and_pack_freeze_reject_drift(self):
        for relative in ("creditlens/visual.py", "scripts/evaluate_document_transfer.py", "configs/document-ai.json", "scripts/build_test_reference.py",
                         self.manifest["documents"][0]["pack_path"]):
            path = self.root/relative
            old = path.read_bytes()
            path.write_bytes(old+b"\n")
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                load_manifest(self.root)
            path.write_bytes(old)

    def test_python_line_endings_are_normalized_but_json_is_byte_bound(self):
        path = self.root/"creditlens/visual.py"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        load_manifest(self.root)
        path = self.root/"configs/document-ai.json"
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        with self.assertRaises(ValueError):
            load_manifest(self.root)

    def test_old_issuer_path_escape_and_incomplete_manifest_membership_rejected(self):
        original = deepcopy(self.manifest)
        changes = [lambda m: m["documents"][0].update(id="msft"),
                   lambda m: m["documents"][0].update(pack_path="../private.json"),
                   lambda m: m["documents"][0]["pages"][0].update(fact_ids=[]),
                   lambda m: m["documents"][0]["pages"].append(deepcopy(m["documents"][0]["pages"][0])),
                   lambda m: m["documents"][0]["pages"][0].update(width=999),
                   lambda m: m["files"].pop("creditlens/word_evidence.py"),
                   lambda m: m.update(frozen_at="2099-01-01T00:00:00Z")]
        for index, change in enumerate(changes):
            self.manifest = deepcopy(original)
            change(self.manifest)
            self.save_manifest()
            with self.subTest(index=index), self.assertRaises(ValueError):
                load_manifest(self.root)

    def test_source_disagreement_and_old_company_cannot_be_relabeled(self):
        for mutation in (lambda p: p.update(company="Apple Inc."),
                         lambda p: p["sources"][0].update(url="https://example.org/other.pdf")):
            original = deepcopy(self.pack)
            mutation(self.pack)
            self.save_pack(); self.save_manifest()
            with self.assertRaises(ValueError):
                load_manifest(self.root)
            self.pack = original

    def test_compact_evidence_preserves_mapping_and_drops_unrelated_text(self):
        page, facts = self.page()
        evidence, pairs = compact_page(page, facts, 0.9)
        self.assertEqual(len(evidence), len(page.lines)-1)
        self.assertEqual(evidence[0]["source_line_index"], 1)
        self.assertEqual(pairs[0]["word"]["checks"][0]["parent_lines"][0]["line_index"], 0)
        self.assertEqual(pairs[0]["line"]["status"], "consistent")
        self.assertFalse(pairs[0]["word"]["automatic_approval"])
        validate_transfer_report(self.root, self.report(page))

    def test_full_failure_and_empty_ocr_are_retained_for_both_methods(self):
        page, facts = self.page()
        empty = WordPage(page=page.page, width=page.width, height=page.height, lines=[])
        report = validate_transfer_report(self.root, self.report(empty))
        self.assertEqual(report["summary"]["word"]["insufficient"], 1)
        self.assertEqual(report["summary"]["line"]["insufficient"], 1)
        bad = page.model_dump(mode="json")
        bad["lines"][1]["line"]["text"] = bad["lines"][1]["words"][0]["text"] = "64,551"
        report = validate_transfer_report(self.root, self.report(WordPage.model_validate(bad)))
        self.assertEqual(report["summary"]["word"]["conflict"], 1)
        self.assertEqual(report["summary"]["line"]["conflict"], 1)

    def test_report_recalculation_rejects_omission_changed_claims_and_totals(self):
        original = self.report()
        changes = [lambda r: r["documents"][0].update(facts=[]),
                   lambda r: r["documents"][0]["facts"][0]["word"].update(status="conflict"),
                   lambda r: r["documents"][0]["facts"][0]["line"]["checks"][0].update(expected="123"),
                   lambda r: r["documents"][0]["facts"][0]["word"].update(automatic_approval=True),
                   lambda r: r["summary"]["word"].update(consistent=999),
                   lambda r: r["documents"][0]["pages"][0].update(image_sha256="0"*64),
                   lambda r: r["documents"][0]["pages"][0]["evidence_lines"][0].update(source_line_index=4999),
                   lambda r: r["environment"]["providers"].update(detection=["CUDAExecutionProvider"])]
        for index, change in enumerate(changes):
            report = deepcopy(original); change(report)
            with self.subTest(index=index), self.assertRaises(ValueError):
                validate_transfer_report(self.root, report)

    def test_view_recalculates_without_model_or_raw_pdf(self):
        self.write(self.root/"artifacts/document-transfer-report.json", self.report())
        view = transfer_view(self.root)
        self.assertTrue(view["read_only"])
        self.assertIn("cannot prove completeness", view["validation_scope"])
        self.assertEqual(view["report"]["summary"]["line"]["consistent"], 1)

    def test_missing_pdf_never_downloads_without_explicit_flag_and_bad_bytes_never_overwrite(self):
        registration = PdfRegistration.model_validate(self.manifest["documents"][0]["pdf"])
        path = self.root/".run/document-transfer/raw/testco/testco-transfer.pdf"
        with patch("urllib.request.build_opener", side_effect=AssertionError("network used")):
            with self.assertRaises(FileNotFoundError):
                runner.obtain_pdf(self.root, path, registration)
        path.parent.mkdir(parents=True)
        path.write_bytes(self.raw_pdf)
        self.assertEqual(runner.obtain_pdf(self.root, path, registration), path.resolve())
        path.write_bytes(b"wrong existing document")
        with self.assertRaises(ValueError):
            runner.obtain_pdf(self.root, path, registration, download=True)
        self.assertEqual(path.read_bytes(), b"wrong existing document")

    def test_unregistered_redirects_and_non_https_urls_rejected(self):
        from creditlens.document_transfer import check_download_url
        for url in ("https://other.org/file.pdf", "http://example.org/file.pdf", "file:///etc/passwd",
                    "https://user@example.org/file.pdf", "https://example.org:444/file.pdf"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                check_download_url(url, ["example.org"])
        redirect = runner.RegisteredRedirect(["example.org"])
        with self.assertRaises(ValueError):
            redirect.redirect_request(None, None, 302, "redirect", {}, "https://other.org/file.pdf")

    def test_explicit_download_has_a_bound_and_never_saves_wrong_bytes(self):
        registration = PdfRegistration.model_validate(self.manifest["documents"][0]["pdf"])
        path = self.root/".run/downloaded/test.pdf"
        response = Mock(url=str(registration.url))
        response.read.return_value = self.raw_pdf
        context = Mock(); context.__enter__ = Mock(return_value=response); context.__exit__ = Mock(return_value=False)
        opener = Mock(); opener.open.return_value = context
        with patch("urllib.request.build_opener", return_value=opener):
            runner.obtain_pdf(self.root, path, registration, download=True)
        response.read.assert_called_once_with(60_000_001)
        self.assertEqual(path.read_bytes(), self.raw_pdf)
        other = self.root/".run/downloaded/wrong.pdf"
        response.read.return_value = b"%PDF-unregistered bytes"
        with patch("urllib.request.build_opener", return_value=opener), self.assertRaises(ValueError):
            runner.obtain_pdf(self.root, other, registration, download=True)
        self.assertFalse(other.exists())

    def test_view_uses_one_manifest_snapshot(self):
        self.write(self.root/"artifacts/document-transfer-report.json", self.report())
        original = Path.read_bytes
        seen = []
        def read(path):
            if path == self.manifest_path:
                seen.append(path)
                if len(seen) > 1:
                    raise AssertionError("Manifest was read again during one view")
            return original(path)
        with patch.object(Path, "read_bytes", read):
            self.assertTrue(transfer_view(self.root)["read_only"])
        self.assertEqual(len(seen), 1)

    def test_invalid_paths_or_freeze_fail_before_model_import_download_or_writes(self):
        actual_import = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"pypdfium2", "PIL", "rapidocr"}:
                raise AssertionError("Model/imaging imported before freeze validation")
            return actual_import(name, *args, **kwargs)
        with patch.object(runner, "ROOT", self.root), patch("builtins.__import__", side_effect=guarded), \
             patch.object(runner, "obtain_pdf", side_effect=AssertionError("download/lookup reached")):
            with self.assertRaises(ValueError):
                runner.run(output=self.root/"artifacts/output.json", download=True)
            with self.assertRaises(ValueError):
                runner.run(output=self.root/".run/document-transfer/raw/testco/page-53.png", download=True)
            with self.assertRaises(ValueError):
                runner.run(local_dir=self.root/".run/document-transfer/raw", download=True)
            (self.root/"creditlens/visual.py").write_text("changed", encoding="utf-8")
            with self.assertRaises(ValueError):
                runner.run(download=True)
        self.assertFalse((self.root/".run").exists())

    def test_same_image_only_invocation_supplies_both_methods_and_keeps_all_facts(self):
        registration = self.manifest["documents"][0]
        path = self.root/".run/reproductions/document-transfer/testco"/registration["pdf"]["filename"]
        path.parent.mkdir(parents=True); path.write_bytes(self.raw_pdf)
        width, height = registration["pages"][0]["width"], registration["pages"][0]["height"]
        image = Mock(size=(int(width*2), int(height*2)))
        image.copy.return_value = image
        image.save.side_effect = lambda buffer, **kwargs: buffer.write(self.image)
        bitmap = Mock(); bitmap.to_pil.return_value = image
        pdf_page = Mock(); pdf_page.get_rotation.return_value = 0
        pdf_page.get_width.return_value = width; pdf_page.get_height.return_value = height
        pdf_page.render.return_value = bitmap
        class Pdf:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __getitem__(self, index): return pdf_page
        engine = Mock(return_value=SimpleNamespace(boxes=None))
        engine.text_det.session.session.get_providers.return_value = ["CPUExecutionProvider"]
        engine.text_rec.session.session.get_providers.return_value = ["CPUExecutionProvider"]
        with patch.object(runner, "ROOT", self.root), patch.dict("sys.modules", {"pypdfium2": SimpleNamespace(PdfDocument=Pdf)}), \
             patch.object(runner, "create_engine", return_value=engine), patch.object(runner, "version", return_value="test-fixture"):
            report = runner.run()
        engine.assert_called_once_with(str(path.parent/f"page-{registration['pages'][0]['page']}.png"),
                                       return_word_box=True, return_single_char_box=False)
        self.assertEqual(report["summary"]["line"]["insufficient"], 1)
        self.assertEqual(report["summary"]["word"]["insufficient"], 1)
        self.assertTrue((self.root/".run/reproductions/document-transfer-report.json").is_file())
        self.assertFalse((self.root/"artifacts/document-transfer-report.json").exists())


if __name__ == "__main__":
    unittest.main()
