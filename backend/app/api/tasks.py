import asyncio
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.environment import get_environment_report
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
service = TaskService()


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=5000)


class ApprovalRequest(BaseModel):
    approved: bool
    approver: str | None = Field(default=None, max_length=100)


@router.post("")
async def create_task(body: TaskRequest):
    return await asyncio.to_thread(service.create_task, str(uuid4()), body.prompt)


@router.get("")
async def list_tasks():
    return service.list_tasks()


@router.get("/environment")
async def environment():
    return await asyncio.to_thread(get_environment_report)


@router.get("/{task_id}")
async def get_task(task_id: str):
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/{task_id}/report")
async def get_report(task_id: str):
    try:
        return await asyncio.to_thread(service.get_report, task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/{task_id}/actions/{action_id}/approval")
async def approve_action(task_id: str, action_id: str, body: ApprovalRequest):
    try:
        return await asyncio.to_thread(
            service.record_approval, task_id, action_id, body.approved,
            body.approver or "local-user",
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Task or action not found")


@router.post("/{task_id}/actions/{action_id}/retry")
async def retry_action(task_id: str, action_id: str):
    """Reset a failed read-only step to awaiting_approval (never executes)."""
    try:
        return await asyncio.to_thread(service.retry_step, task_id, action_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task or action not found")
