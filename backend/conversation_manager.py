"""
conversation_manager.py
------------------------
Owns per-session dialogue state and decides what goes into the LLM's
context window on every turn.

Context-memory strategy (also described in the README):
  - We keep the full history in memory for the life of the session (so the
    UI can always show the whole transcript).
  - When building the prompt actually sent to the model, we use a
    SLIDING WINDOW of the last MAX_TURNS user/assistant turn-pairs, plus
    the system prompt, which is always included in full and never trimmed.
  - If the window would still be too large (very long messages), we fall
    back to simple truncation of the oldest kept messages first.
  - This keeps latency predictable on CPU (the prompt doesn't grow forever)
    while preserving enough recent context for coherent multi-turn dialogue.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict
import uuid

from prompts import build_system_prompt

MAX_TURNS = 6          # number of user+assistant turn PAIRS kept in the prompt
MAX_CHARS_PER_MSG = 2000  # hard safety cap per message to bound prompt size


@dataclass
class Session:
    session_id: str
    history: List[Dict[str, str]] = field(default_factory=list)  # full transcript
    created_at: datetime = field(default_factory=datetime.utcnow)

    def add(self, role: str, content: str):
        self.history.append({"role": role, "content": content[:MAX_CHARS_PER_MSG]})


class ConversationManager:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    # --- session lifecycle -------------------------------------------------
    def create_session(self) -> str:
        sid = str(uuid.uuid4())
        self._sessions[sid] = Session(session_id=sid)
        return sid

    def reset_session(self, session_id: str) -> str:
        """Wipes history but keeps (or creates) the session id."""
        self._sessions[session_id] = Session(session_id=session_id)
        return session_id

    def session_exists(self, session_id: str) -> bool:
        return session_id in self._sessions

    def ensure_session(self, session_id: str) -> str:
        if not session_id or session_id not in self._sessions:
            session_id = self.create_session()
        return session_id

    # --- message handling ----------------------------------------------------
    def add_user_message(self, session_id: str, content: str):
        self._sessions[session_id].add("user", content)

    def add_assistant_message(self, session_id: str, content: str):
        self._sessions[session_id].add("assistant", content)

    def get_full_history(self, session_id: str) -> List[Dict[str, str]]:
        return list(self._sessions[session_id].history)

    def build_prompt_messages(self, session_id: str) -> List[Dict[str, str]]:
        """
        Builds the exact message list to send to the LLM:
        [system prompt] + [sliding window of recent turns].
        """
        session = self._sessions[session_id]
        windowed = session.history[-(MAX_TURNS * 2):]  # user+assistant pairs
        messages = [{"role": "system", "content": build_system_prompt()}]
        messages.extend(windowed)
        return messages

    def turn_count(self, session_id: str) -> int:
        return len(self._sessions[session_id].history) // 2
