"""Label-free evidence ingestion, BM25 retrieval and bounded arithmetic.

This module deliberately has no access to gold answers, programs or retrieval labels.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import math
import re
from typing import Any

SOURCE_REV = "0f16e2867befa6840783e58be38c9efb9229d742"
SOURCE_URL = f"https://github.com/czyssrs/FinQA/blob/{SOURCE_REV}/dataset/dev.json"
DATASET_SHA256 = "a847fb7e0d61a3125a1e2909852df6b89f1ee64d2c5ff1bf689e332214deee51"
STOP = set("a an the of to in on for and or is are was were what how does did with by from as".split())


@dataclass(frozen=True)
class Evidence:
    id: str
    case_id: str
    locator: str
    kind: str
    text: str
    source_url: str
    report_ref: str
    cells: tuple[str, ...] = ()
    published_at: str | None = None
    tenant_id: str = "public"

    def __post_init__(self):
        if self.published_at is not None:
            date.fromisoformat(self.published_at)

    @property
    def sha256(self) -> str:
        payload = asdict(self)
        import json
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def to_dict(self) -> dict:
        return {**asdict(self), "cells": list(self.cells), "sha256": self.sha256}


@dataclass(frozen=True)
class ResearchCase:
    id: str
    report_ref: str
    question: str
    evidence: tuple[Evidence, ...]


def ingest_finqa(record: dict, source_url: str = SOURCE_URL) -> ResearchCase:
    """Explicit allowlist: NEVER ingest model_input, gold_inds or retrieved_*."""
    case_id = str(record["id"])
    report = str(record.get("filename", case_id.rsplit("-", 1)[0]))
    blocks = []
    # A report financial year is not a publication timestamp.
    common = dict(case_id=case_id, source_url=source_url, report_ref=report)
    for i, value in enumerate(record.get("pre_text", []) + record.get("post_text", [])):
        blocks.append(Evidence(id=f"text_{i}", locator=f"{report}#text_{i}",
                               kind="text", text=str(value), **common))
    table = record.get("table", [])
    headers = table[0] if table else []
    for i, row in enumerate(table):
        cells = tuple(str(c) for c in row)
        # The SAME formatter is used for every row, independently of labels.
        if i == 0:
            text = " | ".join(cells)
        else:
            parts = [f"{headers[j] if j < len(headers) else 'column_' + str(j)}: {v}"
                     for j, v in enumerate(cells[1:], 1)]
            text = f"{cells[0] if cells else ''} | " + " | ".join(parts)
        blocks.append(Evidence(id=f"table_{i}", locator=f"{report}#table_{i}",
                               kind="table", text=text, cells=cells, **common))
    return ResearchCase(case_id, report, str(record.get("qa", {}).get("question", "")),
                        tuple(blocks))


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?|[\u4e00-\u9fff]", text.lower())
    return [t for t in tokens if t not in STOP]


def eligible(evidence: Evidence, tenant_id: str, as_of: str | None) -> bool:
    if evidence.tenant_id not in ("public", tenant_id):
        return False
    if as_of is None:
        return True
    cutoff = date.fromisoformat(as_of)
    return evidence.published_at is not None and date.fromisoformat(evidence.published_at) <= cutoff


def retrieve(case: ResearchCase, question: str, *, k: int = 5,
             tenant_id: str = "public", as_of: str | None = None) -> list[dict]:
    if not 1 <= k <= 20:
        raise ValueError("k must be between 1 and 20")
    if as_of is not None:
        date.fromisoformat(as_of)
    documents = [e for e in case.evidence if eligible(e, tenant_id, as_of)]
    if not documents:
        return []
    term_counts = [Counter(tokenize(e.text)) for e in documents]
    lengths = [sum(c.values()) for c in term_counts]
    average = sum(lengths) / len(lengths) or 1.0
    dfs = Counter(t for c in term_counts for t in c)
    n = len(documents)
    query = set(tokenize(question))
    ranked = []
    for ev, counts, length in zip(documents, term_counts, lengths):
        score = 0.0
        for token in query:
            tf = counts[token]
            if tf:
                idf = math.log(1 + (n - dfs[token] + 0.5) / (dfs[token] + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * length / average))
        if score > 0:
            ranked.append({"evidence": ev.to_dict(), "score": score})
    ranked.sort(key=lambda x: (-x["score"], x["evidence"]["id"]))
    return ranked[:k]


def parse_number(raw: str) -> Decimal:
    """Parse displayed magnitudes. Percentages become ratios; scale stays explicit."""
    value = raw.strip().replace(",", "").replace(" ", "").replace("−", "-")
    for symbol in "$€£¥":
        value = value.replace(symbol, "")
    negative = value.startswith("(") and value.endswith(")")
    if negative:
        value = "-" + value[1:-1]
    percent = value.endswith("%")
    if percent:
        value = value[:-1]
    if len(value) > 80 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", value):
        raise ValueError(f"Cell is not an unambiguous numeric value: {raw!r}")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric cell") from exc
    if not result.is_finite():
        raise ValueError("Non-finite number")
    return result / 100 if percent else result


def calculate(steps: list[dict[str, str]], evidence: list[Evidence],
              *, tenant_id: str = "public", as_of: str | None = None) -> dict[str, Any]:
    """Only source cell refs, earlier steps and explicit constants are accepted.

    References: cell:table_3:1, step:0, const:100.
    Dimensional/financial interpretation is NOT automatically certified.
    """
    if not 1 <= len(steps) <= 12:
        raise ValueError("Expected 1 to 12 arithmetic steps")
    if as_of:
        date.fromisoformat(as_of)
    case_ids = {e.case_id for e in evidence}
    if len(case_ids) > 1:
        raise ValueError("Cross-case arithmetic is not supported in this version")
    lookup = {e.id: e for e in evidence if eligible(e, tenant_id, as_of)}
    values: list[Decimal] = []
    trace = []
    used: dict[str, Evidence] = {}

    def resolve(ref: str) -> Decimal:
        if ref.startswith("cell:"):
            bits = ref.split(":")
            if len(bits) != 3 or not bits[2].isdigit():
                raise ValueError("Malformed cell reference")
            ev = lookup.get(bits[1])
            if ev is None or ev.kind != "table":
                raise ValueError("Unavailable source evidence")
            column = int(bits[2])
            if column < 1 or column >= len(ev.cells):
                raise ValueError("Invalid numeric column")
            used[ev.id] = ev
            return parse_number(ev.cells[column])
        if ref.startswith("step:"):
            index = ref[5:]
            if not index.isdigit() or int(index) >= len(values):
                raise ValueError("Reference must point to an earlier step")
            return values[int(index)]
        if ref in {"const:1", "const:100", "const:1000"}:
            return Decimal(ref.split(":")[1])
        raise ValueError("Use source cell references, previous steps or allowed constants")

    with localcontext() as ctx:
        ctx.prec = 28
        for i, step in enumerate(steps):
            if set(step) != {"op", "left", "right"}:
                raise ValueError("Step must contain exactly op, left and right")
            op = step["op"]
            if op not in {"add", "subtract", "multiply", "divide"}:
                raise ValueError("Unsupported operation")
            left, right = resolve(step["left"]), resolve(step["right"])
            if op == "divide" and right == 0:
                raise ValueError("Division by zero")
            value = {"add": lambda: left + right, "subtract": lambda: left - right,
                     "multiply": lambda: left * right, "divide": lambda: left / right}[op]()
            if not value.is_finite() or abs(value) > Decimal("1e50"):
                raise ValueError("Arithmetic result outside supported range")
            values.append(value)
            trace.append({"step": i, **step, "left_value": str(left),
                          "right_value": str(right), "result": str(value)})
    if not used:
        raise ValueError("At least one source-backed cell is required")
    return {"value": str(values[-1]), "trace": trace,
            "citations": [e.to_dict() for e in used.values()],
            "verification": "arithmetic_recomputed",
            "limitations": ["Formula selection and financial units require analyst review."]}
