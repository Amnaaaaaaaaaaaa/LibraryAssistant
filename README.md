# Riverbend Library Assistant

NLP Assignment 1 — Conversational AI (local CPU LLM, no tools, no RAG)

**Domain:** Library Assistant  
**Group of two:**

- Fizza Ali — 22i-8787
- Amna Malik — 22i-2715

A fully local, CPU-only conversational assistant for a community library.
No cloud model APIs, no tools, and no RAG — every reply comes from prompt
orchestration and conversational memory over a locally run quantized LLM.

---

## 1. Use Case Description

Riverbend Community Library wants a chatbot on its website so patrons can
handle routine questions without calling or visiting the front desk.

**In scope (library services only):**

- Searching a small sample catalogue (title, author, copies, holds)
- Borrowing, renewal, and overdue/lost-item policies
- Placing a **simulated** hold and explaining the 3-day pickup window
- Membership tiers (Standard vs Premium) and loan limits
- Opening hours and how to get a library card

**Out of scope:** weather, news, trivia, homework, coding, medical/legal
advice, or anything that is not about this library. The assistant must
refuse those and steer back to library topics.

This domain carries through to Assignments 2 and 3.

### Conversation Flow Design (library-specific)

The five stages live in the system prompt (`backend/prompts.py`). There is
no external state machine — the model is instructed to track the stage
from dialogue context.

1. **Greeting (library desk)** — welcome the patron to Riverbend and offer
   catalogue, loans, holds, hours, or membership help.
2. **Need identification** — decide which *library* task this is:
   catalogue lookup, renewal, hold, fines/due dates, hours, membership
   tier, or library card. Ask a clarifying question if the request is
   vague (e.g. title vs author, Standard vs Premium).
3. **Policy / catalogue resolution** — answer only from the embedded
   knowledge base (hours, copy counts, fine rates, renewal rules). If
   the title is not in the sample catalogue, say so and do **not**
   invent availability.
4. **Hold / renewal confirmation** — before treating a hold or renewal as
   “noted”, restated the item and remind the patron it is a demo: they
   still confirm at the front desk or in the app. Pickup window is 3 days
   once a held item is available.
5. **Follow-up or closing** — ask if they need another library service.
   If they switch (hours → renew *Clean Code*), acknowledge the switch,
   keep earlier facts (membership tier, book already named) from the
   sliding window, and re-enter need identification for the new task.
   Close warmly when they are done.

**Topic change:** a patron can move from Sunday hours to renewing
*Clean Code* in the next turn. The assistant must not restart from a
generic greeting; it keeps Riverbend context and the named title.

**Irrelevant queries:** refuse immediately (no partial answer), then
offer a library alternative. Jailbreak / “ignore your instructions”
prompts are treated the same way. See Dialogue 3 in
[`docs/example_dialogues.md`](docs/example_dialogues.md).

---

## 2. Architecture

```
┌─────────────────────┐        WebSocket (JSON)        ┌───────────────────────────┐
│   Frontend (HTML/JS) │ <-----------------------------> │   FastAPI Backend         │
│   frontend/index.html│        /ws/chat                 │   backend/main.py         │
│   - chat UI           │        REST: /session/*        │                           │
│   - streaming render  │ <-----------------------------> │                           │
└─────────────────────┘                                  │  ┌─────────────────────┐  │
                                                            │  │ ConversationManager │  │
                                                            │  │ - session store      │  │
                                                            │  │ - sliding-window     │  │
                                                            │  │   memory             │  │
                                                            │  │ - system prompt build│  │
                                                            │  └─────────┬───────────┘  │
                                                            │            │ messages[]    │
                                                            │  ┌─────────▼───────────┐  │
                                                            │  │   LLM Engine         │  │
                                                            │  │ (llm_engine.py)      │  │
                                                            │  │  HTTP + streaming    │  │
                                                            │  └─────────┬───────────┘  │
                                                            └────────────┼──────────────┘
                                                                         │ localhost:11434
                                                                ┌────────▼─────────┐
                                                                │  Ollama (local)   │
                                                                │  qwen2.5:1.5b     │
                                                                │  CPU inference    │
                                                                └───────────────────┘
```

