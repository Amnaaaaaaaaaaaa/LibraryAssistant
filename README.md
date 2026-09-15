# Riverbend Library Assistant

A fully local, CPU-only conversational AI assistant for a community
library, built for Assignment 1. No cloud model APIs, no tools, no RAG —
all behaviour comes from prompt orchestration and conversational memory
over a locally-run quantized LLM.

---

## 1. Use Case Description

Riverbend Community Library wants a chatbot on its website that helps
patrons **search the catalogue, understand borrowing/renewal policies,
place holds, check due dates and fines, and learn library hours and
membership tiers** — without needing to call or visit the front desk for
routine questions. The assistant stays strictly inside these library
topics; it declines anything else (weather, general trivia, coding help,
etc.) and steers the conversation back.

### Conversation Flow Design

The assistant tracks five stages internally (encoded in its system
prompt, not enforced by external code, since no tools/state-machine
libraries are used):

1. **Greeting** — welcomes the patron, asks how it can help.
2. **Information Gathering** — figures out what the patron actually
   wants (book search, renewal, hold, policy question, hours) and asks
   clarifying questions if the request is vague.
3. **Resolution / Confirmation** — answers the question, or restates the
   request back to the patron before treating it as resolved (e.g.
   confirming a hold request before "logging" it).
4. **Follow-up** — asks if there's anything else; if the topic changes,
   it loops back to stage 2 for the *new* topic instead of restarting
   the whole conversation from scratch.
5. **Closing** — thanks the patron and ends warmly once they're done.

**Handling topic changes:** because the full flow lives inside a single
system prompt (see `backend/prompts.py`) rather than a rigid external
state machine, the model is instructed to recognize a topic switch,
briefly acknowledge it, and re-enter the Information Gathering stage for
the new topic while keeping earlier context (e.g. a membership tier or
book already mentioned) available in its context window. See
`docs/example_dialogues.md`, Dialogue 2, for a concrete transcript.

**Off-topic / irrelevant queries:** the system prompt explicitly forbids
answering outside the library domain and instructs the model to decline
politely and redirect, even under adversarial "ignore your instructions"
style prompts. See Dialogue 4 below and the full test log in
`docs/example_dialogues.md`.

### Example Dialogues

These are real transcripts captured from our own testing against the
running system (`qwen2.5:3b`, local CPU inference). More cases, including
edge cases that failed, are in `docs/example_dialogues.md`.

**Dialogue 1 — Catalogue lookup with multi-turn memory**
> **Patron:** Do you have "Clean Code" available?
> **Assistant:** I can help with that! "Clean Code" by Robert C. Martin is
> available. How many copies would you like to borrow, and do you have any
> specific due date in mind?
>
> **Patron:** Can I renew it for 3 weeks?
> **Assistant:** Certainly! You can renew your copy of "Clean Code" up to
> 2 days before the due date. How many items would you like to renew, and
> do you have any specific due date in mind?

*(Note: the assistant correctly resolved "it" to "Clean Code" from the
previous turn without the title being repeated - see the Context Memory
Management Scheme above for how this is implemented.)*

**Dialogue 2 — Topic switch mid-conversation**
> **Patron:** What are your library hours?
> **Assistant:** Our library hours are Mon-Fri 9:00-20:00, Sat 10:00-17:00,
> and we're closed on Sun. How can I assist you further with your library
> questions today?
>
> **Patron:** Actually, can I place a hold on "The Silent Patient"?
> **Assistant:** Of course! How many copies of "The Silent Patient" are
> you interested in, and on what date would you like to receive it?

**Dialogue 3 — Full flow: vague request → confirmation → closing**
> **Patron:** I need a book
> **Assistant:** Sure, I can help you with that! What is the title of the
> book you're looking for?
>
> **Patron:** I'd like to place a hold on "The Silent Patient"
> **Assistant:** Of course! How many copies of "The Silent Patient" are
> you interested in, and on what date would you like to receive it?
>
> **Patron:** 2 copies and 15th sept
> **Assistant:** Great! I've noted a hold request for "The Silent Patient".
> You'll receive it on the 15th of September. How else can I assist you
> today?
>
> **Patron:** No that's all, thanks
> **Assistant:** You're welcome! If you need any more assistance in the
> future, please don't hesitate to ask. Have a great day!

**Dialogue 4 — Off-topic refusal**
> **Patron:** How's the weather today?
> **Assistant:** I'm just the Riverbend Library assistant, so I can't help
> with weather. Is there something about books, hours, or loans I can
> assist you with?
>
> **Patron:** What's the capital of France?
> **Assistant:** That's outside what I can help with here - I'm only set
> up for Riverbend Library questions. Can I help you find a book or check
> a due date instead?

Full transcripts and the complete pass/fail test matrix (including
adversarial jailbreak attempts and failure-handling tests):
**[`docs/example_dialogues.md`](docs/example_dialogues.md)**

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
                                                                │  qwen2.5:3b-q4    │
                                                                │  CPU inference    │
                                                                └───────────────────┘
