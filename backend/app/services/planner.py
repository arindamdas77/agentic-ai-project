"""Turn a user prompt into a structured plan of read-only diagnostic steps.

Uses OpenAI when OPENAI_API_KEY is configured; otherwise falls back to a
keyword-based heuristic so the app still works offline.
"""
import json
import re

from app.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_TIMEOUT
from app.services.diagnostics import TOOLS, tool_catalog

MAX_STEPS = 6

SYSTEM_PROMPT = """You are a DevOps assistant planner. Given a user request, produce a JSON plan of \
read-only diagnostic steps for a Linux server. Each step must use exactly one tool from \
the allowed list. Never propose write, delete, restart, or shell commands — only diagnostics.

Respond with JSON only, in this exact shape:
{"steps": [{"title": str, "description": str, "tool": str, "params": {}}]}

Allowed tools:
{catalog}

Rules:
- 1 to {max_steps} steps, ordered logically.
- params may contain: "lines" (int), "all" (bool), "container" (string, docker container name), "unit" (string, systemd unit like apache2.service or nginx).
- For "show docker logs" style requests, prefer docker_logs; add docker_ps for context if unsure which container.
- For service-specific logs (apache, nginx, mysql, postgresql), use journal_logs with params.unit.
- Keep titles short and descriptions to one sentence."""

# Ordered: more specific intents first so e.g. "docker logs" picks docker_logs
# rather than only journal_logs.
_KEYWORDS = {
    "docker_logs": ["docker log", "container log", "logs of", "docker logs"],
    "docker_stats": ["container cpu", "container memory", "docker stats", "container usage"],
    "docker_version": ["docker version", "docker info", "docker installed", "docker daemon"],
    "docker_images": ["docker image", "docker images"],
    "docker_volumes": ["docker volume", "docker volumes"],
    "docker_networks": ["docker network", "docker networks"],
    "docker_ps": ["docker", "container", "containers"],
    "docker_system_df": ["docker disk", "docker space", "docker df", "docker usage", "reclaimable"],
    "journal_logs": ["log", "logs", "journal", "error", "errors", "syslog", "journalctl"],
    "service_status": ["service status", "status of", "is running", "check status"],
    "active_services": ["running services", "active services", "services running", "services are running", "list services", "what services", "which services"],
    "failed_services": ["failed", "crash", "systemd", "service down", "unit failed", "failed services", "failed units"],
    "listening_ports": ["port", "ports", "network", "socket", "listen", "firewall", "open port"],
    "top_processes": ["high cpu", "high memory", "top process", "processes", "what is using", "heavy"],
    "cpu_load": ["cpu", "load", "slow", "hang", "processor"],
    "cpu_info": ["cpu model", "cpu info", "processor info", "lscpu"],
    "memory_usage": ["memory", "ram", "swap", "oom", "free"],
    "disk_usage": ["disk", "storage", "space", "df", "full", "disk space"],
    "uptime": ["uptime", "how long", "boot", "load average"],
    "system_info": ["os", "operating system", "kernel", "distro", "system info", "server info"],
}

# Service-name -> systemd unit hints embedded in prompts.
_SERVICE_UNITS = {
    "apache": "apache2.service",
    "apache2": "apache2.service",
    "httpd": "httpd.service",
    "nginx": "nginx.service",
    "mysql": "mysql.service",
    "mariadb": "mariadb.service",
    "postgres": "postgresql.service",
    "postgresql": "postgresql.service",
    "docker": "docker.service",
    "sshd": "ssh.service",
    "ssh": "ssh.service",
    "jenkins": "jenkins.service",
}

_DEFAULT_STEPS = ["system_info", "disk_usage", "memory_usage"]

_CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")


