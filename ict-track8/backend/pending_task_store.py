"""Bounded SQL clarification snapshots, independent of short chat history."""
from contextlib import contextmanager
import json
import sqlite3
import threading


class PendingTaskStore:
    def __init__(self, owner, *, ttl_seconds=86400, max_records=64):
        if ttl_seconds <= 0 or max_records < 1:
            raise ValueError('待补记录限制必须为正数')
        self.owner = owner
        self.ttl_seconds = float(ttl_seconds)
        self.max_records = max_records
        self._lock = threading.RLock()
        self._memory = None if owner.storage_path else sqlite3.connect(':memory:', check_same_thread=False)
        with self.connection() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS pending_sql_snapshots (
                    session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                    root_id TEXT NOT NULL, question TEXT NOT NULL,
                    effective_question TEXT NOT NULL, state TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL,
                    closed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(session_id, turn_id)
                );
                CREATE INDEX IF NOT EXISTS pending_sql_expiry ON pending_sql_snapshots(expires_at);
                CREATE INDEX IF NOT EXISTS pending_sql_root ON pending_sql_snapshots(session_id, root_id);
            ''')

    @contextmanager
    def connection(self):
        if self._memory is None:
            with self.owner._connect() as connection:
                yield connection
        else:
            with self._lock, self._memory:
                yield self._memory

    def purge(self, connection, now):
        connection.execute('DELETE FROM pending_sql_snapshots WHERE expires_at <= ?', (now,))

    def remember(self, key, turn, parent_id=None, connection=None):
        if connection is None:
            with self.connection() as connection:
                return self.remember(key, turn, parent_id, connection)
        now = turn.created_at
        self.purge(connection, now)
        parent = connection.execute(
            'SELECT root_id, expires_at, closed FROM pending_sql_snapshots WHERE session_id=? AND turn_id=?',
            (key, parent_id)).fetchone() if parent_id else None
        state = turn.state or {}
        if parent and not parent[2] and (state.get('executed_sql_context') or state.get('comparison_completed')):
            connection.execute('UPDATE pending_sql_snapshots SET closed=1 WHERE session_id=? AND root_id=?',
                               (key, parent[0]))
        pending = (state.get('route') == 'sql' and state.get('pending_question') == turn.effective_question
                   and state.get('clarification_code') and not state.get('executed_sql_context')
                   and not state.get('comparison_snapshot'))
        pending = pending or (state.get('route') == 'comparison' and state.get('pending_question')
            and state.get('comparison_context') and state.get('pending_comparison_result'))
        if not pending or parent and parent[2]:
            return
        root_id = parent[0] if parent else turn.turn_id
        expires_at = parent[1] if parent else now + self.ttl_seconds
        connection.execute('INSERT INTO pending_sql_snapshots VALUES(?,?,?,?,?,?,?,?,0)',
            (key, turn.turn_id, root_id, turn.question, turn.effective_question,
             json.dumps(state, ensure_ascii=False), now, expires_at))
        connection.execute('''DELETE FROM pending_sql_snapshots WHERE session_id=? AND turn_id NOT IN
            (SELECT turn_id FROM pending_sql_snapshots WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?)''',
            (key, key, self.max_records))
        sessions = connection.execute('''SELECT session_id FROM pending_sql_snapshots GROUP BY session_id
            ORDER BY MAX(created_at) DESC, MAX(rowid) DESC LIMIT -1 OFFSET ?''', (self.owner.max_sessions,)).fetchall()
        for row in sessions:
            connection.execute('DELETE FROM pending_sql_snapshots WHERE session_id=?', row)

    def resolve(self, session_id, turn_id):
        from .session import ConversationTurn
        key = self.owner.validate_id(session_id)
        with self.connection() as connection:
            self.purge(connection, self.owner._clock())
            row = connection.execute('''SELECT question,effective_question,created_at,state,closed
                FROM pending_sql_snapshots WHERE session_id=? AND turn_id=?''', (key, turn_id)).fetchone()
        if row is None:
            return None, 'unavailable'
        if row[4]:
            return None, 'completed'
        return ConversationTurn(row[0], row[1], row[2], json.loads(row[3]), turn_id), 'pending'

    def clear(self, session_id, connection=None):
        if connection is None:
            with self.connection() as connection:
                return self.clear(session_id, connection)
        connection.execute('DELETE FROM pending_sql_snapshots WHERE session_id=?', (session_id,))

    def list_open(self, session_id):
        """Latest snapshot per open root, without touching either expiry."""
        from .session import ConversationTurn
        key=self.owner.validate_id(session_id)
        with self.connection() as connection:
            self.purge(connection,self.owner._clock())
            rows=connection.execute('''SELECT turn_id,root_id,question,effective_question,state,created_at,expires_at
                FROM pending_sql_snapshots WHERE session_id=? AND closed=0
                ORDER BY created_at DESC,rowid DESC LIMIT ?''',(key,self.max_records)).fetchall()
        seen=set();result=[]
        for identifier,root,question,effective,state,created,expires in rows:
            if root in seen:continue
            seen.add(root)
            result.append({'turn':ConversationTurn(question,effective,created,json.loads(state),identifier),
                           'expires_at':expires})
        return result
