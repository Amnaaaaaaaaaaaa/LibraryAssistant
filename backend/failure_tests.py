"""
failure_tests.py
----------------
Phase VI WebSocket failure-handling checks (tests 5 and 6 in
docs/example_dialogues.md).

Requires the FastAPI server to already be running:
    uvicorn main:app --reload --port 8000

Usage (from the backend folder, venv active):
    python failure_tests.py
"""

import asyncio
import json
import sys

import websockets

WS_URL = "ws://localhost:8000/ws/chat"


async def recv_json(ws) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=10)
    return json.loads(raw)


async def run() -> int:
    failed = 0
    async with websockets.connect(WS_URL) as ws:
        # Test 5a — malformed JSON
        await ws.send("this is not json")
        msg = await recv_json(ws)
        ok = msg.get("type") == "error" and "JSON" in msg.get("content", "")
        print(f"[{'PASS' if ok else 'FAIL'}] malformed JSON → {msg}")
        failed += not ok

        # Test 5b — valid JSON but missing 'message'
        await ws.send(json.dumps({"session_id": ""}))
        msg = await recv_json(ws)
        ok = msg.get("type") == "error" and "message" in msg.get("content", "").lower()
        print(f"[{'PASS' if ok else 'FAIL'}] missing 'message' field → {msg}")
        failed += not ok

        # Test 6 — empty / whitespace-only message (must not call the model)
        await ws.send(json.dumps({"session_id": "", "message": "   "}))
        msg = await recv_json(ws)
        ok = msg.get("type") == "error" and "empty" in msg.get("content", "").lower()
        print(f"[{'PASS' if ok else 'FAIL'}] empty message → {msg}")
        failed += not ok

    print()
    if failed:
        print(f"{failed} check(s) failed. Connection should still have closed cleanly.")
        return 1
    print("All WebSocket failure-handling checks passed. Connection stayed open.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run()))
    except ConnectionRefusedError:
        print("Could not connect to ws://localhost:8000/ws/chat.")
        print("Start the API first:  uvicorn main:app --reload --port 8000")
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        print(f"Unexpected failure (server should not crash): {e}")
        sys.exit(1)
