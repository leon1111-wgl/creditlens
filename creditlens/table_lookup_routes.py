"""Read saved table-lookup development results without rerunning inference."""
from pathlib import Path

from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .table_lookup_experiment import load_table_lookup_snapshot
from .visual_store import VisualStore, VisualArtifactError
from .workflow import Actor


def install_table_lookup_routes(app, actor, root: Path):
    def snapshot():
        try:
            return load_table_lookup_snapshot(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Saved table lookup report is unavailable or failed validation') from exc

    @app.get('/table-lookup', include_in_schema=False)
    def workspace():
        return FileResponse(root / 'creditlens/table-lookup.html')

    @app.get('/api/table-lookup')
    def report(identity: Actor = Depends(actor)):
        return snapshot()

    @app.get('/api/table-lookup/cases/{case_id}/page.png')
    def page(case_id: str, expected_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity: Actor = Depends(actor)):
        saved = snapshot()
        if saved['report_sha256'] != expected_report_sha256:
            raise HTTPException(409, 'Saved report changed; reload the results')
        found = next(((document, case) for document in saved['report']['documents']
                      for case in document['cases'] if case['id'] == case_id), None)
        if found is None:
            raise HTTPException(404, 'Registered lookup case not found')
        document, case = found
        if document['id'] not in {'msft', 'aapl'}:
            raise HTTPException(404, 'Registered local document not found')
        try:
            image = VisualStore(root).image(document['id'], case['page'])
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Local page unavailable; use the official source link') from exc
        except (VisualArtifactError, ValueError, KeyError) as exc:
            raise HTTPException(409, 'Local page failed source validation') from exc
        return Response(image, media_type='image/png')
