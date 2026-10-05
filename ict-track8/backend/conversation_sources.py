"""Bounded source identities, separate from the short planning-history window."""
import json
from .pending_task_store import PendingTaskStore


class ConversationSourceStore(PendingTaskStore):
    def __init__(self, owner, *, ttl_seconds=86400, max_records=64):
        super().__init__(owner, ttl_seconds=ttl_seconds, max_records=max_records)
        with self.connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS conversation_sources (
                session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                question TEXT NOT NULL, effective_question TEXT NOT NULL,
                state TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                PRIMARY KEY(session_id,turn_id))''')

    def purge(self, connection, now):
        connection.execute('DELETE FROM conversation_sources WHERE expires_at <= ?', (now,))

    def remember(self, key, turn, parent_id=None, connection=None):
        if connection is None:
            with self.connection() as connection:
                return self.remember(key, turn, connection=connection)
        self.purge(connection, self.owner._clock())
        state = turn.state or {}
        if not ((state.get('route') == 'document' and state.get('document_context'))
                or (state.get('route') == 'sql' and state.get('executed_sql_context'))):
            return
        connection.execute('INSERT INTO conversation_sources VALUES(?,?,?,?,?,?,?)',
            (key, turn.turn_id, turn.question, turn.effective_question,
             json.dumps(state, ensure_ascii=False), turn.created_at,
             turn.created_at + self.ttl_seconds))
        connection.execute('''DELETE FROM conversation_sources WHERE session_id=? AND turn_id NOT IN
            (SELECT turn_id FROM conversation_sources WHERE session_id=? ORDER BY rowid DESC LIMIT ?)''',
            (key, key, self.max_records))
        for row in connection.execute('''SELECT session_id FROM conversation_sources GROUP BY session_id
                ORDER BY MAX(rowid) DESC LIMIT -1 OFFSET ?''', (self.owner.max_sessions,)).fetchall():
            self.clear(row[0], connection)

    def list_sources(self, session_id):
        from .session import ConversationTurn
        key = self.owner.validate_id(session_id)
        with self.connection() as connection:
            self.purge(connection, self.owner._clock())
            rows = connection.execute('''SELECT question,effective_question,created_at,state,turn_id
                FROM conversation_sources WHERE session_id=? ORDER BY rowid LIMIT ?''',
                (key, self.max_records)).fetchall()
        return tuple(ConversationTurn(row[0],row[1],row[2],json.loads(row[3]),row[4]) for row in rows)

    def resolve(self, session_id, turn_id):
        return next((turn for turn in self.list_sources(session_id) if turn.turn_id == turn_id), None)

    def clear(self, session_id, connection=None):
        if connection is None:
            with self.connection() as connection:
                return self.clear(session_id, connection)
        connection.execute('DELETE FROM conversation_sources WHERE session_id=?', (session_id,))
