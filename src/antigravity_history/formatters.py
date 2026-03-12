"""
Formatted output — Markdown / Obsidian.

Each formatter function takes Conversation data and returns a formatted string.
Kept simple and direct; no ABC abstractions; refactored when needed.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


# ════════════════════════════════
# Markdown format
# ════════════════════════════════

def format_markdown(
    title: str,
    cascade_id: str,
    metadata: dict,
    messages: list[dict],
) -> str:
    """Format a conversation as a Markdown string."""
    lines = [
        f"# {title}", "",
        f"- **Cascade ID**: `{cascade_id}`",
        f"- **Steps**: {metadata.get('stepCount', '?')}",
        f"- **Status**: {metadata.get('status', '?')}",
        f"- **Created**: {metadata.get('createdTime', '?')}",
        f"- **Last Modified**: {metadata.get('lastModifiedTime', '?')}",
        f"- **Last User Input**: {metadata.get('lastUserInputTime', '?')}",
    ]

    # Workspace info
    workspaces = metadata.get("workspaces", [])
    if not workspaces:
        # fallback: try trajectoryMetadata.workspaces
        workspaces = metadata.get("trajectoryMetadata", {}).get("workspaces", [])
    if workspaces:
        ws_uris = [w.get("workspaceFolderAbsoluteUri", "") for w in workspaces if w.get("workspaceFolderAbsoluteUri")]
        if ws_uris:
            lines.append(f"- **Workspace**: {', '.join(ws_uris)}")

    lines.extend([
        f"- **Exported**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "", "---", ""
    ])

    for msg in messages:
        lines.extend(_format_message_md(msg))

    return "\n".join(lines)


def _format_message_md(msg: dict) -> list[str]:
    """Format a single message as Markdown lines."""
    role = msg.get("role", "")
    content = msg.get("content", "")
    timestamp = msg.get("timestamp", "")
    ts_suffix = f"  `{timestamp[:19]}`" if timestamp else ""

    lines = []

    if role == "user":
        lines.append(f"## 🧑 User{ts_suffix}")
        lines.append(content)
        lines.append("")

    elif role == "assistant":
        lines.append(f"## 🤖 Assistant{ts_suffix}")
        # thinking (if present)
        thinking = msg.get("thinking")
        if thinking:
            lines.append("<details><summary>💭 Thinking</summary>")
            lines.append("")
            lines.append(thinking)
            lines.append("")
            lines.append("</details>")
            lines.append("")
        lines.append(content)
        # Meta info
        extras = []
        if msg.get("model"):
            extras.append(f"Model: `{msg['model']}`")
        if msg.get("stop_reason"):
            extras.append(f"Stop: `{msg['stop_reason']}`")
        if msg.get("thinking_duration"):
            extras.append(f"Think: `{msg['thinking_duration']}`")
        if extras:
            lines.append("")
            lines.append(f"*{' | '.join(extras)}*")
        lines.append("")

    elif role == "tool":
        tool_name = msg.get("tool_name", "unknown")
        lines.append(f"### 🔧 Tool: `{tool_name}`{ts_suffix}")

        if tool_name == "code_edit":
            lines.append(content)
            diff = msg.get("diff")
            if diff:
                lines.append("")
                lines.append("```diff")
                # Truncate overly long diff
                if len(diff) > 3000:
                    lines.append(diff[:3000])
                    lines.append(f"... (truncated, {len(diff)} chars total)")
                else:
                    lines.append(diff)
                lines.append("```")

        elif tool_name == "run_command":
            cwd = msg.get("cwd", "")
            exit_code = msg.get("exit_code")
            cwd_info = f" (in `{cwd}`)" if cwd else ""
            exit_info = f" → exit {exit_code}" if exit_code is not None else ""
            lines.append(f"```bash")
            lines.append(content)
            lines.append(f"```")
            if cwd_info or exit_info:
                lines.append(f"*{cwd_info}{exit_info}*")
            # Command output
            output = msg.get("output")
            if output:
                lines.append("")
                lines.append("<details><summary>📤 Output</summary>")
                lines.append("")
                lines.append("```")
                if len(output) > 5000:
                    lines.append(output[:5000])
                    lines.append(f"... (truncated, {len(output)} chars total)")
                else:
                    lines.append(output)
                lines.append("```")
                lines.append("")
                lines.append("</details>")

        elif tool_name == "search_web":
            lines.append(f"Query: {content}")
            search_summary = msg.get("search_summary")
            if search_summary:
                lines.append("")
                lines.append("<details><summary>🔍 Search Results</summary>")
                lines.append("")
                lines.append(search_summary)
                lines.append("")
                lines.append("</details>")

        elif tool_name == "view_file":
            num_lines = msg.get("num_lines")
            num_bytes = msg.get("num_bytes")
            size_info = ""
            if num_lines or num_bytes:
                parts = []
                if num_lines:
                    parts.append(f"{num_lines} lines")
                if num_bytes:
                    parts.append(f"{num_bytes} bytes")
                size_info = f" ({', '.join(parts)})"
            lines.append(f"`{content}`{size_info}")

        else:
            # Other tool types
            if content:
                lines.append(f"`{content[:500]}`")

        lines.append("")

    return lines


# ════════════════════════════════
# JSON format
# ════════════════════════════════

def format_json(conversations: list[dict]) -> str:
    """Format all conversations as a JSON string."""
    return json.dumps(conversations, indent=2, ensure_ascii=False)


def build_conversation_record(
    cascade_id: str,
    title: str,
    metadata: dict,
    messages: list[dict],
) -> dict:
    """Build a JSON record for a single conversation."""
    record = {
        "cascade_id": cascade_id,
        "title": title,
        "step_count": metadata.get("stepCount", 0),
        "created_time": metadata.get("createdTime", ""),
        "last_modified_time": metadata.get("lastModifiedTime", ""),
        "messages": messages,
    }
    workspaces = metadata.get("workspaces", [])
    if workspaces:
        ws_uris = [w.get("workspaceFolderAbsoluteUri", "")
                    for w in workspaces
                    if w.get("workspaceFolderAbsoluteUri")]
        if ws_uris:
            record["workspaces"] = ws_uris
    return record




# ════════════════════════════════
# File writing utilities
# ════════════════════════════════

def safe_filename(title: str, max_len: int = 60) -> str:
    """Convert a title to a safe filename."""
    return re.sub(r'[^\w\s\-]', '_', title)[:max_len].strip()


def write_conversation(
    content: str,
    title: str,
    output_dir: str,
    extension: str = ".md",
) -> str:
    """Write formatted content to a file, return the file path."""
    base = safe_filename(title)
    filepath = os.path.join(output_dir, base + extension)
    # Deduplicate: append _2, _3, ... if file already exists
    counter = 2
    while os.path.exists(filepath):
        filepath = os.path.join(output_dir, f"{base}_{counter}{extension}")
        counter += 1
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath
