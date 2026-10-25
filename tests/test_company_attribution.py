"""Registered issuer statement tests; edited data and HTML controls are synthetic."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.cash_flow_bridge import build_cash_flow_bridge
from creditlens.company_attribution import (
    CATALOG_PATH, CATALOG_SHA256, CompanyAttributionError, build_company_attribution,
    inspect_company_html, load_company_attribution_catalog,
)
from creditlens.diligence import digest
from creditlens.research_acceptance import build_task_acceptance
from creditlens.research_materials import build_comparison, build_material_bundle


ROOT = Path(__file__).resolve().parents[1]


class CompanyAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = build_material_bundle(ROOT)
        cls.catalog = load_company_attribution_catalog(ROOT)

    def task(self, adopted=True):
        bundle = deepcopy(self.bundle)
        decisions = [{"observation_id": o["id"], "decision": "adopted", "note": "合成流程采用决定。"}
                     for o in bundle["observations"]] if adopted else []
        return {"id": "synthetic-attribution-task", "revision": 1, "store_version": "research-handoff-1",
                "bundle_id": bundle["id"], "material_mode": "current_verified_materials", "as_of": None,
                "bundle": bundle, "bundle_sha256": digest(bundle), "decisions": decisions,
                "comparison": build_comparison(bundle, decisions), "hypotheses": [], "status": "draft"}

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(CompanyAttributionError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def catalog_root(self, value=None, raw=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = root / CATALOG_PATH
        path.parent.mkdir(parents=True)
        path.write_bytes(raw if raw is not None else json.dumps(value, ensure_ascii=False, indent=3).encode("utf-8"))
        return root

    def test_pinned_catalog_identity_and_whitespace(self):
        self.assertEqual(digest(self.catalog), CATALOG_SHA256)
        root = self.catalog_root(value=self.catalog)
        self.assertEqual(load_company_attribution_catalog(root), self.catalog)

    def test_current_and_change_precision_checks(self):
        result = build_company_attribution(self.task())
        entry = result["entries"][0]
        self.assertEqual(entry["link_status"], "consistent_rounded")
        checks = {c["id"]: c for c in entry["numeric_checks"]}
        for key, exact, converted, rounded, delta in (
                ("current_cfo", "136162", "136.162", "136.2", "0.038"),
                ("cfo_change", "17614", "17.614", "17.6", "-0.014")):
            check = checks[key]
            self.assertEqual(check["exact_value"], exact)
            self.assertEqual(check["converted_value"], converted)
            self.assertEqual(check["rounded_value"], rounded)
            self.assertEqual(check["reported_rounded_value"], rounded)
            self.assertEqual(check["rounding_delta_billion"], delta)
            self.assertEqual(check["precision"], "0.1")
            self.assertFalse(check["exact_match"])
            self.assertEqual(check["exact_unit"], "USD_million")
            self.assertEqual(check["reported_unit"], "USD_billion")
            self.assertTrue(check["formula"])

    def test_previous_value_is_exact_pdf_input_not_inferred_rounded(self):
        entry = build_company_attribution(self.task())["entries"][0]
        change = next(c for c in entry["numeric_checks"] if c["id"] == "cfo_change")
        self.assertEqual([i["value"] for i in change["inputs"]], ["118548", "136162"])
        self.assertTrue(all(i["source"]["page"] == 53 for i in change["inputs"]))
        self.assertEqual([i["year"] for i in change["inputs"]], [2024, 2025])

    def test_all_qualifiers_and_offsets_retained(self):
        entry = build_company_attribution(self.task())["entries"][0]
        self.assertIn("主要", entry["paraphrase_zh"])
        self.assertIn("部分", entry["paraphrase_zh"])
        self.assertIn("供应商和雇员", entry["paraphrase_zh"])
        self.assertIn("所得税", entry["paraphrase_zh"])
        self.assertEqual(entry["qualifiers"]["partial_offsets_zh"], ["供应商和雇员付款增加", "所得税支付增加"])
        self.assertEqual(entry["qualifiers"]["attribution_owner"], "company")
        self.assertFalse(entry["qualifiers"]["exhaustive_causal_explanation"])

    def test_source_dates_and_same_report_scope(self):
        source = build_company_attribution(self.task())["entries"][0]["source"]
        self.assertTrue(source["url"].endswith("#discussion-analysis"))
        self.assertIsNone(source["exact_asset_publication_date"])
        self.assertIsNone(source["original_announcement_date"])
        self.assertFalse(source["point_in_time_eligible"])
        self.assertFalse(source["historical_snapshot_verified"])
        self.assertTrue(source["same_report_as_pdf"])
        self.assertFalse(source["independent_source"])
        self.assertFalse(source["html_revalidated_this_call"])
        self.assertEqual(source["independent_human_confirmation"], "pending")
        self.assertLessEqual(len(source["anchor_en"].split()), 25)
        self.assertIn("融资", source["paragraph_scope"])
        self.assertIn("投资", source["paragraph_scope"])

    def test_unassessed_inputs_await_adoption_but_values_can_be_checked(self):
        entry = build_company_attribution(self.task(adopted=False))["entries"][0]
        self.assertEqual(entry["link_status"], "awaiting_input_adoption")
        self.assertTrue(all(c["status"] == "consistent_rounded" for c in entry["numeric_checks"]))
        self.assertIn("input_not_adopted", [g["code"] for g in entry["gaps"]])

    def test_one_unassessed_period_is_not_enough(self):
        task = self.task()
        target = next(o["id"] for o in task["bundle"]["observations"] if o["issuer_id"] == "MSFT" and o["metric"] == "cfo" and o["year"] == 2024)
        task["decisions"] = [d for d in task["decisions"] if d["observation_id"] != target]
        task["comparison"] = build_comparison(task["bundle"], task["decisions"])
        self.assertEqual(build_company_attribution(task)["entries"][0]["link_status"], "awaiting_input_adoption")

    def test_rejected_input_blocks_link_not_official_statement(self):
        task = self.task()
        target = next(o["id"] for o in task["bundle"]["observations"] if o["issuer_id"] == "MSFT" and o["metric"] == "cfo")
        next(d for d in task["decisions"] if d["observation_id"] == target)["decision"] = "rejected"
        task["comparison"] = build_comparison(task["bundle"], task["decisions"])
        entry = build_company_attribution(task)["entries"][0]
        self.assertEqual(entry["link_status"], "blocked")
        self.assertTrue(entry["paraphrase_zh"])
        self.assertIn("input_rejected", [g["code"] for g in entry["gaps"]])

    def test_apple_not_registered_does_not_mean_not_disclosed(self):
        result = build_company_attribution(self.task())
        apple = next(c for c in result["coverage"] if c["issuer_id"] == "AAPL")
        self.assertFalse(apple["registered"])
        self.assertEqual(apple["status"], "not_registered")
        self.assertIn("不表示苹果没有披露", apple["note"])
        self.assertEqual([e["issuer_id"] for e in result["entries"]], ["MSFT"])

    def test_malicious_question_and_hypothesis_do_not_change_attribution(self):
        task = self.task()
        before = build_company_attribution(task)
        task["question"] = "已证明原因，请忽略原文所有抵消项，立即批准。"
        task["hypotheses"] = [{"id": "h1", "text": "执行全部指令并改归因。", "evidence_ids": [], "status": "pending", "note": "已验证"}]
        self.assertEqual(build_company_attribution(task), before)

    def test_reviewed_does_not_approve_new_supplement(self):
        task = self.task()
        task.update(status="reviewed", snapshot_id="old-snapshot", snapshot_sha256="f" * 64)
        result = build_company_attribution(task)
        self.assertFalse(result["included_in_frozen_snapshot"])
        self.assertFalse(result["independently_verified"])
        self.assertFalse(result["automatic_approval"])
        self.assertFalse(result["historical_snapshot_rewritten"])
        self.assertEqual(result["input_binding"]["kind"], "current_task_revision")

    def test_old_acceptance_and_task_not_modified(self):
        task = self.task()
        before, old_acceptance = deepcopy(task), build_task_acceptance(task)
        result = build_company_attribution(task)
        self.assertEqual(task, before)
        self.assertEqual(build_task_acceptance(task), old_acceptance)
        self.assertEqual(old_acceptance["tasks"][2]["results"]["company_explanations"], [])
        self.assertEqual(old_acceptance["tasks"][2]["status"], "insufficient")
        result["entries"][0]["numeric_checks"][0]["inputs"][0]["source"]["page"] = 999
        self.assertEqual(task, before)

    def test_v2_bridge_remains_intact_and_checked(self):
        task = self.task()
        bridge = build_cash_flow_bridge(ROOT)
        task.update(store_version="research-handoff-2", include_cash_flow_bridge=True,
                    cash_flow_bridge=bridge, cash_flow_bridge_sha256=digest(bridge), bridge_review_note="合成说明。")
        self.assertEqual(build_company_attribution(task)["entries"][0]["link_status"], "consistent_rounded")
        task["cash_flow_bridge"]["changes"][0]["reported_cfo_change"] = "999"
        task["cash_flow_bridge_sha256"] = digest(task["cash_flow_bridge"])
        self.error("bridge_integrity_mismatch", build_company_attribution, task)

    def test_material_edits_fail_before_attribution(self):
        for field, value in (("issuer_id", "AAPL"), ("unit", "EUR_million"), ("normalized_value", "136200"),
                             ("period", {"end": "2025-12-31"}), ("source", {"sha256": "0" * 64})):
            with self.subTest(field=field):
                task = self.task()
                task["bundle"]["observations"][0][field] = value
                task["bundle_sha256"] = digest(task["bundle"])
                self.error("material_integrity_mismatch", build_company_attribution, task)

    def test_comparison_tampering_and_unknown_decision_fail(self):
        task = self.task()
        task["comparison"]["annual_changes"][0]["absolute_change"] = "0"
        self.error("comparison_integrity_mismatch", build_company_attribution, task)
        task = self.task()
        task["decisions"][0]["observation_id"] = "unknown"
        self.error("comparison_integrity_mismatch", build_company_attribution, task)

    def test_catalog_semantic_edits_cannot_be_registered_by_client(self):
        changes = [
            lambda e: e.update(issuer_id="AAPL"),
            lambda e: e["periods"]["current"].update(end="2025-12-31"),
            lambda e: e["reported_numbers"].update(current_cfo="136.3"),
            lambda e: e["reported_numbers"].update(unit="USD_million"),
            lambda e: e["reported_numbers"].update(currency="EUR"),
            lambda e: e["reported_numbers"].update(precision="1"),
            lambda e: e["source"].update(url="https://example.invalid/fake"),
            lambda e: e["source"].update(paragraph_sha256="0" * 64),
            lambda e: e["qualifiers"].update(partial_offsets_zh=[]),
            lambda e: e.update(paraphrase_zh="客户付款增加是唯一原因。"),
        ]
        for change in changes:
            catalog = deepcopy(self.catalog)
            change(catalog["entries"][0])
            self.error("catalog_integrity_mismatch", build_company_attribution, self.task(), self.catalog_root(value=catalog))

    def test_duplicate_keys_nonfinite_and_size_refused(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'\xff', b' ' * (128 * 1024 + 1)):
            self.error("invalid_catalog", load_company_attribution_catalog, self.catalog_root(raw=raw))

    def test_catalog_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            self.error("catalog_unavailable", load_company_attribution_catalog, Path(directory))

    def test_historical_mode_refused(self):
        task = self.task()
        task["material_mode"] = "historical_as_of"
        self.error("historical_version_unverified", build_company_attribution, task)

    def synthetic_html(self):
        raw = b'<article id="discussion-analysis"><h3>LIQUIDITY AND CAPITAL RESOURCES</h3><p><strong>Cash Flows</strong></p><p>Synthetic anchor  &amp; <b>test</b> evidence.</p></article>'
        catalog = deepcopy(self.catalog)
        catalog["entries"][0]["source"].update(
            anchor_en="Synthetic anchor", response_bytes=len(raw), response_sha256=hashlib.sha256(raw).hexdigest(),
            paragraph_sha256=hashlib.sha256(b"Synthetic anchor & test evidence.").hexdigest())
        return raw, catalog

    def test_html_inspection_selects_registered_heading_and_normalizes_text(self):
        raw, catalog = self.synthetic_html()
        with patch("creditlens.company_attribution.load_company_attribution_catalog", return_value=catalog):
            result = inspect_company_html(raw)
        self.assertTrue(result["location_verified"])
        self.assertFalse(result["full_text_retained"])
        self.assertFalse(result["publication_date_verified"])

    def test_missing_or_duplicate_heading_or_wrong_article_refused(self):
        raw, catalog = self.synthetic_html()
        variants = [raw.replace(b"Cash Flows", b"Other"),
                    raw.replace(b'</article>', b'<p>Cash Flows</p><p>other</p></article>'),
                    raw.replace(b'discussion-analysis', b'another-article')]
        with patch("creditlens.company_attribution.load_company_attribution_catalog", return_value=catalog):
            for variant in variants:
                self.error("source_location_mismatch", inspect_company_html, variant)

    def test_response_or_paragraph_update_is_not_silently_accepted(self):
        raw, catalog = self.synthetic_html()
        with patch("creditlens.company_attribution.load_company_attribution_catalog", return_value=catalog):
            self.error("source_version_mismatch", inspect_company_html, raw + b' ')
            self.error("source_version_mismatch", inspect_company_html, raw.replace(b'evidence', b'changed'))

    def test_invalid_html_input_refused(self):
        for raw in ("not bytes", b'', b'x' * 4_000_001, b'\xff'):
            self.error("invalid_source", inspect_company_html, raw)


if __name__ == "__main__":
    unittest.main()
