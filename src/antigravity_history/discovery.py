"""
Process discovery module — auto-detect running Antigravity LanguageServer instances.

Known issues addressed:
- CSRF Token changes on every restart → extracted from process cmdline args in real-time
- Port is dynamically assigned → scanned via netstat (Win) / lsof (Mac) / netstat or ss (Linux)
- Multiple workspaces = multiple language_server instances → scan all, test each
"""

import json
import platform
import re
import subprocess
import os
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
    elif system == "Linux":
        return _discover_linux()
    else:
        console.print(f"[red]Unsupported platform: {system}[/red]")
        return []


def _discover_linux() -> list[dict]:
    """Linux: query via pgrep + /proc."""
    servers = []
    try:
        # Find language_server process
        result = subprocess.run(
            ["pgrep", "-f", "language_server"],
            capture_output=True, text=True
        )
        for pid in result.stdout.strip().split('\n'):
            if not pid.strip():
                continue
            pid_val = pid.strip()
            
            # Read cmdline (null-separated)
            try:
                with open(f"/proc/{pid_val}/cmdline", "rb") as f:
                    cmd_raw = f.read()
                    cmd = cmd_raw.replace(b'\x00', b' ').decode('utf-8', errors='replace')
            except (IOError, PermissionError):
                continue
            
            csrf = ""
            if m := re.search(r'--csrf_token\s+(\S+)', cmd):
                csrf = m.group(1)
            
            servers.append({"pid": int(pid_val), "csrf": csrf, "cmd": cmd})
    except Exception as e:
        console.print(f"[yellow]Linux discovery failed: {e}[/yellow]")
    return servers


def _discover_windows() -> list[dict]:
    """Windows: query language_server processes via WMI."""
    servers = []
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'language_server*' } | "
             "Select-Object ProcessId, CommandLine | ConvertTo-Json"],
            capture_output=True, text=True, timeout=15
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
            if m := re.search(r'--csrf_token\s+(\S+)', cmd):
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
            ["pgrep", "-f", "language_server_macos"],
            capture_output=True, text=True
        )
        for pid in result.stdout.strip().split('\n'):
            if not pid.strip():
                continue
            ps_result = subprocess.run(
                ["ps", "-p", pid, "-o", "args="],
                capture_output=True, text=True
            )
            cmd = ps_result.stdout.strip()
            csrf = ""
            if m := re.search(r'--csrf_token\s+(\S+)', cmd):
                csrf = m.group(1)
            servers.append({"pid": int(pid), "csrf": csrf, "cmd": cmd})
    except Exception as e:
        console.print(f"[yellow]Process discovery failed: {e}[/yellow]")
    return servers


def find_ports(pid: int) -> list[int]:
    """Find ports the given process is listening on."""
    system = platform.system()
    if system == "Windows":
        return _find_ports_windows(pid)
    elif system == "Darwin":
        return _find_ports_macos(pid)
    elif system == "Linux":
        return _find_ports_linux(pid)
    else:
        return []


def _find_ports_linux(pid: int) -> list[int]:
    """Linux: scan via netstat or ss."""
    ports = []
    # Try netstat
    try:
        result = subprocess.run(
            ["netstat", "-lnpt"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n'):
            if f"{pid}/" in line and "LISTEN" in line:
                if m := re.search(r':(\d+)\s+', line):
                    ports.append(int(m.group(1)))
    except Exception:
        pass
    
    if ports: 
        return list(set(ports))

    # Try ss
    try:
        result = subprocess.run(
            ["ss", "-lnpt"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.split('\n'):
            if f"pid={pid}," in line:
                parts = line.split()
                if len(parts) >= 4:
                    local_addr = parts[3]
                    if m := re.search(r':(\d+)$', local_addr):
                        ports.append(int(m.group(1)))
    except Exception:
        pass
    return list(set(ports))


def _find_ports_windows(pid: int) -> list[int]:
    """Windows: scan via netstat."""
    ports = []
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.split('\n'):
            if "LISTENING" in line and str(pid) in line:
                if m := re.search(r'127\.0\.0\.1:(\d+)', line):
                    ports.append(int(m.group(1)))
    except Exception:
        pass
    return ports


def _find_ports_macos(pid: int) -> list[int]:
    """macOS: scan via lsof."""
    ports = []
    try:
        result = subprocess.run(
            ["lsof", "-p", str(pid), "-i", "-P", "-n"],
            capture_output=True, text=True
        )
        for line in result.stdout.split('\n'):
            if "LISTEN" in line:
                if m := re.search(r':(\d+)\s+\(LISTEN\)', line):
                    ports.append(int(m.group(1)))
    except Exception:
        pass
    return ports


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
