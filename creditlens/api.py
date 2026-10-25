"""Run locally: python -m uvicorn creditlens.api:app --host 127.0.0.1."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import date
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request, Depends, Header
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .service import ResearchService
from .diligence import CasePack, RULE_VERSION
from . import __version__
from .workflow import AccessControl, Actor, WorkflowService, render_memo
from .planner import LocalPlanner
from .temporal import TemporalPack
from .research import research_view
from .visual_store import VisualStore, VisualArtifactError
from .experiments import experiment_view
from .transfer_store import transfer_snapshot, transfer_image

ROOT = Path(__file__).resolve().parents[1]


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["add", "subtract", "multiply", "divide"]
    left: str = Field(max_length=100)
    right: str = Field(max_length=100)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    as_of: date | None = None
    steps: list[Step] | None = Field(default=None, min_length=1, max_length=12)


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    as_of: date | None = None


class ExampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: date = date(2025, 8, 1)
    stress_pct: int = Field(default=20, ge=0, le=80)


class CreateReview(ExampleRequest):
    pack: CasePack


class AppleExampleRequest(ExampleRequest):
    as_of: date = date(2025, 11, 1)


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    action: Literal["disposition", "submit", "approve", "return"]
    issue_id: str | None = Field(default=None, max_length=100)
    disposition: Literal["verified", "accepted_limitation"] | None = None
    note: str = Field(default="", max_length=3000)

    @model_validator(mode="after")
    def meaningful_review(self):
        self.note = self.note.strip()
        if self.action != "submit" and len(self.note) < 10:
            raise ValueError("Provide at least 10 characters explaining your review")
        if self.action == "disposition" and (not self.issue_id or not self.disposition):
            raise ValueError("Issue ID and disposition are required")
        return self


def create_app(dataset: Path | None = None, audit_db: Path | None = None, *,
               auth_mode: str | None = None, principals: list | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        path = dataset or Path(os.getenv("CREDITLENS_DATASET", str(ROOT / "data/raw/finqa-dev.json")))
        db_path = audit_db or Path(os.getenv("CREDITLENS_STATE_DIR", str(ROOT / "data"))) / "audit.sqlite"
        application.state.service = ResearchService(path, db_path) if path.exists() else None
        application.state.access = AccessControl(auth_mode, principals)
        application.state.workflow = WorkflowService(db_path.with_name("workflow.sqlite"))
        yield

    application = FastAPI(title="CreditLens", version=__version__,
                          description="Financial research workbench, source-linked review and benchmark laboratory.",
                          lifespan=lifespan)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @application.middleware("http")
    async def bounded_request(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            from urllib.parse import urlsplit
            origin = request.headers.get("Origin")
            if origin and (urlsplit(origin).netloc != request.headers.get("Host")
                           or urlsplit(origin).scheme not in {"http", "https"}):
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
            if len(await request.body()) > 1_000_000:
                return JSONResponse({"detail": "Request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def actor(request: Request) -> Actor:
        return application.state.access.resolve(request)

    def laboratory():
        if application.state.access.mode != "demo":
            raise HTTPException(403, "The public benchmark laboratory is disabled in API-key mode")
        if application.state.service is None:
            raise HTTPException(503, "FinQA dataset missing; run scripts/download_data.py")
        return application.state.service

    @application.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "creditlens/review.html")

    @application.get("/lab", include_in_schema=False)
    def lab():
        return FileResponse(ROOT / "creditlens/workbench.html")

    @application.get("/research", include_in_schema=False)
    def research():
        return FileResponse(ROOT / "creditlens/research.html")

    @application.get("/documents", include_in_schema=False)
    def document_workspace():
        return FileResponse(ROOT / "creditlens/documents.html")

    @application.get("/experiments", include_in_schema=False)
    def experiment_workspace():
        return FileResponse(ROOT / "creditlens/experiments.html")

    @application.get("/transfer", include_in_schema=False)
    def transfer_workspace():
        return FileResponse(ROOT / "creditlens/transfer.html")

    @application.get("/api/document-transfer")
    def transfer_records(identity: Actor = Depends(actor)):
        try:
            return transfer_snapshot(ROOT)
        except FileNotFoundError as exc:
            raise HTTPException(503, "Transfer experiment files are unavailable") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, "Transfer experiment failed validation") from exc

    @application.get("/api/document-transfer/{document_id}/pages/{page}.png")
    def transfer_page(document_id: str, page: int,
                      expected_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
                      identity: Actor = Depends(actor)):
        try:
            return Response(transfer_image(ROOT, document_id, page, expected_sha256=expected_sha256), media_type="image/png")
        except FileNotFoundError as exc:
            raise HTTPException(404, "Registered transfer image is unavailable") from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(409, "Transfer image failed validation") from exc

    @application.get("/api/experiments")
    def experiment_records(identity: Actor = Depends(actor)):
        try:
            return experiment_view(ROOT)
        except FileNotFoundError as exc:
            raise HTTPException(503, "Frozen experiment files are unavailable") from exc
        except VisualArtifactError as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.get("/api/documents")
    def document_evidence(identity: Actor = Depends(actor)):
        try:
            return VisualStore(ROOT).view()
        except FileNotFoundError as exc:
            raise HTTPException(503, str(exc)) from exc
        except VisualArtifactError as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.get("/api/documents/{document_id}/pages/{page}.png")
    def document_image(document_id: str, page: int, identity: Actor = Depends(actor)):
        try:
            return Response(VisualStore(ROOT).image(document_id, page), media_type="image/png")
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except VisualArtifactError as exc:
            raise HTTPException(409, str(exc)) from exc

    @application.get("/api/research/quarters")
    def research_quarters(identity: Actor = Depends(actor), as_of: date = date(2025, 7, 31),
                          dataset: Literal["msft-fy2025"] = "msft-fy2025"):
        # Registered public examples only. No user path, private case or tenant data.
        pack = TemporalPack.model_validate_json(
            (ROOT / "data/examples/msft-fy2025-temporal.json").read_text(encoding="utf-8"))
        return research_view(pack, as_of)

    @application.get("/health")
    def health():
        svc = application.state.service
        return {"status": "ok", "version": __version__, "rule_version": RULE_VERSION, "cases": len(svc.cases) if svc else 0,
                "mode": "public_benchmark_local", "auth_mode": application.state.access.mode,
                "dataset_sha256": svc.dataset_sha256 if svc else None}

    @application.get("/api/session")
    def session(identity: Actor = Depends(actor)):
        return {"user_id": identity.user_id, "tenant_id": identity.tenant_id, "role": identity.role,
                "mode": application.state.access.mode}

    @application.get("/api/reviews")
    def reviews(identity: Actor = Depends(actor)):
        return application.state.workflow.list(identity)

    @application.get("/api/example-pack")
    def example_pack(identity: Actor = Depends(actor), format: Literal["html", "pdf", "apple-pdf"] = "html"):
        filename = {"html": "msft-fy2025.json", "pdf": "msft-fy2025-pdf.json", "apple-pdf": "aapl-fy2025-pdf.json"}[format]
        return json.loads((ROOT / "data/examples" / filename).read_text(encoding="utf-8"))

    @application.post("/api/reviews/example")
    def example_review(body: ExampleRequest, identity: Actor = Depends(actor),
                       request_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100)):
        pack = CasePack.model_validate_json((ROOT / "data/examples/msft-fy2025.json").read_text(encoding="utf-8"))
        return application.state.workflow.create(identity, pack, body.as_of, body.stress_pct, request_key,
                                                 "official_tables_source_specific_adapter")

    @application.post("/api/reviews/example-pdf")
    def pdf_example_review(body: ExampleRequest, identity: Actor = Depends(actor),
                           request_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100)):
        pack = CasePack.model_validate_json((ROOT / "data/examples/msft-fy2025-pdf.json").read_text(encoding="utf-8"))
        return application.state.workflow.create(identity, pack, body.as_of, body.stress_pct, request_key,
                                                 "official_pdf_source_specific_adapter")

    @application.post("/api/reviews")
    def create_review(body: CreateReview, identity: Actor = Depends(actor),
                      request_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100)):
        return application.state.workflow.create(identity, body.pack, body.as_of, body.stress_pct, request_key)

    @application.post("/api/reviews/example-apple")
    def apple_example_review(body: AppleExampleRequest, identity: Actor = Depends(actor),
                             request_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=100)):
        pack = CasePack.model_validate_json((ROOT / "data/examples/aapl-fy2025-pdf.json").read_text(encoding="utf-8"))
        return application.state.workflow.create(identity, pack, body.as_of, body.stress_pct, request_key,
                                                 "official_pdf_source_specific_adapter")

    @application.get("/api/reviews/{case_id}")
    def review_case(case_id: str, identity: Actor = Depends(actor)):
        return application.state.workflow.get(case_id, identity)

    @application.post("/api/reviews/{case_id}/actions")
    def action(case_id: str, body: ReviewUpdate, identity: Actor = Depends(actor)):
        return application.state.workflow.update(case_id, identity, **body.model_dump())

    @application.get("/api/reviews/{case_id}/memo", response_class=PlainTextResponse)
    def memo(case_id: str, identity: Actor = Depends(actor)):
        return PlainTextResponse(render_memo(application.state.workflow.get(case_id, identity)),
                                 headers={"Content-Disposition": 'attachment; filename="credit-review.md"'})

    @application.get("/api/cases")
    def cases(q: str = Query(default="", max_length=200), limit: int = Query(default=20, ge=1, le=100)):
        return laboratory().list_cases(q, limit)

    @application.post("/api/analyze")
    def analyze(request: AnalysisRequest):
        try:
            return laboratory().analyze(
                request.case_id, request.question, k=request.k,
                as_of=request.as_of.isoformat() if request.as_of else None,
                steps=[s.model_dump() for s in request.steps] if request.steps else None)
        except KeyError:
            raise HTTPException(status_code=404, detail="Case not found")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @application.get("/api/reports/{report_id}")
    def report(report_id: str):
        payload = laboratory().get_report(report_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Report not found")
        return payload

    @application.post("/api/agent/analyze")
    def model_plan(body: AgentRequest, identity: Actor = Depends(actor)):
        from .workflow import require
        import httpx
        require(identity, "analyst")
        svc = laboratory()
        if body.case_id not in svc.cases:
            raise HTTPException(404, "Case not found")
        try:
            planner = LocalPlanner.configured()
            return planner.run(svc.cases[body.case_id], body.question, k=body.k,
                               as_of=body.as_of.isoformat() if body.as_of else None)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            raise HTTPException(502, "Local model response failed transport, schema or evidence validation; no plan was accepted")

    return application


app = create_app()
