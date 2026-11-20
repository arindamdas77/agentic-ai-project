from uuid import uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.task_service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
service = TaskService()

class TaskRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=5000)

class ApprovalRequest(BaseModel):
    approved: bool

@router.post("")
async def create_task(body: TaskRequest):
    return service.create_task(str(uuid4()), body.prompt)

@router.get("/{task_id}")
async def get_task(task_id: str):
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@router.post("/{task_id}/actions/{action_id}/approval")
async def approve_action(task_id: str, action_id: str, body: ApprovalRequest):
    try:
        return service.record_approval(task_id, action_id, body.approved)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task or action not found")