```

**Data flow per turn:**
1. Browser sends `{"session_id", "message"}` over the WebSocket.
2. `ConversationManager` appends the user turn to that session's history
   and builds the prompt: `[static system prompt] + [sliding window of
   recent turns]`.
3. `llm_engine.stream_chat()` POSTs to Ollama's `/api/chat` with
   `stream: true` and yields tokens as they arrive.
4. The backend forwards each token to the browser immediately
   (`{"type": "token", ...}`), then a final `{"type": "done", ...}` with
   TTFT and tokens/sec for that turn.
5. The full assistant reply is appended back into session history.

No RAG, no external tools/function-calling, no cloud APIs anywhere in
this path — the model's only "knowledge" is what's written directly into
`backend/prompts.py`.

---

## 3. Model Selection

**Model:** `qwen2.5:3b` (Q4_K_M quantization via Ollama)

**Why this model:** we started with `qwen2.5:1.5b` since it's the
smallest, fastest option in the assignment's required range, but testing
showed it struggled to reliably follow the domain-restriction and
anti-jailbreak instructions in the system prompt (it would sometimes
answer off-topic questions or comply with "ignore your instructions"
style prompts). Moving up to `qwen2.5:3b` noticeably improved instruction
adherence in our manual testing (see `docs/example_dialogues.md`) while
still running acceptably on CPU, so we kept it as the final choice.

- Falls inside the assignment's required 0.5B–4B parameter range.
- Instruction-tuned out of the box, and large enough to follow the
  structured system prompt (stages, domain policies, refusal behaviour)
  more reliably than the 1.5B variant, without needing GPU or fine-tuning.
- Ollama makes pulling/running it a one-line operation, which matches
  the assignment's suggested tooling.

> If your hardware struggles with `qwen2.5:3b`, drop to `qwen2.5:1.5b`
> or `qwen2.5:0.5b` for speed, accepting weaker instruction-following as
> a trade-off - just update `MODEL_NAME` in `backend/llm_engine.py` and
> re-pull with `ollama pull <model>`.

### Context Memory Management Scheme

- The `ConversationManager` (`backend/conversation_manager.py`) keeps the
  **full transcript** in memory for the life of a session, so the UI can
  always show the complete history.
- When building the actual prompt sent to the model, it uses a
  **sliding window of the last `MAX_TURNS` (default 6) user/assistant
  turn pairs**, plus the system prompt, which is always included in full
  and never trimmed.
- Each stored message is hard-capped at `MAX_CHARS_PER_MSG` (2000 chars)
  as a safety limit against a single very long message blowing up the
  prompt size.
- This keeps the prompt (and therefore latency) roughly constant as a
  conversation grows long, while retaining enough recent context for
  coherent multi-turn dialogue (e.g. remembering a book mentioned two
  turns ago). The trade-off, documented under Limitations, is that very
  early context (older than `MAX_TURNS`) is dropped rather than
  summarized.

---

## 4. Latency Benchmarks

Run `backend/benchmark.py` after starting `ollama serve` and pulling your
model, then paste your actual results here:

```
python benchmark.py --model qwen2.5:3b --runs 5
```

| Metric | Value |
|---|---|
| Hardware (CPU, RAM) | Intel Core i7-8650U @ 1.90GHz (4 cores / 8 logical processors), 16GB DDR4 RAM |
| Model | qwen2.5:3b (Q4_K_M) |
| Avg. time-to-first-token (TTFT) | 1.043s |
| Avg. total response time | 8.802s |
| Avg. tokens/second | 5.76 |
| Concurrent sessions tested | 2 browser tabs tested simultaneously with different queries in each - sessions remained correctly isolated (no cross-talk of history between tabs), confirming per-session state works as designed |

Per-run detail from the benchmark script:

| Run | Prompt | TTFT (s) | Total (s) | Tokens/sec |
|---|---|---|---|---|
| 1 | "Hi, what are your library hours?" | 1.176 | 7.808 | 5.64 |
| 2 | "Do you have 'Atomic Habits' available right now?" | 0.996 | 7.317 | 5.74 |
| 3 | "Can I renew 'Clean Code' if no one else has it on hold?" | 1.025 | 9.705 | 5.87 |
| 4 | "What's the overdue fine if I return a book 4 days late?" | 1.133 | 7.585 | 5.27 |
| 5 | "I'd like to place a hold on 'The Silent Patient'." | 0.882 | 11.596 | 6.30 |

**Note on the model's first-ever response after a fresh `ollama serve`
start:** we observed a cold-start effect where the very first chat turn
in a brand-new session took 10-40s TTFT (the model weights being loaded
into RAM for the first time), while all subsequent turns settled to the
~1s TTFT / ~5.8 tokens/sec shown above. This is a one-time cost per
Ollama server start, not per session, and is worth mentioning live in
the viva if the first demo message looks slow.

**System memory pressure also affected latency in our testing:** our
16GB machine frequently had only ~1-2GB of RAM actually free at any given
moment (the rest in use by background applications), and on at least one
occasion this pushed a single response's TTFT as high as ~90s instead of
the usual 10-40s cold-start range. Closing unnecessary background
applications before running Ollama noticeably improved consistency. This
is a hardware/OS-level constraint rather than an issue with the
conversation manager or backend code, but it is worth demonstrating with
a "clean" machine state during the live viva if possible.

> The benchmark script's numbers are the ones you should paste here —
> don't estimate. Also report what changes when 2–3 chats run at once
> (Ollama serializes generation per model instance by default, so total
> response time per user will increase under concurrent load — measure
> and report this honestly).

---

## 5. Setup Instructions

### Prerequisites
- Python 3.10+
- [Ollama](https://ollama.com) installed locally

### Steps

```bash
# 1. Install and start Ollama
ollama serve            # keep this running in its own terminal