**Data flow per turn:**

1. Browser sends `{"session_id", "message"}` over the WebSocket.
2. `ConversationManager` appends the user turn and builds
   `[system prompt] + [sliding window of recent turns]`.
3. `llm_engine.stream_chat()` POSTs to Ollama `/api/chat` with
   `stream: true`.
4. The backend forwards each token (`{"type": "token"}`), then
   `{"type": "done", "ttft", "tokens_per_sec"}`.
5. The full assistant reply is stored on the session.

No RAG, no function calling, no cloud APIs. Catalogue and policy text
are static inside `backend/prompts.py`.

### API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness (`{"status": "ok"}`) |
| `POST` | `/session/new` | Create a session; returns `{session_id}` |
| `POST` | `/session/{id}/reset` | Wipe that session’s history |
| `WS` | `/ws/chat` | Streaming chat |

**WebSocket client → server**

```json
{"session_id": "<uuid or empty>", "message": "<patron text>"}
```

**Server → client** (one or more per turn)

```json
{"type": "session", "session_id": "<uuid>"}
{"type": "token", "content": "<partial text>"}
{"type": "done", "content": "<full text>", "ttft": 0.0, "tokens_per_sec": 0.0}
{"type": "error", "content": "<message>"}
```

Malformed JSON, a missing `message` field, or an empty message return
`type: error` and **keep the socket open**. A downed Ollama process
returns a connection error instead of crashing the API.

Each WebSocket is its own asyncio task, so accepting and reading from
user A does not block user B. Ollama itself typically generates one
sequence at a time; that queueing is documented under Limitations.

---

## 3. Model Selection

**Model:** `qwen2.5:1.5b` (Q4 quantization via Ollama)

**Why this model:**

- Inside the assignment’s 0.5B–4B range.
- Instruction-tuned, so it follows the library system prompt (stages,
  catalogue facts, refusals) without fine-tuning.
- Fits an 8 GB CPU laptop (this submission machine is an HP ZBook
  Firefly 14 G7, Intel i5-10310U, 8 GB RAM). A 3B model is allowed by
  the spec but is tighter on this RAM budget and slower for a live viva.
- Ollama makes pull + serve a one-line local setup; no cloud APIs.

`MODEL_NAME` in `backend/llm_engine.py` and the default in
`backend/benchmark.py` are both `qwen2.5:1.5b`.

### Context Memory Management Scheme

- **Full transcript** stays in server RAM for the life of the session
  (the UI can show every bubble).
- **Prompt sent to the model** is a sliding window of the last
  `MAX_TURNS` (6) user/assistant **pairs**, plus the system prompt,
  which is never trimmed.
- Each stored message is capped at `MAX_CHARS_PER_MSG` (2000) so one
  huge paste cannot blow the context.
- Older turns are dropped, not summarized. Latency stays roughly
  constant; very early details (e.g. a book named 8+ turns ago) can
  fall out of the window.

---

## 4. Latency Benchmarks

Measured on this laptop with `python benchmark.py --model qwen2.5:1.5b --runs 5`
after `ollama serve` and a pulled `qwen2.5:1.5b`. Numbers are from the
script output, not estimates.

```powershell
cd backend
.\venv\Scripts\python.exe benchmark.py --model qwen2.5:1.5b --runs 5
```

| Metric | Value |
|---|---|
| Hardware (CPU, RAM) | Intel Core i5-10310U (4 cores / 8 threads), 8 GB RAM, HP ZBook Firefly 14 G7 |
| Model | qwen2.5:1.5b (Q4 via Ollama) |
| Avg. time-to-first-token (TTFT) | **0.932 s** |
| Avg. total response time | **7.791 s** |
| Avg. tokens/second | **11.18** |
| Concurrent sessions tested | FastAPI accepts several WebSockets at once; Ollama generates **one** sequence at a time, so extra chats queue. First UI turn after a cold model load showed TTFT **56.1 s** (load), then warmed to ~1 s. |

