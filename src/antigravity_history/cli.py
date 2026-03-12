"""
CLI entry point — aghistory command.

Subcommands:
  export   Export conversations to Markdown / JSON / Obsidian
  list     List all conversations
  recover  Recover lost conversations
  info     Show LanguageServer status
"""

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from typing import Optional

try:
    import typer
    from rich.console import Console
    from rich.table import Table
    from rich.progress import track
except ImportError:
    print("ERROR: Missing dependencies. Run: pip install antigravity-history")
    sys.exit(1)

from antigravity_history import __version__
from antigravity_history.discovery import (
    discover_language_servers,
    find_all_endpoints,
    find_working_endpoint,
)
from antigravity_history.api import (
    get_all_trajectories,
    get_all_trajectories_merged,
    get_trajectory_steps,
)
from antigravity_history.parser import parse_steps, FieldLevel
from antigravity_history.formatters import (
    format_markdown,
    format_json,
    build_conversation_record,
    write_conversation,
    safe_filename,
)

app = typer.Typer(
    name="aghistory",
    help="Export and recover your Antigravity conversations.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)


def _discover_endpoints(
    port: Optional[int] = None,
    token: Optional[str] = None,
    log: Optional[Console] = None,
) -> list[dict]:
    """Discover all available LS endpoints; exit on failure."""
    log = log or console
    if port and token:
        log.print(f"[dim]Using manual config: port={port}[/dim]")
        return [{"port": port, "csrf": token, "pid": 0}]

    log.print("[dim]Discovering LanguageServer...[/dim]")
    servers = discover_language_servers()
    if not servers:
        err_console.print(
            "[bold red]No Antigravity LanguageServer process found.[/bold red]\n"
            "[yellow]Please make sure Antigravity is running and try again.[/yellow]"
        )
        raise typer.Exit(1)
    log.print(f"[dim]  Found {len(servers)} language_server instance(s)[/dim]")

    endpoints = find_all_endpoints(servers)
    if not endpoints:
        err_console.print(
            "[bold red]Cannot connect to any LanguageServer port.[/bold red]\n"
            "[yellow]Please make sure Antigravity is running with an open workspace.[/yellow]"
        )
        raise typer.Exit(1)
    log.print(f"[dim]  Connected to {len(endpoints)} endpoint(s)[/dim]")
    return endpoints


# ════════════════════════════════
# export subcommand
# ════════════════════════════════

@app.command()
def export(
    output: str = typer.Option(
        "./antigravity_export", "-o", "--output",
        help="Output directory",
    ),
    format: str = typer.Option(
        "all", "-f", "--format",
        help="Output format: md / json / all",
    ),
    today: bool = typer.Option(False, "--today", help="Export only today's conversations"),
    ids: Optional[list[str]] = typer.Option(None, "--id", help="Export specific cascade ID(s)"),
    thinking: bool = typer.Option(False, "--thinking", help="Include AI thinking process"),
    full: bool = typer.Option(False, "--full", help="Include all extended fields (thinking+diff+output)"),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """Export conversations to Markdown / JSON format."""
    # Determine field level
    if full:
        level = FieldLevel.FULL
    elif thinking:
        level = FieldLevel.THINKING
    else:
        level = FieldLevel.DEFAULT

    console.print(f"\n[bold]Antigravity History Export[/bold] v{__version__}")
    console.print(f"[dim]Field level: {level}[/dim]\n")

    endpoints = _discover_endpoints(port, token)

    # Fetch conversation list from all LS instances (merge & deduplicate)
    console.print("[dim]Fetching conversation list (scanning all workspaces)...[/dim]")
    summaries, cascade_ep, failed_eps = get_all_trajectories_merged(endpoints)
    indexed_count = len(summaries)
    console.print(f"[dim]  Indexed conversations: {indexed_count}[/dim]")
    if failed_eps:
        console.print(f"[dim]  [yellow]LS endpoints failed: {len(failed_eps)}[/yellow][/dim]")

    default_ep = endpoints[0]

    # Scan .pb files to find unindexed conversations
    conv_dir = os.path.expanduser("~/.gemini/antigravity/conversations")
    if os.path.isdir(conv_dir):
        pb_files = [f for f in os.listdir(conv_dir) if f.endswith('.pb')]
        unindexed_count = 0
        for f in pb_files:
            cid = f.replace('.pb', '')
            if cid not in summaries:
                summaries[cid] = {
                    "summary": f"[unindexed] {cid[:8]}...",
                    "stepCount": 1000,
                }
                cascade_ep[cid] = {"port": default_ep["port"], "csrf": default_ep["csrf"]}
                unindexed_count += 1
        if unindexed_count:
            console.print(f"[dim]  Unindexed .pb files: {unindexed_count}[/dim]")
        console.print(f"[dim]  Total to export: {len(summaries)}[/dim]")

    # Specified IDs (support on-demand loading)
    if ids:
        for cid in ids:
            if cid not in summaries:
                summaries[cid] = {
                    "summary": f"[on-demand] {cid[:8]}...",
                    "stepCount": 1000,
                }
                cascade_ep[cid] = {"port": default_ep["port"], "csrf": default_ep["csrf"]}

    # Filter today's conversations
    if today:
        today_str = date.today().isoformat()
        summaries = {
            k: v for k, v in summaries.items()
            if v.get("lastModifiedTime", "").startswith(today_str)
        }
        console.print(f"[dim]  Today's conversations: {len(summaries)}[/dim]")

    if not summaries:
        console.print("[yellow]No conversations match the criteria.[/yellow]")
        raise typer.Exit(0)

    # Create output directory
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Sort: newest first
    sorted_items = sorted(
        summaries.items(),
        key=lambda x: x[1].get("lastModifiedTime", ""),
        reverse=True,
    )

    # Concurrent fetch + parse (thread-safe pure functions)
    def _fetch_one(cascade_id, info):
        title = info.get("summary", "Untitled")
        step_count = info.get("stepCount", 1000)
        ep = cascade_ep.get(cascade_id, {"port": default_ep["port"], "csrf": default_ep["csrf"]})
        steps = get_trajectory_steps(ep["port"], ep["csrf"], cascade_id, step_count)
        messages = parse_steps(steps, level)
        return cascade_id, title, info, messages


    all_records = []
    exported_count = 0
    failed_list = []  # [(cascade_id, error_str)]
    exported_list = []  # [(cascade_id, title, msg_count)]
    MAX_WORKERS = 4

    from rich.progress import Progress
    with Progress() as progress:
        task = progress.add_task("Exporting...", total=len(sorted_items))
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_fetch_one, cid, info): cid
                for cid, info in sorted_items
            }
            for future in as_completed(futures):
                cid = futures[future]
                try:
                    cascade_id, title, info, messages = future.result()
                except Exception as e:
                    err_console.print(f"[red]Skipped {cid[:8]}...: {e}[/red]")
                    failed_list.append((cid, str(e)))
                    progress.advance(task)
                    continue

                # Write files (main thread, no conflict between different files)
                if format in ("md", "all"):
                    md_content = format_markdown(title, cascade_id, info, messages)
                    write_conversation(md_content, title, str(output_dir), ".md")

                if format in ("json", "all"):
                    record = build_conversation_record(cascade_id, title, info, messages)
                    all_records.append(record)

                exported_count += 1
                exported_list.append((cascade_id, title, len(messages)))
                progress.advance(task)

    # Write JSON
    if format in ("json", "all") and all_records:
        json_path = output_dir / "conversations_export.json"
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(format_json(all_records))

    # Write export report
    _write_export_report(output_dir, exported_list, failed_list, failed_eps)

    # Summary
    total_msgs = sum(len(r["messages"]) for r in all_records) if all_records else 0

    console.print(f"\n[bold green]Export complete![/bold green]")
    console.print(f"  Conversations: {exported_count}")
    if failed_list:
        console.print(f"  [red]Failed: {len(failed_list)}[/red]")
    if total_msgs:
        console.print(f"  Messages: {total_msgs}")
    console.print(f"  Report: {output_dir.absolute() / 'export_report.txt'}")
    console.print(f"  Output directory: {output_dir.absolute()}")


