"""Read registered company statements alongside an exact accounting snapshot."""
from pathlib import Path

from fastapi import Depends, HTTPException, Query

from .margin_claim_experiment import load_margin_claim_snapshot
from .workflow import Actor


def install_margin_claim_routes(app, actor, root: Path):
    @app.get('/api/margin-claims')
    def report(expected_margin_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
               identity: Actor = Depends(actor)):
        try:
            saved = load_margin_claim_snapshot(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Registered statements or calculations failed validation') from exc
        if saved['margin_report_sha256'] != expected_margin_report_sha256:
            raise HTTPException(409, 'Accounting report changed; reload before linking statements')
        return saved
