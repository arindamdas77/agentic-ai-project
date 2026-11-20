"""Allowlisted read-only diagnostic tools and the execution engine.

Security model:
- Every tool is a fixed argv list executed via subprocess with shell=False.
  No model- or user-generated command strings are ever executed.
- Params are whitelisted and strictly validated (ints, bools, safe names).
- Executables are resolved to absolute paths before execution; missing
  binaries produce actionable errors instead of opaque "command not found".
- A sanitized environment (PATH guaranteed to include standard bin dirs) is
  used so a broken backend PATH cannot hide installed tools.
- Every command is bounded by a timeout and its output is truncated.
"""
import os
import re
import subprocess
import time

from app.services.environment import FALLBACK_BIN_DIRS, resolve_executable

TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 12000
MAX_COMMANDS_PER_TOOL = 6

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,127}$")
_UNIT_RE = re.compile(r"^[a-zA-Z0-9_.@-]{1,100}$")


def _cmd(*args):
    return [str(a) for a in args]


def _clamp_int(params, key, default, lo, hi):
    try:
        n = int((params or {}).get(key, default))
    except (TypeError, ValueError):
        n = default
    return max(lo, min(n, hi))


def _safe_name(params, key):
    """Return a validated container/unit name or None."""
    v = (params or {}).get(key)
    if isinstance(v, str) and _NAME_RE.match(v):
        return v
    return None


def _safe_unit(params):
    v = (params or {}).get("unit")
    if isinstance(v, str) and _UNIT_RE.match(v):
        return v
    return None


def _docker_ps(params):
    argv = _cmd("docker", "ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}")
    if (params or {}).get("all"):
        argv.insert(2, "-a")
    return [argv]


def _service_logs(params):
    unit = _safe_unit(params)
    lines = _clamp_int(params, "lines", 100, 10, 500)
    if unit:
        return [_cmd("journalctl", "-u", unit, "-n", str(lines), "--no-pager")]
    return [_cmd("journalctl", "-n", str(lines), "--no-pager")]


# name -> spec. `argv(params)` returns a list of argv lists; an argv list may
# start with "@dynamic:<kind>" to request runtime expansion (see run_tool).
TOOLS = {
    # ---- System information ----
    "system_info": {
        "description": "OS distribution, kernel, uptime, CPU and memory summary",
        "category": "system",
        "requires": ["uname", "uptime", "nproc", "free"],
        "argv": lambda p: [
            _cmd("uname", "-a"),
            _cmd("cat", "/etc/os-release"),
            _cmd("uptime"),
            _cmd("nproc"),
            _cmd("free", "-h"),
        ],
    },
    "system_overview": {
        "description": "OS, kernel, uptime and CPU count",
        "category": "system",
        "requires": ["uname", "uptime", "nproc"],
        "argv": lambda p: [
            _cmd("uname", "-a"),
            _cmd("uptime"),
            _cmd("nproc"),
        ],
    },
    "cpu_load": {
        "description": "Load averages and top CPU-consuming processes",
        "category": "system",
        "requires": ["ps"],
        "argv": lambda p: [
            _cmd("cat", "/proc/loadavg"),
            _cmd("ps", "-eo", "pid,comm,%cpu,%mem", "--sort=-%cpu"),
        ],
    },
    "cpu_info": {
        "description": "CPU model, cores and flags",
        "category": "system",
        "requires": ["lscpu"],
        "argv": lambda p: [_cmd("lscpu")],
    },
    "memory_usage": {
        "description": "Memory and swap usage (free -h)",
        "category": "system",
        "requires": ["free"],
        "argv": lambda p: [_cmd("free", "-h")],
    },
    "disk_usage": {
        "description": "Filesystem disk usage (df -h)",
        "category": "system",
        "requires": ["df"],
        "argv": lambda p: [_cmd("df", "-h", "-x", "tmpfs", "-x", "devtmpfs")],
    },
    "uptime": {
        "description": "System uptime and load average",
        "category": "system",
        "requires": ["uptime", "cat"],
        "argv": lambda p: [
            _cmd("uptime"),
            _cmd("cat", "/proc/loadavg"),
        ],
    },
    "top_processes": {
        "description": "Top processes by CPU and memory",
        "category": "system",
        "requires": ["ps"],
        "argv": lambda p: [
            _cmd("ps", "-eo", "pid,user,%cpu,%mem,comm", "--sort=-%cpu"),
            _cmd("ps", "-eo", "pid,user,%cpu,%mem,comm", "--sort=-%mem"),
        ],
    },
    "listening_ports": {
        "description": "TCP/UDP listening sockets",
        "category": "network",
        "requires": ["ss"],
        "argv": lambda p: [_cmd("ss", "-tuln")],
    },
    "failed_services": {
        "description": "Failed systemd units",
        "category": "services",
        "requires": ["systemctl"],
        "argv": lambda p: [_cmd("systemctl", "--failed", "--no-pager", "--plain")],
    },
    "active_services": {
        "description": "Active systemd services summary",
        "category": "services",
        "requires": ["systemctl"],
        "argv": lambda p: [_cmd("systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain")],
    },

    # ---- Docker diagnostics ----
    "docker_version": {
        "description": "Docker client and daemon version",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [_cmd("docker", "version")],
    },
    "docker_ps": {
        "description": "Running Docker containers (or all with params.all)",
        "category": "docker",
        "requires": ["docker"],
        "argv": _docker_ps,
    },
    "docker_images": {
        "description": "Docker images",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [_cmd("docker", "images", "--format", "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}")],
    },
    "docker_system_df": {
        "description": "Docker disk usage summary",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [_cmd("docker", "system", "df")],
    },
    "docker_volumes": {
        "description": "Docker volumes",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [_cmd("docker", "volume", "ls")],
    },
    "docker_networks": {
        "description": "Docker networks",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [_cmd("docker", "network", "ls")],
    },
    "docker_stats": {
        "description": "Live CPU/memory usage of running containers (single snapshot)",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [_cmd("docker", "stats", "--no-stream", "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}")],
    },
    "docker_logs": {
        "description": "Container logs (params.container, or all running containers)",
        "category": "docker",
        "requires": ["docker"],
        "argv": lambda p: [],
        "dynamic": "docker_logs",
    },

    # ---- System logs / services ----
    "journal_logs": {
        "description": "Recent system journal entries (params.unit for a specific service)",
        "category": "logs",
        "requires": ["journalctl"],
        "argv": _service_logs,
    },
    "service_status": {
        "description": "Status of a systemd service (params.unit)",
        "category": "services",
        "requires": ["systemctl"],
        "argv": lambda p: [
            _cmd("systemctl", "status", *([_safe_unit(p)] if _safe_unit(p) else []), "--no-pager", "-l")
        ],
    },
}

