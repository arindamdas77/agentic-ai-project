from datetime import datetime, timezone
from threading import Lock

class TaskService:
    """In-memory MVP store. Replace with PostgreSQL before multi-user deployment."""
    def __init__(self):
        self._tasks = {}
        self._lock = Lock()

    def create_task(self, task_id: str, prompt: str):
        # MVP deliberately creates a plan requiring review; it does not execute commands.
        task = {
            "id": task_id,
            "prompt": prompt,
            "status": "awaiting_plan_review",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "steps": [{
                "id": "step-1",
                "title": "Analyze the request and prepare an execution plan",
                "description": "No server or external-system action is performed in this starter build.",
                "status": "awaiting_approval",
                "approval": None,
                "execution_enabled": False,
            }],
        }
        with self._lock:
            self._tasks[task_id] = task
        return task

    def get_task(self, task_id: str):
        with self._lock:
            return self._tasks.get(task_id)

    def record_approval(self, task_id: str, action_id: str, approved: bool):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            step = next((s for s in task["steps"] if s["id"] == action_id), None)
            if not step:
                raise KeyError(action_id)
            # Approval is recorded for UI workflow only. It cannot trigger execution.
            step["approval"] = "approved" if approved else "rejected"
            step["status"] = "approved_not_executable" if approved else "rejected"
            return task
