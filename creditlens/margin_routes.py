"""Read registered gross-margin arithmetic and its source-bound page preview."""
from hashlib import sha256
from pathlib import Path

from fastapi import Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from .margin_experiment import load_margin_bridge_snapshot, margin_image_metadata
from .visual_store import VisualStore, VisualArtifactError
from .workflow import Actor


def install_margin_routes(app, actor, root: Path):
    def snapshot():
        try:
            return load_margin_bridge_snapshot(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Registered margin evidence or saved calculations failed validation') from exc

    @app.get('/margin', include_in_schema=False)
    def workspace():
        return FileResponse(root / 'creditlens/margin.html')

    @app.get('/api/margin-bridge')
    def report(identity: Actor = Depends(actor)):
        return snapshot()

    @app.get('/api/margin-bridge/cases/{case_id}/page.png')
    def page(case_id: str, expected_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity: Actor = Depends(actor)):
        saved = snapshot()
        if saved['report_sha256'] != expected_report_sha256:
            raise HTTPException(409, 'Margin report changed; reload the evidence')
        if not any(c['id'] == case_id for c in saved['report']['cases']):
            raise HTTPException(404, 'Registered margin case not found')
        try:
            metadata = margin_image_metadata(saved, case_id)
            image = VisualStore(root).image(metadata['document_id'], metadata['page'])
            if sha256(image).hexdigest() != metadata['image_sha256']:
                raise VisualArtifactError('Page does not belong to this report snapshot')
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Local page unavailable; use the official source link') from exc
        except (VisualArtifactError, ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(409, 'Margin page failed source validation') from exc
        return Response(image, media_type='image/png')
