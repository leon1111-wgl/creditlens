"""Fixed development controls and report replay, not inference accuracy tests."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from creditlens import currency_diagnostic as baseline
from creditlens import evidence_guard_diagnostic as diagnostic
from creditlens.diligence import digest
from scripts import evaluate_evidence_guard as runner
from tests import test_currency_diagnostic as currency_fixtures

ROOT = Path(__file__).resolve().parents[1]


class EvidenceGuardDiagnosticTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids discovering/recounting the inherited fixture tests.
        self.fixture = currency_fixtures.CurrencyDiagnosticTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        for relative in (*diagnostic.IMPLEMENTATION_FILES, diagnostic.CATALOG):
            target = self.root/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, target)
        self.currency_report = baseline.build_currency_diagnostic(self.root)
        self.write(self.root/diagnostic.CURRENCY_REPORT, self.currency_report)
        self.write(self.root/diagnostic.CURRENCY_RELEASE, {"schema_version": 1, "files": {
            diagnostic.CURRENCY_REPORT: sha256((self.root/diagnostic.CURRENCY_REPORT).read_bytes()).hexdigest()}})

    @staticmethod
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8", newline="\n")

    def test_full_public_inventory_and_separate_fixed_controls(self):
        report = diagnostic.build_evidence_guard_diagnostic(self.root)
        view = diagnostic.evidence_guard_diagnostic_view(self.root, report)
        self.assertEqual(report, view["report"])
        self.assertEqual(view["source"]["report"], self.fixture.source_report)
        self.assertTrue(view["read_only"])
        self.assertEqual(report["summary"]["facts"], 1)
        self.assertEqual(report["controls"]["summary"]["cases"], 38)
        self.assertEqual(report["controls"]["summary"]["passed"], 38)
        self.assertEqual(report["controls"]["summary"]["failed"], 0)
        self.assertEqual(report["controls"]["summary"]["positive_cases"], 8)
        self.assertEqual(report["controls"]["summary"]["negative_cases"], 30)
        self.assertEqual(report["scope"]["independent_human_gold_count"], 0)
        self.assertFalse(report["scope"]["full_ocr_verified"])
        self.assertFalse(report["automatic_approval"])

    def test_previous_false_agreements_are_visible_and_fixed(self):
        report = diagnostic.build_evidence_guard_diagnostic(self.root)
        cases = {c["id"]: c for c in report["controls"]["cases"]}
        for key in ("parent_shift_x", "wrong_yen", "nearby_empty_minus"):
            self.assertEqual(cases[key]["currency"]["status"], "consistent")
            self.assertNotEqual(cases[key]["guarded"]["status"], "consistent")
            self.assertTrue(cases[key]["passed"])
        self.assertGreaterEqual(report["controls"]["summary"]["old_false_agreements"], 3)

    def test_expected_signs_and_positive_controls_are_not_candidate_generated(self):
        raw, catalog = diagnostic.control_catalog(self.root)
        before = deepcopy(catalog)
        report = diagnostic.build_evidence_guard_diagnostic(self.root)
        self.assertEqual(diagnostic.control_catalog(self.root), (raw, before))
        cases = {c["id"]: c for c in report["controls"]["cases"]}
        for key in ("negative_parentheses", "negative_minus"):
            self.assertEqual(cases[key]["input"]["fact"]["value"], "-1234")
            self.assertEqual(cases[key]["expected"]["overall_status"], "consistent")
            self.assertEqual(cases[key]["guarded"]["status"], "consistent")
        for case in cases.values():
            self.assertEqual(case["input_sha256"], digest(case["input"]))
            self.assertEqual(case["human_confirmation"], "pending")
            self.assertEqual(case["guarded"]["legacy_currency_result"], case["currency"])
            self.assertTrue(case["non_amount_unchanged"])

    def test_unit_or_date_failure_is_never_upgraded(self):
        report = diagnostic.build_evidence_guard_diagnostic(self.root)
        cases = {c["id"]: c for c in report["controls"]["cases"]}
        for key in ("wrong_year", "wrong_unit", "missing_amount"):
            self.assertNotEqual(cases[key]["currency"]["status"], "consistent")
            self.assertNotEqual(cases[key]["guarded"]["status"], "consistent")
            self.assertTrue(cases[key]["non_amount_unchanged"])

    def test_failed_control_stays_in_denominator(self):
        from creditlens.evidence_guard import EVIDENCE_GUARD_RULE_VERSION, crosscheck_guarded_fact
        _, catalog = diagnostic.control_catalog(self.root)
        def faulty(fact, page, **kwargs):
            value = crosscheck_guarded_fact(fact, page, **kwargs)
            value["status"] = "consistent"
            return value
        report = diagnostic._controls(catalog, .9, faulty, EVIDENCE_GUARD_RULE_VERSION)
        self.assertEqual(report["summary"]["cases"], 38)
        self.assertEqual(report["summary"]["failed"], 30)
        self.assertEqual(report["cases"][6]["expected"]["overall_status"], "not_consistent")

    def test_entire_semantics_not_just_counts_are_checked(self):
        original = diagnostic.build_evidence_guard_diagnostic(self.root)
        changes = [
            lambda r: r["documents"][0]["facts"][0]["guarded"]["checks"][0].update(reason="forged"),
            lambda r: r["documents"][0]["facts"].clear(),
            lambda r: r["summary"].update(old_false_agreements=99),
            lambda r: r["controls"]["cases"][0]["input"]["page"]["lines"][6]["line"].update(text="999"),
            lambda r: r["controls"]["cases"][0].update(human_confirmation="confirmed"),
            lambda r: r["controls"]["cases"].pop(),
            lambda r: r["scope"].update(full_ocr_verified=True),
            lambda r: r["source"].update(currency_report_sha256="f"*64),
        ]
        for change in changes:
            value = deepcopy(original); change(value)
            with self.assertRaises(ValueError):
                diagnostic.validate_evidence_guard_diagnostic(self.root, value)

    def test_rule_implementation_and_catalog_are_bound(self):
        original = diagnostic.build_evidence_guard_diagnostic(self.root)
        path = self.root/"creditlens/evidence_guard.py"
        path.write_text(path.read_text(encoding="utf-8")+"\n# changed version\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            diagnostic.validate_evidence_guard_diagnostic(self.root, original)
        shutil.copyfile(ROOT/"creditlens/evidence_guard.py", path)
        _, catalog = diagnostic.control_catalog(self.root)
        catalog["cases"][0]["modification"] += " changed"
        self.write(self.root/diagnostic.CATALOG, catalog)
        with self.assertRaises(ValueError):
            diagnostic.validate_evidence_guard_diagnostic(self.root, original)

    def test_catalog_rejects_bad_shape_duplicate_and_nonfinite(self):
        _, catalog = diagnostic.control_catalog(self.root)
        changes = [lambda c:c.update(schema_version=True), lambda c:c.pop("id"),
                   lambda c:c["cases"].append(deepcopy(c["cases"][0])),
                   lambda c:c["cases"][0]["expected"].update(overall_status=[]),
                   lambda c:c.update(independent_human_gold_count=True)]
        for change in changes:
            value = deepcopy(catalog); change(value)
            self.write(self.root/diagnostic.CATALOG, value)
            with self.assertRaises(ValueError):
                diagnostic.control_catalog(self.root)
        (self.root/diagnostic.CATALOG).write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            diagnostic.control_catalog(self.root)
        (self.root/diagnostic.CATALOG).write_text('{"value":NaN}', encoding="utf-8")
        with self.assertRaises(ValueError):
            diagnostic.control_catalog(self.root)

    def test_source_release_drift_is_rejected(self):
        original = diagnostic.build_evidence_guard_diagnostic(self.root)
        changed = deepcopy(self.currency_report)
        changed["documents"][0]["facts"][0]["currency"]["checks"][0]["reason"] = "same-count-change"
        self.write(self.root/diagnostic.CURRENCY_REPORT, changed)
        with self.assertRaises(ValueError):
            diagnostic.validate_evidence_guard_diagnostic(self.root, original)

    def test_duplicate_source_identity_and_duplicate_facts_are_rejected(self):
        source, provenance, packs, threshold, previous = diagnostic._source(self.root)
        duplicate = deepcopy(source["report"]["documents"][0]); duplicate["id"] = "another-id"
        source["report"]["documents"].append(duplicate)
        previous_duplicate = deepcopy(previous["documents"][0]); previous_duplicate["id"] = "another-id"
        previous["documents"].append(previous_duplicate)
        packs["another-id"] = next(iter(packs.values()))
        with self.assertRaisesRegex(ValueError, "Duplicate source PDF"):
            diagnostic._build(self.root, source, provenance, packs, threshold, previous)
        source, provenance, packs, threshold, previous = diagnostic._source(self.root)
        source["report"]["documents"][0]["pages"][0]["fact_ids"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate reference fact"):
            diagnostic._build(self.root, source, provenance, packs, threshold, previous)

    def test_reject_invalid_timestamp(self):
        for value in ("2026-01-01T00:00:00", "2000-01-01T00:00:00+00:00", (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()):
            with self.assertRaises(ValueError):
                diagnostic.build_evidence_guard_diagnostic(self.root, generated_at=value)

    def test_new_output_only_and_no_frozen_writes(self):
        with patch.object(runner, "ROOT", self.root), redirect_stdout(StringIO()):
            report = runner.run()
            target = self.root/".run/reproductions/evidence-guard-report.json"
            original = target.read_bytes()
            self.assertEqual(json.loads(original), report)
            with self.assertRaises(ValueError):
                runner.run()
            self.assertEqual(target.read_bytes(), original)
        for relative in ("artifacts/new.json", "configs/new.json", ".run/document-transfer/new.json",
                         ".run/reproductions/sub/new.json", ".run/reproductions/new.txt"):
            with self.assertRaises(ValueError):
                runner.output_path(self.root, self.root/relative)


if __name__ == "__main__":
    unittest.main()
