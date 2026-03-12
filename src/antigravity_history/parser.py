"""
Step parser — parse raw API steps into structured messages.

Three-level field strategy:
  default:  response, userResponse, basic tool calls
  thinking: + thinking, timestamp, exitCode, cwd, stopReason
  full:     + diff, combinedOutput, searchSummary, model, thinkingDuration

Supports 14 step types (10 content types + 4 system types skipped)
"""

from typing import Optional


class FieldLevel:
    """Field export level."""
    DEFAULT = "default"
    THINKING = "thinking"
    FULL = "full"


def parse_steps(
    steps: list[dict],
    level: str = FieldLevel.DEFAULT,
) -> list[dict]:
    """Parse raw steps into a list of structured messages.

    Args:
        steps: Raw steps returned by the API
        level: Field level (default / thinking / full)

    Returns:
        [{"role": "user"|"assistant"|"tool", "content": str, ...}, ...]
    """
    include_thinking = level in (FieldLevel.THINKING, FieldLevel.FULL)
    include_full = level == FieldLevel.FULL

    messages = []
    for step in steps:
        step_type = step.get("type", "")
        metadata = step.get("metadata", {})
        timestamp = metadata.get("createdAt") if include_thinking else None

        msg = _parse_step(step, step_type, include_thinking, include_full)
        if msg is None:
            continue

        if timestamp:
            msg["timestamp"] = timestamp
        messages.append(msg)

    return messages


def _parse_step(
    step: dict,
    step_type: str,
    include_thinking: bool,
    include_full: bool,
) -> Optional[dict]:
    """Parse a single step, return a message dict or None (skip system types)."""

    # ── User input ──
    if step_type == "CORTEX_STEP_TYPE_USER_INPUT":
        return _parse_user_input(step, include_full)

    # ── AI response (core) ──
    if step_type == "CORTEX_STEP_TYPE_PLANNER_RESPONSE":
        return _parse_planner_response(step, include_thinking, include_full)

    # ── Code edit ──
    if step_type == "CORTEX_STEP_TYPE_CODE_ACTION":
        return _parse_code_action(step, include_full)

    # ── Terminal command ──
    if step_type == "CORTEX_STEP_TYPE_RUN_COMMAND":
        return _parse_run_command(step, include_thinking, include_full)

    # ── View file ──
    if step_type == "CORTEX_STEP_TYPE_VIEW_FILE":
        return _parse_view_file(step, include_thinking)

    # ── File search ──
    if step_type == "CORTEX_STEP_TYPE_FIND":
        find = step.get("find", {})
        return {"role": "tool", "tool_name": "find", "content": find.get("query", "[File Search]")}

    # ── List directory ──
    if step_type == "CORTEX_STEP_TYPE_LIST_DIRECTORY":
        ld = step.get("listDirectory", {})
        path = ld.get("directoryPath", ld.get("path", ""))
        return {"role": "tool", "tool_name": "list_dir", "content": path or "[List Directory]"}

    # ── Web search ──
    if step_type == "CORTEX_STEP_TYPE_SEARCH_WEB":
        return _parse_search_web(step, include_full)

    # ── Read URL content ──
    if step_type == "CORTEX_STEP_TYPE_READ_URL_CONTENT":
        ru = step.get("readUrlContent", {})
        return {"role": "tool", "tool_name": "read_url", "content": ru.get("url", "[Read URL]")}

    # ── Command status check ──
    if step_type == "CORTEX_STEP_TYPE_COMMAND_STATUS":
        return {"role": "tool", "tool_name": "command_status", "content": "[Check Command Status]"}

    # ── System types: skip ──
    # EPHEMERAL_MESSAGE, CONVERSATION_HISTORY, CHECKPOINT, KNOWLEDGE_ARTIFACTS, ERROR_MESSAGE
    return None


# ─────────────────────────────────
# Diff normalization
# ─────────────────────────────────

_DIFF_PREFIX = {
    "UNIFIED_DIFF_LINE_TYPE_INSERT": "+",
    "UNIFIED_DIFF_LINE_TYPE_DELETE": "-",
    "UNIFIED_DIFF_LINE_TYPE_CONTEXT": " ",
}


def _normalize_diff(diff) -> str:
    """Normalize diff to string. API may return str or structured dict."""
    if isinstance(diff, str):
        return diff
    if isinstance(diff, dict):
        lines_data = diff.get("unifiedDiff", {}).get("lines", [])
        if not lines_data:
            return str(diff)
        parts = []
        for line in lines_data:
            text = line.get("text", "")
            prefix = _DIFF_PREFIX.get(line.get("type", ""), " ")
            parts.append(f"{prefix}{text}")
        return "\n".join(parts)
    return str(diff)