Per-run log:

| Run | Prompt (short) | TTFT | Total | tok/s |
|---|---|---|---|---|
| 1 | library hours | 1.443 s | 4.394 s | 8.65 |
| 2 | Atomic Habits availability | 0.715 s | 5.628 s | 11.02 |
| 3 | renew Clean Code | 0.879 s | 4.552 s | 10.76 |
| 4 | overdue fine, 4 days | 0.791 s | 16.338 s | 12.79 |
| 5 | hold Silent Patient | 0.832 s | 8.042 s | 12.68 |

---

## 5. Setup Instructions

### Prerequisites

- Windows 10/11, Python 3.10+
- [Ollama](https://ollama.com/download) installed (adds `ollama` to PATH)

### Steps (Windows PowerShell)

```powershell
# 1. Install Ollama from https://ollama.com/download if `ollama` is not
#    recognized. Then start the server (leave this window open):
ollama serve

# 2. In a second terminal, pull the model used by this repo:
ollama pull qwen2.5:1.5b

# 3. Backend
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
# If that is blocked: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
pip install -r requirements.txt

# 4. API
uvicorn main:app --reload --port 8000
```

Open `frontend/index.html` in a browser (double-click). It connects to
`ws://localhost:8000/ws/chat`.

Health check: `http://localhost:8000/health` → `{"status": "ok"}`.

### Benchmark

```powershell
cd backend
python benchmark.py --model qwen2.5:1.5b --runs 5
```

### WebSocket failure tests (Phase VI, tests 5–6)

With uvicorn already running:

```powershell
cd backend
python failure_tests.py
```

---

## 6. Known Limitations

- **No persistence:** sessions are in-process memory; restarting uvicorn
  wipes history. No database.
- **Ollama concurrency:** FastAPI handles many WebSockets; the local
  model typically generates one reply at a time, so concurrent patrons
  queue.
- **Sliding window, not summarization:** after 6 turn-pairs, older
  context is dropped.
- **1.5B reasoning limits:** persistent jailbreaks or ambiguous wording
  can still cause drift or hallucination. Mitigated by a fixed knowledge
  base and explicit “do not invent catalogue data” rules, not eliminated.
- **Simulated transactions:** holds, renewals, and fines are
  conversational only — nothing is written to a real ILS.
- **CORS `*`:** acceptable for local grading, not production.
- **8 GB RAM:** this laptop is tight; keep the 1.5B model for the demo.

---

## 7. Bonus (optional — one item)

**Chosen: UX / persona polish** (not cloud deployment).

Beyond a minimal chat box, the UI is a Riverbend desk: shelf greens,
paper background, streaming caret, per-reply TTFT, auto-reconnect, a
visible session id, and an on-load greeting that stays in character.
The system prompt uses library-specific few-shot refusals so the
persona holds under off-topic and “ignore your instructions” prompts
(see Dialogues 3–4 and the Phase VI checklist).

Cloud URL: not deployed (local CPU + Ollama cannot sit on a free
static host by itself).

---

## 8. Project Structure

```
library-assistant/
├── backend/
│   ├── main.py                 # FastAPI: REST + WebSocket
│   ├── conversation_manager.py # sessions + sliding-window memory
│   ├── llm_engine.py           # streaming client for local Ollama
│   ├── prompts.py              # knowledge base, policies, stages
│   ├── benchmark.py            # TTFT / tok/s measurement
│   ├── failure_tests.py        # malformed / empty WebSocket checks
│   └── requirements.txt
├── frontend/
│   └── index.html              # chat UI (HTML/CSS/vanilla JS)
├── docs/
│   └── example_dialogues.md    # transcripts + Phase VI checklist
└── README.md
```
