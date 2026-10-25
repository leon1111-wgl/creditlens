"""Read-only selection of registered cross-page financial evidence."""
from pathlib import Path

from fastapi import Depends, HTTPException, Request, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .cross_page_evidence import CrossPageEvidenceError, evaluate_evidence_selection
from .cross_page_materials import build_cross_page_materials
from .diligence import digest
from .preparation_routes import body
from .workflow import Actor
from .visual_store import VisualStore, VisualArtifactError


class EvidenceSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    task_id: str = Field(min_length=1, max_length=200)
    selected_ids: list[str] = Field(max_length=20)
    expected_catalog_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def install_cross_page_routes(app, actor, root: Path):
    def registered_catalog():
        try:
            catalog = build_cross_page_materials(root)
            return catalog, digest(catalog)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise HTTPException(409, 'Registered cross-page materials are unavailable or changed') from exc

    @app.get('/cross-page', include_in_schema=False)
    def workspace():
        return FileResponse(root / 'creditlens/cross-page.html')

    @app.get('/api/cross-page-evidence')
    def catalog(identity: Actor = Depends(actor)):
        value, sha = registered_catalog()
        return {'catalog_sha256': sha, 'catalog': value}

    @app.get('/api/cross-page-evidence/cards/{card_id}/page.png')
    def page(card_id: str, expected_catalog_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity: Actor = Depends(actor)):
        value, sha = registered_catalog()
        if sha != expected_catalog_sha256:
            raise HTTPException(409, 'Materials changed; reload the catalog before checking')
        card = next((item for item in value['cards'] if item['id'] == card_id), None)
        if card is None or card['role'] == 'consolidation_scope':
            raise HTTPException(404, 'No registered local page preview for this evidence')
        document_id = {'MSFT': 'msft', 'AAPL': 'aapl'}.get(card['issuer_id'])
        if document_id is None:
            raise HTTPException(404, 'Registered document not found')
        try:
            image = VisualStore(root).image(document_id, card['location']['page'])
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Local page is unavailable; use the official source link') from exc
        except (VisualArtifactError, ValueError, KeyError) as exc:
            raise HTTPException(409, 'Page preview failed source validation') from exc
        return Response(image, media_type='image/png')

    @app.post('/api/cross-page-evidence/check')
    async def check(request: Request, identity: Actor = Depends(actor)):
        try:
            selection = EvidenceSelection.model_validate(await body(request))
        except ValidationError as exc:
            raise HTTPException(422, 'Expected task ID, bounded evidence IDs and current catalog digest') from exc
        if any(not item or len(item) > 256 for item in selection.selected_ids):
            raise HTTPException(422, 'Evidence IDs must be nonempty bounded strings')
        # No user-submitted amount, source, path or status is treated as a fact.
        value, sha = await run_in_threadpool(registered_catalog)
        if sha != selection.expected_catalog_sha256:
            raise HTTPException(409, 'Materials changed; reload the catalog before checking')
        task = next((item for item in value['tasks'] if item['id'] == selection.task_id), None)
        if task is None:
            raise HTTPException(404, 'Registered task not found')
        try:
            result = await run_in_threadpool(evaluate_evidence_selection, task, value['cards'], selection.selected_ids)
        except CrossPageEvidenceError as exc:
            raise HTTPException(422, {'code': exc.code, 'path': exc.path}) from exc
        return {'schema_version': 1, 'catalog_sha256': sha, 'task': task,
                'result': result, 'operation': 'read_only_evidence_selection',
                'saved_to_research_task': False, 'automatic_approval': False}
