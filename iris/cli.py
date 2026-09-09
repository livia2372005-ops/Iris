"""Command Line Interface for Iris."""

import sys
from pathlib import Path
import click

from iris import __version__
from iris.storage.db import get_db_path, init_db


@click.group()
@click.version_option(version=__version__, prog_name="iris")
def main() -> None:
    """Iris: Flight recorder for CPython code via PEP 669 & MCP."""
    pass


@main.command(name="mcp")
def run_mcp() -> None:
    """Start the Iris MCP stdio server for AI agents."""
    from iris.mcp.server import main as mcp_main

    mcp_main()


@main.command(name="init")
def init_database() -> None:
    """Initialize the SQLite WAL database for tracing."""
    init_db()
    click.echo(f"Initialized Iris database at: {get_db_path()}")


@main.command(name="info")
def show_info() -> None:
    """Display system and environment diagnostics for Iris."""
    click.echo(f"Iris Version: {__version__}")
    click.echo(f"Python Version: {sys.version}")
    pep669_supported = hasattr(sys, "monitoring")
    click.echo(f"PEP 669 (sys.monitoring) Available: {'YES' if pep669_supported else 'NO'}")
    click.echo(f"Trace Database Location: {get_db_path()}")


@main.command(name="clean")
@click.option("--all", "all_sessions", is_flag=True, help="Clear all sessions from database")
@click.option("--keep", default=5, type=int, help="Keep latest N sessions (default: 5)")
def clean_database(all_sessions: bool, keep: int) -> None:
    """Clean and prune old session records from Iris trace database."""
    from iris.storage.db import clean_db

    deleted = clean_db(keep_latest=keep, all_sessions=all_sessions)
    if all_sessions:
        click.echo("Cleaned all sessions and reset Iris database.")
    else:
        click.echo(f"Pruned {deleted} old sessions. Retained latest {keep} sessions.")


@main.command(name="setup-agent")
@click.option(
    "--global",
    "is_global",
    is_flag=True,
    help="Register Iris globally for all Antigravity workspaces (~/.gemini/config)",
)
@click.option(
    "--python-path",
    default=None,
    help="Explicit Python interpreter path to use in MCP config",
)
def setup_agent(is_global: bool, python_path: str | None) -> None:
    """Configure Antigravity to automatically discover and use Iris MCP server."""
    import json
    py_exec = python_path or sys.executable

    rule_content = """# Iris Flight Recorder Guidelines for Antigravity Agent

Iris is an active flight recorder MCP server configured in this workspace. It observes Python code execution using PEP 669 without stopping or freezing the runtime.

## When to Use Iris MCP Tools
Use Iris when diagnosing subtle bugs, unexpected return values, silent variable corruptions, or checking whether specific branches were taken during a test or API request.

## Standard Debugging Flow
1. **Arm (iris_arm_entry)**: Call `iris_arm_entry(entry_file, entry_function, timeout_seconds=60)` before running your reproduction script or pytest.
2. **Trigger**: Run the target test or Python script using the shell or test runner.
3. **Verify Status (iris_get_session_status)**: Check `iris_get_session_status(session_id)` to ensure execution was captured (`COMPLETED`).
4. **Locate (iris_query_call_tree)**: Query `iris_query_call_tree(session_id, depth_limit=2)` to inspect the function call hierarchy.
5. **Inspect (iris_inspect_execution_flow)**: Query `iris_inspect_execution_flow(execution_id)` to view actual executed lines and variable state transformations (`payload_json`).
"""

    if is_global:
        global_config_dir = Path.home() / ".gemini" / "config"
        global_config_dir.mkdir(parents=True, exist_ok=True)
        mcp_file = global_config_dir / "mcp_config.json"

        data = {"mcpServers": {}}
        if mcp_file.exists():
            try:
                data = json.loads(mcp_file.read_text(encoding="utf-8"))
            except Exception:
                data = {"mcpServers": {}}

        data.setdefault("mcpServers", {})["iris"] = {
            "command": py_exec,
            "args": ["-m", "iris.mcp.server"],
            "env": {"PYTHONUNBUFFERED": "1"},
        }
        mcp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

        rules_dir = global_config_dir / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "iris.md").write_text(rule_content, encoding="utf-8")

        click.echo(f"[Success] Registered Iris MCP server globally in Antigravity ({mcp_file}).")
        click.echo("Iris is now automatically available in all Antigravity projects.")
    else:
        workspace_dir = Path.cwd()
        plugin_dir = workspace_dir / ".agents" / "plugins" / "iris"
        plugin_dir.mkdir(parents=True, exist_ok=True)

        plugin_manifest = {
            "name": "iris",
            "version": __version__,
            "description": "Iris: Flight Recorder for CPython code inspection via PEP 669 & MCP",
        }
        (plugin_dir / "plugin.json").write_text(json.dumps(plugin_manifest, indent=2), encoding="utf-8")

        mcp_server_config = {
            "mcpServers": {
                "iris": {
                    "command": py_exec,
                    "args": ["-m", "iris.mcp.server"],
                    "env": {"PYTHONUNBUFFERED": "1"},
                }
            }
        }
        (plugin_dir / "mcp_config.json").write_text(json.dumps(mcp_server_config, indent=2), encoding="utf-8")

        agents_dir = workspace_dir / ".agents"
        (agents_dir / "mcp_config.json").write_text(json.dumps(mcp_server_config, indent=2), encoding="utf-8")

        rules_dir = agents_dir / "rules"
        rules_dir.mkdir(parents=True, exist_ok=True)
        (rules_dir / "iris.md").write_text(rule_content, encoding="utf-8")

        click.echo(f"[Success] Configured Iris plugin in workspace: {workspace_dir}")
        click.echo("Antigravity will now automatically detect Iris MCP server in this workspace.")


@main.command(
    name="run",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
@click.argument("script_path", type=click.Path(exists=True))
@click.pass_context
def run_command(ctx: click.Context, script_path: str) -> None:
    """Execute a Python script under Iris flight recording."""
    from iris.core.runtime import run_script

    run_script(script_path, ctx.args)


if __name__ == "__main__":
    main()
