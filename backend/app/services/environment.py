"""Detect the environment the backend executor is running in.

Used to explain *why* a diagnostic may be unavailable (container vs host,
WSL vs native Linux, missing binaries, broken PATH) instead of failing
opaquely. All probes are local, read-only, and bounded.
"""
import os
import platform
import shutil
import subprocess
from pathlib import Path

# Executables the diagnostic engine may need, mapped to a human hint.
REQUIRED_TOOLS = {
    "uname": "core system info",
    "uptime": "uptime / load average",
    "nproc": "CPU count",
    "df": "disk usage",
    "free": "memory usage",
    "ps": "process list",
    "cat": "file reads (/proc)",
    "ss": "listening ports",
    "docker": "Docker diagnostics",
    "journalctl": "system journal logs",
    "systemctl": "systemd services",
}

# Extra search dirs used when PATH is broken or minimal (e.g. container).
FALLBACK_BIN_DIRS = [
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
    "/snap/bin",
]

# Docker Desktop on Windows exposes docker.exe inside WSL at predictable paths.
WSL_DOCKER_CANDIDATES = [
    "/usr/bin/docker.exe",
    "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe",
]


def _current_user():
    try:
        return os.getlogin()
    except OSError:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def _read_os_release():
    info = {"pretty_name": "", "id": "", "version_id": ""}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            for line in f:
                if "=" not in line:
                    continue
                k, v = line.strip().split("=", 1)
                v = v.strip('"')
                if k == "PRETTY_NAME":
                    info["pretty_name"] = v
                elif k == "ID":
                    info["id"] = v
                elif k == "VERSION_ID":
                    info["version_id"] = v
    except OSError:
        pass
    return info


def _is_container():
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as f:
            return "docker" in f.read() or "kubepods" in f.read() or "containerd" in f.read()
    except OSError:
        return False


def _is_wsl():
    if os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _virtualization():
    try:
        out = subprocess.run(
            ["systemd-detect-virt"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        virt = out.stdout.strip()
        if virt and virt != "none":
            return virt
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _docker_socket_available():
    for sock in ("/var/run/docker.sock", os.environ.get("DOCKER_HOST", "")):
        if sock.startswith("unix://"):
            sock = sock[len("unix://"):]
        if sock and Path(sock).exists():
            return True
    return False


def resolve_executable(name):
    """Resolve an executable to an absolute path.

    Order: PATH lookup (via shutil.which), then standard bin dirs, then
    WSL Docker Desktop candidates for 'docker'. Returns None if absent.
    """
    found = shutil.which(name)
    if found:
        return found
    for d in FALLBACK_BIN_DIRS:
        p = Path(d) / name
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    if name == "docker":
        for p in WSL_DOCKER_CANDIDATES:
            if Path(p).is_file() and os.access(p, os.X_OK):
                return p
        # docker.exe on PATH (WSL interop appends Windows PATH sometimes)
        found = shutil.which("docker.exe")
        if found:
            return found
    return None


def get_environment_report():
    """Return a JSON-serializable description of the execution environment."""
    osr = _read_os_release()
    path_env = os.environ.get("PATH", "")
    container = _is_container()
    wsl = _is_wsl()

    tools = {}
    for name, hint in REQUIRED_TOOLS.items():
        path = resolve_executable(name)
        tools[name] = {
            "available": path is not None,
            "path": path,
            "used_for": hint,
        }

    systemd = tools.get("systemctl", {}).get("available", False)
    journald = tools.get("journalctl", {}).get("available", False)
    docker_ok = tools.get("docker", {}).get("available", False)
    virt = _virtualization()

    warnings = []
    if container:
        warnings.append(
            "Backend is running inside a container. Host commands like docker, "
            "systemctl and journalctl are unavailable unless the container is "
            "explicitly configured with host access. Run the backend on the host "
            "for full diagnostics."
        )
    if not docker_ok:
        warnings.append(
            "Docker CLI not found. Install Docker, ensure it is on PATH, or on "
            "WSL enable Docker Desktop integration. Docker diagnostics will be skipped."
        )
    elif not _docker_socket_available():
        warnings.append(
            "Docker CLI found but no reachable Docker socket "
            "(/var/run/docker.sock or DOCKER_HOST). The daemon may not be running "
            "or the user may lack permission (try the docker group)."
        )
    if not journald:
        warnings.append(
            "journalctl not found. System journal logs are unavailable on this "
            "system (non-systemd or minimal environment)."
        )
    if not systemd:
        warnings.append(
            "systemctl not found. Systemd service diagnostics are unavailable."
        )
    missing_core = [n for n in ("df", "free", "ps", "ss") if not tools[n]["available"]]
    if missing_core:
        warnings.append(f"Core utilities missing: {', '.join(missing_core)}.")

    return {
        "os": {
            "system": platform.system(),
            "pretty_name": osr["pretty_name"] or platform.platform(),
            "id": osr["id"],
            "version_id": osr["version_id"],
            "kernel": platform.release(),
            "architecture": platform.machine(),
        },
        "runtime": {
            "environment": (
                "container" if container
                else "wsl" if wsl
                else "virtual_machine" if virt not in ("none", "unknown")
                else "host"
            ),
            "virtualization": virt if not (wsl or container) else ("wsl" if wsl else "container"),
            "user": _current_user(),
            "uid": os.getuid() if hasattr(os, "getuid") else None,
            "is_root": hasattr(os, "getuid") and os.getuid() == 0,
            "path": path_env,
            "python": platform.python_version(),
        },
        "docker": {
            "cli_available": docker_ok,
            "cli_path": tools["docker"]["path"],
            "socket_available": _docker_socket_available(),
            "docker_host": os.environ.get("DOCKER_HOST", ""),
        },
        "systemd": {
            "available": systemd,
            "journalctl_available": journald,
        },
        "tools": tools,
        "warnings": warnings,
    }
