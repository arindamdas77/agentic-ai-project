# Personal AI Agent — starter scaffold

A web dashboard and FastAPI starter for an approval-first DevOps/personal assistant.

## Current safety boundary

This starter only creates a task record and records a plan approval decision. It does **not** execute shell commands, access servers, call AWS, or invoke an LLM. Approval in this starter is not permission to execute anything. Do not expose it publicly as-is: authentication, persistent storage, CSRF/origin protections, and production hardening are not implemented yet.

## Run locally with Docker Compose

From this directory:

```bash
docker compose -f infrastructure/docker-compose.yml up --build
```

Open http://localhost:5173. API health: http://localhost:8000/api/health. API docs: http://localhost:8000/docs.

## Next implementation milestones

1. Add authentication and PostgreSQL persistence.
2. Add an LLM planner that returns a validated, structured plan (no tools enabled).
3. Add immutable, one-time approvals bound to exact action payloads, user, task, expiry, and policy version.
4. Add a small allowlisted read-only tool set in an isolated worker; require fresh approval for each invocation.
5. Add write/destructive actions only after audit logging, backups/rollback design, least-privilege credentials, and explicit per-action approvals.
6. Add integrations (SSH, Docker, Git, Jenkins, AWS) incrementally with tests.

Never implement arbitrary model-generated shell execution or let an approval for one step authorize subsequent steps.
