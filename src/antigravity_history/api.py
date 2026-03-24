"""
LanguageServer API client.

Known issues addressed:
- Self-signed certificate → verify=False + suppress urllib3 warnings
- Unindexed conversations loaded on demand → just call with cascadeId
- API only available at runtime → all calls have timeout + friendly error messages
"""

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
