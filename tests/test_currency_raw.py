"""Complete saved-OCR verification fixtures; no PDF, image, OCR or network use."""
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from creditlens import currency_diagnostic as diagnostic
from creditlens.diligence import Fact, digest
from creditlens.word_evidence import WordPage
from scripts import verify_currency_raw as verifier
from tests import test_currency_diagnostic as diagnostic_fixtures
from tests import test_currency_evidence as currency_fixtures


class CurrencyRawTests(unittest.TestCase):
    def setUp(self):
        self.fixture = diagnostic_fixtures.CurrencyDiagnosticTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        issuer = self.fixture.fixture
        fact, raw = currency_fixtures.controlled()
        second = fact.model_dump(mode="json")
        second.update(id="cfo-2024", value="1000", raw_value="1,000", period_start="2023-12-31", period_end="2024-12-28")
        second["pdf_location"].update(year=2024,year_bbox=currency_fixtures.box(300,50,320,60),
            period_header="December 28, 2024",period_bbox=currency_fixtures.box(285,35,340,65),
            amount_bbox=currency_fixtures.box(305,100,330,110))
        second = Fact.model_validate(second)
        raw["lines"].append(currency_fixtures.line("December 28,",currency_fixtures.box(285,35,340,45)))
        extra = currency_fixtures.line("$1,000",currency_fixtures.box(288,98,334,112))
        extra["words"][0]["bbox"] = currency_fixtures.box(290,98,332,112)
        raw["lines"].append(extra)
        raw["lines"].insert(0,currency_fixtures.line("unrelated footer",currency_fixtures.box(1,1,60,10)))
        self.words = WordPage.model_validate(raw)
        issuer.page = self.words
        issuer.pack["facts"] = [fact.model_dump(mode="json"),second.model_dump(mode="json")]
        issuer.manifest["documents"][0]["pages"][0]["fact_ids"] = [fact.id,second.id]
        issuer.save()
        self.source_report = issuer.report()
        self.write(self.root/diagnostic.REPORT, self.source_report)
        self.write(self.root/diagnostic.RELEASE, {"schema_version":1,"files":{
            diagnostic.REPORT:sha256((self.root/diagnostic.REPORT).read_bytes()).hexdigest(),
            diagnostic.MANIFEST:sha256((self.root/diagnostic.MANIFEST).read_bytes()).hexdigest()}})
        self.report = diagnostic.build_currency_diagnostic(self.root)
        self.report_path = self.root/".run/currency-candidate.json"
        self.write(self.report_path,self.report)
        self.document_id = self.source_report["documents"][0]["id"]
        self.raw_dir = self.root/".run/currency-saved-ocr"
        self.word_path = self.raw_dir/self.document_id/"ocr-words-1.json"
        self.line_path = self.raw_dir/self.document_id/"ocr-lines-1.json"
        self.write(self.word_path,self.words.model_dump(mode="json"))
        self.write(self.line_path,self.words.line_page().model_dump(mode="json"))

    @staticmethod
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n",encoding="utf-8")

    def verify(self):
        return verifier.verify(self.root,self.raw_dir,report_path=self.report_path)

    def test_full_replay_maps_new_currency_header_indices_without_pdf_or_images(self):
        before = {path:path.read_bytes() for path in self.raw_dir.rglob("*.json")}
        result = self.verify()
        self.assertEqual(result["status"],"verified")
        self.assertEqual(result["facts_checked"],2)
        self.assertTrue(all(f["association_applied"] and f["exact_after_parent_index_mapping"] for f in result["facts"]))
        self.assertLess(result["published_parent_lines_checked"],len(self.words.lines))
        self.assertFalse(result["pdf_and_image_bytes_verified"])
        self.assertFalse(result["original_model_execution_verified"])
        self.assertFalse(result["model_inference_performed"])
        self.assertEqual(len(result["raw_ocr_file_sha256"]),2)
        self.assertEqual({path:path.read_bytes() for path in self.raw_dir.rglob("*.json")},before)
        self.assertTrue(all(path.suffix==".json" for path in self.raw_dir.rglob("*") if path.is_file()))

    def test_raw_json_whitespace_is_allowed_but_semantic_changes_are_not(self):
        original = self.words.model_dump(mode="json")
        self.word_path.write_text(json.dumps(original,separators=(",",":")),encoding="utf-8")
        self.assertEqual(self.verify()["status"],"verified")
        for change in (lambda p:p["lines"][1]["line"].update(text="$9,999"),
                       lambda p:p["lines"].pop(),lambda p:p.update(width=501),
                       lambda p:p.update(page="1"),lambda p:p.update(width=500)):
            data=deepcopy(original);change(data);self.write(self.word_path,data)
            with self.assertRaises(ValueError):self.verify()

    def test_line_projection_tampering_or_missing_file_refused(self):
        data=self.words.line_page().model_dump(mode="json")
        data["spans"][1]["confidence"]=.5
        self.write(self.line_path,data)
        with self.assertRaises(ValueError):self.verify()
        self.line_path.unlink()
        with self.assertRaises(ValueError):self.verify()

    def test_duplicate_keys_nonfinite_and_oversize_are_refused(self):
        for text in ('{"page":1,"page":2}', '{"page":NaN}', '{"page":Infinity}', '{"width":1e9999}'):
            self.word_path.write_text(text,encoding="utf-8")
            with self.subTest(text=text),self.assertRaises(ValueError):self.verify()
        self.write(self.word_path,self.words.model_dump(mode="json"))
        with patch.object(verifier,"RAW_JSON_LIMIT",100),self.assertRaises(ValueError):self.verify()

    def test_parent_mapping_rejects_invalid_indices_and_never_maps_word_indices(self):
        value={"line_index":0,"candidate_parent_indices":[1],"column_bounds":{"current_header_index":1},
               "word_index":99,"year_word_index":3,"selected_word_indices":[4,8]}
        mapped=verifier.remap_parent_indices(value,[5,9])
        self.assertEqual(mapped,{"line_index":5,"candidate_parent_indices":[9],"column_bounds":{"current_header_index":9},
                                 "word_index":99,"year_word_index":3,"selected_word_indices":[4,8]})
        for index in (-1,2,True,1.0,"1"):
            with self.subTest(index=index),self.assertRaises(ValueError):
                verifier.remap_parent_indices({"current_header_index":index},[5,9])
        for indices in ([5,5],[-1,2],[True,2]):
            with self.assertRaises(ValueError):verifier.remap_parent_indices(value,indices)

    def test_published_source_membership_is_checked_even_after_view_validation(self):
        original=diagnostic.currency_diagnostic_view(self.root,self.report)
        for index in (-1,len(self.words.lines),True,0):
            view=deepcopy(original)
            view["source"]["report"]["documents"][0]["pages"][0]["evidence_lines"][0]["source_line_index"]=index
            # Isolate this verifier's defense; the real view rejects edited
            # releases before this stage, but membership must still be checked.
            with patch.object(verifier,"currency_diagnostic_view",return_value=view),self.assertRaises(ValueError):
                self.verify()

    def test_full_hidden_prefix_is_detected_even_if_public_fragment_still_passes(self):
        raw=self.words.model_dump(mode="json")
        prefix=currency_fixtures.line("CAD",currency_fixtures.box(183,100,189,110));prefix["words"]=[]
        raw["lines"].append(prefix)
        changed=WordPage.model_validate(raw)
        self.write(self.word_path,changed.model_dump(mode="json"))
        self.write(self.line_path,changed.line_page().model_dump(mode="json"))
        view=diagnostic.currency_diagnostic_view(self.root,self.report)
        page=view["source"]["report"]["documents"][0]["pages"][0]
        page.update(full_word_ocr_sha256=digest(changed.model_dump(mode="json")),
                    full_line_ocr_sha256=digest(changed.line_page().model_dump(mode="json")),ocr_line_count=len(changed.lines))
        # Simulate a signed-off raw inventory with a previously omitted parent.
        # Exact full-rule replay must catch what public-only checking cannot.
        with patch.object(verifier,"currency_diagnostic_view",return_value=view),self.assertRaisesRegex(ValueError,"public currency diagnostic differ"):
            self.verify()

    def test_changed_new_report_or_frozen_source_release_is_refused(self):
        changed=deepcopy(self.report);changed["summary"]["facts"]+=1
        self.write(self.report_path,changed)
        with self.assertRaises(ValueError):self.verify()
        self.write(self.report_path,self.report)
        source=deepcopy(self.source_report);source["summary"]["facts"]+=1
        self.write(self.root/diagnostic.REPORT,source)
        with self.assertRaises(ValueError):self.verify()

    def test_cli_emits_small_summary_or_structured_refusal_and_writes_nothing(self):
        before={str(p.relative_to(self.root)) for p in self.root.rglob("*")}
        with patch.object(verifier,"ROOT",self.root),redirect_stdout(StringIO()) as stdout:
            self.assertEqual(verifier.main(["--raw-dir",str(self.raw_dir),"--report",str(self.report_path)]),0)
        self.assertEqual(json.loads(stdout.getvalue())["facts_checked"],2)
        self.assertEqual({str(p.relative_to(self.root)) for p in self.root.rglob("*")},before)
        with patch.object(verifier,"ROOT",self.root),redirect_stdout(StringIO()) as stdout:
            self.assertEqual(verifier.main(["--raw-dir",str(self.root/"missing"),"--report",str(self.report_path)]),1)
        self.assertEqual(json.loads(stdout.getvalue())["status"],"refused")


if __name__=="__main__":
    unittest.main()
