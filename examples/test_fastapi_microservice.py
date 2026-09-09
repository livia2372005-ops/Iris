"""
Real-world test scenario using FastAPI + ASGI Middleware:
- Production-style asynchronous checkout microservice.
- Uses IrisASGIMiddleware for on-demand tracing via HTTP headers.
- Evaluates:
  1. On-demand tracing activation via 'X-Iris-Trace'.
  2. Automatic secret redaction for API tokens.
  3. Causal Data Lineage graph diagnosing a missing discount bug.
"""
import sys
import os
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from starlette.testclient import TestClient

from iris.middleware.asgi import IrisASGIMiddleware
from iris.storage.db import (
    init_db,
    clean_db,
    get_session,
    query_call_tree,
    inspect_execution_flow,
    query_data_lineage,
    get_db_path,
)

# Models
class CartItem(BaseModel):
    name: str
    price: float
    quantity: int

class CheckoutRequest(BaseModel):
    items: List[CartItem]
    promo_code: Optional[str] = None
    tax_rate: float = 0.10

# Initialize FastAPI App
app = FastAPI(title="Store Checkout Microservice")

# Mount Iris ASGI Middleware
app.add_middleware(IrisASGIMiddleware)

async def verify_auth_token(authorization: Optional[str] = Header(None)) -> str:
    """Dependency validating API bearer token."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    api_token = authorization.split(" ")[1]
    # Simulate user lookup
    user_id = "user_premium_99"
    return user_id

@app.post("/api/v1/checkout")
async def checkout_endpoint(
    req: CheckoutRequest,
    user: str = Depends(verify_auth_token)
):
    """
    Checkout endpoint with calculation logic.
    Contains a subtle logic bug: discount_amount is calculated but omitted from final_total!
    """
    raw_subtotal = 0.0
    for item in req.items:
        item_cost = item.price * item.quantity
        raw_subtotal += item_cost

    subtotal = round(raw_subtotal, 2)

    # Calculate discount
    discount_amount = 0.0
    if req.promo_code == "SUMMER50":
        discount_amount = round(subtotal * 0.50, 2)
    elif req.promo_code == "WELCOME10":
        discount_amount = 10.0

    # Tax calculation
    taxable_base = subtotal - discount_amount
    tax_amount = round(taxable_base * req.tax_rate, 2)

    # BUG: Developer wrote `subtotal + tax_amount` instead of `subtotal - discount_amount + tax_amount`!
    final_total = round(subtotal + tax_amount, 2)

    return {
        "user": user,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "tax_amount": tax_amount,
        "final_total": final_total,
        "bug_detected": final_total != (subtotal - discount_amount + tax_amount)
    }

def run_fastapi_benchmark():
    print("=" * 70)
    print("🚀 IRIS REAL-WORLD BENCHMARK: FastAPI Microservice & ASGI Middleware")
    print("=" * 70)

    clean_db()
    init_db()

    client = TestClient(app)

    payload = {
        "items": [
            {"name": "Mechanical Keyboard", "price": 120.0, "quantity": 1},
            {"name": "Wireless Mouse", "price": 40.0, "quantity": 2},
            {"name": "Desk Mat", "price": 20.0, "quantity": 1}
        ],
        "promo_code": "SUMMER50",
        "tax_rate": 0.08
    }

    headers = {
        "X-Iris-Trace": "true",
        "X-Iris-Target": f"{Path(__file__).name}:checkout_endpoint",
        "Authorization": "Bearer secret_jwt_production_access_key_99999"
    }

    print("\n[1] Dispatching HTTP POST /api/v1/checkout with 'X-Iris-Trace: true'...")
    response = client.post("/api/v1/checkout", json=payload, headers=headers)
    print(f"    HTTP Status: {response.status_code}")
    
    session_id = response.headers.get("X-Iris-Session-Id")
    print(f"    Injected X-Iris-Session-Id: {session_id}")
    res_data = response.json()
    print(f"    Response Payload: {res_data}")

    assert session_id, "Middleware should have injected X-Iris-Session-Id!"

    # Allow async DB flush
    import time
    time.sleep(0.5)

    # Inspect Session in SQLite
    status = get_session(session_id)
    print(f"\n[2] Session Flight Recorder Status:")
    print(f"    State: {status['state']}")
    print(f"    Target: {status['entry_function']}() in {status['entry_file']}")
    print(f"    Total Events Captured: {status['total_events']}")

    # Call Tree
    call_tree = query_call_tree(session_id, depth_limit=3)
    print(f"\n[3] Hierarchical Call Tree:")
    for node in call_tree:
        print(f"    - {node['function_name']}() in {Path(node['file_path']).name}")
        for child in node.get("children", []):
            print(f"      └── {child['function_name']}() in {Path(child['file_path']).name}")

    # Inspect execution flow & Secret Redaction
    root_exec_id = call_tree[0]["execution_id"] if call_tree else None
    if root_exec_id:
        print(f"\n[4] Execution Line Flow & Variable State (Execution ID: {root_exec_id}):")
        flow = inspect_execution_flow(session_id, root_exec_id)
        print(f"    Total Executed Line Steps: {len(flow)}")
        
        # Check Value Refs
        import sqlite3
        with sqlite3.connect(get_db_path()) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT value_ref_id, variable_name, version, line_number, epistemic_status, value_snapshot FROM value_refs WHERE execution_id = ?",
                (root_exec_id,)
            )
            vrefs = cur.fetchall()
            print(f"\n[5] Variable Value Refs in checkout_endpoint(): {len(vrefs)} variables")
            var_map = {}
            for vref_id, var_name, version, line_no, epistemic_status, val_snap in vrefs:
                var_map[var_name] = vref_id
                val_clean = (val_snap or "")[:50]
                print(f"    - L{line_no} [{epistemic_status}] {var_name}_v{version} = {val_clean}")

        # Data Lineage Analysis: Trace BACKWARD from final_total
        final_total_vref = var_map.get("final_total")
        if final_total_vref:
            print(f"\n[6] 🔍 Diagnosing Bug via BACKWARD Lineage on 'final_total' ({final_total_vref}):")
            lineage = query_data_lineage(final_total_vref, direction="BACKWARD", depth_limit=5)
            print("    ASCII Causal Flow (Why does final_total have this value?):")
            print(lineage.get("ascii_flow", ""))
            
            # Check if discount_amount is in the causal inputs
            has_discount = any(n.get("variable_name") == "discount_amount" for n in lineage.get("nodes", []))
            print(f"\n    ⚠️  Causal Audit: Is 'discount_amount' an input to 'final_total'?: {has_discount}")
            if not has_discount:
                print("    🎯 ROOT CAUSE CONFIRMED: 'discount_amount' was NEVER connected to 'final_total'!")

        # Data Lineage Analysis: Trace FORWARD from discount_amount (Blast Radius)
        discount_vref = var_map.get("discount_amount")
        if discount_vref:
            print(f"\n[7] 📡 Tracing FORWARD Blast Radius of 'discount_amount' ({discount_vref}):")
            f_lineage = query_data_lineage(discount_vref, direction="FORWARD", depth_limit=5)
            print("    ASCII Blast Radius Flow:")
            print(f_lineage.get("ascii_flow", ""))
            impacted = [n.get("variable_name") for n in f_lineage.get("nodes", [])]
            print(f"    Impacted Variables: {impacted}")
            if "final_total" not in impacted:
                print("    ⚠️  Notice: 'final_total' is NOT in the blast radius of 'discount_amount'!")

    print("\n" + "=" * 70)
    print("✅ FASTAPI BENCHMARK COMPLETED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    run_fastapi_benchmark()
