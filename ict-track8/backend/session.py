"""赛题八 Demo 的进程内会话上下文；无 session_id 时完全无状态。"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from contextlib import contextmanager
import json
from pathlib import Path


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class ConversationTurn:
    question: str
    effective_question: str
    created_at: float
    state: dict | None = None


class ConversationStore:
    """有界、带 TTL 的进程内会话存储，适用于独立 Demo。"""

    def __init__(
        self,
        *,
        max_sessions: int = 512,
        max_turns: int = 8,
        ttl_seconds: float = 1800.0,
        clock=time.monotonic,
        storage_path: str | Path | None = None,
    ):
        if max_sessions < 1 or max_turns < 1 or ttl_seconds <= 0:
            raise ValueError("会话限制必须为正数")
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self.ttl_seconds = float(ttl_seconds)
        self.storage_path = Path(storage_path) if storage_path else None
        # Monotonic time is ideal for an in-memory TTL, but its origin changes
        # after a process restart. Persisted sessions therefore use wall-clock
        # time unless a test explicitly injects another clock.
        self._clock = time.time if self.storage_path and clock is time.monotonic else clock
        self._sessions: dict[str, tuple[float, deque[ConversationTurn]]] = {}
        self._lock = threading.Lock()
        if self.storage_path:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()

    @staticmethod
    def validate_id(session_id: str) -> str:
        value = str(session_id or "").strip()
        if not _SESSION_ID_RE.fullmatch(value):
            raise ValueError("session_id 只允许 1-128 位字母、数字、点、下划线、冒号或连字符")
        return value

    def context(self, session_id: str) -> tuple[ConversationTurn, ...]:
        key = self.validate_id(session_id)
        now = self._clock()
        if self.storage_path:
            with self._connect() as connection:
                self._persistent_purge(connection, now)
                record = connection.execute(
                    "SELECT question, effective_question, created_at, state "
                    "FROM conversation_turns WHERE session_id=? ORDER BY sequence DESC LIMIT ?",
                    (key, self.max_turns),
                ).fetchall()
                connection.execute(
                    "UPDATE conversation_sessions SET touched_at=? WHERE session_id=?",
                    (now, key),
                )
                return tuple(
                    ConversationTurn(str(row[0]), str(row[1]), float(row[2]), json.loads(row[3]))
                    for row in reversed(record)
                )
        with self._lock:
            self._purge(now)
            record = self._sessions.get(key)
            if not record:
                return ()
            touched, turns = record
            self._sessions[key] = (now, turns)
            return tuple(turns)

    def clear(self, session_id):
        key = self.validate_id(session_id)
        if self.storage_path:
            with self._connect() as connection:
                connection.execute('DELETE FROM conversation_sessions WHERE session_id=?', (key,))
        else:
            with self._lock:
                self._sessions.pop(key, None)

    def remember(self, session_id: str, *, question: str, effective_question: str, state: dict | None = None) -> None:
        key = self.validate_id(session_id)
        if not str(question).strip() or not str(effective_question).strip():
            return
        now = self._clock()
        encoded_state = json.dumps(state or {}, ensure_ascii=False)
        if len(encoded_state) > 32000:
            raise ValueError('会话状态超过大小限制')
        if self.storage_path:
            with self._connect() as connection:
                self._persistent_purge(connection, now)
                session_exists = connection.execute(
                    "SELECT 1 FROM conversation_sessions WHERE session_id=?", (key,)
                ).fetchone()
                if not session_exists:
                    count = connection.execute("SELECT COUNT(*) FROM conversation_sessions").fetchone()[0]
                    if int(count) >= self.max_sessions:
                        oldest = connection.execute(
                            "SELECT session_id FROM conversation_sessions ORDER BY touched_at ASC LIMIT 1"
                        ).fetchone()
                        if oldest:
                            connection.execute("DELETE FROM conversation_sessions WHERE session_id=?", (oldest[0],))
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence), -1) + 1 FROM conversation_turns WHERE session_id=?",
                    (key,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO conversation_sessions(session_id, touched_at) VALUES(?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET touched_at=excluded.touched_at",
                    (key, now),
                )
                connection.execute(
                    "INSERT INTO conversation_turns(session_id, sequence, question, effective_question, created_at, state) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (key, int(sequence), str(question).strip(), str(effective_question).strip(), now, encoded_state),
                )
                connection.execute(
                    "DELETE FROM conversation_turns WHERE session_id=? AND sequence < "
                    "(SELECT COALESCE(MAX(sequence), 0) - ? + 1 FROM conversation_turns WHERE session_id=?)",
                    (key, self.max_turns, key),
                )
            return
        with self._lock:
            self._purge(now)
            if key not in self._sessions and len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions, key=lambda item: self._sessions[item][0])
                self._sessions.pop(oldest, None)
            turns = self._sessions.get(key, (now, deque(maxlen=self.max_turns)))[1]
            turns.append(ConversationTurn(str(question).strip(), str(effective_question).strip(), now, state or {}))
            self._sessions[key] = (now, turns)

    def _purge(self, now: float) -> None:
        expired = [key for key, (touched, _) in self._sessions.items() if now - touched >= self.ttl_seconds]
        for key in expired:
            self._sessions.pop(key, None)

    @contextmanager
    def _connect(self):
        if self.storage_path is None:
            raise RuntimeError("当前会话存储未启用 SQLite")
        connection = sqlite3.connect(self.storage_path, timeout=2.0)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    touched_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    question TEXT NOT NULL,
                    effective_question TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (session_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_sessions_touched
                    ON conversation_sessions(touched_at);
                """
            )
            columns = {row[1] for row in connection.execute('PRAGMA table_info(conversation_turns)')}
            if 'state' not in columns:
                connection.execute("ALTER TABLE conversation_turns ADD COLUMN state TEXT NOT NULL DEFAULT '{}'")

    def _persistent_purge(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            "DELETE FROM conversation_sessions WHERE touched_at <= ?",
            (now - self.ttl_seconds,),
        )
