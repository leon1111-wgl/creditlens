"""Local reproduction destinations must not mutate frozen/public evidence."""
import builtins
from contextlib import chdir
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from creditlens.experiment_paths import (EXPERIMENTS, default_local_dir, default_report_path,
                                        validate_experiment_paths, validate_write_path)
from scripts import evaluate_document_ai as baseline
from scripts import evaluate_word_evidence as words
from scripts import evaluate_visual_robustness as stress


class ExperimentPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.container = Path(self.temp.name)
        self.root = self.container/"repo"
        for name in ("artifacts", "configs", "data", ".run"):
            (self.root/name).mkdir(parents=True)
        self.frozen = self.root/"artifacts/frozen.json"
        self.frozen.write_bytes(b'{"frozen":true}\n')
        self.local = self.root/".run/reproduction"
        self.output = self.root/".run/report.json"

    def tearDown(self):
        self.temp.cleanup()

    def symlink(self, target, alias, *, directory=False):
        try:
            alias.symlink_to(target, target_is_directory=directory)
        except OSError as exc:
            if os.name == "nt" and directory:
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                                        capture_output=True, text=True)
                if result.returncode == 0:
                    return
            self.skipTest("Symlink creation unavailable (filesystem error "+str(getattr(exc, "winerror", exc.errno))+")")

    def test_defaults_are_distinct_private_outputs(self):
        reports, directories = set(), set()
        for experiment in EXPERIMENTS:
            report = default_report_path(self.root, experiment)
            local = default_local_dir(self.root, experiment)
            self.assertEqual(report.parent, self.root/".run/reproductions")
            self.assertEqual(local.parent, self.root/".run/reproductions")
            validated = validate_experiment_paths(self.root, report, local)
            self.assertEqual(validated.output, report.resolve())
            self.assertEqual(validated.local_dir, local.resolve())
            reports.add(report)
            directories.add(local)
        self.assertEqual(len(reports), 3)
        self.assertEqual(len(directories), 3)
        for function in (default_report_path, default_local_dir):
            with self.assertRaises(ValueError):
                function(self.root, "../artifacts")
        self.assertFalse((self.root/".run/reproductions").exists())

    def test_direct_relative_and_normalized_public_paths_are_rejected(self):
        for directory in ("artifacts", "configs", "data"):
            targets = [self.root/directory/"report.json", self.root/".run/.."/directory/"report.json"]
            for target in targets:
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, "frozen/public"):
                    validate_experiment_paths(self.root, target, self.local)
            with chdir(self.root), self.assertRaisesRegex(ValueError, "frozen/public"):
                validate_experiment_paths(self.root, Path(directory)/"report.json", self.local)
            with self.assertRaisesRegex(ValueError, "frozen/public"):
                validate_experiment_paths(self.root, self.output, self.root/directory/"raw-ocr")
        self.assertEqual(self.frozen.read_bytes(), b'{"frozen":true}\n')

    def test_other_repository_files_and_ancestor_directories_are_not_output_roots(self):
        for target in (self.root/"README.md", self.root/"docs/report.json", self.root):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_write_path(self.root, target)
        for target in (self.root, self.container):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_experiment_paths(self.root, self.output, target)

    def test_explicit_external_output_and_existing_regular_private_output_are_supported(self):
        external = self.container/"private-run"
        result = validate_experiment_paths(self.root, external/"report.json", external/"raw")
        self.assertEqual(result.output, (external/"report.json").resolve())
        self.output.write_text("old reproduction", encoding="utf-8")
        self.assertEqual(validate_write_path(self.root, self.output), self.output.resolve())
        self.assertEqual(self.output.read_text(encoding="utf-8"), "old reproduction")

    def test_files_and_directories_cannot_change_roles(self):
        self.local.mkdir()
        self.output.write_text("file", encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_experiment_paths(self.root, self.local, self.root/".run/other")
        with self.assertRaises(ValueError):
            validate_experiment_paths(self.root, self.root/".run/other.json", self.output)
        with self.assertRaises(ValueError):
            validate_write_path(self.root, self.output/"child.json")
        with self.assertRaises(ValueError):
            validate_experiment_paths(self.root, self.root/".run/new", self.root/".run/new/child")
        with self.assertRaises(ValueError):
            validate_experiment_paths(self.root, self.local/"msft/ocr-53.json", self.local)

    def test_reports_and_nonbaseline_raw_outputs_cannot_overwrite_registered_previews(self):
        preview = self.root/".run/document-ai"
        with self.assertRaisesRegex(ValueError, "registered document previews"):
            validate_experiment_paths(self.root, preview/"msft/page-50.png", self.local)
        with self.assertRaisesRegex(ValueError, "registered document previews"):
            validate_experiment_paths(self.root, self.output, preview)
        paths = validate_experiment_paths(self.root, self.output, preview, allow_registered_preview=True)
        self.assertEqual(paths.local_dir, preview.resolve())

    def test_input_directories_and_outputs_cannot_overlap_or_alias(self):
        source = self.root/".run/inputs"
        source.mkdir()
        with self.assertRaisesRegex(ValueError, "read-only input"):
            validate_experiment_paths(self.root, source/"msft/page-50.png", self.local, read_only_dirs=(source,))
        with self.assertRaisesRegex(ValueError, "read-only input"):
            validate_experiment_paths(self.root, self.output, source, read_only_dirs=(source,))
        alias = self.root/".run/source-alias"
        self.symlink(source, alias, directory=True)
        with self.assertRaisesRegex(ValueError, "read-only input"):
            validate_experiment_paths(self.root, alias/"ocr.json", self.local, read_only_dirs=(source,))

    def test_output_file_symlink_into_frozen_report_is_rejected(self):
        self.symlink(self.frozen, self.output)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_experiment_paths(self.root, self.output, self.local)

    def test_directory_symlink_to_public_area_and_alias_repository_are_rejected(self):
        alias = self.root/".run/alias"
        self.symlink(self.root/"artifacts", alias, directory=True)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_experiment_paths(self.root, alias/"new.json", self.local)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_experiment_paths(self.root, self.output, alias)
        repo_alias = self.container/"alias-repo"
        self.symlink(self.root, repo_alias, directory=True)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_write_path(self.root, repo_alias/"configs/report.json")

    def test_lexically_public_path_is_rejected_even_when_its_alias_points_outside(self):
        external = self.container/"outside"
        external.mkdir()
        alias = self.root/"configs/private-link"
        self.symlink(external, alias, directory=True)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_write_path(self.root, alias/"report.json")

    def test_nested_local_alias_is_detected_before_any_writes(self):
        self.local.mkdir()
        alias = self.local/"msft"
        self.symlink(self.root/"artifacts", alias, directory=True)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_experiment_paths(self.root, self.output, self.local)
        self.assertFalse(self.output.exists())

    def test_hard_linked_report_or_raw_output_is_rejected(self):
        try:
            os.link(self.frozen, self.output)
        except OSError as exc:
            self.skipTest("Hard links unavailable: "+str(exc))
        with self.assertRaisesRegex(ValueError, "Multiply-linked"):
            validate_experiment_paths(self.root, self.output, self.local)
        self.output.unlink()
        self.local.mkdir()
        os.link(self.frozen, self.local/"ocr.json")
        with self.assertRaisesRegex(ValueError, "Multiply-linked"):
            validate_experiment_paths(self.root, self.output, self.local)
        self.assertEqual(self.frozen.read_bytes(), b'{"frozen":true}\n')

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_windows_junction_to_public_area_is_rejected(self):
        alias = self.root/".run/junction"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.root/"artifacts")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_experiment_paths(self.root, alias/"new.json", self.local)
        with self.assertRaisesRegex(ValueError, "frozen/public"):
            validate_experiment_paths(self.root, self.output, alias)

    def test_all_runners_fail_before_ocr_imaging_or_download_on_invalid_destinations(self):
        actual_import = builtins.__import__
        def no_ocr(name, *args, **kwargs):
            if name.split(".")[0] in {"rapidocr", "pypdfium2", "PIL"}:
                raise AssertionError("Imaging/OCR import happened before path validation")
            return actual_import(name, *args, **kwargs)
        for runner in (baseline, words, stress):
            for output, local in [(self.frozen, self.local), (self.output, self.root/"data/raw-ocr")]:
                with self.subTest(runner=runner.__name__, output=output), \
                     patch.object(runner, "ROOT", self.root), \
                     patch("builtins.__import__", side_effect=no_ocr), \
                     patch.object(baseline, "verified_pdf", side_effect=AssertionError("download called")), \
                     patch.object(stress, "create_engine", side_effect=AssertionError("engine called")):
                    with self.assertRaises(ValueError):
                        runner.run(output=output, local_dir=local)
        self.assertFalse(self.local.exists())
        self.assertFalse(self.output.exists())

    def test_runners_use_private_default_helpers_before_work(self):
        class StopPreflight(Exception):
            pass
        for runner, experiment in [(baseline, "document-ai"), (words, "document-ai-words"),
                                   (stress, "visual-robustness")]:
            with self.subTest(runner=runner.__name__), patch.object(runner, "ROOT", self.root), \
                 patch.object(runner, "validate_experiment_paths", side_effect=StopPreflight) as preflight:
                with self.assertRaises(StopPreflight):
                    runner.run()
                extra = ({"allow_registered_preview": True} if runner is baseline else
                         {"read_only_dirs": (default_local_dir(self.root, "document-ai"),)})
                preflight.assert_called_once_with(self.root, default_report_path(self.root, experiment),
                                                  default_local_dir(self.root, experiment), **extra)


