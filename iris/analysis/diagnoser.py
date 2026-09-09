"""Smart Trace Diagnosis & Event Compression Engine for AI Coding Agents.

Analyzes raw execution events to:
1. Collapse repetitive loop iterations into compact delta summaries (saving >90% tokens).
2. Detect unexpected variable type mutations (e.g. dict -> NoneType).
3. Pinpoint the exact root-cause origin line for exceptions.
4. Produce a concise, token-efficient diagnosis report for LLMs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from iris.storage.db import get_connection, get_db_path, get_session


class TraceDiagnoser:
    """Performs static & dynamic anomaly detection across recorded trace sessions."""

    def __init__(self, session_id: str, db_path: Optional[Path] = None):
        self.session_id = session_id
        self.db_path = db_path or get_db_path()

    def diagnose(self) -> Dict[str, Any]:
        """Run full diagnostic analysis on the session."""
        session = get_session(self.session_id, self.db_path)
        if not session:
            return {"error": f"Session '{self.session_id}' not found."}

        conn = get_connection(self.db_path)
        try:
            # Fetch executions
            exec_rows = conn.execute(
                """
                SELECT execution_id, function_name, file_path, start_time_mono_ns, end_time_mono_ns
                FROM executions
                WHERE session_id = ?
                ORDER BY start_time_mono_ns ASC
                """,
                (self.session_id,),
            ).fetchall()

            if not exec_rows:
                return {
                    "session_id": self.session_id,
                    "state": session["state"],
                    "summary": "No executions recorded in this session.",
                }

            all_diagnostics = []
            total_raw_events = session.get("total_events", 0)

            for ex in exec_rows:
                ex_id = ex["execution_id"]
                fn_name = ex["function_name"]
                file_path = ex["file_path"]

                # Fetch events for this execution
                event_rows = conn.execute(
                    """
                    SELECT sequence_number, event_type, line_number, branch_taken, payload_json
                    FROM events
                    WHERE execution_id = ?
                    ORDER BY sequence_number ASC
                    """,
                    (ex_id,),
                ).fetchall()

                parsed_events = []
                for r in event_rows:
                    payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
                    parsed_events.append({
                        "seq": r["sequence_number"],
                        "type": r["event_type"],
                        "line": r["line_number"],
                        "branch_taken": r["branch_taken"],
                        "payload": payload,
                    })

                exec_diagnosis = self._analyze_execution(fn_name, file_path, parsed_events)
                all_diagnostics.append(exec_diagnosis)

            report_markdown = self._generate_markdown_report(session, all_diagnostics)

            return {
                "session_id": self.session_id,
                "state": session["state"],
                "total_raw_events": total_raw_events,
                "executions": all_diagnostics,
                "report_markdown": report_markdown,
            }
        finally:
            conn.close()

    def _analyze_execution(
        self,
        func_name: str,
        file_path: str,
        events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyze events within a single function execution."""
        type_mutations = []
        variable_history: Dict[str, List[Tuple[int, str, Any]]] = {}
        exception_info = None

        # 1. Track variable type changes across line events
        for ev in events:
            if ev["type"] == "LINE":
                line_no = ev["line"]
                locals_dict = ev.get("payload", {}).get("locals", {})
                for var_name, val_info in locals_dict.items():
                    val_type = "NoneType" if val_info is None else type(val_info).__name__
                    val_repr = str(val_info)

                    if var_name not in variable_history:
                        variable_history[var_name] = [(line_no, val_type, val_repr)]
                    else:
                        prev_line, prev_type, _ = variable_history[var_name][-1]
                        if val_type != prev_type:
                            type_mutations.append({
                                "variable": var_name,
                                "from_type": prev_type,
                                "to_type": val_type,
                                "at_line": line_no,
                                "value": val_repr,
                            })
                        variable_history[var_name].append((line_no, val_type, val_repr))

            elif ev["type"] == "EXCEPTION":
                exception_info = {
                    "line": ev["line"],
                    "exception": ev.get("payload", {}).get("exception", "Unknown"),
                    "exc_type": ev.get("payload", {}).get("exc_type", "Exception"),
                }

        # 2. Collapse repetitive loops
        compressed_flow, loop_summaries = self._collapse_loops(events)

        # 3. Locate root cause for exception if present
        root_cause = None
        if exception_info:
            root_cause = self._identify_root_cause(exception_info, type_mutations, variable_history)

        return {
            "function_name": func_name,
            "file_path": Path(file_path).name,
            "total_events": len(events),
            "compressed_events_count": len(compressed_flow),
            "loops_detected": loop_summaries,
            "type_mutations": type_mutations,
            "exception": exception_info,
            "root_cause": root_cause,
        }

    def _collapse_loops(
        self,
        events: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Detect repeated cycles of lines and collapse into loop summaries."""
        if not events:
            return [], []

        compressed = []
        loops = []

        line_events = [e for e in events if e["type"] == "LINE"]
        if len(line_events) < 6:
            return events, []

        i = 0
        while i < len(events):
            ev = events[i]
            if ev["type"] != "LINE":
                compressed.append(ev)
                i += 1
                continue

            # Check for repetition of line sequences (loop body lengths 1 to 5)
            pattern_found = False
            for pattern_len in range(1, 6):
                if i + pattern_len * 2 > len(events):
                    continue

                pattern = [events[i + k]["line"] for k in range(pattern_len)]
                next_chunk = [events[i + pattern_len + k]["line"] for k in range(pattern_len)]

                if pattern == next_chunk:
                    # Count repetitions
                    repetitions = 0
                    curr = i
                    while curr + pattern_len <= len(events):
                        chunk = [events[curr + k]["line"] for k in range(pattern_len)]
                        if chunk == pattern:
                            repetitions += 1
                            curr += pattern_len
                        else:
                            break

                    if repetitions >= 3:
                        # Significant loop repetition detected
                        first_locals = events[i].get("payload", {}).get("locals", {})
                        last_locals = events[curr - 1].get("payload", {}).get("locals", {})

                        # Calculate cumulative delta across the entire loop
                        cumulative_delta = {}
                        for k, v in last_locals.items():
                            if k not in first_locals or first_locals[k] != v:
                                cumulative_delta[k] = v

                        loop_summary = {
                            "lines": f"{min(pattern)}-{max(pattern)}",
                            "iterations": repetitions,
                            "pattern_lines": pattern,
                            "cumulative_delta": cumulative_delta,
                        }
                        loops.append(loop_summary)
                        compressed.append({
                            "type": "LOOP_COLLAPSED",
                            "summary": loop_summary,
                        })
                        i = curr
                        pattern_found = True
                        break

            if not pattern_found:
                compressed.append(ev)
                i += 1

        return compressed, loops

    def _identify_root_cause(
        self,
        exception_info: Dict[str, Any],
        type_mutations: List[Dict[str, Any]],
        variable_history: Dict[str, List[Tuple[int, str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        """Heuristic to correlate exception with earlier unexpected mutations."""
        exc_str = exception_info.get("exception", "").lower()
        exc_type = exception_info.get("exc_type", "")

        # Case 1: NoneType error (e.g. 'NoneType' object has no attribute or cannot subscript)
        if "nonetype" in exc_str or "none" in exc_str:
            for mut in reversed(type_mutations):
                if mut["to_type"] in ("NoneType", "none", "None"):
                    return {
                        "confidence": "HIGH",
                        "suspect_line": mut["at_line"],
                        "variable": mut["variable"],
                        "reason": f"Variable '{mut['variable']}' unexpectedly mutated from {mut['from_type']} to NoneType before exception at line {exception_info['line']}.",
                    }

        # Case 2: KeyError
        if exc_type == "KeyError":
            return {
                "confidence": "MEDIUM",
                "suspect_line": exception_info["line"],
                "reason": f"Key access failed at line {exception_info['line']} with key: {exception_info['exception']}.",
            }

        return None

    def _generate_markdown_report(
        self,
        session: Dict[str, Any],
        diagnostics: List[Dict[str, Any]],
    ) -> str:
        """Generate token-optimized markdown report for AI Agents."""
        lines = [
            f"### 🔍 Iris Trace Diagnosis: Session `{session['session_id']}`",
            f"- **Target**: `{session['entry_function']}()` in `{session['entry_file']}`",
            f"- **State**: `{session['state']}` | **Total Raw Events**: {session.get('total_events', 0)}",
            "",
        ]

        has_issues = False

        for diag in diagnostics:
            fn = diag["function_name"]
            loops = diag.get("loops_detected", [])
            mutations = diag.get("type_mutations", [])
            exc = diag.get("exception")
            root_cause = diag.get("root_cause")

            if loops:
                lines.append(f"#### 🔁 Collapsed Loops in `{fn}()`:")
                for lp in loops:
                    lines.append(f"- **Lines {lp['lines']}** ran **{lp['iterations']} times** (compressed to 1 summary).")
                    if lp["cumulative_delta"]:
                        lines.append(f"  - Final variable state: `{json.dumps(lp['cumulative_delta'])}`")
                lines.append("")

            if mutations:
                has_issues = True
                lines.append(f"#### ⚠️ Unexpected Variable Type Mutations in `{fn}()`:")
                for m in mutations:
                    lines.append(
                        f"- **Line {m['at_line']}**: Variable `{m['variable']}` mutated from `{m['from_type']}` ➔ `{m['to_type']}` (Value: `{m['value']}`)."
                    )
                lines.append("")

            if exc:
                has_issues = True
                lines.append(f"#### 💥 Exception Detected in `{fn}()`:")
                lines.append(f"- **Line {exc['line']}**: `{exc['exc_type']}: {exc['exception']}`")
                if root_cause:
                    lines.append(f"- **🎯 Probable Root Cause**: {root_cause['reason']} *(Confidence: {root_cause['confidence']})*")
                lines.append("")

        if not has_issues:
            lines.append("✅ **Clean Execution**: No unexpected type mutations or exceptions detected.")

        return "\n".join(lines)
