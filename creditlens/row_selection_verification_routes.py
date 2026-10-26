"""Bounded, authenticated verification of a saved row selection; no persistence."""
from pathlib import Path

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .row_selection_verification import (
    MAX_BYTES, RowSelectionSourceError, RowSelectionVerificationError,
    load_and_verify_row_selection,
)
from .workflow import Actor


def install_row_selection_verification_routes(app, actor, root: Path):
    @app.post('/api/row-selection-verification')
    async def verify(request: Request, identity: Actor = Depends(actor)):
        if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return JSONResponse({'detail': {'code': 'unsupported_media_type', 'path': '$'}}, status_code=415)
        raw = bytearray()
        try:
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BYTES:
                    return JSONResponse({'detail': {'code': 'input_too_large', 'path': '$'}}, status_code=413)
                raw.extend(chunk)
        except ClientDisconnect:
            return JSONResponse({'detail': {'code': 'input_incomplete', 'path': '$'}}, status_code=400)
        try:
            return await run_in_threadpool(load_and_verify_row_selection, bytes(raw), root)
        except RowSelectionVerificationError as exc:
            return JSONResponse({'detail': {'code': exc.code, 'path': exc.path}}, status_code=exc.http_status)
        except RowSelectionSourceError:
            return JSONResponse({'detail': {'code': 'trusted_snapshot_unavailable', 'path': '$'}}, status_code=409)
