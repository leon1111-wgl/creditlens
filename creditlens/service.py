"""Local research service. Public benchmark data only; no production IAM."""
from __future__ import annotations
from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .core import calculate, ingest_finqa, retrieve, SOURCE_URL, DATASET_SHA256


class ResearchService:
    def __init__(self, dataset: Path, audit_db: Path):
        self.dataset = Path(dataset)
        self.dataset_sha256 = hashlib.sha256(self.dataset.read_bytes()).hexdigest()
        self.source_url = SOURCE_URL if self.dataset_sha256 == DATASET_SHA256 else "urn:sha256:" + self.dataset_sha256
        records = json.loads(self.dataset.read_text(encoding="utf-8"))
        self.cases = {}
        for record in records:
            case = ingest_finqa(record, self.source_url)
            if case.id in self.cases:
                raise ValueError("Duplicate case id")
            self.cases[case.id] = case
        # Gold-bearing records are intentionally not retained.
        audit_db.parent.mkdir(parents=True, exist_ok=True)
        self.audit_db = audit_db
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.audit_db, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_cases(self, query="", limit=20):
        query = query.lower()
        return [{"id": c.id, "question": c.question, "report_ref": c.report_ref}
                for c in self.cases.values()
                if query in (c.id + " " + c.question).lower()][:limit]

    def analyze(self, case_id: str, question: str, *, k: int = 5,
                as_of: str | None = None, steps: list[dict] | None = None) -> dict:
        case = self.cases[case_id]
        hits = retrieve(case, question, k=k, as_of=as_of)
        status = "needs_review" if hits else "insufficient_evidence"
        calculation = None
        if steps:
            selected = {hit["evidence"]["id"] for hit in hits}
            evidence = [ev for ev in case.evidence if ev.id in selected]
            calculation = calculate(steps, evidence, as_of=as_of)
        report = {
            "id": uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "case_id": case_id,
            "question": question,
            "as_of": as_of,
            "status": status,
            "mode": "evidence_and_arithmetic" if steps else "retrieval_only",
            "dataset_sha256": self.dataset_sha256,
            "source_url": self.source_url,
            "retrieval": {"method": "within_page_bm25", "k": k, "hits": hits},
            "calculation": calculation,
            "review_notes": [
                "No language model was called. No automated credit opinion is generated.",
                "Original publication dates are not supplied by this benchmark; strict as-of queries exclude undated blocks.",
                "Citations locate benchmark blocks, not verified original-PDF coordinates.",
            ],
        }
        with self._db() as db:
            db.execute("INSERT INTO reports VALUES (?, ?, ?)",
                       (report["id"], report["created_at"], json.dumps(report, ensure_ascii=False)))
        return report

    def get_report(self, report_id: str) -> dict | None:
        with self._db() as db:
            result = db.execute("SELECT payload FROM reports WHERE id = ?", (report_id,)).fetchone()
        return json.loads(result[0]) if result else None
