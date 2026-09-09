"""
Real-world test scenario using Textualize/rich:
- Renders a complex multi-column Table with box styling and ANSI markup.
- Records execution using Iris PEP 669 flight recorder.
- Queries call tree, loop compression diagnosis, and data lineage via Iris database APIs.
"""
import sys
import os
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add cloned rich to sys.path so we test against the local clone
rich_path = Path(__file__).resolve().parent / "cloned_rich"
if str(rich_path) not in sys.path:
    sys.path.insert(0, str(rich_path))

from iris.core.observer import IrisObserver
from iris.core.fsm import SessionState
from iris.core.runtime import trace_context
from iris.storage.db import (
    init_db,
    clean_db,
    get_session,
    query_call_tree,
    inspect_execution_flow,
    diagnose_session,
    query_data_lineage,
    get_db_path
)
from rich.console import Console
from rich.table import Table
from rich.tree import Tree

def render_rich_complex_report():
    """Target function that builds and renders a complex rich table and tree."""
    console = Console(record=True, width=80)
    
    # 1. Build a nested Table
    table = Table(title="Production Systems Telemetry", border_style="blue")
    table.add_column("Service", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta")
    table.add_column("Latency (ms)", justify="right", style="green")
    table.add_column("Error Rate", justify="right", style="red")

    rows = [
        ("Auth Gateway", "Operational", "14.2", "0.001%"),
        ("Payment Engine", "Operational", "42.8", "0.003%"),
        ("Order Dispatcher", "Degraded", "128.4", "0.052%"),
        ("Notification Worker", "Operational", "8.1", "0.000%"),
        ("Analytics Aggregator", "Operational", "312.0", "0.012%"),
    ]
    
    for service, status, latency, error_rate in rows:
        table.add_row(service, status, latency, error_rate)
        
    console.print(table)
    
    # 2. Build a hierarchical Tree
    root_tree = Tree("[bold green]Cluster Topology")
    us_east = root_tree.add("[bold blue]us-east-1 (Primary)")
    us_east.add("Node A (c6g.2xlarge) [green]Healthy")
    us_east.add("Node B (c6g.2xlarge) [green]Healthy")
    
    eu_west = root_tree.add("[bold yellow]eu-west-1 (Secondary)")
    eu_west.add("Node C (c6g.xlarge) [green]Healthy")
    eu_west.add("Node D (c6g.xlarge) [yellow]Warning: Memory 87%")
    
    console.print(root_tree)
    
    rendered_text = console.export_text()
    line_count = len(rendered_text.splitlines())
    return line_count

def run_test():
    print("=" * 70)
    print("🚀 IRIS REAL-WORLD BENCHMARK: Textualize/rich Rendering Engine")
    print("=" * 70)
    
    clean_db()
    init_db()
    
    session_id = "rich_benchmark_session"
    entry_file = Path(__file__).name
    
    print(f"\n[1] Arming Iris on target: 'render_rich_complex_report' in '{entry_file}'...")
    with trace_context(entry_file=entry_file, entry_function="render_rich_complex_report", session_id=session_id) as sid:
        print("[2] Executing target function...")
        total_lines = render_rich_complex_report()
        print(f"    Target executed successfully! Rendered {total_lines} lines of rich output.")
    
    # Verify Session Status
    status = get_session(session_id)
    print(f"\n[3] Session Status: {status['state']}")
    print(f"    Total Events Captured: {status['total_events']}")
    print(f"    Target: {status['entry_function']}() in {status['entry_file']}")
    
    # Query Call Tree
    call_tree = query_call_tree(session_id, depth_limit=3)
    print(f"\n[4] Hierarchical Call Tree (Top Depth Level):")
    print(f"    Root Nodes Recorded: {len(call_tree)}")
    for exec_node in call_tree:
        fn_name = exec_node.get("function_name", "unknown")
        children = exec_node.get("children", [])
        print(f"    - Root: {fn_name}() | Sub-calls: {len(children)}")
        for child in children[:5]:
            child_fn = child.get("function_name", "unknown")
            sub_children = child.get("children", [])
            print(f"      └── {child_fn}() (sub-children: {len(sub_children)})")
        if len(children) > 5:
            print(f"      └── ... and {len(children) - 5} more child functions")
        
    # Anomaly & Loop Compression Diagnosis
    print(f"\n[5] Running Smart Diagnosis & Loop Compression (iris_diagnose_anomaly)...")
    diag = diagnose_session(session_id)
    print(f"    Total Raw Events Analyzed: {diag.get('total_raw_events', 0)}")
    print(f"    Executions Analyzed: {len(diag.get('executions', []))}")
    print("\n--- Diagnostic Report Preview (First 25 lines) ---")
    report_lines = diag.get("report_markdown", "").splitlines()
    print("\n".join(report_lines[:25]))
    if len(report_lines) > 25:
        print(f"    ... and {len(report_lines) - 25} more lines in full diagnosis report")
    print("-------------------------------------------------")
    
    # Data Lineage Traversal
    print(f"\n[6] Variable Data Lineage Graph (iris_trace_data_lineage)...")
    root_exec_id = call_tree[0]["execution_id"] if call_tree else None
    if root_exec_id:
        flow = inspect_execution_flow(session_id, root_exec_id)
        print(f"    Root function executed {len(flow)} line steps.")
        
        # Check value refs for root execution
        import sqlite3
        with sqlite3.connect(get_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT value_ref_id, variable_name, version, epistemic_status, value_snapshot FROM value_refs WHERE execution_id = ?",
                (root_exec_id,)
            )
            vrefs = cur.fetchall()
            print(f"    Value Refs tracked in root function: {len(vrefs)}")
            for vref_id, var_name, version, epistemic_status, value_snapshot in vrefs[:10]:
                val_display = (value_snapshot or "")[:40].replace("\n", " ")
                print(f"      - [{epistemic_status}] {var_name}_v{version} = {val_display} (id: {vref_id})")
                
            if vrefs:
                # Pick the last variable (e.g. line_count) and trace BACKWARD
                sample_vref = vrefs[-1][0]
                sample_name = vrefs[-1][1]
                print(f"\n[7] Tracing BACKWARD Data Lineage for '{sample_name}' (vref: {sample_vref}):")
                lineage = query_data_lineage(sample_vref, direction="BACKWARD", depth_limit=10)
                root_node = lineage["nodes"][0] if lineage.get("nodes") else {}
                print(f"    Target Node: {root_node.get('variable_name')} (ID: {root_node.get('value_ref_id')})")
                print(f"    Nodes in Causal Chain: {len(lineage.get('nodes', []))}")
                print(f"    Edges in Causal Chain: {len(lineage.get('edges', []))}")
                print("    ASCII Causal Flow:")
                print(lineage.get("ascii_flow", "No Flow"))

                # Also trace FORWARD from the first variable (e.g. rows or console)
                first_vref = vrefs[0][0]
                first_name = vrefs[0][1]
                print(f"\n[8] Tracing FORWARD Data Lineage (Blast Radius) from '{first_name}' (vref: {first_vref}):")
                forward_lineage = query_data_lineage(first_vref, direction="FORWARD", depth_limit=10)
                f_root_node = forward_lineage["nodes"][0] if forward_lineage.get("nodes") else {}
                print(f"    Source Node: {f_root_node.get('variable_name')} (ID: {f_root_node.get('value_ref_id')})")
                print(f"    Nodes Impacted: {len(forward_lineage.get('nodes', []))}")
                print(f"    Edges in Impact Chain: {len(forward_lineage.get('edges', []))}")
                print("    ASCII Impact Flow:")
                print(forward_lineage.get("ascii_flow", "No Flow"))

    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_test()
