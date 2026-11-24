"""Operator endpoints for submitting and tracking online coding tasks."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.admin_deps import require_operator
from online_tasks.task_queue_store import TaskQueueConflict


class StoryRequest(BaseModel):
    jira_id: str
    summary: str
    description: str
    acceptance_criteria: str


def router_for(service):
    router = APIRouter(dependencies=[Depends(require_operator)])

    def available():
        if service is None:
            raise HTTPException(503, "Online task queue is not configured")
        return service

    @router.post("/story-tasks", status_code=202)
    @router.post("/story-runs", status_code=202, include_in_schema=False)
    async def start_story(req: StoryRequest, user=Depends(require_operator)):
        instance = available()
        try:
            run_id = await instance.submit_story(
                req.jira_id, req.summary, req.description, req.acceptance_criteria,
                user["uid"],
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        instance.wake()
        return {"id": run_id, "jira_id": req.jira_id,
                "url": f"/api/agent/story-tasks/{run_id}"}

    async def read_story(run_id):
        record = await available().store.read(run_id)
        if not record:
            raise HTTPException(404, "Story task not found")
        return record

    @router.get("/story-tasks/{run_id}")
    @router.get("/story-runs/{run_id}", include_in_schema=False)
    async def get_story(run_id: str):
        return await read_story(run_id)

    @router.post("/story-tasks/{run_id}/retry", status_code=202)
    @router.post("/story-runs/{run_id}/retry", status_code=202, include_in_schema=False)
    async def retry_story(run_id: str):
        instance = available()
        record = await read_story(run_id)
        if record["task_id"] is None:
            raise HTTPException(409, "Resubmit the four Story fields from the Dashboard")
        try:
            await instance.store.retry(run_id, record["issue_key"])
        except KeyError as exc:
            raise HTTPException(404, "Story task not found") from exc
        except TaskQueueConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        instance.wake()
        return {"id": run_id, "status": "queued"}

    return router
