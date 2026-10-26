"""Read-only, source-bound requests for explicitly selected table rows."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response

from .row_disambiguation_experiment import load_row_disambiguation_snapshot
from .visual_store import VisualArtifactError, VisualStore
from .workflow import Actor


def install_row_disambiguation_routes(app, actor, root: Path):
    def snapshot():
        try:
            return load_row_disambiguation_snapshot(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Row choices are unavailable or failed source validation') from exc

    def bound_query(query_id, expected):
        saved = snapshot()
        if saved['report_sha256'] != expected:
            raise HTTPException(409, 'Row choice report changed; reload the candidates')
        query = next((q for q in saved['report']['queries'] if q['id'] == query_id), None)
        if query is None:
            raise HTTPException(404, 'Registered row query not found')
        return saved, query

    @app.get('/api/row-disambiguation')
    def report(expected_role_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
               identity: Actor = Depends(actor)):
        saved = snapshot()
        if saved['role_report_sha256'] != expected_role_report_sha256:
            raise HTTPException(409, 'Automatic lookup report changed; reload the experiment')
        return saved

    @app.get('/api/row-disambiguation/queries/{query_id}/selection')
    def selection(query_id: str, row_index: int = Query(ge=0),
                  expected_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
                  identity: Actor = Depends(actor)):
        saved, query = bound_query(query_id, expected_report_sha256)
        path = next((p for p in query['paths'] if p['row_index'] == row_index), None)
        if path is None:
            raise HTTPException(409, 'Selected row is not a candidate for this query')
        return {'schema_version': 1, 'report_sha256': saved['report_sha256'],
                'role_report_sha256': saved['role_report_sha256'],
                'query_id': query_id, 'document_id': query['document_id'], 'page': query['page'],
                'query': deepcopy(query['query']), 'source': deepcopy(query['source']),
                'page_metadata': deepcopy(query['page_metadata']),
                'discovery': deepcopy(query['discovery']), 'result': deepcopy(path['result']),
                'read_only': True, 'persisted': False, 'full_ocr_replayed': False,
                'automatic_approval': False, 'selection_basis': 'explicit_request_for_saved_line_layout_result'}

    @app.get('/api/row-disambiguation/queries/{query_id}/page.png')
    def page(query_id: str, expected_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity: Actor = Depends(actor)):
        _, query = bound_query(query_id, expected_report_sha256)
        if query['document_id'] not in {'msft', 'aapl'}:
            raise HTTPException(404, 'Registered local document not found')
        try:
            image = VisualStore(root).image(query['document_id'], query['page'])
            if sha256(image).hexdigest() != query['page_metadata']['image_sha256']:
                raise VisualArtifactError('Image differs from the selected source snapshot')
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Local page unavailable; use the official source link') from exc
        except (VisualArtifactError, ValueError, KeyError, OSError) as exc:
            raise HTTPException(409, 'Local page failed source validation') from exc
        return Response(image, media_type='image/png')
