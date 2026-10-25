"""Small synchronous preparation attempts with strict JSON and tenant-scoped state."""
import json
from pathlib import Path

from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from .preparation_examples import preparation_examples
from .workflow import Actor


def _object(pairs):
    value={}
    for key,item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON key')
        value[key]=item
    return value


def _constant(value):
    raise ValueError('Non-finite JSON number')


async def body(request:Request):
    try:
        value=json.loads(await request.body(),object_pairs_hook=_object,parse_constant=_constant)
        if not isinstance(value,dict):raise ValueError('Expected object')
        return value
    except (ValueError,UnicodeDecodeError,RecursionError) as exc:
        raise HTTPException(422,'Expected a strict JSON object without duplicate keys or non-finite numbers') from exc


def install_preparation_routes(app, actor, root:Path):
    @app.get('/document-preparation',include_in_schema=False)
    def workspace():
        return FileResponse(root/'creditlens/preparation.html')

    @app.get('/api/document-preparation/examples')
    def examples(identity:Actor=Depends(actor)):
        try:return preparation_examples(root)
        except (ValueError,FileNotFoundError) as exc:
            raise HTTPException(409,'Registered example materials failed validation') from exc

    @app.get('/api/document-preparation/tasks')
    def tasks(identity:Actor=Depends(actor)):
        return app.state.batch_attempts.list(identity)

    @app.post('/api/document-preparation/tasks',status_code=201)
    async def create(request:Request,identity:Actor=Depends(actor),idempotency_key:str=Header(alias='Idempotency-Key')):
        payload=await body(request)
        return await run_in_threadpool(app.state.batch_attempts.create,identity,payload,idempotency_key)

    @app.get('/api/document-preparation/tasks/{task_id}')
    def get(task_id:str,identity:Actor=Depends(actor)):
        return app.state.batch_attempts.get(task_id,identity)

    @app.post('/api/document-preparation/tasks/{task_id}/retry')
    async def retry(task_id:str,request:Request,identity:Actor=Depends(actor),idempotency_key:str=Header(alias='Idempotency-Key')):
        value=await body(request)
        if set(value)!={'revision','payload'} or type(value['revision']) is not int or value['revision']<1:
            raise HTTPException(422,'Expected a positive integer revision and payload')
        return await run_in_threadpool(app.state.batch_attempts.retry,identity,task_id,value['payload'],value['revision'],idempotency_key)

    @app.get('/api/document-preparation/tasks/{task_id}/export')
    def export(task_id:str,attempt_id:str|None=None,identity:Actor=Depends(actor)):
        result=app.state.batch_attempts.export(task_id,identity,attempt_id)
        raw=json.dumps(result,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
        return Response(raw,media_type='application/json',headers={'Content-Disposition':'attachment; filename="creditlens-preparation-attempt.json"'})
