from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from pydantic import ValidationError

from creditlens.document import Token, EvidenceError, extract_table
from creditlens.diligence import CasePack, analyze_pack, digest
from scripts.build_pdf_pack import compare, verified_pdf

ROOT = Path(__file__).resolve().parents[1]
PDF_PACK = json.loads((ROOT/"data/examples/msft-fy2025-pdf.json").read_text(encoding="utf-8"))
HTML_PACK = json.loads((ROOT/"data/examples/msft-fy2025.json").read_text(encoding="utf-8"))


def words(text, x, y):
    result = []
    for word in text.split():
        result.append(Token(word, x, y, x+len(word)*4, y+8))
        x += len(word)*4+3
    return result


def sample():
    return (words("STATEMENT", 10, 10)+words("(In millions)", 10, 30)
            +words("Year Ended June 30,", 10, 50)
            +[Token("2025", 314, 50, 330, 58), Token("2024", 414, 50, 430, 58)]
            +words("Net cash from operations", 10, 80)
            +[Token("1,200", 310, 80, 330, 88), Token("(100)", 410, 80, 430, 88)])


def read(tokens):
    return extract_table(tokens, page=1, width=600, height=800, title="STATEMENT",
                         unit_header="(In millions)", period_header="Year Ended June 30,",
                         years=[2025, 2024], metrics={"cfo": "Net cash from operations"})


class DocumentTests(unittest.TestCase):
    def test_columns_read_by_position_with_sign_and_evidence(self):
        result = read(list(reversed(sample())))
        self.assertEqual([r["value"] for r in result], ["1200", "-100"])
        self.assertEqual(result[1]["pdf_location"]["amount_bbox"]["x1"], 430)
        self.assertEqual(result[0]["pdf_location"]["year"], 2025)

    def test_missing_unit_fails_closed(self):
        with self.assertRaises(EvidenceError):
            read([t for t in sample() if t.top != 30])

    def test_missing_or_duplicate_year_fails_closed(self):
        for year_text in ("", "2025"):
            with self.subTest(year_text=year_text), self.assertRaises(EvidenceError):
                read([replace(t, text=year_text) if t.text == "2024" else t for t in sample()])

    def test_repeated_row_never_selects_first_occurrence(self):
        duplicate = [replace(t, top=t.top+40, bottom=t.bottom+40) for t in sample() if t.top == 80]
        with self.assertRaises(EvidenceError):
            read(sample()+duplicate)

    def test_misaligned_cell_rejected(self):
        with self.assertRaises(EvidenceError):
            read([replace(t, x0=t.x0-20, x1=t.x1-20) if t.text == "1,200" else t for t in sample()])

    def test_unreadable_or_missing_cell_rejected(self):
        for text in ("1,20", "O.1", "—"):
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                read([replace(t, text=text) if t.text == "1,200" else t for t in sample()])
        with self.assertRaises(EvidenceError):
            read([t for t in sample() if t.text != "1,200"])

    def test_no_text_layer_is_explicit_failure(self):
        with self.assertRaises(EvidenceError):
            read([])

    def test_out_of_page_and_wrong_year_locators_rejected(self):
        for invalid in ("bounds", "year", "nan"):
            pack = deepcopy(PDF_PACK)
            loc = pack["facts"][0]["pdf_location"]
            if invalid == "bounds":
                loc["amount_bbox"]["x1"] = loc["page_width"]+1
            elif invalid == "year":
                loc["year"] = 2020
            else:
                loc["amount_bbox"]["top"] = float("nan")
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                CasePack.model_validate(pack)

    def test_optional_location_preserves_legacy_serialization(self):
        pack = CasePack.model_validate(HTML_PACK)
        self.assertTrue(all("pdf_location" not in f for f in pack.model_dump(mode="json")["facts"]))
        self.assertEqual(digest(pack.model_dump(mode="json")), "c108699fe321af20c85de2fbd84a9c6358a0bae280a231a530b2b5bb4e7e3806")

    def test_official_examples_have_matching_financial_basis(self):
        pdf, html = CasePack.model_validate(PDF_PACK), CasePack.model_validate(HTML_PACK)
        report = compare(pdf, html)
        self.assertEqual((report["compared"], report["matched"], report["missing_pdf_fact_ids"]), (20, 20, []))
        self.assertEqual(analyze_pack(pdf, date(2025, 8, 1), 20)["metrics"], analyze_pack(html, date(2025, 8, 1), 20)["metrics"])

    def test_changed_cached_document_rejected_before_parse(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/"changed.pdf"
            path.write_bytes(b"%PDF-1.7\nnot the registered source")
            with self.assertRaises(EvidenceError):
                verified_pdf(path)

    def test_cross_format_mismatch_reported_not_silently_corrected(self):
        changed = deepcopy(PDF_PACK)
        changed["facts"][0].update(value="1", raw_value="1")
        report = compare(CasePack.model_validate(changed), CasePack.model_validate(HTML_PACK))
        self.assertEqual(report["matched"], 19)
        self.assertEqual(report["checks"][0]["pdf_value"], "1")
