"""Tenant-scoped reference observations; workflow agreement is not human gold."""
from hashlib import sha256
import json
from pathlib import Path

from fastapi import Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from .preparation_routes import body
from .reference_contract import build_reference_catalog
from .visual_store import VisualArtifactError, VisualStore
from .workflow import Actor


def install_reference_routes(app, actor, root: Path):
    @app.get('/reference-review', include_in_schema=False)
    def workspace():
        return FileResponse(root / 'creditlens/reference-review.html')

    @app.get('/api/reference-review/catalog')
    def catalog(identity: Actor = Depends(actor)):
        try:
            value = build_reference_catalog(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Reference task registration is unavailable') from exc
        return {'catalog': value, 'identity': {'user_id': identity.user_id, 'role': identity.role},
                'auth_mode': app.state.access.mode, 'human_gold': False}

    @app.get('/api/reference-review/reviews')
    def reviews(identity: Actor = Depends(actor)):
        return app.state.reference_reviews.list(identity)

    @app.post('/api/reference-review/reviews', status_code=201)
    async def create(request: Request, identity: Actor = Depends(actor),
                     idempotency_key: str = Header(alias='Idempotency-Key')):
        payload = await body(request)
        return await run_in_threadpool(app.state.reference_reviews.create, identity, payload, idempotency_key)

    @app.get('/api/reference-review/reviews/{review_id}')
    def get(review_id: str, identity: Actor = Depends(actor)):
        return app.state.reference_reviews.get(review_id, identity)

    @app.post('/api/reference-review/reviews/{review_id}/observations')
    async def submit(review_id: str, request: Request, identity: Actor = Depends(actor),
                     idempotency_key: str = Header(alias='Idempotency-Key')):
        value = await body(request)
        if set(value) != {'revision', 'observation'} or type(value['revision']) is not int or value['revision'] < 1:
            raise HTTPException(422, 'Expected revision and observation')
        return await run_in_threadpool(app.state.reference_reviews.submit, review_id, identity,
                                       value['revision'], value['observation'], idempotency_key)

    async def action(review_id, request, identity, method):
        value = await body(request)
        if set(value) != {'revision', 'note'} or type(value['revision']) is not int or value['revision'] < 1:
            raise HTTPException(422, 'Expected revision and note')
        return await run_in_threadpool(method, review_id, identity, value['revision'], value['note'])

    @app.post('/api/reference-review/reviews/{review_id}/freeze')
    async def freeze(review_id: str, request: Request, identity: Actor = Depends(actor)):
        return await action(review_id, request, identity, app.state.reference_reviews.freeze)

    @app.post('/api/reference-review/reviews/{review_id}/close')
    async def close(review_id: str, request: Request, identity: Actor = Depends(actor)):
        return await action(review_id, request, identity, app.state.reference_reviews.close)

    @app.get('/api/reference-review/reviews/{review_id}/export')
    def export(review_id: str, identity: Actor = Depends(actor)):
        value = app.state.reference_reviews.export(review_id, identity)
        return Response(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False),
                        media_type='application/json', headers={'Content-Disposition': 'attachment; filename="creditlens-reference-candidate.json"'})

    @app.get('/api/reference-review/reviews/{review_id}/page.png')
    def page(review_id: str, expected_task_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity: Actor = Depends(actor)):
        review = app.state.reference_reviews.get(review_id, identity)
        if review['task_sha256'] != expected_task_sha256:
            raise HTTPException(409, 'Task changed; reload the saved review')
        task = review['task']
        if task['doc_id'] not in {'msft', 'aapl'}:
            raise HTTPException(404, 'Registered local page not found')
        try:
            raw = VisualStore(root).image(task['doc_id'], task['page'])
            if sha256(raw).hexdigest() != task['source']['image_sha256']:
                raise VisualArtifactError('Page does not match this task snapshot')
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Local page unavailable; use the official source') from exc
        except (ValueError, OSError, KeyError) as exc:
            raise HTTPException(409, 'Page failed source validation') from exc
        return Response(raw, media_type='image/png')
