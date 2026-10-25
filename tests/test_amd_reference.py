"""Reference-builder controls use synthetic tokens, never model predictions."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from creditlens.diligence import Fact
from creditlens.document import EvidenceError, Token
from scripts import build_amd_reference as builder


def words(text,x,y):
    result=[]
    for value in text.split():
        result.append(Token(value,x,y,x+len(value)*3,y+6))
        x+=len(value)*3+3
    return result


def sample(spec):
    tokens=words(spec["title"],30,20)
    if spec.get("duration_header"):
        tokens+=words(spec["duration_header"],390,35)
    for i,(year,label) in enumerate(zip(spec["years"],spec["dates"])):
        tokens+=words(label,380+i*60,50)
        tokens+=[Token(str(year),392+i*60,62,408+i*60,68)]
    for i,label in enumerate(spec["unit_rows"]):
        tokens+=words(label,390,80+i*10)
    y=120
    rows={}
    def row(label,values=None):
        nonlocal y,tokens
        rows[label]=y
        tokens+=words(label,30,y)
        if values is not None:
            tokens+=words(". . .",320,y)
            for i,value in enumerate(values):
                tokens+=words(value,394+i*60,y)
        y+=14
    if spec["page"]==74:
        row("Current assets:")
        row("Cash and cash equivalents",["11","22"])
        row("Total current assets",["33","44"])
        row("Total current liabilities",["55","66"])
        row("Long-term debt, net of current portion",["77","88"])
    else:
        row("Net income",["11","22","33"])
        row("Net cash provided by operating activities of continuing operations",["44","55","66"])
        row("Net cash provided by operating activities",["88","99","100"])
        row("Cash flows from investing activities:")
        row("Purchases of property and equipment",["(7)","(8)","(9)"])
        row("Net cash used in investing activities of continuing operations",["(10)","(11)","(12)"])
        row("Proceeds from divestiture, net of cash divested",["20","21","22"])
        # Repeated label deliberately uses different values and is outside the
        # selected continuing section. Numeric values do not disambiguate it.
        row("Purchases of property and equipment",["(3)","(4)","(5)"])
    return tokens,rows


class AmdReferenceTests(unittest.TestCase):
    def extract(self,tokens,spec):
        return builder.extract_tokens(tokens,spec,width=594,height=774)

    def test_two_line_dates_and_unit_continuation_are_preserved(self):
        spec=builder.TABLES[0];tokens,_=sample(spec)
        facts=self.extract(tokens,spec)
        self.assertEqual(len(facts),8)
        first=facts[0]
        self.assertEqual(first.raw_value,"11")
        self.assertEqual(first.pdf_location.period_header,"December 27, 2025")
        self.assertLess(first.pdf_location.period_bbox.top,first.pdf_location.year_bbox.top)
        self.assertEqual(first.pdf_location.unit_header,"(In millions, except par value amounts)")
        self.assertGreater(first.pdf_location.unit_bbox.bottom-first.pdf_location.unit_bbox.top,10)

    def test_continuing_scope_not_total_or_duplicate_capex_selects_cells(self):
        spec=builder.TABLES[1];tokens,_=sample(spec)
        facts=self.extract(tokens,spec)
        values={f.id:f for f in facts}
        self.assertEqual(values["cfo-2025"].raw_value,"44")
        self.assertEqual(values["capex-2025"].raw_value,"(7)")
        self.assertEqual(str(values["capex-2025"].value),"7")
        self.assertEqual(values["capex-2025"].pdf_location.section.end_label,
                         "Net cash used in investing activities of continuing operations")
        self.assertIn("continuing_operations",values["cfo-2025"].locator)
        self.assertIn("including_discontinued",values["net_income-2025"].locator)
        self.assertTrue(all(f.period_end.year!=2023 for f in facts))

    def test_amount_changes_are_read_instead_of_searched_against_expected_values(self):
        spec=builder.TABLES[0];tokens,_=sample(spec)
        tokens=[replace(t,text="91") if t.text=="11" else t for t in tokens]
        self.assertEqual(self.extract(tokens,spec)[0].raw_value,"91")

    def test_missing_duplicate_or_cross_column_amount_is_rejected(self):
        spec=builder.TABLES[0];original,_=sample(spec)
        cell=next(t for t in original if t.text=="11")
        mutations=[([t for t in original if t is not cell]),original+[cell],
                   [replace(t,x0=t.x0+60,x1=t.x1+60) if t is cell else t for t in original]]
        for tokens in mutations:
            with self.subTest(tokens=len(tokens)),self.assertRaises(EvidenceError):
                self.extract(tokens,spec)

    def test_value_column_dot_cannot_be_discarded_as_a_leader(self):
        spec=builder.TABLES[0];tokens,_=sample(spec)
        cell=next(t for t in tokens if t.text=="11")
        tokens.append(Token(".",410,cell.top,411,cell.bottom))
        with self.assertRaisesRegex(EvidenceError,"dot inside"):
            self.extract(tokens,spec)

    def test_missing_unit_continuation_and_wrong_dates_fail_closed(self):
        spec=builder.TABLES[0];original,_=sample(spec)
        for tokens in ([t for t in original if t.text!="amounts)"],
                       [replace(t,text="2023") if t.text=="2024" else t for t in original],
                       [replace(t,x0=t.x0-60,x1=t.x1-60) if t.text=="28," else t for t in original]):
            with self.subTest(tokens=len(tokens)),self.assertRaises(EvidenceError):
                self.extract(tokens,spec)

    def test_repeated_capex_without_section_cannot_be_chosen_by_value(self):
        spec=deepcopy(builder.TABLES[1]);tokens,_=sample(spec)
        spec["metrics"][-1].pop("section")
        with self.assertRaisesRegex(EvidenceError,"ambiguous exact row"):
            self.extract(tokens,spec)

    def test_missing_local_source_and_mismatched_hash_do_not_download_silently(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(builder.urllib.request,"urlopen") as network:
            path=Path(directory)/"source.pdf"
            with self.assertRaises(EvidenceError):builder.verified_source(path)
            with self.assertRaisesRegex(EvidenceError,"registered cache"):builder.verified_source(path,download=True)
            path.write_bytes(b"%PDF-changed")
            with self.assertRaisesRegex(EvidenceError,"bytes changed"):builder.verified_source(path)
            network.assert_not_called()

    def test_public_output_is_rejected_before_download_or_pdf_import(self):
        with patch.object(builder.sys,"argv",["builder","--download","--output",str(builder.ROOT/"configs/invalid.json")]),\
                patch.object(builder,"verified_source") as source:
            with self.assertRaises(ValueError):builder.main()
            source.assert_not_called()

    def test_published_reference_inventory_and_dates_have_explicit_limits(self):
        pack=json.loads((builder.ROOT/"data/examples/amd-fy2025-evaluation.json").read_text(encoding="utf-8"))
        facts=[Fact.model_validate(f) for f in pack["facts"]]
        self.assertEqual(pack["purpose"],"frozen_issuer_evaluation")
        self.assertEqual(len(facts),14)
        self.assertEqual({f.pdf_location.page for f in facts},{74,76})
        self.assertTrue(all(f.period_end.year in {2025,2024} for f in facts))
        self.assertEqual({f.raw_value for f in facts if f.metric=="capex"},{"(974)","(636)"})
        self.assertEqual({f.raw_value for f in facts if f.metric=="cfo"},{"6,493","3,041"})
        for f in facts:
            if f.period_start:self.assertEqual((f.period_end-f.period_start).days+1,364)
        source=pack["sources"][0]
        self.assertEqual(source["wrapper_filing_date"],"2026-03-27")
        self.assertEqual(source["underlying_filing_date"],"2026-02-04")
        self.assertIsNone(source["exact_asset_publication_date"])
        self.assertIsNone(source["first_available_at"])
        self.assertFalse(source["historical_snapshot_verified"])
        self.assertFalse(source["point_in_time_eligible"])


if __name__=="__main__":
    unittest.main()
