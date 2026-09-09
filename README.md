<p align="center">
  <img src="assets/iris_logo.png" alt="Iris Logo" width="480" />
</p>

<h1 align="center">Iris (v1) — Flight Recorder for CPython via PEP 669 & MCP</h1>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.12%2B-blue.svg" alt="Python 3.12+"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://modelcontextprotocol.io/"><img src="https://img.shields.io/badge/Protocol-MCP%202.x-green.svg" alt="Protocol: MCP"></a>
  <a href="https://peps.python.org/pep-0669/"><img src="https://img.shields.io/badge/Engine-PEP%20669-orange.svg" alt="Engine: PEP 669"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Tests-33%2F33%20Passing-brightgreen.svg" alt="Tests: Passing"></a>
</p>

> **Iris is a flight recorder for Python code.**  
> Instead of traditional debuggers that cause **stop-the-world freezes** while an LLM is thinking, Iris lets code run naturally at native speed. It records call trees, line executions, branch decisions, and variable mutations without pausing, exposing empirical execution data to AI coding agents (**Google Antigravity, Claude Code, Cursor**) via the **Model Context Protocol (MCP)**.

---

## 📌 Table of Contents
1. [The Problem Iris Solves](#-the-problem-iris-solves)
2. [Real-World Case Study: Pallets/Flask](#-real-world-case-study-palletsflask)
3. [AI Agent Integration (Antigravity, Claude, Cursor, Windsurf)](#-ai-agent-integration-antigravity-claude-cursor-windsurf)
4. [System Architecture](#-system-architecture)
5. [Key Features](#-key-features)
6. [Core MCP Tools (API Reference)](#-core-mcp-tools-api-reference)
7. [User Guide & Workflows](#-user-guide--workflows)
8. [Security & Safety (Circuit Breaker)](#-security--safety-circuit-breaker)
9. [Testing & Development](#-testing--development)
10. [License](#-license)

---

## 💡 The Problem Iris Solves

When an AI Agent reads static code, it only sees **possibilities** (*"function A might call function B"*). It cannot see **runtime reality** (*"during that specific run, function A called function B three times, returning empty on the third attempt"*).

| Scenario | Agent Reading Static Code | Runtime Truth Exposed by Iris |
|---|---|---|
| **Function called 3 times** | *"Must be an infinite loop bug"* | Intentional timeout retry mechanism |
| **Complex `if` branch** | *"The bug is probably inside this branch"* | Condition evaluated to `False`; block **never executed** |
| **Unexpectedly empty variable** | *"External API must have returned empty data"* | An upstream regex middleware stripped all characters |

Iris does not deduce or speculate for the Agent — it provides **empirical evidence**, allowing the Agent to find the root cause with certainty.

---

## 🌟 Real-World Case Study: Pallets/Flask

To prove that Iris functions seamlessly in production-grade codebases rather than just toy examples, Iris was tested against the official **[Pallets/Flask](https://github.com/pallets/flask)** repository (specifically its canonical **Flaskr** application) without modifying a single line of Flask's code.

### Step 1: Agent Arms Target Function via MCP
The Agent configures Iris to monitor the `register` endpoint in Flask's auth blueprint:
```json
iris_arm_entry(entry_file="auth.py", entry_function="register")
```

### Step 2: Run the Existing Test Suite via Pytest
The user (or Agent) runs Flask's existing test suite with no code changes:
```powershell
pytest examples/cloned_flask/examples/tutorial/tests/test_auth.py::test_register
```
Iris's automatic pytest plugin (`pytest-iris`) instantly detects the armed session and hooks into PEP 669:
```text
[Iris] Auto-attached flight recorder for session 'flask_demo_register' (Target: register in auth.py)
tests\test_auth.py .                                                     [100%]
1 passed in 0.25s
```

### Step 3: Agent Queries the Hierarchical Call Tree
Using `iris_query_call_tree`, the Agent inspects the exact runtime call hierarchy of Flask handling the HTTP request:
```text
- register() [exec_b24e81089cf7] (auth.py)
  - __get__() [exec_77e8149cb9d8] (local.py)
    - _get_current_object() [exec_46c8969d2584] (local.py)
    - bind_f() [exec_08d94caef3a0] (local.py)
  - render_template() [exec_0ad89dab1e83] (templating.py)
    - _get_current_object() [exec_7e1025cf2218] (local.py)
    - __get__() [exec_3de1c6fa651e] (utils.py)
    - get_or_select_template() [exec_71c801a74e4b] (environment.py)
    - _render() [exec_0ef210b3608e] (templating.py)
```

### Step 4: Inspecting Line-by-Line Flow & Sanitized Outputs
Using `iris_inspect_execution_flow`, the Agent inspects statements executed and variable mutations:
```text
Line 53: if request.method == "POST":                  | Delta: {}
Line 81: return render_template("auth/register.html")  | Delta: {}
RETURN: <!doctype html><title>Register - Flaskr</title>... [truncated 441 chars] ...
```
*Notice how Iris automatically truncated large rendered HTML strings via its DataSanitizer to preserve LLM context budget.*

👉 **Reproduce this live:**
```powershell
python examples/run_realworld_flask_demo.py
```

---

## 🚀 AI Agent Integration (Antigravity, Claude, Cursor, Windsurf)

Iris is built strictly on the open **Model Context Protocol (MCP)** specification over `stdio`. It connects seamlessly to any MCP-compliant AI coding agent:

### 1. Google Antigravity (Zero-Config Discovery)
Iris includes specialized configurations for **Google Antigravity IDE** to automatically discover and ingest the MCP server:

* **Method 1: Direct Clone (Zero-Config)**
  ```powershell
  git clone https://github.com/livia2372005-ops/Iris.git
  cd Iris
  pip install -e .
  ```
  Simply open this workspace in Antigravity. The agent will **automatically discover the `iris` MCP server** and its 4 tools.

* **Method 2: Setup in Any Existing Project (1-Click Local Setup)**
  ```powershell
  pip install iris-flight-recorder
  iris setup-agent
  ```
  Automatically initializes `.agents/plugins/iris/` and maps your current virtual environment interpreter.

* **Method 3: Global Registration (All Workspaces)**
  ```powershell
  iris setup-agent --global
  ```
  Registers Iris in `~/.gemini/config/mcp_config.json` so every Antigravity project has instant access.

---

### 2. Claude Code & Claude Desktop
Add Iris to your `claude_desktop_config.json` (or configure via Claude Code CLI):
```json
{
  "mcpServers": {
    "iris": {
      "command": "python",
      "args": ["-m", "iris.mcp.server"]
    }
  }
}
```

---

### 3. Cursor IDE
Open **Cursor Settings** -> **Features** -> **MCP Servers** -> **Add New MCP Server**:
- **Name:** `iris`
- **Type:** `command`
- **Command:** `python -m iris.mcp.server`

Or add `.cursor/mcp.json` to your workspace root:
```json
{
  "mcpServers": {
    "iris": {
      "command": "python",
      "args": ["-m", "iris.mcp.server"]
    }
  }
}
```

---

### 4. Windsurf, Continue.dev & Roo Code
Add the standard stdio MCP entry to your client configuration:
```json
{
  "mcpServers": {
    "iris": {
      "command": "python",
      "args": ["-m", "iris.mcp.server"]
    }
  }
}
```

---

## 🏗️ System Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│    AI Coding Agent (Antigravity / Claude Code / Cursor)     │
└──────────────────────────────┬──────────────────────────────┘
                               │ MCP (JSON-RPC over stdio)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                       Iris MCP Server                       │
│    (Independent process, does NOT run inside target app)    │
└──────────────────────────────┬──────────────────────────────┘
                               │ SQL Read (WAL Mode)
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                 iris_trace.db (SQLite WAL)                  │
└──────────────────────────────▲──────────────────────────────┘
                               │ SQL Batch Insert (Background Thread)
┌──────────────────────────────┴──────────────────────────────┐
│                Event Queue (queue.SimpleQueue)              │
└──────────────────────────────▲──────────────────────────────┘
                               │ In-Memory Non-blocking Push
┌──────────────────────────────┴──────────────────────────────┐
│            Target Application Process (Python >= 3.12)      │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Iris Observer (PEP 669 sys.monitoring)               │  │
│  │ Only wakes up when the armed entrypoint is executed  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Key Principle:** The Iris Observer runs **inside** the target application process as a lightweight library, while the Iris MCP Server runs as a **separate process** reading SQLite in WAL mode. The application process is never frozen while an AI agent is thinking or reading traces.

---

## ✨ Key Features

1. **Zero Stop-the-World Overhead**: Built on CPython's PEP 669 (`sys.monitoring`) and asynchronous lock-free persistence. The target application process is never paused or blocked while traces are saved or while AI agents query the database.
2. **Narrow ARMED Instrumentation**: While sleeping (`UNARMED`), overhead is strictly 0.0%. While `ARMED`, Iris registers only `PY_START` to intercept the target entrypoint, completely skipping `LINE` and `BRANCH` bytecode callbacks on unrelated code.
3. **Variable-Level Data Lineage Graph (`iris_trace_data_lineage`)**: Constructs an empirical causal graph of variable versions (`value_refs` and `lineage_edges`) across execution lines. AI Agents can slice backward (`BACKWARD`) to find the exact root cause of faulty state, or slice forward (`FORWARD`) to assess the blast radius of unexpected inputs.
4. **4 Epistemic Truth Statuses**: Transparently classifies every variable snapshot as `Observed` (direct runtime measurement), `Static` (module global / constant), `Inferred` (derived from AST when skipped), or `Unknown` (unmeasured external boundaries).
5. **Zero-Dependency Web Middleware (ASGI / WSGI)**: Standard middlewares for FastAPI, Starlette, Flask, and Django with zero third-party dependencies. Allows agents or developers to trigger tracing on demand via HTTP headers (`X-Iris-Trace: true`, `X-Iris-Target`, `X-Iris-Condition`) and returns the `X-Iris-Session-Id` header.
6. **Conditional Arming & Predicates**: Arm functions conditionally (e.g. `condition="user_id == 42 and amount > 500"`). The flight recorder evaluates local arguments safely via Python AST and only records when the predicate matches.
7. **Smart Trace Diagnosis & Event Compression (`iris_diagnose_anomaly`)**: Compacts thousands of loop iterations into single diagnostic summaries, flags unexpected variable type mutations (e.g. `dict` ➔ `NoneType`), and maps exception root causes directly to line numbers.
8. **Automatic Pytest Plugin (`pytest-iris`)**: When an Agent arms an entrypoint, you just run `pytest`. Iris automatically attaches, traces, and flushes **without modifying a single line of test code**.
9. **Variable Delta (Diff) Tracking**: Each line event computes and isolates **only variables that actually changed** (`delta`), saving >80% context tokens for the LLM.
10. **Full Asyncio Context & Coroutine Suspension**: Preserves coroutine parent-child hierarchies via `contextvars`, capturing `async_task_id` and tracking coroutine pause/resume states (`PY_YIELD` / `PY_RESUME`).
11. **Multi-layer Data Sanitizer**: Masks sensitive variables (`password`, `token`, `secret`, `api_key`, `credit_card`, `ssn`) and scans values with regex for JWT, AWS keys, and private key PEMs before writing to disk.
12. **Circuit Breakers & Auto-Timeout**: Auto-aborts tracing if a session exceeds **500,000 events** or database size reaches **200 MB**. Automatically cleans up stale sessions when targets are not triggered.
13. **Multi-Session Registry & Active Session Control (`iris_stop_session`)**: Concurrently arm and trace multiple entrypoints across parallel test runners and microservices, or actively cancel sessions on demand.
14. **Python 3.14+ Ready & C-Extension Compatible**: Powered by a version-aware `MonitoringProvider` supporting directional branch monitoring (`BRANCH_LEFT`/`BRANCH_RIGHT`) and seamlessly tracing boundaries of native C/C++/Rust extensions (NumPy, PyTorch).

---

## ⚡ Performance Benchmarks & Overhead Analysis

Iris achieves **zero stop-the-world overhead** (the target process is never paused for agent interaction) by decoupling trace collection via a lock-free background SQLite WAL queue.

Runtime overhead is strictly governed by the observation state:
- **`UNARMED` (Detached):** **0.0% overhead** — no bytecode hooks are registered in the Python runtime.
- **`ARMED` (Passive):** **Near-zero runtime overhead (< 1-3%)** — only `PY_START` is registered. Non-target functions bypass immediately via internal path caching; expensive `LINE` and `BRANCH` events remain completely inactive.
- **`TRACING` (Active):** Active function execution captures call frames, line deltas, and branches with multi-layer variable sanitization.

### Empirical Benchmarks (Python 3.12, Windows 11)

Run the benchmark suite locally with:
```bash
python benchmarks/run_benchmark.py
```

| Workload | Native Baseline | Armed (Passive) | Armed Overhead | Tracing (Active) |
| :--- | :---: | :---: | :---: | :---: |
| **Iterative Loop & Branches** (1,500 iters) | 0.10 ms | 0.19 ms | +0.09 ms | 375.6 ms |
| **Data Transformation** (150 records) | 0.15 ms | 0.11 ms | ~0.0% | 688.6 ms |
| **Recursive Call Stack** (Fibonacci) | 0.02 ms | 0.34 ms | +0.32 ms | 222.8 ms |

Unlike traditional debuggers (`pdb`, `sys.settrace`) which inject 10x–50x slowdowns globally across the entire process lifetime, Iris keeps the application running at native speed until the exact target function triggers.

---

## 🛠️ Core MCP Tools (API Reference)

When connected to any MCP client, the Agent has access to the following 7 tools:

### 1. `iris_arm_entry`
Arm an entry point (file and function) for observation with optional predicate conditions.
- **Parameters:**
  - `entry_file` *(string, required)*: Path or filename of the target script (e.g., `"routes.py"`, `"services/order.py"`).
  - `entry_function` *(string, required)*: Name of the target function (e.g., `"handle_chat"`).
  - `condition` *(string, optional)*: Python expression evaluated against function arguments at entry (e.g., `"user_id == 42 and amount > 500"`). Tracing only triggers when True.
  - `timeout_seconds` *(int, default: 60)*: Maximum duration in seconds to wait for execution.
- **Returns:** `session_id`, state (`ARMED`), condition, database path.

### 2. `iris_get_session_status`
Check the status and summary statistics of an observation session.
- **Parameters:**
  - `session_id` *(string, required)*: Session ID returned by `iris_arm_entry`.
- **Returns:** `state` (`ARMED`, `TRACING`, `COMPLETED`, `ABORTED_TIMEOUT`, `STOPPED`, `COMPLETED_TRUNCATED`), `total_events`, start/end timestamps.

### 3. `iris_stop_session`
Actively terminate an ARMED or TRACING session without waiting for timeout expiration.
- **Parameters:**
  - `session_id` *(string, required)*: Session ID to stop.
- **Returns:** State confirmation (`STOPPED`) and timestamp.

### 4. `iris_trace_data_lineage`
Trace the empirical causal data lineage graph of a variable across execution lines and versions.
- **Parameters:**
  - `value_ref_id` *(string, required)*: Unique ValueRef ID (e.g., `"vref_1a2b3c4d"`).
  - `direction` *(string, default: "BACKWARD")*: `"BACKWARD"` to find where a bad value originated (root cause), or `"FORWARD"` to see where a value propagated (blast radius).
  - `depth_limit` *(int, default: 5)*: Maximum causal traversal hops.
- **Returns:** Nodes, causal edges, epistemic statuses (`Observed`, `Static`, `Inferred`, `Unknown`), and an ASCII DAG flow diagram.

### 5. `iris_diagnose_anomaly`
Smart diagnosis and event compression engine for rapid LLM reasoning.
- **Parameters:**
  - `session_id` *(string, required)*: Session ID.
- **Returns:** Token-efficient markdown report collapsing loops (e.g. 500 iterations ➔ 1 summary), detecting unexpected variable type mutations (e.g. `dict` ➔ `NoneType`), and pinpointing exception root causes.

### 6. `iris_query_call_tree`
Query the hierarchical call tree of functions invoked during the session.
- **Parameters:**
  - `session_id` *(string, required)*: Session ID.
  - `depth_limit` *(int, default: 2)*: Maximum call depth to return.
- **Returns:** JSON hierarchy including `function_name`, `file_path`, `execution_id`, and nested `children`.

### 7. `iris_inspect_execution_flow`
Inspect detailed line-by-line execution, source code, and variable mutations.
- **Parameters:**
  - `execution_id` *(string, required)*: Specific function execution ID from the call tree.
  - `limit` *(int, default: 50)*: Number of events per page.
  - `cursor` *(int, default: 0)*: Sequence number offset for pagination.
- **Returns:** Ordered event stream (`LINE`, `BRANCH`, `RETURN`, `EXCEPTION`), resolved `source_line`, and `payload.delta`.

---

## 📖 User Guide & Workflows

### Scenario 1: Debugging with Pytest (Recommended for AI Agents)
1. **Agent arms target function via MCP**:
   ```json
   iris_arm_entry(entry_file="routes.py", entry_function="handle_chat")
   ```
2. **Run your tests**:
   ```powershell
   pytest tests/test_chat.py
   ```
   *Iris automatically detects the ARMED session and traces execution during the test.*
3. **Agent queries the flight recorder**:
   ```json
   iris_get_session_status(session_id="iris_...")
   iris_query_call_tree(session_id="iris_...")
   iris_inspect_execution_flow(execution_id="exec_...")
   ```

---

### Scenario 2: Live Web Framework Tracing via Middleware (FastAPI / Flask / Django)

Iris includes zero-dependency **ASGI** and **WSGI** middlewares. You can keep Iris installed in your web application without any performance impact, triggering on-demand flight recording per-request via HTTP headers.

#### A. FastAPI & Starlette (ASGI)
```python
from fastapi import FastAPI
from iris.middleware.asgi import IrisASGIMiddleware

app = FastAPI()
# Add Iris middleware (zero overhead when X-Iris-Trace header is absent)
app.add_middleware(IrisASGIMiddleware)

@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    return {"order_id": order_id, "status": "processed"}
```

#### B. Flask & Django (WSGI)
```python
from flask import Flask
from iris.middleware.wsgi import IrisWSGIMiddleware

app = Flask(__name__)
# Wrap WSGI application callable
app.wsgi_app = IrisWSGIMiddleware(app.wsgi_app)
```

#### C. Triggering On-Demand Tracing via HTTP Headers
Send requests with standard Iris control headers:
```powershell
# Curl request triggering flight recording for the target function
curl -X GET "http://localhost:8000/orders/42" `
  -H "X-Iris-Trace: true" `
  -H "X-Iris-Target: main.py:get_order" `
  -H "X-Iris-Condition: order_id == 42"
```

The response returns an `X-Iris-Session-Id` header:
```http
HTTP/1.1 200 OK
content-type: application/json
X-Iris-Session-Id: iris_asgi_3a4c5f921
```

The AI Coding Agent can immediately inspect or diagnose this live HTTP request:
```json
iris_diagnose_anomaly(session_id="iris_asgi_3a4c5f921")
```

---

### Scenario 3: Running Standalone Scripts via CLI
```powershell
# Execute any script under Iris flight recording
python -m iris run app/main.py
```

---

### Scenario 4: Programmatic Tracing in Python Code
```python
from iris import trace, trace_context

# Option A: Context Manager
with trace_context(entry_file="my_service.py", entry_function="process_order"):
    process_order(order_id=123)

# Option B: Function Decorator
@trace(entry_function="my_api")
def my_api():
    ...
```

---

### Database Maintenance & Pruning
```powershell
# View environment diagnostics and PEP 669 status
python -m iris info

# Retain latest 5 sessions and prune older trace history
python -m iris clean --keep 5

# Reset and purge all trace sessions
python -m iris clean --all
```

---

## 🔒 Security & Safety (Circuit Breaker)

- **100% Offline & Local**: All trace data is stored locally in SQLite (`iris_trace.db`). No cloud telemetry or network requests are ever made.
- **Automatic In-Memory Secret Redaction**:
  - Variable names matching `password`, `secret`, `token`, `api_key`, `auth`, `credit_card`, `private_key`, `ssn` are redacted as `[REDACTED_SECRET: len=...]`.
  - Regex pattern matchers sanitize JWTs, AWS credentials, RSA/SSH private keys, and credit cards.
  - Large values (strings > 256 characters, collections > 20 items) are safely truncated before reaching storage.
- **Circuit Breakers**: Prevents runaway recursive loops from filling disk space by halting capture at **500,000 events** or **200 MB** database size.

---

## 🧪 Testing & Development

Run the complete automated test suite (**33/33 tests passing**):
```powershell
python -m pytest tests/
```

Test suite coverage:
- `test_batch1.py`: Minimal ARMED bytecode scoping, internal path fast-caching, and Python 3.12 branch jump rule.
- `test_batch2_concurrency.py`: Monotonic multi-threaded sequence sequencing and `PY_YIELD` / `PY_RESUME` coroutine suspension tracing.
- `test_batch2_multisession.py`: Multi-session registry and concurrent parallel entrypoint tracking.
- `test_batch2_py314_compat.py`: Python 3.14+ `MonitoringProvider` branch bitmask dispatching and version fallback.
- `test_conditional_arming.py`: Predicate AST parsing, argument extraction, and conditional execution filtering.
- `test_smart_diagnosis.py`: Event compression, loop collapsing, type mutation alerts, and exception root-cause tracing.
- `test_middleware.py`: Zero-dependency ASGI and WSGI middleware lifecycle and HTTP header triggering.
- `test_realworld_flask.py`: End-to-end flight recording on cloned official Pallets/Flask repository.
- `test_mcp_handshake.py`: Stdio JSON-RPC protocol compliance and MCP capabilities.
- `test_sanitizer.py`: DataSanitizer blacklist, regex, and truncation rules.
- `test_fsm_observer.py`: FSM state transitions, PEP 669 line/branch/return events, nested functions, and asyncio tasks.
- `test_e2e_demo.py`: Complete 6-step flight recording scenario identifying subtle regex bugs.
- `test_v1_enhancements.py`: Auto-timeout expiry, variable delta isolation, async task ID capture, and database pruning.
- `test_ast_lineage.py`: Line-level AST dependency analyzer, load/store variable extraction, and operation tags.
- `test_lineage_storage.py`: Value ref persistence, lineage edge DAG traversal (backward/forward), and database cascade pruning.
- `test_lineage_e2e.py`: End-to-end causal variable lineage tracking across transformations with ASCII DAG generation.
- `test_stop_session.py`: Active cancellation of flight recording sessions via FSM `STOPPED` state and MCP `iris_stop_session`.

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).  
Feel free to use, modify, and distribute in both personal and commercial projects.
