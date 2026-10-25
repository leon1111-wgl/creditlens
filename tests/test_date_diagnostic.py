"""Synthetic date-diagnostic contracts; no real OCR, images or financial samples."""
import builtins
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from creditlens import date_diagnostic as diagnostic
from creditlens.document_transfer import file_hash
from creditlens.transfer_store import MANIFEST, RELEASE, REPORT
from creditlens.word_evidence import WordPage
from scripts import evaluate_date_diagnostic as runner
from tests import test_document_transfer as fixtures


class DateDiagnosticTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing fixture; do not inherit and count its tests again.
        self.fixture = fixtures.TransferTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        for relative in diagnostic.IMPLEMENTATION_FILES:
            shutil.copyfile(fixtures.ROOT/relative, self.root/relative)
        loc = self.fixture.pack["facts"][0]["pdf_location"]
        loc["period_header"] = "June 30, 2025"
        loc["period_bbox"] = {**loc["year_bbox"], "x0": loc["year_bbox"]["x0"]-48}
        self.fixture.save_pack(); self.fixture.save_manifest()
        self.page = self.date_page()
        self.save_source()
        self.output = self.root/".run/reproductions/date-diagnostic-report.json"

    def date_page(self):
        page = self.fixture.page()[0].model_dump(mode="json")
        year = page["lines"][2]["words"][0]
        box = year["bbox"]
        month = {"text": "June", "confidence": 0.99,
                 "bbox": {**box, "x0": box["x0"]-48, "x1": box["x0"]-20}}
        day = {"text": "30,", "confidence": 0.99,
               "bbox": {**box, "x0": box["x0"]-17, "x1": box["x0"]-3}}
        parent = {"text": "June 30, 2025", "confidence": 0.99,
                  "bbox": {**box, "x0": month["bbox"]["x0"]}}
        page["lines"][2] = {"line": parent, "words": [month, day, year]}
        # Both registered year/period now refer to this one date line.
        page["lines"].pop(3)
        return WordPage.model_validate(page)

    def save_source(self):
        self.source_report = self.fixture.report(self.page)
        self.fixture.write(self.root/REPORT, self.source_report)
        self.fixture.write(self.root/RELEASE, {"schema_version": 1, "files": {
            REPORT: sha256((self.root/REPORT).read_bytes()).hexdigest(),
            MANIFEST: sha256((self.root/MANIFEST).read_bytes()).hexdigest()}})

    def run_diagnostic(self, output=None):
        with patch.object(runner, "ROOT", self.root), redirect_stdout(StringIO()):
            return runner.run(output)

    def add_shared_fact(self, *, separate_region=False, conflicting_date=False):
        added = deepcopy(self.fixture.pack["facts"][0])
        added["id"] = "capex-second"
        if separate_region:
            added["pdf_location"]["year_bbox"]["x0"] -= 100
            added["pdf_location"]["year_bbox"]["x1"] -= 100
        if conflicting_date:
            added["period_end"] = "2025-06-29"
        self.fixture.pack["facts"].append(added)
        self.fixture.manifest["documents"][0]["pages"][0]["fact_ids"].append(added["id"])
        self.fixture.save_pack(); self.fixture.save_manifest(); self.save_source()

    def test_published_parent_evidence_recomputed_and_legacy_results_unchanged(self):
        before = {path: path.read_bytes() for path in (self.root/REPORT, self.root/MANIFEST, self.root/RELEASE)}
        report = self.run_diagnostic()
        self.assertEqual(report["summary"]["facts"], 1)
        self.assertEqual(report["summary"]["distinct_date_groups"], 1)
        self.assertEqual(report["summary"]["date"]["consistent"], 1)
        self.assertEqual(report["summary"]["legacy_word"]["insufficient"], 1)
        self.assertTrue(report["summary"]["non_year_unchanged"])
        fact = report["documents"][0]["facts"][0]
        self.assertEqual(fact["legacy_line"], self.source_report["documents"][0]["facts"][0]["line"])
        self.assertEqual(fact["legacy_word"], self.source_report["documents"][0]["facts"][0]["word"])
        self.assertFalse(report["automatic_approval"])
        self.assertFalse(report["scope"]["full_ocr_verified"])
        self.assertFalse(report["scope"]["model_inference_performed"])
        self.assertFalse(report["scope"]["blind_evaluation"])
        self.assertEqual(report["source"]["report_sha256"], sha256(before[self.root/REPORT]).hexdigest())
        view = diagnostic.date_diagnostic_view(self.root, report)
        self.assertEqual(view["source"]["report"], self.source_report)
        self.assertEqual(view["report"], report)
        self.assertTrue(self.output.is_file())
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_shared_headers_are_counted_once_but_all_facts_remain(self):
        self.add_shared_fact()
        report = diagnostic.build_date_diagnostic(self.root)
        self.assertEqual(report["summary"]["facts"], 2)
        self.assertEqual(report["summary"]["distinct_date_groups"], 1)
        group = report["documents"][0]["date_groups"][0]
        self.assertEqual(group["fact_ids"], ["capex-2025", "capex-second"])
        self.assertEqual(group["status"], "consistent")

    def test_same_date_at_a_different_location_is_a_separate_group(self):
        self.add_shared_fact(separate_region=True)
        report = diagnostic.build_date_diagnostic(self.root)
        self.assertEqual(report["summary"]["distinct_date_groups"], 2)

    def test_preexisting_year_agreement_is_not_counted_as_new_date_association(self):
        raw = self.page.model_dump(mode="json")
        # The date's day is now beyond the old guard radius; year already agrees.
        raw["lines"][2]["words"][1]["bbox"]["x1"] -= 7
        self.page = WordPage.model_validate(raw)
        self.save_source()
        report = diagnostic.build_date_diagnostic(self.root)
        self.assertEqual(report["summary"]["legacy_word"]["consistent"], 1)
        self.assertEqual(report["summary"]["date_association_applied"], 0)
        group = report["documents"][0]["date_groups"][0]
        self.assertEqual(group["status"], "consistent")
        self.assertEqual(group["date_association_applied_facts"], 0)
        self.assertEqual(report["scope"]["date_group_status_basis"],
                         "year_check_after_optional_complete_date_association")

    def test_conflicting_expected_dates_under_one_spatial_header_rejected(self):
        self.add_shared_fact(conflicting_date=True)
        with self.assertRaisesRegex(ValueError, "Shared date header"):
            self.run_diagnostic()
        self.assertFalse(self.output.exists())

    def test_all_results_evidence_mapping_hashes_scope_and_summaries_are_recomputed(self):
        good = diagnostic.build_date_diagnostic(self.root)
        changes = (
            lambda r: r["summary"].update(facts=2),
            lambda r: r["documents"][0]["facts"].append(deepcopy(r["documents"][0]["facts"][0])),
            lambda r: r["documents"][0]["facts"][0].update(non_year_unchanged=False),
            lambda r: r["documents"][0]["facts"][0]["date"].update(status="conflict"),
            lambda r: r["documents"][0]["facts"][0]["date"]["checks"][0].update(reason="forged"),
            lambda r: r["documents"][0]["date_groups"][0].update(fact_ids=[]),
            lambda r: r["documents"][0]["pages"][0].update(evidence_sha256="0"*64),
            lambda r: r["documents"][0]["pages"][0].update(full_word_ocr_sha256="0"*64),
            lambda r: r["source"].update(report_sha256="0"*64),
            lambda r: r["implementation"]["files"].update({"creditlens/date_evidence.py": "0"*64}),
            lambda r: r["rule_versions"].update(date="other"),
            lambda r: r["scope"].update(full_ocr_verified=True),
            lambda r: r.update(automatic_approval=0),
            lambda r: r.update(schema_version=True),
            lambda r: r.update(unregistered_field=True),
        )
        for change in changes:
            bad = deepcopy(good); change(bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                diagnostic.validate_date_diagnostic(self.root, bad)

    def test_modified_source_release_pack_and_implementation_are_rejected(self):
        report = diagnostic.build_date_diagnostic(self.root)
        paths = (self.root/REPORT, self.root/MANIFEST, self.root/RELEASE,
                 self.root/self.fixture.manifest["documents"][0]["pack_path"],
                 self.root/"creditlens/date_evidence.py", self.root/"creditlens/date_diagnostic.py")
        for path in paths:
            raw = path.read_bytes(); path.write_bytes(raw+b"\n")
            with self.subTest(path=path), self.assertRaises(ValueError):
                diagnostic.validate_date_diagnostic(self.root, report)
            path.write_bytes(raw)

    def test_source_changes_after_snapshot_are_rejected_before_writes(self):
        actual = diagnostic.transfer_snapshot
        def raced(root):
            snapshot = actual(root)
            self.source_report["documents"][0]["facts"][0]["word"]["status"] = "consistent"
            self.fixture.write(self.root/REPORT, self.source_report)
            return snapshot
        with patch.object(diagnostic, "transfer_snapshot", side_effect=raced), self.assertRaises(ValueError):
            self.run_diagnostic()
        self.assertFalse(self.output.exists())

    def test_non_year_changes_and_lost_legacy_year_are_rejected(self):
        actual = diagnostic.crosscheck_date_fact
        for lose_year in (False, True):
            def changed(*args, **kwargs):
                result = actual(*args, **kwargs)
                if lose_year:
                    result["date_association"]["legacy_year"] = {}
                else:
                    result["checks"][0]["expected"] = "rewritten amount"
                return result
            with patch.object(diagnostic, "crosscheck_date_fact", side_effect=changed), self.assertRaises(ValueError):
                self.run_diagnostic()
        self.assertFalse(self.output.exists())

    def test_no_ocr_rendering_or_network_calls(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {"rapidocr", "onnxruntime", "PIL", "pypdfium2"}:
                raise AssertionError("Diagnostic imported model or rendering")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), \
             patch("urllib.request.urlopen", side_effect=AssertionError("Network used")), \
             patch("urllib.request.build_opener", side_effect=AssertionError("Network used")):
            self.run_diagnostic()

    def test_invalid_or_existing_output_fails_before_source_reads_and_writes(self):
        existing = self.root/".run/existing.json"
        existing.parent.mkdir(); existing.write_text("preserved", encoding="utf-8")
        outputs = (self.root/REPORT, self.root/"configs/new.json", self.root/".run/document-ai/new.json",
                   self.root/".run/document-transfer/new.json", self.root/".run/transfer-attempts/new.json",
                   self.root/".run/reproductions/document-transfer/new.json", self.root/".run/new.txt", existing)
        with patch.object(runner, "build_date_diagnostic", side_effect=AssertionError("Source read before path checks")):
            for output in outputs:
                with self.subTest(output=output), self.assertRaises(ValueError):
                    self.run_diagnostic(output)
        self.assertEqual(existing.read_text(encoding="utf-8"), "preserved")
        self.assertFalse(self.output.exists())

    def test_times_must_be_aware_after_source_and_not_future(self):
        for value in ("2020-01-01T00:00:00Z", "2026-01-01T00:00:00", "not-a-date",
                      datetime.now(timezone.utc)+timedelta(days=1)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                diagnostic.build_date_diagnostic(self.root, generated_at=value)

    def test_output_directory_alias_cannot_redirect_into_raw_evidence(self):
        raw_dir = self.root/".run/document-transfer"
        raw_dir.mkdir(parents=True)
        alias = self.root/".run/reproductions"
        try:
            alias.symlink_to(raw_dir, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Directory aliases unavailable")
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(raw_dir)],
                                    capture_output=True, text=True)
            if result.returncode:
                self.skipTest("Directory junction unavailable")
        with self.assertRaises(ValueError):
            self.run_diagnostic()
        self.assertFalse((raw_dir/"date-diagnostic-report.json").exists())

    def test_strict_input_json_and_size_bounds(self):
        path = self.root/"bad.json"
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'[]'):
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                diagnostic.read_json(path)
        path.write_bytes(b'{"valid":true}')
        with self.assertRaises(ValueError):
            diagnostic.read_json(path, limit=2)

    def test_cli_default_is_new_local_json(self):
        with patch.object(runner, "ROOT", self.root), redirect_stdout(StringIO()):
            runner.main([])
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(diagnostic.validate_date_diagnostic(self.root, report), report)
        self.assertEqual(report["implementation"]["files"]["scripts/evaluate_date_diagnostic.py"],
                         file_hash(self.root/"scripts/evaluate_date_diagnostic.py"))

    def test_cli_accepts_a_new_filename_but_no_original_directory(self):
        with patch.object(runner, "ROOT", self.root), redirect_stdout(StringIO()):
            runner.main(["--output", "another-diagnostic.json"])
        self.assertTrue((self.root/".run/reproductions/another-diagnostic.json").is_file())


if __name__ == "__main__":
    unittest.main()
