"""Synthetic date-association controls, not a measurement of OCR quality."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from creditlens.date_evidence import DATE_RULE_VERSION, crosscheck_date_fact
from creditlens.diligence import Fact
from creditlens.document_transfer import TransferPack
from creditlens.word_evidence import WordPage, crosscheck_word_fact


ROOT = Path(__file__).resolve().parents[1]


def box(x0, top, x1, bottom):
    return dict(x0=x0, top=top, x1=x1, bottom=bottom)


def span(text, bounds, confidence=.99):
    return dict(text=text, confidence=confidence, bbox=bounds)


def controlled():
    fact = Fact.model_validate(dict(
        id="cash-2025", metric="cash_equivalents", label="Cash", value="1234",
        unit="USD_million", period_end="2025-01-26", source_id="synthetic",
        locator="Synthetic rule test only", raw_value="1,234",
        pdf_location=dict(page=1, page_width=500, page_height=700,
            coordinate_system="pdf_points_top_left", amount_bbox=box(155,100,180,110),
            row_label="Cash", row_label_bbox=box(30,100,70,110), year=2025,
            year_bbox=box(160,50,180,60), period_header="Jan 26, 2025",
            period_bbox=box(100,50,180,60), unit_header="In millions",
            unit_bbox=box(200,20,250,30), parser_version="synthetic-date-1")))
    loc = fact.pdf_location
    lines = [dict(line=span("Jan 26, 2025", box(100,50,180,60)), words=[
        span("Jan",box(100,50,125,60)), span("26,",box(145,50,157,60)),
        span("2025",box(160,50,180,60))])]
    for text, bounds in ((fact.raw_value,loc.amount_bbox), (loc.row_label,loc.row_label_bbox),
                         (loc.unit_header,loc.unit_bbox)):
        s = span(text,bounds.model_dump())
        lines.append(dict(line=deepcopy(s),words=[deepcopy(s)]))
    return fact, dict(page=1,width=500,height=700,lines=lines)


def set_date(page, month="Jan", day="26,", year="2025"):
    entry = page["lines"][0]
    for word,text in zip(entry["words"],(month,day,year)):
        word["text"] = text
    entry["line"]["text"] = " ".join((month,day,year))


class DateEvidenceTests(unittest.TestCase):
    def evaluate(self, fact, raw):
        page = WordPage.model_validate(raw)
        before = page.model_dump(mode="json")
        legacy = crosscheck_word_fact(fact,page)
        result = crosscheck_date_fact(fact,page)
        self.assertEqual([c for c in legacy["checks"] if c["field"]!="year"],
                         [c for c in result["checks"] if c["field"]!="year"])
        self.assertEqual(result["date_association"]["legacy_year"], legacy["checks"][1])
        self.assertEqual(page.model_dump(mode="json"),before)
        self.assertFalse(result["automatic_approval"])
        return result

    def rejected(self, fact, raw):
        result = self.evaluate(fact,raw)
        self.assertFalse(result["date_association"]["applied"])
        self.assertNotEqual(result["status"],"consistent")
        self.assertEqual(result["checks"][1],result["date_association"]["legacy_year"])
        return result

    def test_complete_date_explains_day_without_changing_other_fields(self):
        fact,raw = controlled()
        result = self.evaluate(fact,raw)
        self.assertEqual(result["date_rule_version"],DATE_RULE_VERSION)
        self.assertEqual(result["status"],"consistent")
        self.assertTrue(result["date_association"]["applied"])
        self.assertEqual(result["date_association"]["parsed_date"],"2025-01-26")
        self.assertEqual(result["date_association"]["year_word_index"],2)
        self.assertEqual(len(result["date_association"]["consumed_guard_observations"]),1)

    def test_calendar_and_full_month_names_are_not_specific_to_one_date(self):
        for month,day,year,expected in (("February","29,","2024","2024-02-29"),
                                        ("Dec","31,","2026","2026-12-31")):
            with self.subTest(expected=expected):
                fact,raw=controlled();set_date(raw,month=month,day=day,year=year)
                values=fact.model_dump(mode="json")
                values["period_end"]=expected
                values["pdf_location"].update(year=int(year),period_header=f"{month} {day} {year}")
                fact=Fact.model_validate(values)
                self.assertTrue(self.evaluate(fact,raw)["date_association"]["applied"])

    def test_already_consistent_year_is_not_rewritten(self):
        fact,raw=controlled()
        raw["lines"][0]["words"][1]["bbox"].update(x0=130,x1=145)
        result=self.evaluate(fact,raw)
        self.assertEqual(result["status"],"consistent")
        self.assertFalse(result["date_association"]["applied"])
        self.assertEqual(result["checks"][1],result["date_association"]["legacy_year"])

    def test_wrong_day_and_year_do_not_pass(self):
        for day,year in (("27,","2025"),("26,","2024")):
            with self.subTest(day=day,year=year):
                fact,raw=controlled();set_date(raw,day=day,year=year)
                result=self.rejected(fact,raw)
                if year=="2024":
                    self.assertEqual(result["date_association"]["reason"],"existing_year_conflict")

    def test_reference_header_cannot_override_period_end(self):
        fact,raw=controlled();set_date(raw,day="27,")
        fact=fact.model_copy(update={"pdf_location":fact.pdf_location.model_copy(update={"period_header":"Jan 27, 2025"})})
        self.assertEqual(self.rejected(fact,raw)["date_association"]["reason"],"complete_date_differs_from_period_end")

    def test_invalid_calendar_date_is_rejected_even_with_matching_reference_text(self):
        fact,raw=controlled();set_date(raw,month="Feb",day="30,")
        fact=fact.model_copy(update={"pdf_location":fact.pdf_location.model_copy(update={"period_header":"Feb 30, 2025"})})
        self.assertEqual(self.rejected(fact,raw)["date_association"]["reason"],"unsupported_or_invalid_complete_date")

    def test_missing_year_and_year_only_are_not_complete_dates(self):
        for text,indices in (("Jan 26,",(0,1)),("2025",(2,))):
            with self.subTest(text=text):
                fact,raw=controlled();e=raw["lines"][0]
                e["line"]["text"]=text;e["words"]=[e["words"][i] for i in indices]
                self.rejected(fact,raw)

    def test_low_parent_confidence_cannot_be_replaced_by_native_token_scores(self):
        fact,raw=controlled();raw["lines"][0]["line"]["confidence"]=.3
        self.rejected(fact,raw)

    def test_lost_word_or_punctuation_cannot_be_recovered_from_expected_date(self):
        for mutation in ("missing_day","missing_comma","empty_words"):
            with self.subTest(mutation=mutation):
                fact,raw=controlled();e=raw["lines"][0]
                if mutation=="missing_day":e["words"].pop(1)
                elif mutation=="missing_comma":e["words"][1]["text"]="26"
                else:e["words"]=[]
                self.rejected(fact,raw)

    def test_two_dates_and_duplicate_parent_are_not_single_candidates(self):
        for duplicate in (False,True):
            with self.subTest(duplicate=duplicate):
                fact,raw=controlled();e=raw["lines"][0]
                if duplicate:raw["lines"].append(deepcopy(e))
                else:
                    e["line"]["text"]+=" Jan 26, 2025"
                    e["words"]+=deepcopy(e["words"])
                self.rejected(fact,raw)

    def test_matching_date_in_next_column_does_not_replace_selected_wrong_date(self):
        fact,raw=controlled();other=deepcopy(raw["lines"][0]);set_date(raw,day="27,")
        for s in [other["line"],*other["words"]]:
            s["bbox"]["x0"]+=100;s["bbox"]["x1"]+=100
        raw["lines"].append(other)
        self.rejected(fact,raw)

    def test_same_year_date_and_amount_must_share_the_registered_column(self):
        fact,raw=controlled()
        shifted=box(255,100,280,110)
        values=fact.model_dump(mode="json")
        values["pdf_location"]["amount_bbox"]=shifted
        fact=Fact.model_validate(values)
        raw["lines"][1]["line"]["bbox"]=shifted
        raw["lines"][1]["words"][0]["bbox"]=shifted
        self.assertEqual(self.rejected(fact,raw)["date_association"]["reason"],"date_and_amount_are_not_in_the_same_column")

    def test_year_reference_must_be_inside_date_and_words_inside_parent(self):
        fact,raw=controlled()
        values=fact.model_dump(mode="json")
        values["pdf_location"]["year_bbox"]["x1"]=181
        self.assertEqual(self.rejected(Fact.model_validate(values),raw)["date_association"]["reason"],
                         "date_and_amount_are_not_in_the_same_column")
        fact,raw=controlled()
        raw["lines"][0]["words"][2]["bbox"]["bottom"]=61
        self.assertEqual(self.rejected(fact,raw)["date_association"]["reason"],"date_word_outside_parent")

    def test_invalid_confidence_threshold_is_rejected(self):
        fact,raw=controlled()
        for threshold in (-.1,1.1,float("nan")):
            with self.subTest(threshold=threshold),self.assertRaises(ValueError):
                crosscheck_date_fact(fact,WordPage.model_validate(raw),min_confidence=threshold)

    def test_unrelated_nearby_number_and_missing_word_parent_remain_blocking(self):
        for words in (True,False):
            with self.subTest(words=words):
                fact,raw=controlled();extra=span("7",box(182,50,185,60))
                raw["lines"].append(dict(line=extra,words=[deepcopy(extra)] if words else []))
                result=self.evaluate(fact,raw)
                # A parent just outside the year is still a nearby numeric
                # hazard, even when its word projection is missing.
                self.assertFalse(result["date_association"]["applied"])

    def test_no_substring_or_extra_suffix_is_accepted(self):
        for suffix in (" 7","%"," Jan 27, 2025"):
            with self.subTest(suffix=suffix):
                fact,raw=controlled();e=raw["lines"][0]
                e["line"]["text"]+=suffix;e["words"][-1]["text"]+=suffix
                self.rejected(fact,raw)

    def test_amount_sign_and_numeric_guards_are_unchanged(self):
        for lost in (False,True):
            with self.subTest(lost=lost):
                fact,raw=controlled()
                fact=Fact.model_validate({**fact.model_dump(),"id":"capex-2025","metric":"capex",
                    "period_start":"2024-01-29","raw_value":"(1,234)","transformation":"absolute_cash_outflow"})
                e=raw["lines"][1]
                e["line"]["text"]="(1,234)";e["words"][0]["text"]="1,234" if lost else "(1,234)"
                if not lost:
                    extra=span("-",box(182,100,185,110))
                    raw["lines"].append(dict(line=extra,words=[deepcopy(extra)]))
                result=self.evaluate(fact,raw)
                self.assertTrue(result["date_association"]["applied"])
                self.assertNotEqual(result["checks"][0]["status"],"consistent")
                self.assertNotEqual(result["status"],"consistent")

    def test_known_nvidia_v2_fragments_are_an_observed_diagnostic_only(self):
        pack=TransferPack.model_validate_json((ROOT/"data/examples/nvda-fy2025-transfer-v2.json").read_text(encoding="utf-8"))
        report=json.loads((ROOT/"artifacts/document-transfer-report.json").read_text(encoding="utf-8"))
        pages={p["page"]:WordPage.model_validate(dict(page=p["page"],width=p["width"],height=p["height"],
            lines=[{k:e[k] for k in ("line","words")} for e in p["evidence_lines"]])) for p in report["documents"][0]["pages"]}
        for fact in pack.facts:
            with self.subTest(fact=fact.id):
                result=self.evaluate(fact,pages[fact.pdf_location.page].model_dump(mode="json"))
                self.assertTrue(result["date_association"]["applied"])
                self.assertEqual(result["checks"][1]["status"],"consistent")


if __name__ == "__main__":
    unittest.main()