def _write_export_report(
    output_dir: Path,
    exported: list[tuple],
    failed: list[tuple],
    failed_endpoints: list[tuple] = None,
):
    """Write export_report.txt summarizing the export results."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(exported) + len(failed)
    sep = "=" * 60
    lines = [
        sep,
        "  EXPORT REPORT",
        sep,
        "",
        f"  Time:      {now}",
        f"  Total:     {total}",
        f"  Exported:  {len(exported)}",
        f"  Failed:    {len(failed)}",
        "",
    ]

    if failed_endpoints:
        lines.append("-" * 60)
        lines.append("  LS ENDPOINT FAILURES")
        lines.append("-" * 60)
        lines.append("  These instances failed to return conversation lists.")
        lines.append("  Affected conversations were recovered via .pb scanning")
        lines.append("  but may have missing titles.")
        lines.append("")
        for i, (port, err) in enumerate(failed_endpoints, 1):
            lines.append(f"  {i}. Port {port} - {err}")
        lines.append("")

    if failed:
        lines.append("-" * 60)
        lines.append("  FAILED CONVERSATIONS")
        lines.append("-" * 60)
        for i, (cid, err) in enumerate(failed, 1):
            lines.append(f"  {i}. {cid}")
            lines.append(f"     Error: {err}")
        lines.append("")

    if exported:
        lines.append("-" * 60)
        lines.append(f"  EXPORTED CONVERSATIONS ({len(exported)})")
        lines.append("-" * 60)
        for i, (cid, title, msg_count) in enumerate(exported, 1):
            lines.append(f"  {i:3d}. {title[:50]}")
            lines.append(f"       Messages: {msg_count}  |  ID: {cid[:8]}...")
        lines.append("")

    lines.append(sep)

    report_path = output_dir / "export_report.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ════════════════════════════════
# list subcommand
# ════════════════════════════════

@app.command(name="list")
def list_conversations(
    limit: int = typer.Option(50, "-n", "--limit", help="Max number of conversations to show"),
    today: bool = typer.Option(False, "--today", help="Show only today's conversations"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON (pipe-friendly)"),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """List all conversations."""
    # In JSON mode, logs go to stderr to keep stdout clean
    out = err_console if json_output else console
    out.print(f"\n[bold]Antigravity Conversations[/bold]\n")

    endpoints = _discover_endpoints(port, token, log=out)
    summaries, _ = get_all_trajectories_merged(endpoints)

    if today:
        today_str = date.today().isoformat()
        summaries = {
            k: v for k, v in summaries.items()
            if v.get("lastModifiedTime", "").startswith(today_str)
        }

    sorted_items = sorted(
        summaries.items(),
        key=lambda x: x[1].get("lastModifiedTime", ""),
        reverse=True,
    )[:limit]

    if json_output:
        import json as json_mod
        records = []
        for cid, info in sorted_items:
            records.append({
                "cascade_id": cid,
                "title": info.get("summary", ""),
                "step_count": info.get("stepCount", 0),
                "last_modified": info.get("lastModifiedTime", ""),
                "created": info.get("createdTime", ""),
            })
        print(json_mod.dumps(records, indent=2, ensure_ascii=False))
    else:
        table = Table(title=f"{len(summaries)} conversation(s) total")
        table.add_column("#", style="dim", width=4)
        table.add_column("Last Modified", width=20)
        table.add_column("Steps", justify="right", width=6)
        table.add_column("Title", max_width=50)
        table.add_column("ID", style="dim", width=10)

        for i, (cid, info) in enumerate(sorted_items):
            t = info.get("lastModifiedTime", "?")[:19]
            table.add_row(
                str(i + 1),
                t,
                str(info.get("stepCount", "?")),
                info.get("summary", "?")[:50],
                cid[:8] + "...",
            )

        console.print(table)


# ════════════════════════════════
# recover subcommand
# ════════════════════════════════

@app.command()
def recover(
    conv_dir: str = typer.Option(
        None, "--conv-dir",
        help="Conversations directory path (default: ~/.gemini/antigravity/conversations)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Detect only, do not recover"),
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """Recover lost conversations (scan .pb files and reload via API)."""
    if conv_dir is None:
        conv_dir = os.path.expanduser("~/.gemini/antigravity/conversations")

    if not os.path.isdir(conv_dir):
        err_console.print(f"[red]Directory not found: {conv_dir}[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold]Antigravity Conversation Recovery[/bold]\n")

    endpoints = _discover_endpoints(port, token)
    default_ep = endpoints[0]
    p, c = default_ep["port"], default_ep["csrf"]

    # Indexed conversations (merged from all LS instances)
    indexed, _ = get_all_trajectories_merged(endpoints)
    indexed_ids = set(indexed.keys())
    console.print(f"[dim]Indexed conversations: {len(indexed_ids)}[/dim]")

    # Scan .pb files
    pb_files = sorted([f for f in os.listdir(conv_dir) if f.endswith('.pb')])
    console.print(f"[dim].pb files: {len(pb_files)}[/dim]\n")

    activated = []
    failed = []
    already_indexed = []

    for i, f in enumerate(track(pb_files, description="Scanning...")):
        cascade_id = f.replace('.pb', '')
        is_indexed = cascade_id in indexed_ids
        size_kb = os.path.getsize(os.path.join(conv_dir, f)) // 1024

        if is_indexed:
            already_indexed.append(cascade_id)
            continue

        if dry_run:
            console.print(f"  [yellow]Unindexed[/yellow] {cascade_id[:8]}... ({size_kb}KB)")
            continue

        # Try on-demand loading via API
        result = get_trajectory_steps(p, c, cascade_id, step_count=5)
        if result:
            activated.append(cascade_id)
            console.print(f"  [green]Activated[/green] {cascade_id[:8]}... ({size_kb}KB, {len(result)}+ steps)")
        else:
            failed.append(cascade_id)
            console.print(f"  [red]Failed[/red] {cascade_id[:8]}... ({size_kb}KB)")

    # Summary
    console.print(f"\n[bold]{'─' * 40}[/bold]")
    console.print(f"  Total .pb files: {len(pb_files)}")
    console.print(f"  Indexed: {len(already_indexed)}")
    if dry_run:
        unindexed = len(pb_files) - len(already_indexed)
        console.print(f"  Unindexed: {unindexed}")
        console.print(f"\n[yellow]Dry run mode. Remove --dry-run to perform actual recovery.[/yellow]")
    else:
        console.print(f"  [green]Newly activated: {len(activated)}[/green]")
        if failed:
            console.print(f"  [red]Failed: {len(failed)}[/red]")


# ════════════════════════════════
# info subcommand
# ════════════════════════════════

@app.command()
def info(
    port: Optional[int] = typer.Option(None, "--port", help="Manually specify port"),
    token: Optional[str] = typer.Option(None, "--token", help="Manually specify CSRF token"),
):
    """Show LanguageServer status information."""
    console.print(f"\n[bold]Antigravity History[/bold] v{__version__}\n")

    endpoints = _discover_endpoints(port, token)
    summaries, _ = get_all_trajectories_merged(endpoints)

    console.print(f"  LanguageServer endpoints: {len(endpoints)}")
    console.print(f"  Total conversations: {len(summaries)}")

    if summaries:
        sorted_items = sorted(
            summaries.items(),
            key=lambda x: x[1].get("lastModifiedTime", ""),
        )
        oldest = sorted_items[0][1].get("createdTime", "?")[:10]
        newest = sorted_items[-1][1].get("lastModifiedTime", "?")[:10]
        total_steps = sum(v.get("stepCount", 0) for v in summaries.values())
        console.print(f"  Total steps: {total_steps}")
        console.print(f"  Time range: {oldest} ~ {newest}")


# ════════════════════════════════
# version callback
# ════════════════════════════════

def version_callback(value: bool):
    if value:
        console.print(f"antigravity-history v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-v",
        callback=version_callback,
        is_eager=True,
        help="Show version",
    ),
):
    """Export and recover your Antigravity conversations."""
    pass