# Params that the planner/sanitizer may pass through.
ALLOWED_PARAMS = {"lines", "all", "container", "unit"}


def tool_catalog():
    return [
        {"name": name, "description": spec["description"], "category": spec.get("category", "")}
        for name, spec in TOOLS.items()
    ]


def _subprocess_env():
    """Environment for child processes with a guaranteed-complete PATH."""
    env = dict(os.environ)
    path = env.get("PATH", "")
    dirs = path.split(os.pathsep) if path else []
    for d in FALLBACK_BIN_DIRS:
        if d not in dirs:
            dirs.append(d)
    env["PATH"] = os.pathsep.join(dirs)
    return env


def _hint_for(stderr, argv0):
    s = (stderr or "").lower()
    if "cannot connect to the docker daemon" in s or "is the docker daemon running" in s:
        return "Docker daemon is not reachable. Start Docker (or Docker Desktop) and ensure your user can access the socket."
    if "permission denied" in s and "docker" in argv0:
        return "Permission denied talking to Docker. Add your user to the 'docker' group or check socket permissions."
    if "access denied" in s or "must be run as root" in s or "need to be root" in s:
        return "This command requires elevated privileges. Run the backend as root or grant the needed permissions."
    if "no entries" in s:
        return None
    return None


def _run_argv(argv):
    """Run one argv list. Returns a result dict."""
    exe = argv[0]
    resolved = resolve_executable(exe)
    display = list(argv)
    if resolved:
        display[0] = resolved
    entry = {
        "argv": display,
        "command": " ".join(display),
        "exit_code": None,
        "duration_ms": None,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "missing": False,
    }
    if not resolved:
        entry["missing"] = True
        entry["stderr"] = f"executable not found: {exe}"
        return entry
    start = time.monotonic()
    try:
        proc = subprocess.run(
            display,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
            env=_subprocess_env(),
        )
        entry["duration_ms"] = round((time.monotonic() - start) * 1000)
        entry["exit_code"] = proc.returncode
        entry["stdout"] = proc.stdout
        entry["stderr"] = proc.stderr
    except subprocess.TimeoutExpired:
        entry["duration_ms"] = round((time.monotonic() - start) * 1000)
        entry["timed_out"] = True
        entry["stderr"] = f"timed out after {TIMEOUT_SECONDS}s"
    except OSError as e:
        entry["duration_ms"] = round((time.monotonic() - start) * 1000)
        entry["stderr"] = f"failed to execute: {e}"
    return entry


