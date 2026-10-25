from copy import deepcopy
from decimal import Decimal
import builtins
import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from creditlens.cash_flow_sources import (
    DATASET_PATH, DATASET_SHA256, SOURCE_SPECS, CashFlowSourcesError,
    canonical_sha256, load_cash_flow_sources, validate_source_bundle,
)
from creditlens.document import EvidenceError, Token
from creditlens.research_materials import build_material_bundle
from scripts import build_cash_flow_bridge as builder


ROOT = Path(__file__).resolve().parents[1]


def synthetic_apple_tokens():
    """Artificial values test layout rules; no claim of PDF/model evaluation."""
    spec, tokens = SOURCE_SPECS[1], []
    def words(text, x, y, width=3.5):
        for word in text.split():
            tokens.append(Token(word, x, y, x+len(word)*width, y+6))
            x += (len(word)+1)*width
    words(spec["title"], 70, 10)
    words("(In millions)", 20, 22)
    words("Years ended", 380, 34)
    for i, year in enumerate(builder.YEARS):
        center = 380+70*i
        words(spec["date_headers"][i], center-28, 46)
        tokens.append(Token(str(year), center-7, 58, center+7, 64))
    words(spec["start"], 20, 76)
    y, previous = 90, None
    for _, role, label, _ in spec["rows"]:
        if role == "reconciliation_adjustments" and role != previous:
            words(spec["adjustment_heading"], 20, y)
            y += 15
        if role == "operating_assets_and_liabilities" and role != previous:
            words(spec["change_heading"], 20, y)
            y += 15
        words(label, 30, y)
        for i, raw in enumerate(("(3)", "2", "1")):
            tokens.append(Token(raw, 368+70*i, y, 388+70*i, y+6))
        y += 15
        previous = role
    words(spec["end"], 20, y+7)
    # Same label in a different financial section must not be selected.
    words("Other", 30, y+22)
    for i in range(3):
        tokens.append(Token("999", 368+70*i, y+22, 388+70*i, y+28))
    return tokens


