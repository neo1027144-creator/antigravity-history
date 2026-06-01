"""
Process discovery module — auto-detect running Antigravity LanguageServer instances.

Known issues addressed:
- CSRF Token changes on every restart → extracted from process cmdline args in real-time
- Port is dynamically assigned → scanned via netstat (Win) / lsof (Mac)
- Multiple workspaces = multiple language_server instances → scan all, test each
- macOS: Electron binds Unix Domain Sockets (not TCP) → lsof -i finds nothing → hang
  Fix: all subprocess calls have explicit timeout; Unix socket paths surfaced for future use
"""

import json
import platform
import re
import subprocess
from typing import Optional

from rich.console import Console

console = Console(stderr=True)


def discover_language_servers() -> list[dict]:
    """Discover all running language_server processes.

    Returns:
        [{"pid": int, "csrf": str, "cmd": str}, ...]
    """
    system = platform.system()
    if system == "Windows":
        return _discover_windows()
    elif system == "Darwin":
        return _discover_macos()
    else:
        console.print(f"[red]Unsupported platform: {system}[/red]")
        return []


def _discover_windows() -> list[dict]:
    """Windows: query language_server processes via WMI."""
    servers: list[dict] = []
    try:
        result = subprocess.run(
            [
                "powershell",
                "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'language_server*' } | "
                "Select-Object ProcessId, CommandLine | ConvertTo-Json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return servers

        data = json.loads(result.stdout)
        if isinstance(data, dict):
            data = [data]

        for proc in data:
            cmd = proc.get("CommandLine", "")
            pid = proc.get("ProcessId")
            if not cmd:
                continue
            csrf = ""
            if m := re.search(r"--csrf_token\s+(\S+)", cmd):
                csrf = m.group(1)
            servers.append({"pid": pid, "csrf": csrf, "cmd": cmd})
    except Exception as e:
        console.print(f"[yellow]WMI query failed: {e}[/yellow]")
    return servers


def _discover_macos() -> list[dict]:
    """macOS: query via pgrep + ps."""
    servers = []
    try:
        result = subprocess.run(
            ["pgrep", "-f", "language_server"], capture_output=True, text=True, timeout=5
        )
        for pid in result.stdout.strip().split("\n"):
            if not pid.strip():
                continue
            ps_result = subprocess.run(
                ["ps", "-p", pid, "-o", "args="], capture_output=True, text=True, timeout=5
            )
            cmd = ps_result.stdout.strip()
            if not cmd or "language_server" not in cmd:
                continue
            csrf = ""
            if m := re.search(r"--csrf_token\s+(\S+)", cmd):
                csrf = m.group(1)
            servers.append({"pid": int(pid), "csrf": csrf, "cmd": cmd})
    except subprocess.TimeoutExpired:
        console.print("[yellow]Process discovery timed out.[/yellow]")
    except Exception as e:
        console.print(f"[yellow]Process discovery failed: {e}[/yellow]")
    return servers


def find_ports(pid: int) -> list[int]:
    """Find ports the given process is listening on."""
    if platform.system() == "Windows":
        return _find_ports_windows(pid)
    else:
        return _find_ports_macos(pid)


def _find_ports_windows(pid: int) -> list[int]:
    """Windows: scan via netstat."""
    ports = []
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.split("\n"):
            if "LISTENING" in line and str(pid) in line:
                if m := re.search(r"127\.0\.0\.1:(\d+)", line):
                    ports.append(int(m.group(1)))
    except Exception:
        pass
    return ports


def _find_ports_macos(pid: int) -> list[int]:
    """macOS: scan TCP LISTEN ports via lsof.

    Note: On macOS, Electron-based apps (like Antigravity) typically bind
    Unix Domain Sockets rather than TCP ports. If no TCP ports are found,
    callers should fall back to _find_unix_sockets_macos().
    """
    ports = []
    try:
        result = subprocess.run(
            ["lsof", "-p", str(pid), "-i", "-P", "-n"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split("\n"):
            if "LISTEN" in line:
                if m := re.search(r":(\d+)\s+\(LISTEN\)", line):
                    ports.append(int(m.group(1)))
    except subprocess.TimeoutExpired:
        console.print(f"[yellow]lsof timed out for pid {pid}[/yellow]")
    except Exception:
        pass
    return ports


def _find_unix_sockets_macos(pid: int) -> list[str]:
    """macOS: find Unix Domain Socket paths for a process via lsof -U.

    Returns socket paths that look like Antigravity IPC endpoints.
    These are not yet used for API calls (future Phase 2 work).
    """
    sockets = []
    try:
        result = subprocess.run(
            ["lsof", "-p", str(pid), "-U"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split("\n"):
            parts = line.split()
            if len(parts) >= 9:
                path = parts[-1]
                # Filter to plausible Antigravity / Electron IPC sockets
                if any(kw in path for kw in ("antigravity", "Antigravity", "electron", "/tmp/")):
                    sockets.append(path)
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return sockets


def find_all_endpoints(
    servers: list[dict],
    manual_port: Optional[int] = None,
    manual_token: Optional[str] = None,
) -> list[dict]:
    """Discover all available (port, csrf, pid) endpoints.

    Returns:
        [{"port": int, "csrf": str, "pid": int}, ...]
    """
    if manual_port and manual_token:
        return [{"port": manual_port, "csrf": manual_token, "pid": 0}]

    from antigravity_history.api import call_api

    endpoints = []
    seen_ports = set()

    for srv in servers:
        ports = find_ports(srv["pid"])
        for port in ports:
            if port in seen_ports:
                continue
            result = call_api(port, srv["csrf"], "GetAllCascadeTrajectories", timeout=5)
            if result is not None:
                endpoints.append({"port": port, "csrf": srv["csrf"], "pid": srv["pid"]})
                seen_ports.add(port)
                break  # Only need one port per process

    return endpoints


def find_working_endpoint(
    servers: list[dict],
    manual_port: Optional[int] = None,
    manual_token: Optional[str] = None,
) -> tuple[Optional[int], Optional[str], Optional[int]]:
    """Compatibility wrapper: return the first available endpoint."""
    endpoints = find_all_endpoints(servers, manual_port, manual_token)
    if endpoints:
        ep = endpoints[0]
        return ep["port"], ep["csrf"], ep["pid"]
    return None, None, None
