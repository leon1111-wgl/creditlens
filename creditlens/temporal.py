"""Disclosure-date-aware quarterly observations, without forecasting or imputation."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from creditlens.diligence import Fact, Source, StrictModel, digest


METRICS = ("cfo", "capex", "net_income")


def month_start(start: date, offset: int) -> date:
    index = start.year * 12 + start.month - 1 + offset
    return date(index // 12, index % 12 + 1, 1)


class TemporalSource(Source):
    retrieved_at: AwareDatetime


class TemporalFact(Fact):
    metric: Literal["cfo", "capex", "net_income"]
    observation_kind: Literal["reported"] = "reported"
    basis_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")


class TemporalPack(StrictModel):
    schema_version: Literal[1] = 1
    entity_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,50}$")
    company: str = Field(min_length=1, max_length=200)
    fiscal_year_start: date
    fiscal_calendar: Literal["calendar_month_quarters"]
    sources: list[TemporalSource] = Field(min_length=1, max_length=100)
    facts: list[TemporalFact] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def references_and_periods(self):
        if self.fiscal_year_start.day != 1:
            raise ValueError("This adapter requires fiscal quarters made of three calendar months")
        sources = {s.id: s for s in self.sources}
        if len(sources) != len(self.sources) or len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError("Duplicate source or fact identifier")
        periods = set()
        for quarter in range(1, 5):
            end = month_start(self.fiscal_year_start, quarter * 3) - timedelta(days=1)
            periods.add((month_start(self.fiscal_year_start, (quarter - 1) * 3), end))
            periods.add((self.fiscal_year_start, end))
        for fact in self.facts:
            if fact.source_id not in sources:
                raise ValueError("Unknown source")
            if (fact.period_start, fact.period_end) not in periods:
                raise ValueError("Fact is neither an exact fiscal quarter nor fiscal-year-to-date")
            if fact.period_end > sources[fact.source_id].published_at:
                raise ValueError("Financial period ends after its disclosure")
        return self


def quarterly_snapshot(pack: TemporalPack, as_of: date) -> dict:
    """Day-resolution cutoff; publication day itself is conservatively excluded.

    Website captures are not historical archives. A cumulative difference is a
    candidate requiring review of restatements and scope, never a reported fact.
    """
    sources = {s.id: s for s in pack.sources
               if s.published_at + timedelta(days=1) <= as_of}
    visible = [f for f in pack.facts if f.source_id in sources]
    rows = []
    for quarter in range(1, 5):
        start = month_start(pack.fiscal_year_start, (quarter - 1) * 3)
        end = month_start(pack.fiscal_year_start, quarter * 3) - timedelta(days=1)
        for metric in METRICS:
            direct = [f for f in visible if f.metric == metric and
                      (f.period_start, f.period_end) == (start, end)]
            cumulative = [f for f in visible if f.metric == metric and
                          (f.period_start, f.period_end) == (pack.fiscal_year_start, end)]
            previous = [f for f in visible if f.metric == metric and
                        (f.period_start, f.period_end) == (pack.fiscal_year_start, start - timedelta(days=1))]
            evidence = list({f.id: f for f in direct + cumulative + previous}.values())
            row = {"quarter": quarter, "metric": metric, "period_start": start.isoformat(),
                   "period_end": end.isoformat(), "value": None, "unit": None,
                   "status": "missing", "eligible_as_observed": False,
                   "fact_ids": [f.id for f in evidence], "formula": None, "checks": []}
            if any(len(group) > 1 for group in (direct, cumulative, previous)):
                row.update(status="blocked", checks=["ambiguous_versions"])
            elif len({(f.unit, f.basis_id) for f in evidence}) > 1:
                row.update(status="blocked", checks=["incompatible_unit_or_basis"])
            else:
                difference = cumulative[0].value - previous[0].value if cumulative and previous else None
                if direct:
                    if difference is not None and direct[0].value != difference:
                        row.update(status="blocked", checks=["quarter_vs_cumulative_mismatch"])
                    else:
                        row.update(value=str(direct[0].value), unit=direct[0].unit,
                                   status="reported", eligible_as_observed=True,
                                   formula=direct[0].id,
                                   checks=["cumulative_difference_matches"] if difference is not None
                                   else ["not_applicable_first_quarter" if quarter == 1 else "cumulative_check_unavailable"])
                elif difference is not None:
                    if metric == "capex" and difference < 0:
                        row.update(status="blocked", checks=["negative_derived_capex"])
                    else:
                        row.update(value=str(difference), unit=cumulative[0].unit,
                                   status="derived_requires_review",
                                   formula=f"{cumulative[0].id} - {previous[0].id}",
                                   checks=["cross_disclosure_restatement_review_required"])
                else:
                    row["checks"] = ["missing_quarter_or_cumulative_operand"]
            if row["value"] is not None:
                row["available_from"] = max(sources[f.source_id].published_at + timedelta(days=1)
                                            for f in evidence).isoformat()
            rows.append(row)
    evidence_payload = {"facts": [f.model_dump(mode="json") for f in visible],
                        "sources": [s.model_dump(mode="json") for s in sources.values()]}
    return {"entity_id": pack.entity_id, "as_of": as_of.isoformat(),
            "availability_policy": "publication_date_plus_one_calendar_day",
            "snapshot_sha256": digest(evidence_payload), "quarters": rows,
            "evidence": evidence_payload, "usable_for_prediction_training": False,
            "limitations": ["Current website captures do not establish historical vintage integrity.",
                            "Derived values need restatement and scope review; no automatic approval.",
                            "Single fiscal year and issuer; no outcome labels or model evaluation.",
                            "No estimates or missing-value imputation; no 52/53-week calendar support."]}