def _expand_dynamic(tool_name, params):
    """Expand runtime-dependent argv lists.

    Returns (pre_entry, argv_lists): pre_entry is an already-executed probe
    result to include in the output (e.g. the container list used to pick
    log targets), argv_lists are commands still to run.
    """
    if tool_name != "docker_logs":
        return None, []
    container = _safe_name(params, "container")
    tail = _clamp_int(params, "lines", 100, 10, 1000)
    if container:
        return None, [["docker", "logs", "--tail", str(tail), container]]
    # No container given: list running containers, then log the first 3.
    listing = _run_argv(["docker", "ps", "--format", "{{.Names}}"])
    names = [n for n in listing["stdout"].splitlines() if n.strip()][:3]
    argv_lists = [["docker", "logs", "--tail", str(tail), n] for n in names]
    if not names:
        listing["stderr"] = (listing["stderr"] or "") + (
            "no running containers found" if not listing["stderr"] else "; no running containers found"
        )
        listing["exit_code"] = listing["exit_code"] if listing["exit_code"] is not None else 0
    return listing, argv_lists


def _missing_executables(argv_lists):
    missing = []
    for argv in argv_lists:
        exe = argv[0]
        if not resolve_executable(exe) and exe not in missing:
            missing.append(exe)
    return missing


def run_tool(name, params=None):
    """Execute an allowlisted diagnostic.

    Returns {tool, description, commands, results, output, error, timed_out}.
    Never raises for missing binaries, timeouts or non-zero exits.
    """
    spec = TOOLS.get(name)
    if not spec:
        return {
            "tool": name, "description": "", "commands": [], "results": [],
            "output": "", "error": f"Unknown tool: {name}", "timed_out": False,
        }
    params = params or {}

    pre_entry = None
    if spec.get("dynamic"):
        pre_entry, argv_lists = _expand_dynamic(spec["dynamic"], params)
    else:
        argv_lists = spec["argv"](params)
    argv_lists = argv_lists[:MAX_COMMANDS_PER_TOOL]

    missing = _missing_executables(argv_lists)
    errors = []
    sections = []
    results = []
    timed_out = False

    if missing:
        for exe in missing:
            hint = (
                "Docker CLI not found — install Docker or enable Docker Desktop "
                "integration (WSL). Docker diagnostics are unavailable."
                if exe.startswith("docker")
                else "journalctl not found — system journal logs require systemd."
                if exe == "journalctl"
                else f"{exe}: executable not found on this system."
            )
            errors.append(hint)
        results.append({
            "argv": [], "command": "", "exit_code": None, "duration_ms": None,
            "stdout": "", "stderr": "; ".join(errors), "timed_out": False,
            "missing": True,
        })
    else:
        if pre_entry is not None:
            results.append(pre_entry)
            out = (pre_entry["stdout"] or "").strip()
            if out:
                sections.append(f"$ {' '.join(pre_entry['argv'])}\n{out}")
            if "no running containers found" in (pre_entry["stderr"] or ""):
                sections.append("$ docker ps\n(no running containers found — nothing to collect logs from)")
            if pre_entry["missing"]:
                errors.append(f"docker: executable not found — install Docker or enable Docker Desktop integration.")
            elif pre_entry["exit_code"] not in (None, 0):
                errors.append(
                    f"{' '.join(pre_entry['argv'])}: {(pre_entry['stderr'] or '').strip() or 'probe failed'}"
                )
        for argv in argv_lists:
            r = _run_argv(argv)
            results.append(r)
            if r["missing"]:
                errors.append(f"{argv[0]}: executable not found")
                continue
            if r["timed_out"]:
                timed_out = True
                errors.append(f"{' '.join(argv)}: timed out after {TIMEOUT_SECONDS}s")
                continue
            if r["exit_code"] != 0:
                err = (r["stderr"] or "").strip() or f"exit code {r['exit_code']}"
                errors.append(f"{' '.join(argv)}: {err}")
                hint = _hint_for(r["stderr"], argv[0])
                if hint:
                    errors.append(f"hint: {hint}")
            out = (r["stdout"] or "").strip()
            if out:
                sections.append(f"$ {' '.join(argv)}\n{out}")
            elif r["exit_code"] == 0 and r["stderr"].strip():
                sections.append(f"$ {' '.join(argv)}\n{r['stderr'].strip()}")

    output = "\n\n".join(sections)
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n... [truncated]"
    return {
        "tool": name,
        "description": spec["description"],
        "commands": [" ".join(a) for a in argv_lists],
        "results": results,
        "output": output,
        "error": "; ".join(dict.fromkeys(errors)) or None,
        "timed_out": timed_out,
    }
