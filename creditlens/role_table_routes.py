"""Read saved three-method lookup comparisons without new OCR or fact adoption."""
from pathlib import Path
from hashlib import sha256

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response

from .role_table_experiment import load_role_table_lookup_snapshot
from .visual_store import VisualStore, VisualArtifactError
from .workflow import Actor


def install_role_table_routes(app, actor, root: Path):
    def snapshot():
        try:
            return load_role_table_lookup_snapshot(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Saved role lookup report is unavailable or failed validation') from exc

    @app.get('/api/role-table-lookup')
    def report(identity: Actor = Depends(actor)):
        return snapshot()

    @app.get('/api/role-table-lookup/cases/{case_id}/page.png')
    def page(case_id: str, expected_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity: Actor = Depends(actor)):
        saved = snapshot()
        if saved['report_sha256'] != expected_report_sha256:
            raise HTTPException(409, 'Saved report changed; reload the results')
        found = next(((d, c) for d in saved['report']['documents'] for c in d['cases'] if c['id'] == case_id), None)
        if found is None:
            raise HTTPException(404, 'Registered role lookup case not found')
        document, case = found
        if document['id'] not in {'msft', 'aapl'}:
            raise HTTPException(404, 'Registered local document not found')
        try:
            image = VisualStore(root).image(document['id'], case['page'])
            registered = next(p for p in document['pages'] if p['page'] == case['page'])
            if sha256(image).hexdigest() != registered['image_sha256']:
                raise VisualArtifactError('Image does not belong to this report snapshot')
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Local page unavailable; use the official source link') from exc
        except (VisualArtifactError, ValueError, KeyError, StopIteration, OSError) as exc:
            raise HTTPException(409, 'Local page failed source validation') from exc
        return Response(image, media_type='image/png')
