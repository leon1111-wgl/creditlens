"""Read-only unit applicability checks bound to saved extraction reports."""
from pathlib import Path

from fastapi import Depends, HTTPException, Query

from .unit_applicability_experiment import (
    get_unit_case, get_unit_selection, load_unit_applicability_snapshot,
)


def install_unit_applicability_routes(app, actor, root: Path):
    def snapshot(expected_role, expected_row=None):
        try:
            saved = load_unit_applicability_snapshot(root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, 'Unit checks are unavailable or failed source validation; reload the experiment') from exc
        if saved['role_report_sha256'] != expected_role or (
            expected_row is not None and saved['row_report_sha256'] != expected_row
        ):
            raise HTTPException(409, 'Source report changed; reload the experiment and row choices')
        return saved

    @app.get('/api/unit-applicability')
    def report(expected_role_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
               identity=Depends(actor)):
        return snapshot(expected_role_report_sha256)

    @app.get('/api/unit-applicability/cases/{case_id}')
    def case(case_id: str, method: str = Query(pattern=r'^(text|line|word|role)$'),
             expected_role_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
             identity=Depends(actor)):
        saved = snapshot(expected_role_report_sha256)
        try:
            return get_unit_case(saved, case_id, method)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(404, 'Registered unit-check case or method not found') from exc

    @app.get('/api/unit-applicability/queries/{query_id}/selection')
    def selection(query_id: str, row_index: int = Query(ge=0),
                  expected_role_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
                  expected_row_report_sha256: str = Query(pattern=r'^[0-9a-f]{64}$'),
                  identity=Depends(actor)):
        saved = snapshot(expected_role_report_sha256, expected_row_report_sha256)
        try:
            return get_unit_selection(saved, query_id, row_index)
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(404, 'Registered unit-check selection not found') from exc
