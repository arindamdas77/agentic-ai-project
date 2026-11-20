from datetime import datetime, timezone
from threading import Lock

from app.services import diagnostics
from app.services.diagnostics import run_tool
from app.services.planner import create_plan

# Steps using these tools are read-only diagnostics and may be retried after
# a failure (still requiring fresh approval before re-execution).
RETRYABLE = set(diagnostics.TOOLS.keys())


def _now():
    return datetime.now(timezone.utc).isoformat()


def _new_step(i, s):
    return {
        "id": f"step-{i}",
        "title": s["title"],
        "description": s["description"],
        "tool": s["tool"],
        "params": s["params"],
        "status": "awaiting_approval",
        "approval": None,
        "approved_at": None,
        "approved_by": None,
        "rejected_at": None,
        "rejected_by": None,
        "output": None,
        "error": None,
        "commands": [],
        "results": [],
        "exit_codes": [],
        "duration_ms": None,
        "timed_out": False,
        "executed_at": None,
        "retry_count": 0,
    }


class TaskService:
    """In-memory store. Replace with PostgreSQL before multi-user deployment.

    Approval-first model: a step only runs after an explicit approval call.
    Steps can only invoke tools from the allowlist in diagnostics.py —
    no arbitrary command execution is possible.
    """

    def __init__(self):
        self._tasks = {}
        self._lock = Lock()

    def create_task(self, task_id: str, prompt: str):
        steps, planner_used = create_plan(prompt)
        task = {
            "id": task_id,
            "prompt": prompt,
            "status": "awaiting_approval",
            "planner": planner_used,
            "created_at": _now(),
            "steps": [_new_step(i, s) for i, s in enumerate(steps, start=1)],
        }
        with self._lock:
            self._tasks[task_id] = task
        return task

    def list_tasks(self):
        with self._lock:
            tasks = []
            for task in reversed(list(self._tasks.values())):
                t = dict(task)
                t["steps"] = [
                    {**s, "output": (s.get("output") or "")[:500] or None,
                     "results": None}
                    for s in task["steps"]
                ]
                tasks.append(t)
            return tasks

    def get_task(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def _get_step(self, task_id, action_id):
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        step = next((s for s in task["steps"] if s["id"] == action_id), None)
        if not step:
            raise KeyError(action_id)
        return task, step

    def record_approval(self, task_id: str, action_id: str, approved: bool,
                        approver: str = "local-user"):
        with self._lock:
            task, step = self._get_step(task_id, action_id)
            if step["status"] != "awaiting_approval":
                return task
            if not approved:
                step["approval"] = "rejected"
                step["status"] = "rejected"
                step["rejected_at"] = _now()
                step["rejected_by"] = approver
                self._refresh_status(task)
                return task

            step["approval"] = "approved"
            step["approved_at"] = _now()
            step["approved_by"] = approver
            step["status"] = "running"

        # Execute outside the lock; diagnostics are read-only and bounded.
        result = run_tool(step["tool"], step["params"])
        duration = sum(r.get("duration_ms") or 0 for r in result.get("results", []))
        with self._lock:
            ok = bool(result["output"]) or not result["error"]
            step["status"] = "completed" if ok else "failed"
            step["output"] = result["output"] or None
            step["error"] = result["error"]
            step["commands"] = result["commands"]
            step["results"] = result.get("results", [])
            step["exit_codes"] = [r.get("exit_code") for r in result.get("results", [])]
            step["duration_ms"] = duration or None
            step["timed_out"] = result.get("timed_out", False)
            step["executed_at"] = _now()
            self._refresh_status(task)
            return task

    def retry_step(self, task_id: str, action_id: str):
        """Reset a failed read-only step to awaiting_approval.

        Retrying never executes anything — the step must be approved again.
        """
        with self._lock:
            task, step = self._get_step(task_id, action_id)
            if step["status"] != "failed" or step["tool"] not in RETRYABLE:
                return task
            step["status"] = "awaiting_approval"
            step["approval"] = None
            step["approved_at"] = None
            step["approved_by"] = None
            step["output"] = None
            step["error"] = None
            step["commands"] = []
            step["results"] = []
            step["exit_codes"] = []
            step["duration_ms"] = None
            step["timed_out"] = False
            step["executed_at"] = None
            step["retry_count"] = step.get("retry_count", 0) + 1
            self._refresh_status(task)
            return task

    def _refresh_status(self, task):
        statuses = {s["status"] for s in task["steps"]}
        if statuses <= {"completed", "failed", "rejected"}:
            task["status"] = (
                "completed" if "failed" not in statuses and "rejected" not in statuses else "finished_with_issues"
            )
            task["report"] = self._build_report(task)
        elif "running" in statuses:
            task["status"] = "running"
        elif "awaiting_approval" in statuses:
            task["status"] = "awaiting_approval"
        else:
            task["status"] = "in_progress"

    @staticmethod
    def _build_report(task):
        lines = [f"# Report: {task['prompt']}", ""]
        lines.append(f"- Task ID: {task['id']}")
        lines.append(f"- Planner: {task.get('planner', 'unknown')}")
        lines.append(f"- Status: {task['status'].replace('_', ' ')}")
        lines.append(f"- Created: {task.get('created_at', 'n/a')}")
        lines.append("")
        for step in task["steps"]:
            lines.append(f"## Step {step['id']}: {step['title']}")
            lines.append(f"- Tool: `{step['tool']}` — status: {step['status'].replace('_', ' ')}")
            if step.get("approved_by"):
                lines.append(f"- Approved by {step['approved_by']} at {step.get('approved_at')}")
            if step.get("rejected_by"):
                lines.append(f"- Rejected by {step['rejected_by']} at {step.get('rejected_at')}")
            if step.get("duration_ms") is not None:
                lines.append(f"- Duration: {step['duration_ms']} ms")
            if step.get("error"):
                lines.append(f"- Error: {step['error']}")
            if step.get("output"):
                lines.append("")
                lines.append("```")
                lines.append(step["output"][:4000])
                lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def get_report(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        with self._lock:
            if not task.get("report"):
                task["report"] = self._build_report(task)
            return {"task_id": task_id, "report": task["report"]}