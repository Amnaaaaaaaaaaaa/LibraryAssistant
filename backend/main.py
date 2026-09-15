"""
main.py
-------
FastAPI backend for the Riverbend Library Assistant.

Endpoints:
  GET  /health              - simple liveness check
  POST /session/new         - create a new session id
  POST /session/{id}/reset  - wipe a session's history
  WS   /ws/chat             - real-time streaming chat

Concurrency notes:
  - Each WebSocket connection is handled by its own asyncio task (FastAPI/
    Starlette do this automatically), so one user's slow request does not
    block another's connection from being accepted or from sending/
    receiving its own messages.
  - The ConversationManager is a single in-memory object shared across
    connections, keyed by session_id, so concurrent sessions don't see
    each other's history.
  - Because a single local LLM (Ollama) instance typically processes one
    generation at a time, true parallel *inference* throughput is limited
    by the model server itself - this is a known limitation, documented
    in the README, and expected for a CPU-only single-model setup.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json

from conversation_manager import ConversationManager
from llm_engine import stream_chat

app = FastAPI(title="Riverbend Library Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # fine for a local demo; tighten before any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)

conv_manager = ConversationManager()


class NewSessionResponse(BaseModel):
    session_id: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/session/new", response_model=NewSessionResponse)
async def new_session():
    sid = conv_manager.create_session()
    return {"session_id": sid}


@app.post("/session/{session_id}/reset", response_model=NewSessionResponse)
async def reset_session(session_id: str):
    sid = conv_manager.reset_session(session_id)
    return {"session_id": sid}


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    """
    Expected incoming JSON message shape:
      {"session_id": "<uuid or empty>", "message": "<user text>"}

    Outgoing JSON message shapes (one or more per user turn):
      {"type": "session", "session_id": "<uuid>"}          -- sent once, on connect/first turn
      {"type": "token", "content": "<partial text>"}        -- streamed repeatedly
      {"type": "done", "content": "<full text>",
       "ttft": <float>, "tokens_per_sec": <float>}          -- end of a turn
      {"type": "error", "content": "<message>"}             -- malformed input or model failure
    """
    await websocket.accept()
    session_id = None

    try:
        while True:
            raw = await websocket.receive_text()

            # --- robust parsing: never let a bad payload crash the connection ---
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "content": "Malformed request: expected valid JSON."}
                )
                continue

            if not isinstance(data, dict) or "message" not in data:
                await websocket.send_json(
                    {"type": "error", "content": "Malformed request: missing 'message' field."}
                )
                continue

            user_message = str(data.get("message", "")).strip()
            incoming_sid = data.get("session_id") or session_id

            if not user_message:
                await websocket.send_json(
                    {"type": "error", "content": "Empty message ignored - please type something."}
                )
                continue

            session_id = conv_manager.ensure_session(incoming_sid)
            if session_id != incoming_sid:
                await websocket.send_json({"type": "session", "session_id": session_id})

            conv_manager.add_user_message(session_id, user_message)
            prompt_messages = conv_manager.build_prompt_messages(session_id)

            assistant_text = ""
            try:
                async for event in stream_chat(prompt_messages):
                    if event["type"] == "token":
                        assistant_text += event["content"]
                        await websocket.send_json(event)
                    elif event["type"] == "done":
                        await websocket.send_json(event)
                        conv_manager.add_assistant_message(session_id, event["content"])
                    elif event["type"] == "error":
                        await websocket.send_json(event)
            except Exception as e:  # noqa: BLE001 - keep the socket alive on model errors
                await websocket.send_json(
                    {"type": "error", "content": f"Model error while streaming: {e}"}
                )

    except WebSocketDisconnect:
        # Client closed the tab / lost connection mid-stream - nothing to crash.
        pass
    except Exception as e:  # noqa: BLE001 - last-resort guard so the server never dies
        try:
            await websocket.send_json({"type": "error", "content": f"Server error: {e}"})
        except Exception:
            pass