def _parse_user_input(step: dict, include_full: bool) -> Optional[dict]:
    ui = step.get("userInput", {})
    content = ui.get("userResponse", "")
    if not content:
        return None

    msg = {"role": "user", "content": content}

    if include_full:
        # Editor state (full level only)
        state = ui.get("activeUserState", {})
        active_doc = state.get("activeDocument", {})
        if active_doc.get("absoluteUri"):
            msg["active_file"] = active_doc["absoluteUri"]
            msg["editor_language"] = active_doc.get("editorLanguage", "")

    return msg


def _parse_planner_response(
    step: dict, include_thinking: bool, include_full: bool
) -> Optional[dict]:
    pr = step.get("plannerResponse", {})
    # Prefer modifiedResponse (post-processed), fall back to response
    content = pr.get("modifiedResponse") or pr.get("response", "")
    if not content:
        return None

    msg = {"role": "assistant", "content": content}

    # thinking level: include reasoning chain, stop reason
    if include_thinking:
        thinking = pr.get("thinking")
        if thinking:
            msg["thinking"] = thinking
        stop_reason = pr.get("stopReason")
        if stop_reason:
            msg["stop_reason"] = stop_reason

    # full level: include model name, thinking duration, message ID
    if include_full:
        metadata = step.get("metadata", {})
        model = metadata.get("generatorModel")
        if model:
            msg["model"] = model
        thinking_duration = pr.get("thinkingDuration")
        if thinking_duration:
            msg["thinking_duration"] = thinking_duration
        message_id = pr.get("messageId")
        if message_id:
            msg["message_id"] = message_id

    return msg


def _parse_code_action(step: dict, include_full: bool) -> Optional[dict]:
    ca = step.get("codeAction", {})
    description = ca.get("description", "")

    # File path: prefer actionResult, fall back to actionSpec
    file_path = ""
    action_result = ca.get("actionResult", {})
    edit = action_result.get("edit", {})
    if edit.get("absoluteUri"):
        file_path = edit["absoluteUri"]
    elif ca.get("actionSpec", {}).get("createFile", {}).get("path"):
        file_path = ca["actionSpec"]["createFile"]["path"]

    summary = f"[Code Edit] {file_path}" if file_path else "[Code Edit]"
    if description:
        summary += f"\n{description}"

    msg = {"role": "tool", "tool_name": "code_edit", "content": summary}

    if file_path:
        msg["file_path"] = file_path

    # full level: include diff
    if include_full:
        diff = edit.get("diff")
        if diff:
            msg["diff"] = _normalize_diff(diff)
        # artifact metadata
        artifact = ca.get("artifactMetadata", {})
        if artifact.get("summary"):
            msg["artifact_summary"] = artifact["summary"]
        if artifact.get("artifactType"):
            msg["artifact_type"] = artifact["artifactType"]
        is_artifact = ca.get("isArtifactFile")
        if is_artifact:
            msg["is_artifact"] = True

    return msg


def _parse_run_command(
    step: dict, include_thinking: bool, include_full: bool
) -> Optional[dict]:
    rc = step.get("runCommand", {})
    command = rc.get("commandLine", rc.get("command", ""))
    if not command:
        return None

    msg = {"role": "tool", "tool_name": "run_command", "content": command}

    # thinking level: working directory, exit code
    if include_thinking:
        cwd = rc.get("cwd")
        if cwd:
            msg["cwd"] = cwd
        exit_code = rc.get("exitCode")
        if exit_code is not None:
            msg["exit_code"] = exit_code

    # full level: full command output
    if include_full:
        output = rc.get("combinedOutput", {}).get("full")
        if output:
            msg["output"] = output

    return msg


def _parse_view_file(step: dict, include_thinking: bool) -> Optional[dict]:
    vf = step.get("viewFile", {})
    path = vf.get("absolutePathUri", vf.get("filePath", vf.get("path", "")))
    if not path:
        return None

    msg = {"role": "tool", "tool_name": "view_file", "content": path}

    # thinking level: file size info
    if include_thinking:
        num_lines = vf.get("numLines")
        num_bytes = vf.get("numBytes")
        if num_lines:
            msg["num_lines"] = num_lines
        if num_bytes:
            msg["num_bytes"] = num_bytes

    # Note: never export viewFile.content (full file content, too large and redundant)
    return msg


def _parse_search_web(step: dict, include_full: bool) -> Optional[dict]:
    sw = step.get("searchWeb", {})
    query = sw.get("query", "")

    msg = {"role": "tool", "tool_name": "search_web", "content": query or "[Web Search]"}

    # full level: complete search results summary (~7KB)
    if include_full:
        summary = sw.get("summary")
        if summary:
            msg["search_summary"] = summary
        provider = sw.get("thirdPartyConfig", {}).get("provider")
        if provider:
            msg["search_provider"] = provider

    return msg