def _detect_params(tool, prompt):
    """Heuristically fill safe params for tools that accept them."""
    params = {}
    text = prompt.lower()
    if tool in ("journal_logs", "service_status"):
        for name, unit in _SERVICE_UNITS.items():
            if re.search(rf"\b{name}\b", text):
                params["unit"] = unit
                break
    if tool == "journal_logs" and "error" in text:
        params["lines"] = 200
    if tool == "docker_ps" and any(w in text for w in ("all", "stopped", "exited", "every")):
        params["all"] = True
    if tool == "docker_logs":
        m = re.search(r"\b([a-zA-Z0-9][a-zA-Z0-9_.-]{1,60})\s+(?:container\s+)?logs?\b", text)
        # Prefer explicit "logs of <name>" / "<name> container logs" patterns.
        m2 = re.search(r"(?:logs?\s+(?:of|for)\s+|container\s+)([a-zA-Z0-9][a-zA-Z0-9_.-]{1,60})", text)
        candidate = (m2 or m)
        if candidate:
            name = candidate.group(1)
            if _CONTAINER_RE.match(name) and name not in {
                "docker", "the", "my", "all", "system", "container", "recent", "last",
            }:
                params["container"] = name
    return params


def _fallback_plan(prompt):
    text = prompt.lower()
    tools = []
    for name, keywords in _KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(k) + r"\b", text) for k in keywords):
            tools.append(name)
    if not tools:
        tools = list(_DEFAULT_STEPS)
    # Service-name mentions should pull in service-aware diagnostics even when
    # no generic keyword matched (e.g. "show apache status", "is nginx up").
    mentions_service = any(
        re.search(r"\b" + re.escape(name) + r"\b", text) for name in _SERVICE_UNITS
    )
    if mentions_service:
        if re.search(r"\b(log|logs|error|errors)\b", text) and "journal_logs" not in tools:
            tools.append("journal_logs")
        if re.search(r"\b(status|running|up|health|alive|down)\b", text) and "service_status" not in tools:
            tools.append("service_status")
    # De-duplicate while preserving order, cap at MAX_STEPS.
    seen = set()
    ordered = []
    for t in tools:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    ordered = ordered[:MAX_STEPS]
    return [
        {
            "title": TOOLS[t]["description"],
            "description": f"Run the {t} diagnostic.",
            "tool": t,
            "params": _detect_params(t, prompt),
        }
        for t in ordered
    ]


def _sanitize(raw_steps, prompt=""):
    steps = []
    for s in raw_steps[:MAX_STEPS]:
        tool = s.get("tool")
        if tool not in TOOLS:
            continue
        params = s.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        clean_params = {}
        if "lines" in params:
            try:
                clean_params["lines"] = int(params["lines"])
            except (TypeError, ValueError):
                pass
        if isinstance(params.get("all"), bool):
            clean_params["all"] = params["all"]
        for key in ("container", "unit"):
            v = params.get(key)
            if isinstance(v, str) and re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}$", v):
                clean_params[key] = v
        # Fill gaps the model missed with safe heuristics.
        for k, v in _detect_params(tool, prompt).items():
            clean_params.setdefault(k, v)
        steps.append(
            {
                "title": str(s.get("title") or TOOLS[tool]["description"])[:200],
                "description": str(s.get("description") or "")[:1000],
                "tool": tool,
                "params": clean_params,
            }
        )
    return steps or _fallback_plan(prompt)


def _openai_plan(prompt):
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=OPENAI_TIMEOUT)
    catalog = "\n".join(
        f"- {t['name']} ({t.get('category', '')}): {t['description']}"
        for t in tool_catalog()
    )
    system = SYSTEM_PROMPT.replace("{catalog}", catalog).replace(
        "{max_steps}", str(MAX_STEPS)
    )
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    content = resp.choices[0].message.content or "{}"
    data = json.loads(content)
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("Planner returned no steps")
    return _sanitize(raw_steps, prompt)


def create_plan(prompt):
    """Return (steps, planner_used)."""
    if OPENAI_API_KEY:
        try:
            return _openai_plan(prompt), "openai"
        except Exception:
            # Fall back to heuristics rather than failing the request.
            pass
    return _fallback_plan(prompt), "heuristic"
