from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens.experiments import FILES
from creditlens.reproduction import _differences, default_candidates, exit_code, review_reproduction
from scripts.check_document_reproduction import export_summary, main

ROOT = Path(__file__).resolve().parents[1]


class ReproductionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release_files = (*FILES, "configs/experiment-artifacts.json", "data/examples/msft-fy2025-pdf.json",
                              "data/examples/aapl-fy2025-pdf.json")
        for name in self.release_files:
            target = self.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        self.paths = default_candidates(self.root)
        self.records = {}
        for (kind, path), name in zip(self.paths.items(), FILES[:3]):
            self.records[kind] = json.loads((self.root/name).read_text(encoding="utf-8"))
            self.write(kind, self.records[kind])

    def write(self, kind, record):
        self.paths[kind].parent.mkdir(parents=True, exist_ok=True)
        self.paths[kind].write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def review(self):
        return review_reproduction(self.root, self.paths)

    def test_all_report_evidence_equal_without_requiring_file_hash_equal(self):
        result = self.review()
        self.assertEqual(result["status"], "matched", result)
        self.assertEqual(exit_code(result), 0)
        for kind, item in result["reports"].items():
            self.assertEqual(item["semantic_changes"], [])
            self.assertEqual(item["evidence_changes"], [])
            self.assertNotEqual(item["candidate_file_sha256"], sha256((self.root/FILES[list(self.paths).index(kind)]).read_bytes()).hexdigest())
        self.assertIn("complete original OCR/WordPage bytes and their declared digests", result["validation_scope"]["not_checked"])

    def test_runtime_metadata_and_script_changes_are_explicit_but_not_semantic(self):
        for kind, original in self.records.items():
            record = deepcopy(original)
            record["generated_at"] = "2026-10-26T00:00:00+00:00"
            record["environment"]["packages"]["numpy"] = "99.0-test"
            if kind == "stress":
                record["documents"][0]["conditions"][0]["elapsed_seconds"] += 1
            else:
                record["documents"][0]["pages"][0]["elapsed_seconds"] += 1
            if kind == "word":
                record["implementation_sha256"]["scripts/evaluate_word_evidence.py"] = "1"*64
            self.write(kind, record)
        result = self.review()
        self.assertEqual(result["status"], "matched", result)
        for item in result["reports"].values():
            self.assertTrue(item["run_metadata_changes"])
            self.assertFalse(item["semantic_changes"])
        changes = result["reports"]["word"]["run_metadata_changes"]
        self.assertTrue(any(c["path"] == "implementation_sha256.scripts/evaluate_word_evidence.py" and c["after"] == "1"*64 for c in changes))
        self.assertTrue(any(c["path"] == "environment.packages.numpy" and c["after"] == "99.0-test" for c in changes))

    def test_changed_ocr_hash_is_evidence_change_even_when_counts_match(self):
        for kind, original in self.records.items():
            record = deepcopy(original)
            page = record["documents"][0]["conditions" if kind == "stress" else "pages"][0]
            page["word_ocr_sha256" if kind == "word" else "ocr_sha256"] = "1"*64
            self.write(kind, record)
        result = self.review()
        self.assertEqual(result["status"], "changed", result)
        self.assertEqual(exit_code(result), 2)
        for item in result["reports"].values():
            self.assertEqual(item["status"], "changed")
            self.assertTrue(item["evidence_changes"])
            self.assertFalse(item["semantic_changes"])

    def test_same_counts_do_not_hide_changed_word_observation_or_parent_line(self):
        record = deepcopy(self.records["word"])
        check = record["documents"][0]["facts"][0]["checks"][0]
        check["observations"][0]["bbox"]["x0"] += 0.1
        parent_id = check["parent_lines"][0]["line_index"]
        page = record["documents"][0]["facts"][0]["page"]
        for fact in record["documents"][0]["facts"]:
            if fact["page"] == page:
                for field in fact["checks"]:
                    for parent in field["parent_lines"]:
                        if parent["line_index"] == parent_id:
                            parent["line"]["text"] += " "
        self.write("word", record)
        result = self.review()
        self.assertEqual(result["status"], "changed", result)
        word = result["reports"]["word"]
        self.assertEqual(word["status_changes"], [])
        self.assertTrue(any(c["path"].endswith("observations") for c in word["semantic_changes"]))
        self.assertTrue(any(c["path"].endswith("parent_lines") for c in word["semantic_changes"]))

    def test_changed_line_decision_does_not_rebind_word_or_stress(self):
        record = deepcopy(self.records["line"])
        fact = record["documents"][0]["facts"][0]
        check = fact["checks"][0]
        check["observations"][0]["text"] = "281,725"
        check.update(status="conflict", reason="different_displayed_content")
        fact["status"] = "conflict"
        for summary in (record["summary"], record["documents"][0]["summary"]):
            summary["consistent"] -= 1
            summary["conflict"] += 1
        self.write("line", record)
        result = self.review()
        self.assertEqual(result["status"], "changed", result)
        self.assertTrue(result["reports"]["line"]["status_changes"])
        self.assertEqual(result["reports"]["word"]["status"], "matched")
        self.assertEqual(result["reports"]["stress"]["status"], "matched")

    def test_changed_stress_failure_observation_is_compared_with_same_counts(self):
        record = deepcopy(self.records["stress"])
        check = next(c for f in record["documents"][0]["conditions"][0]["facts"] for c in f["checks"] if c["status"] != "consistent")
        check["observations"][0]["confidence"] = 0.95
        self.write("stress", record)
        result = self.review()
        self.assertEqual(result["status"], "changed", result)
        self.assertTrue(result["reports"]["stress"]["semantic_changes"])
        self.assertFalse(result["reports"]["stress"]["status_changes"])

    def test_missing_duplicate_facts_pages_conditions_and_fields_are_invalid(self):
        mutations = [
            ("line", lambda r: r["documents"][0]["facts"].pop()),
            ("line", lambda r: r["documents"][0]["pages"].append(deepcopy(r["documents"][0]["pages"][0]))),
            ("word", lambda r: r["documents"][0]["facts"].append(deepcopy(r["documents"][0]["facts"][0]))),
            ("word", lambda r: r["documents"][0]["facts"][0]["checks"][0].pop("parent_lines")),
            ("word", lambda r: r["documents"][0]["facts"][0]["checks"][0].pop("word_guards")),
            ("word", lambda r: r["documents"][0]["facts"][0]["checks"][0].pop("guard_observations")),
            ("stress", lambda r: r["documents"][0]["conditions"].pop()),
            ("stress", lambda r: r["documents"][0]["conditions"].append(deepcopy(r["documents"][0]["conditions"][0]))),
            ("stress", lambda r: r["documents"][0]["conditions"][0]["facts"][0]["checks"].pop()),
        ]
        for kind, mutate in mutations:
            record = deepcopy(self.records[kind]); mutate(record); self.write(kind, record)
            result = self.review()
            with self.subTest(kind=kind, mutate=mutate):
                self.assertEqual(result["reports"][kind]["status"], "invalid", result)
                self.assertEqual(exit_code(result), 1)
            self.write(kind, self.records[kind])

    def test_wrong_sources_profile_hashes_reference_positions_and_summaries_are_invalid(self):
        mutations = [
            ("line", lambda r: r["documents"][0]["source"].update(url="https://example.invalid")),
            ("line", lambda r: r["documents"][0]["pages"][0].update(image_sha256="0"*64)),
            ("line", lambda r: r["documents"][0]["pages"][0].update(ocr_sha256="bad")),
            ("line", lambda r: r["summary"].update(consistent=42)),
            ("word", lambda r: r.update(baseline_report_sha256="0"*64)),
            ("word", lambda r: r.update(profile_sha256="0"*64)),
            ("word", lambda r: r["implementation_sha256"].update({"scripts/evaluate_word_evidence.py": "bad"})),
            ("word", lambda r: r["documents"][0]["facts"][0]["checks"][0]["region"].update(x0=1)),
            ("word", lambda r: r["documents"][0]["facts"][0]["checks"][0].update(expected="wrong")),
            ("stress", lambda r: r.update(config_sha256="0"*64)),
            ("stress", lambda r: r["documents"][0]["conditions"][1].update(image_sha256="0"*64)),
            ("stress", lambda r: r["summary_by_variant"][0]["summary"].update(consistent=20)),
        ]
        for kind, mutate in mutations:
            record = deepcopy(self.records[kind]); mutate(record); self.write(kind, record)
            with self.subTest(kind=kind, mutate=mutate):
                self.assertEqual(self.review()["reports"][kind]["status"], "invalid")
            self.write(kind, self.records[kind])

    def test_false_field_decisions_and_missing_parent_evidence_are_invalid(self):
        original = self.records["word"]
        for mutate in (lambda c: c["observations"][0].update(text="wrong"),
                       lambda c: c.update(parent_lines=[]),
                       lambda c: c.update(reason="invented_reason"),
                       lambda c: c.update(word_guards=["token_text_not_conserved"])):
            record = deepcopy(original)
            mutate(record["documents"][0]["facts"][0]["checks"][0])
            self.write("word", record)
            self.assertEqual(self.review()["reports"]["word"]["status"], "invalid")

    def test_json_duplicates_nan_missing_files_and_release_drift_fail_closed(self):
        for raw in ('{"schema_version":1,"schema_version":1}', '{"value":NaN}', '{"value":1e999}', '[]'):
            self.paths["line"].write_text(raw, encoding="utf-8")
            self.assertEqual(self.review()["reports"]["line"]["status"], "invalid")
        self.paths["line"].unlink()
        self.assertEqual(self.review()["reports"]["line"]["status"], "invalid")
        self.write("line", self.records["line"])
        (self.root/FILES[0]).write_bytes((self.root/FILES[0]).read_bytes()+b" ")
        result = self.review()
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reports"], {})

    def test_cli_summary_only_creates_new_local_json_and_preserves_release(self):
        original = {name: (self.root/name).read_bytes() for name in self.release_files}
        output = self.root/".run/reproduction-review.json"
        with patch("scripts.check_document_reproduction.ROOT", self.root), redirect_stdout(StringIO()) as stream:
            self.assertEqual(main(["--output", str(output)]), 0)
        printed = json.loads(stream.getvalue())
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), printed)
        for name, raw in original.items():
            self.assertEqual((self.root/name).read_bytes(), raw)
        for rejected in (self.root/"artifacts/new.json", self.root/".run/../artifacts/new.json", self.paths["line"], output,
                         self.root/".run/report.txt", self.root/".run/document-ai/review.json"):
            with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                export_summary(self.root, rejected, printed, self.paths)

    def test_summary_rejects_run_alias_to_public_files_and_alias_to_previews(self):
        from tests.test_experiment_paths import ExperimentPathTests
        alias_root = self.root/"alias-check"
        public = alias_root/"artifacts"
        public.mkdir(parents=True)
        ExperimentPathTests.symlink(self, public, alias_root/".run", directory=True)
        with self.assertRaises(ValueError):
            export_summary(alias_root, alias_root/".run/new.json", {}, self.paths)
        self.assertFalse((public/"new.json").exists())
        preview = self.root/".run/document-ai"
        preview.mkdir()
        alias = self.root/".run/preview-alias"
        ExperimentPathTests.symlink(self, preview, alias, directory=True)
        with self.assertRaises(ValueError):
            export_summary(self.root, alias/"new.json", {}, self.paths)
        self.assertFalse((preview/"new.json").exists())

    def test_null_implementation_hashes_invalid_and_presence_diff_is_not_ignored(self):
        record = deepcopy(self.records["line"])
        record["implementation_sha256"] = None
        self.write("line", record)
        result = self.review()
        self.assertEqual(result["status"], "invalid", result)
        changes = _differences({}, {"new_metadata": None})
        self.assertEqual(len(changes), 1)
        self.assertFalse(changes[0]["baseline_present"])
        self.assertTrue(changes[0]["candidate_present"])


if __name__ == "__main__":
    unittest.main()