class RegisteredPreviewTests(unittest.TestCase):
    class Image:
        def __init__(self, raw):
            self.raw = raw
        def save(self, buffer, **kwargs):
            buffer.write(self.raw)

    def test_registered_preview_requires_frozen_bytes_and_never_replaces_different_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/".run/document-ai/msft/page-50.png"
            target.parent.mkdir(parents=True)
            raw = b"synthetic PNG bytes for path/write tests only"
            expected = hashlib.sha256(raw).hexdigest()
            with patch.object(baseline, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "frozen image hash"):
                    baseline.save_page_image(target, self.Image(raw))
                with self.assertRaisesRegex(ValueError, "frozen registration"):
                    baseline.save_page_image(target, self.Image(b"different"), expected_sha256=expected)
                self.assertFalse(target.exists())
                baseline.save_page_image(target, self.Image(raw), expected_sha256=expected)
                before = target.stat().st_mtime_ns
                baseline.save_page_image(target, self.Image(raw), expected_sha256=expected)
                self.assertEqual(target.stat().st_mtime_ns, before)
                target.write_bytes(b"previous file must survive")
                with self.assertRaisesRegex(ValueError, "Existing document preview differs"):
                    baseline.save_page_image(target, self.Image(raw), expected_sha256=expected)
                self.assertEqual(target.read_bytes(), b"previous file must survive")


if __name__ == "__main__":
    unittest.main()
