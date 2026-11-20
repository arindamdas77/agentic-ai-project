# Personal AI Agent

A web dashboard and FastAPI service for an approval-first DevOps assistant.

## What it does now

- Describe a task in plain language ("check disk usage", "show docker logs", "is nginx running").
- The planner (OpenAI when `OPENAI_API_KEY` is set, otherwise a keyword heuristic) produces a structured plan of **read-only diagnostic steps** across system, Docker, services and logs.
- Each step only runs after you click **Approve & run**. Steps can only invoke tools from a fixed allowlist — the model can never generate shell commands. Failed steps can be retried, but only after approving again.
- The executor resolves binaries to absolute paths, guarantees a sane `PATH` for child processes, detects the runtime environment (host / WSL / container / VM), enforces timeouts, and reports actionable errors for missing tools (e.g. no Docker socket, no systemd).
- Available diagnostics: `system_info`, `system_overview`, `cpu_load`, `cpu_info`, `memory_usage`, `disk_usage`, `uptime`, `top_processes`, `listening_ports`, `failed_services`, `active_services`, `service_status`, `docker_version`, `docker_ps`, `docker_images`, `docker_system_df`, `docker_volumes`, `docker_networks`, `docker_stats`, `docker_logs`, `journal_logs`.
- `GET /api/tasks/environment` reports the detected environment and tool availability; `GET /api/tasks/{id}/report` returns a readable Markdown report; `POST /api/tasks/{id}/actions/{action_id}/retry` re-queues a failed step for approval.

## Safety boundary

Commands are fixed argv lists executed without a shell, with timeouts and truncated output. There is no arbitrary command execution, no writes, and no remote/SSH access yet. Still add authentication before exposing this beyond localhost.

## Run locally

The backend **must run on the host** — diagnostics like `docker`, `systemctl`
and `journalctl` query the host system and cannot work inside the API
container (which is kept only as an opt-in `container` compose profile).

```bash
# 1. Backend (host) — full diagnostics
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp ../.env.example ../.env   # add OPENAI_API_KEY if you want LLM planning
.venv/bin/uvicorn app.main:app --port 8000

# 2. Frontend (separate terminal)
docker compose -f infrastructure/docker-compose.yml up web
# or fully on the host: cd frontend && npm install && npm run dev
```

Open http://localhost:5173. API: http://localhost:8000/api (docs at `/docs`).

Tests: `cd backend && .venv/bin/pip install pytest httpx && .venv/bin/python -m pytest ../tests -q`

## Next milestones

1. Authentication + PostgreSQL persistence (task history currently lives in memory).
2. Analysis step: feed diagnostic output back to the LLM for a summary/recommendation.
3. Write actions only behind immutable per-action approvals, audit logging, and least-privilege credentials.
4. SSH/Docker/Kubernetes integrations incrementally, each tool explicitly allowlisted.
