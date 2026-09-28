"""
LanguageServer API client + local on-disk store reader.

Known issues addressed:
- Self-signed certificate → verify=False + suppress urllib3 warnings
- Unindexed conversations loaded on demand → just call with cascadeId
- API only available at runtime → all calls have timeout + friendly error messages
- API index is partial (only loaded workspaces) → merge with local
  conversation_summaries.db / *.db files which hold the full history
"""

import os
import sqlite3
from typing import Any, Optional

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_PATH = "exa.language_server_pb.LanguageServerService"


def call_api(
    port: int,
    csrf_token: str,
    method: str,
    params: Optional[dict] = None,
    timeout: int = 15,
) -> Optional[dict]:
    """Call LanguageServer gRPC-Web API.

    Args:
        port: Local port
        csrf_token: CSRF token extracted from process args
        method: API method name (e.g. "GetAllCascadeTrajectories")
        params: Request body
        timeout: Timeout in seconds

    Returns:
        Response JSON dict, or None on failure
    """
    url = f"https://localhost:{port}/{BASE_PATH}/{method}"
    headers = {
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "X-Codeium-Csrf-Token": csrf_token,
    }
    try:
        resp = requests.post(
            url, headers=headers, json=params or {}, verify=False, timeout=timeout
        )
        if resp.status_code == 200:
            return resp.json()
    except requests.exceptions.ConnectionError:
        pass
    except requests.exceptions.Timeout:
        pass
    except Exception:
        pass
    return None


def get_all_trajectories(port: int, csrf: str) -> dict[str, Any]:
    """Get all conversation summaries from a single LS instance."""
    result = call_api(port, csrf, "GetAllCascadeTrajectories", timeout=3)
    if not result:
        return {}
    return result.get("trajectorySummaries", {})


def get_all_trajectories_merged(endpoints: list[dict]) -> tuple[dict[str, Any], dict[str, dict], list[tuple]]:
    """Query all LS instances and merge/deduplicate conversation summaries.

    Args:
        endpoints: [{"port": int, "csrf": str, "pid": int}, ...]

    Returns:
        (merged_summaries, cascade_to_endpoint, failed_endpoints)
        - merged_summaries: {cascadeId: summary_dict}
        - cascade_to_endpoint: {cascadeId: {"port": int, "csrf": str}}
        - failed_endpoints: [(port, error_str)] for endpoints that timed out or failed
    """
    import concurrent.futures
    merged = {}
    cascade_ep = {}
    failed_eps = []

    def fetch(ep):
        return ep, get_all_trajectories(ep["port"], ep["csrf"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, max(1, len(endpoints)))) as executor:
        futures = {executor.submit(fetch, ep): ep for ep in endpoints}
        for future in concurrent.futures.as_completed(futures):
            ep = futures[future]
            try:
                _, summaries = future.result()
                if not summaries:
                    failed_eps.append((ep["port"], "empty response or timeout"))
                else:
                    for cid, info in summaries.items():
                        if cid not in merged:
                            merged[cid] = info
                            cascade_ep[cid] = {"port": ep["port"], "csrf": ep["csrf"]}
            except Exception as e:
                failed_eps.append((ep["port"], str(e)))

    return merged, cascade_ep, failed_eps


def get_trajectory_steps(
    port: int, csrf: str, cascade_id: str, step_count: int = 1000
) -> list[dict]:
    """Get all steps for a conversation.

    Supports on-demand loading for unindexed conversations — just request with cascadeId.

    Args:
        cascade_id: Conversation UUID
        step_count: Estimated step count (used to set endIndex)

    Returns:
        List of steps
    """
    result = call_api(
        port, csrf, "GetCascadeTrajectorySteps",
        {"cascadeId": cascade_id, "startIndex": 0, "endIndex": step_count + 10},
        timeout=30,
    )
    if not result:
        return []
    return result.get("steps", result.get("messages", []))


def default_conv_dir() -> str:
    """Local conversations directory (one SQLite .db per conversation)."""
    return os.path.expanduser("~/.gemini/antigravity/conversations")


def default_summaries_db() -> str:
    """Local full-history index (SQLite) covering all conversations on disk."""
    return os.path.expanduser("~/.gemini/antigravity/conversation_summaries.db")


def _normalize_time(value: Any) -> str:
    """Normalize disk timestamps to API-style ISO format for sorting/filtering."""
    if not value:
        return ""
    text = str(value)
    # Disk format: "2026-09-27 18:56:39.164163+00:00"
    # API format:  "2026-09-27T18:56:39.164163Z"
    return text.replace(" ", "T", 1).replace("+00:00", "Z")


def get_local_summaries(db_path: Optional[str] = None) -> dict[str, Any]:
    """Read the full conversation index from the local summaries database.

    The LanguageServer API only returns conversations loaded in memory
    (typically the active workspaces), while this database lists every
    conversation stored on disk.

    Args:
        db_path: Override path (defaults to ~/.gemini/antigravity/conversation_summaries.db)

    Returns:
        {cascadeId: summary_dict} with keys compatible with API summaries:
        summary / stepCount / lastModifiedTime / createdTime / workspace.
        Returns {} if the database is missing or unreadable.
    """
    path = db_path or default_summaries_db()
    if not os.path.isfile(path):
        return {}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                "SELECT conversation_id, title, preview, step_count,"
                " last_modified_time, workspace_uris FROM conversation_summaries"
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return {}
    summaries: dict[str, Any] = {}
    for cid, title, preview, step_count, last_modified, workspace_uris in rows:
        if not cid:
            continue
        summaries[cid] = {
            "summary": title or preview or f"(unindexed) {cid[:8]}...",
            "stepCount": step_count or 0,
            "lastModifiedTime": _normalize_time(last_modified),
            "createdTime": "",
            "workspace": workspace_uris or "",
        }
    return summaries


def scan_disk_conversation_ids(conv_dir: Optional[str] = None) -> set[str]:
    """List conversation IDs that have a data file on disk.

    Current Antigravity versions store one SQLite .db per conversation;
    older versions used .pb — both extensions are recognized.
    """
    directory = conv_dir or default_conv_dir()
    try:
        files = os.listdir(directory)
    except OSError:
        return set()
    ids = set()
    for name in files:
        if name.endswith((".db", ".pb")):
            ids.add(name.rsplit(".", 1)[0])
    return ids
