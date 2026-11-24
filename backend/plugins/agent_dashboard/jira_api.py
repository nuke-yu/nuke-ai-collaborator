"""Operator endpoints for Jira Story and Epic runs."""
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from api.admin_deps import require_operator
from online_tasks.jira_source import issue_key
from online_tasks.jira_store import JiraConflict
from .api import CreateTaskRequest


class RepositoryMapping(BaseModel):
    repo_url: str
    base_branch: str = "main"
    test_command: str = ""

    @field_validator("repo_url")
    @classmethod
    def validate_repo(cls, value):
        return CreateTaskRequest.validate_repo_url(value)

    @field_validator("base_branch")
    @classmethod
    def validate_branch(cls, value):
        if (not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,199}", value)
                or ".." in value or any(not part or part.startswith(".") or part.endswith((".", ".lock"))
                                        for part in value.split("/"))):
            raise ValueError("Invalid Git base branch")
        return value

    @field_validator("test_command")
    @classmethod
    def validate_test(cls, value):
        return CreateTaskRequest.validate_test_command(value)


class StoryRequest(BaseModel):
    story_key: str
    repository: RepositoryMapping | None = None


class EpicRequest(BaseModel):
    epic_key: str
    mappings: dict[str, RepositoryMapping] = Field(default_factory=dict, max_length=1000)
    max_parallel: int = Field(1, ge=1, le=32)


def router_for(service):
    router = APIRouter(dependencies=[Depends(require_operator)])

    def available():
        if service is None:
            raise HTTPException(503, "Jira online tasks are not configured")
        return service

    async def submit(kind, key, mappings, parallel, user):
        instance = available()
        try:
            key = issue_key(key)
            normalized = {}
            for match, mapping in mappings.items():
                match = issue_key(match)
                if match in normalized and normalized[match] != mapping.model_dump():
                    raise JiraConflict(f"Conflicting mappings for {match}")
                normalized[match] = mapping.model_dump()
            run_id = await instance.store.submit(
                instance.source.site, kind, key, normalized, parallel, user["uid"],
            )
        except JiraConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        instance.wake()
        path = "story-runs" if kind == "story" else "epic-runs"
        return {"id": run_id, "issue_key": key, "url": f"/api/agent/{path}/{run_id}"}

    @router.post("/story-runs", status_code=202)
    async def start_story(req: StoryRequest, user=Depends(require_operator)):
        mappings = {req.story_key: req.repository} if req.repository else {}
        return await submit("story", req.story_key, mappings, 1, user)

    @router.post("/epic-runs", status_code=202)
    async def start_epic(req: EpicRequest, user=Depends(require_operator)):
        return await submit("epic", req.epic_key, req.mappings, req.max_parallel, user)

    async def read(run_id, kind):
        record = await available().store.read(run_id)
        if not record or record["kind"] != kind:
            raise HTTPException(404, "Jira run not found")
        return record

    @router.get("/story-runs/{run_id}")
    async def get_story(run_id: str):
        return await read(run_id, "story")

    @router.get("/epic-runs/{run_id}")
    async def get_epic(run_id: str):
        return await read(run_id, "epic")

    @router.post("/epic-runs/{run_id}/pause")
    async def pause_epic(run_id: str):
        await read(run_id, "epic")
        await available().store.set_paused(run_id, True)
        return {"id": run_id, "paused": True}

    @router.post("/epic-runs/{run_id}/resume")
    async def resume_epic(run_id: str):
        await read(run_id, "epic")
        instance = available()
        await instance.store.set_paused(run_id, False)
        instance.wake()
        return {"id": run_id, "paused": False}

    @router.post("/epic-runs/{run_id}/sync", status_code=202)
    async def sync_epic(run_id: str):
        await read(run_id, "epic")
        instance = available()
        await instance.store.sync(run_id)
        instance.wake()
        return {"id": run_id, "status": "discovering"}

    async def retry(run_id, story_key):
        instance = available()
        try:
            await instance.store.retry(run_id, issue_key(story_key))
        except KeyError as exc:
            raise HTTPException(404, "Story not found in the run") from exc
        except JiraConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        instance.wake()
        return {"id": run_id, "status": "queued"}

    @router.post("/story-runs/{run_id}/retry", status_code=202)
    async def retry_story(run_id: str):
        record = await read(run_id, "story")
        if not record["stories"]:
            instance = available()
            await instance.store.set_paused(run_id, False)
            instance.wake()
            return {"id": run_id, "status": "discovering"}
        return await retry(run_id, record["stories"][0]["issue_key"])

    @router.post("/epic-runs/{run_id}/stories/{story_key}/retry", status_code=202)
    async def retry_epic_story(run_id: str, story_key: str):
        await read(run_id, "epic")
        return await retry(run_id, story_key)

    return router