class CashFlowSourceTests(unittest.TestCase):
    def setUp(self):
        self.data = load_cash_flow_sources(ROOT)
        self.materials = build_material_bundle(ROOT)

    def copy_root(self, directory):
        target = Path(directory)
        for relative in (DATASET_PATH, "data/examples/msft-fy2025-pdf.json", "data/examples/aapl-fy2025-pdf.json"):
            (target/relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/relative, target/relative)
        return target

    def test_real_52_signed_amounts_match_visual_transcription(self):
        expected = {
            ("MSFT", 2024): [88136, 22287, 10734, 305, -4738, -7191, 1284, -1648, -6817, 3545, 5348, 1687, 4867, 749, 118548],
            ("MSFT", 2025): [101832, 34153, 11974, 609, -7056, -10581, 309, -3044, -2950, 569, 5438, -38, 5922, -975, 136162],
            ("AAPL", 2024): [93736, 11445, 11688, -2266, -3788, -1356, -1046, -11731, 6020, 15552, 118254],
            ("AAPL", 2025): [112010, 11698, 12863, -89, -6682, -347, 1400, -9197, 902, -11076, 111482],
        }
        all_ids = []
        for statement in self.data["statements"]:
            values = [int(f["signed_value"]) for f in statement["facts"]]
            self.assertEqual(values, expected[statement["issuer_id"], statement["year"]])
            self.assertEqual(sum(values[:-1]), values[-1])
            self.assertEqual(statement["total_fact_id"], statement["facts"][-1]["id"])
            self.assertNotIn("cfo", statement["expected_row_keys"])
            all_ids.extend(f["id"] for f in statement["facts"])
        self.assertEqual(len(all_ids), 52)
        self.assertEqual(len(set(all_ids)), 52)

    def test_runtime_is_deterministic_and_does_not_rehash_raw_pdf_or_claim_causality(self):
        self.assertEqual(canonical_sha256(self.data), DATASET_SHA256)
        self.assertEqual(self.data, load_cash_flow_sources(ROOT))
        for source in self.data["source_versions"]:
            self.assertTrue(source["build_pdf_sha256_verified"])
            self.assertFalse(source["raw_pdf_rehashed"])
            self.assertFalse(source["historical_version_verified"])
        self.assertFalse(self.data["contract"]["causal_explanation"])
        self.assertFalse(self.data["contract"]["prediction"])

    def test_original_grouping_and_other_subsection_preserved(self):
        other = next(f for s in self.data["statements"] if s["issuer_id"] == "AAPL" for f in s["facts"] if f["row_key"] == "other_adjustments")
        self.assertEqual(other["role"], "reconciliation_adjustments")
        self.assertEqual(other["label_en"], "Other")
        self.assertIn("Adjustments to reconcile", other["location"]["section"]["start_label"])
        self.assertEqual(other["location"]["section"]["end_label"], "Changes in operating assets and liabilities:")
        self.assertTrue(other["raw_value"].startswith("("))
        self.assertTrue(other["signed_value"].startswith("-"))

    def test_loader_ignores_json_whitespace_but_not_semantic_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_root(directory)
            path = root/DATASET_PATH
            path.write_text(json.dumps(self.data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            self.assertEqual(load_cash_flow_sources(root), self.data)
            bad = deepcopy(self.data)
            bad["statements"][0]["facts"][1]["signed_value"] = "1"
            path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CashFlowSourcesError, "source_hash_mismatch"):
                load_cash_flow_sources(root)

    def test_duplicate_json_keys_nonfinite_and_oversize_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_root(directory)
            path = root/DATASET_PATH
            for content in ('{"a":{"x":1,"x":2}}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e9999}', ' '* (2*1024*1024+1)):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content[:30]), self.assertRaises(CashFlowSourcesError):
                    load_cash_flow_sources(root)

    def test_changed_or_missing_old_cfo_material_refuses_current_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.copy_root(directory)
            path = root/"data/examples/msft-fy2025-pdf.json"
            original = path.read_text(encoding="utf-8")
            path.write_text(original.replace('"136162"', '"136163"'), encoding="utf-8")
            with self.assertRaises(CashFlowSourcesError):
                load_cash_flow_sources(root)
            path.unlink()
            with self.assertRaises(CashFlowSourcesError):
                load_cash_flow_sources(root)

    def test_missing_or_reordered_rows_cannot_pass_on_total_alone(self):
        for change in ("drop", "duplicate", "reorder", "required_keys"):
            data = deepcopy(self.data)
            st = data["statements"][0]
            if change == "drop":
                st["facts"].pop(2)
            elif change == "duplicate":
                st["facts"][2] = deepcopy(st["facts"][1])
            elif change == "reorder":
                st["facts"][1:3] = reversed(st["facts"][1:3])
            else:
                st["expected_row_keys"].pop(1)
            with self.subTest(change=change), self.assertRaises(CashFlowSourcesError):
                validate_source_bundle(data, self.materials)

    def test_signed_amount_and_cfo_identity_changes_refused(self):
        modifications = [
            lambda s: s["facts"][4].update(signed_value="4738"),
            lambda s: s.update(reported_cfo_observation_id="MSFT:cfo-2024"),
            lambda s: s.update(total_fact_id=s["facts"][0]["id"]),
            lambda s: s["facts"][-1].update(raw_value="118,549", signed_value="118549"),
            lambda s: s["facts"][1].update(raw_value="22,288", signed_value="22288"),
        ]
        for change in modifications:
            data = deepcopy(self.data)
            change(data["statements"][0])
            with self.assertRaises(CashFlowSourcesError):
                validate_source_bundle(data, self.materials)

    def test_issuer_source_period_and_unit_conflicts_refused(self):
        for key, value in (("issuer_id", "AAPL"), ("source_sha256", "0"*64), ("unit", "USD"), ("scope", "continuing_only")):
            data = deepcopy(self.data)
            data["statements"][0][key] = value
            with self.subTest(key=key), self.assertRaises(CashFlowSourcesError):
                validate_source_bundle(data, self.materials)
        data = deepcopy(self.data)
        data["statements"][0]["period"]["days"] = 365
        with self.assertRaises(CashFlowSourcesError):
            validate_source_bundle(data, self.materials)

    def test_unknown_fields_roles_and_outside_or_wrong_section_boxes_refused(self):
        modifications = [
            lambda f: f.update(role="all_noncash"),
            lambda f: f.update(unit="USD"),
            lambda f: f["location"]["amount_bbox"].update(x1=9999),
            lambda f: f["location"]["section"].update(start_label="Investing activities:"),
            lambda f: f.update(page=1),
        ]
        for change in modifications:
            data = deepcopy(self.data)
            change(data["statements"][0]["facts"][1])
            with self.assertRaises(CashFlowSourcesError):
                validate_source_bundle(data, self.materials)