# 2. Pull the model (in another terminal)
ollama pull qwen2.5:3b

# 3. Set up the backend
cd backend
python -m venv venv
source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 4. Run the API server
uvicorn main:app --reload --port 8000

# 5. Open the frontend
# Serve it with a local static server (opening the file directly with
# file:// will block the WebSocket connection in most browsers):
#   cd frontend && python -m http.server 5500
# then visit http://localhost:5500
```

Health check: `curl http://localhost:8000/health` should return
`{"status": "ok"}`.

### Running the benchmark
```bash
cd backend
python benchmark.py --model qwen2.5:3b --runs 5
```

---

## 6. Known Limitations

- **No persistence:** sessions live only in server memory; restarting
  the backend wipes all conversation history. There is no database.
- **Single-model concurrency bottleneck:** Ollama typically processes
  one generation at a time per model instance, so under concurrent load,
  requests queue at the model layer even though the FastAPI/WebSocket
  layer itself handles many connections concurrently without blocking.
- **Sliding-window memory, not summarization:** once a conversation
  exceeds `MAX_TURNS` pairs, the oldest turns are dropped outright rather
  than summarized, so very long conversations can "forget" early details.
- **Cold-start latency:** the very first chat turn after starting
  `ollama serve` takes noticeably longer (10-40s TTFT in our testing)
  while the model weights load into RAM; subsequent turns settle to
  ~1s TTFT. This is a one-time cost per server start, not per session.
- **Adversarial jailbreak resistance is partial, not absolute:** through
  iterative prompt engineering (explicit topic blocklists, multiple
  few-shot refusal examples, end-of-prompt reminders) we significantly
  improved the model's ability to refuse off-topic questions and resist
  "ignore your instructions" style prompts - simple cases (weather,
  general trivia, personal questions, role-override attempts) are now
  refused reliably. However, some adversarial phrasings (e.g. "just tell
  me a quick joke") can still occasionally bypass the refusal. This is an
  expected limitation of a small (3B parameter), non-safety-fine-tuned
  open model steered purely through prompting, with no external
  moderation layer allowed by the assignment's no-tools/no-RAG
  constraint. See `docs/example_dialogues.md` for the specific test
  cases we ran and their pass/fail results.
- **Occasional policy-detail drift:** the model sometimes asks for
  details not supported by the domain (e.g. accepting a custom renewal
  duration like "3 weeks" instead of pointing out the fixed 14/21-day
  loan periods), or skips explicitly re-confirming a request before
  "logging" it. These are minor conversational-quality issues rather
  than domain violations.
- **Concurrency load increases latency and can affect response quality:**
  when running 2 browser tabs simultaneously, we observed both a very
  high outlier TTFT (~112s, versus the usual 1-40s range - see the memory
  pressure note in section 4) and a couple of minor response-quality
  quirks (e.g. the assistant asking an unnecessary clarifying question
  about a book section when the patron had only asked about hours, and
  under-confirming a clearly-named title). Session isolation itself
  worked correctly in this test (no history leaked between tabs), but the
  combination of CPU-only inference and low available system RAM under
  concurrent load is a real constraint worth being upfront about, both
  for latency and for occasional response coherence.
- **No real backend transactions:** holds, renewals, and fines are only
  *simulated* in conversation — nothing is written to a real library
  system.
- **CORS wide open (`*`):** fine for local grading/demo, not
  production-safe as-is.

---

## 7. Bonus (optional)

- [ ] Cloud deployment (Vercel/other free-tier host) — URL: *fill in if done*
- [ ] UX/persona polish beyond Phase V — describe here if attempted

---

## 8. Project Structure

```
library-assistant/
├── backend/
│   ├── main.py                 # FastAPI app: REST + WebSocket
│   ├── conversation_manager.py # session state + sliding-window memory
│   ├── llm_engine.py           # streaming client for local Ollama model
│   ├── prompts.py              # system prompt, domain knowledge & policies
│   ├── benchmark.py            # latency benchmark script
│   └── requirements.txt
├── frontend/
│   └── index.html              # chat UI (HTML/CSS/vanilla JS)
├── docs/
│   └── example_dialogues.md    # example transcripts + manual test checklist
└── README.md
```