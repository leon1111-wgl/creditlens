"""Read-only, explicit research selections; original task questions stay background."""
from fastapi import Depends, HTTPException

from .research_query import ResearchEvidenceQuery, ResearchQueryError, build_research_query


def register_research_query_routes(application, actor):
    @application.post('/api/research/tasks/{task_id}/evidence-query')
    def research_evidence_query(task_id: str, payload: ResearchEvidenceQuery,
                                identity=Depends(actor)):
        task = application.state.research_tasks.get(task_id, identity)
        if task['revision'] != payload.revision:
            raise HTTPException(409, '任务版本已变化，请重新载入后按所选范围查证据。')
        try:
            return build_research_query(task, payload)
        except ResearchQueryError as exc:
            raise HTTPException(422 if exc.code == 'invalid_query' else 409,
                                {'code': exc.code, 'message': exc.message,
                                 'recovery': '核对查询范围、任务版本及登记材料后重试；保留旧底稿与审批。'}) from exc