class CashFlowBuilderTests(unittest.TestCase):
    def test_subsection_selects_other_without_financing_or_asset_row_collision(self):
        records = builder.extract_operating_page(synthetic_apple_tokens(), spec=SOURCE_SPECS[1], width=612, height=792)
        selected = [r for r in records if r["metric"] == "other_adjustments"]
        self.assertEqual([r["raw_value"] for r in selected], ["(3)", "2", "1"])
        self.assertEqual(selected[0]["value"], "-3")
        self.assertEqual(len(records), 33)

    def test_missing_and_extra_operating_rows_fail_even_if_zero(self):
        tokens = synthetic_apple_tokens()
        target = next(t for t in tokens if t.text == "Inventories")
        missing = [t for t in tokens if t.cy != target.cy]
        extra = tokens + [Token("Unregistered", 30, target.top+8, 72, target.top+14),
                          Token("0", 368, target.top+8, 388, target.top+14)]
        for candidate in (missing, extra):
            with self.assertRaises(EvidenceError):
                builder.extract_operating_page(candidate, spec=SOURCE_SPECS[1], width=612, height=792)

    def test_wrong_amount_column_or_invalid_numeric_token_refused(self):
        tokens = synthetic_apple_tokens()
        index = next(i for i, t in enumerate(tokens) if t.text == "(3)")
        value = tokens[index]
        for replacement in (Token(value.text, 575, value.top, 595, value.bottom),
                            Token("(3", value.x0, value.top, value.x1, value.bottom)):
            candidate = list(tokens)
            candidate[index] = replacement
            with self.assertRaises(EvidenceError):
                builder.extract_operating_page(candidate, spec=SOURCE_SPECS[1], width=612, height=792)

    def test_output_to_frozen_inputs_or_preview_or_existing_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = root/".run/inputs"
            inputs.mkdir(parents=True)
            old = root/".run/old.json"
            old.write_text("keep", encoding="utf-8")
            targets = (root/DATASET_PATH, root/"artifacts/a.json", inputs/"out.json", old,
                       root/".run/document-ai/new.json", root/".run/document-transfer/new.json")
            for output in targets:
                with self.subTest(output=str(output)), self.assertRaises(ValueError):
                    builder.validate_output(root, output, inputs)
            self.assertEqual(old.read_text(encoding="utf-8"), "keep")
            self.assertEqual(builder.validate_output(root, root/".run/reproductions/new.json", inputs), root/".run/reproductions/new.json")

    def test_cli_refuses_public_output_before_build_or_pdf_import(self):
        with patch.object(builder, "build", side_effect=AssertionError("build must not run")), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.main(["--output", str(ROOT/DATASET_PATH)]), 1)

    def test_bad_pdf_hash_refused_before_optional_pdf_import(self):
        original_import = builtins.__import__
        def forbidden_import(name, *args, **kwargs):
            if name == "pypdfium2":
                raise AssertionError("PDF library imported before hash verification")
            return original_import(name, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            for spec in SOURCE_SPECS:
                (Path(directory)/spec["filename"]).write_bytes(b"not the registered PDF")
            with patch("builtins.__import__", side_effect=forbidden_import), self.assertRaisesRegex(EvidenceError, "SHA256 mismatch"):
                builder.build(ROOT, Path(directory))


if __name__ == "__main__":
    unittest.main()
