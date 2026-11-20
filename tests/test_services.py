"""Tests for the diagnostics allowlist, planner and task service."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import diagnostics, environment, planner  # noqa: E402
from app.services.task_service import TaskService  # noqa: E402


def test_unknown_tool_returns_error():
    result = diagnostics.run_tool("rm_rf_root", {})
    assert result["error"] == "Unknown tool: rm_rf_root"
    assert result["output"] == ""


def test_system_overview_runs_readonly_commands():
    result = diagnostics.run_tool("system_overview", {})
    assert result["error"] is None
    assert "uname -a" in result["commands"][0]
    assert result["output"]


def test_missing_binary_reports_actionable_error(monkeypatch):
    monkeypatch.setattr(diagnostics, "resolve_executable", lambda exe: None)
    result = diagnostics.run_tool("docker_ps", {})
    assert result["error"]
    assert "Docker" in result["error"] or "docker" in result["error"]
    assert result["output"] == ""
    assert result["results"][0]["missing"] is True


def test_missing_journalctl_message(monkeypatch):
    # Simulate journalctl missing while others resolve.
    monkeypatch.setattr(
        diagnostics, "resolve_executable",
        lambda exe: None if exe == "journalctl" else "/bin/true",
    )
    result = diagnostics.run_tool("journal_logs", {})
    assert "journalctl" in result["error"]


def test_journal_lines_param_is_clamped():
    argv = diagnostics.TOOLS["journal_logs"]["argv"]({"lines": 99999})[0]
    assert argv[argv.index("-n") + 1] == "500"
    argv = diagnostics.TOOLS["journal_logs"]["argv"]({"lines": "not-a-number"})[0]
    assert argv[argv.index("-n") + 1] == "100"


def test_docker_logs_container_param_validated():
    argv = diagnostics._expand_dynamic("docker_logs", {"container": "web; rm -rf /"})[1]
    # Invalid name rejected -> falls back to listing running containers.
    assert all("rm -rf" not in " ".join(a) for a in argv)
    argv = diagnostics._expand_dynamic("docker_logs", {"container": "my_container-1"})[1]
    assert argv == [["docker", "logs", "--tail", "100", "my_container-1"]]


def test_planner_fallback_matches_keywords():
    steps, used = planner.create_plan("what is my docker log")
    tools = {s["tool"] for s in steps}
    assert "docker_logs" in tools
    assert "journal_logs" in tools


def test_planner_intents():
    cases = {
        "check cpu usage": "cpu_load",
        "show disk space": "disk_usage",
        "check ram usage": "memory_usage",
        "server uptime": "uptime",
        "show failed services": "failed_services",
        "which ports are listening": "listening_ports",
        "find high cpu processes": "top_processes",
        "show mysql logs": "journal_logs",
        "docker disk usage": "docker_system_df",
        "which containers are running": "docker_ps",
    }
    for prompt, expected in cases.items():
        steps, _ = planner.create_plan(prompt)
        tools = {s["tool"] for s in steps}
        assert expected in tools, f"{prompt!r} -> {tools}"


def test_planner_service_unit_detection():
    steps, _ = planner.create_plan("show mysql logs")
    jl = next(s for s in steps if s["tool"] == "journal_logs")
    assert jl["params"].get("unit") == "mysql.service"


def test_planner_sanitize_drops_invalid_tools_and_params():
    steps = planner._sanitize(
        [
            {"title": "x", "tool": "not_a_tool", "params": {}},
            {"title": "y", "tool": "journal_logs", "params": {"lines": "5", "evil": "x; rm -rf /"}},
        ],
        "",
    )
    assert len(steps) == 1
    assert steps[0]["tool"] == "journal_logs"
    assert steps[0]["params"] == {"lines": 5}


def test_task_lifecycle_approve_and_reject():
    svc = TaskService()
    task = svc.create_task("t1", "check disk usage")
    assert task["status"] == "awaiting_approval"
    step = task["steps"][0]

    updated = svc.record_approval("t1", step["id"], approved=False, approver="tester")
    rejected = next(s for s in updated["steps"] if s["id"] == step["id"])
    assert rejected["status"] == "rejected"
    assert rejected["output"] is None
    assert rejected["rejected_by"] == "tester"


def test_task_list_endpoint_shape():
    svc = TaskService()
    svc.create_task("t2", "check memory")
    tasks = svc.list_tasks()
    assert tasks and tasks[0]["id"] == "t2"
    assert tasks[0]["steps"]


def test_approval_executes_and_records_audit():
    svc = TaskService()
    task = svc.create_task("t3", "check memory")
    step = task["steps"][0]
    updated = svc.record_approval("t3", step["id"], approved=True, approver="alice")
    done = next(s for s in updated["steps"] if s["id"] == step["id"])
    assert done["status"] in ("completed", "failed")
    assert done["approved_by"] == "alice"
    assert done["approved_at"]
    assert done["executed_at"]


def test_unapproved_step_never_executes(monkeypatch):
    svc = TaskService()
    task = svc.create_task("t4", "check memory")
    called = []
    monkeypatch.setattr(
        "app.services.task_service.run_tool",
        lambda *a, **k: called.append(a) or {"output": "", "error": None, "commands": [], "results": []},
    )
    # No approval call at all — run_tool must not be invoked.
    svc.get_task("t4")
    svc.list_tasks()
    assert called == []


def test_retry_requires_approval_again(monkeypatch):
    svc = TaskService()
    task = svc.create_task("t5", "check memory")
    step = task["steps"][0]
    monkeypatch.setattr(
        "app.services.task_service.run_tool",
        lambda *a, **k: {"output": "", "error": "boom", "commands": [], "results": [], "timed_out": False},
    )
    svc.record_approval("t5", step["id"], approved=True)
    failed = next(s for s in svc.get_task("t5")["steps"] if s["id"] == step["id"])
    assert failed["status"] == "failed"

    retried = svc.retry_step("t5", step["id"])
    rs = next(s for s in retried["steps"] if s["id"] == step["id"])
    assert rs["status"] == "awaiting_approval"
    assert rs["retry_count"] == 1

    # Approving again re-executes (still via run_tool mock).
    svc.record_approval("t5", step["id"], approved=True)
    rs = next(s for s in svc.get_task("t5")["steps"] if s["id"] == step["id"])
    assert rs["status"] == "failed"  # mock still fails, but it ran again


def test_timeout_handling(monkeypatch):
    import subprocess

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=k.get("timeout", 15))

    monkeypatch.setattr(diagnostics.subprocess, "run", fake_run)
    result = diagnostics.run_tool("system_overview", {})
    assert result["timed_out"] is True
    assert result["error"]
    assert "timed out" in result["error"]


def test_environment_report_shape():
    report = environment.get_environment_report()
    assert report["os"]["system"]
    assert report["runtime"]["environment"] in {"host", "container", "wsl", "virtual_machine"}
    assert "docker" in report["tools"]
    assert isinstance(report["warnings"], list)
