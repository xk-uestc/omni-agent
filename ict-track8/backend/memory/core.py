"""Small persistent memory boundary, implemented independently of QiMem.

Only trusted server provisioning may call Store.put. There is no client-facing
write/confirmation endpoint. Query observations are a different SQLite table
and never become confirmed business knowledge automatically.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def timestamp(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('memory timestamps require timezone')
    return dt.timestamp()


@dataclass(frozen=True)
class TrustedScope:
    deployment: str
    project: str
    data_sources: tuple[str, ...]

    def __post_init__(self):
        names = (self.deployment, self.project, *self.data_sources)
        if not self.data_sources or any(not isinstance(n, str) or not re.fullmatch(r'[\w.:-]{1,128}', n) for n in names):
            raise ValueError('trusted server scope requires deployment, project and data sources')
        object.__setattr__(self, 'data_sources', tuple(sorted(set(self.data_sources))))

    @property
    def key(self):
        return encoded(asdict(self))


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    memory_type: str
    term: str
    content: str
    binding: dict
    scope: TrustedScope
    provenance: dict
    source_version: dict
    valid_from: str
    valid_to: str | None
    verification_state: str
    created_at: str
    updated_at: str
    task_trace_id: str | None = None


@dataclass(frozen=True)
class RecallContext:
    question: str
    now: str
    schema_version: str
    sources: dict
    metrics: frozenset
    filter_values: frozenset
    protected_spans: tuple = ()


@dataclass(frozen=True)
class Budget:
    max_items: int = 3
    max_characters: int = 1800

    def __post_init__(self):
        if not 1 <= self.max_items <= 16 or not 1 <= self.max_characters <= 12000:
            raise ValueError('invalid memory budget')


@dataclass
class RecallResult:
    selected: tuple[MemoryRecord, ...] = ()
    decisions: list[dict] = field(default_factory=list)
    degraded: bool = False


class MemoryStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS memory_metadata(version INTEGER NOT NULL);
                INSERT INTO memory_metadata SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM memory_metadata);
                CREATE TABLE IF NOT EXISTS memories(
                    scope TEXT NOT NULL, memory_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(scope,memory_id));
                CREATE TABLE IF NOT EXISTS memory_events(
                    scope TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL, PRIMARY KEY(scope,event_id));
            ''')
            if db.execute('SELECT version FROM memory_metadata').fetchall() != [(1,)]:
                raise ValueError('unsupported memory store version')

    def connect(self):
        return sqlite3.connect(self.path, timeout=0.25)

    def put(self, record: MemoryRecord):
        """Trusted offline provisioning only; never called by observe/query.

        Confirmation receipts describe the external approval and independent
        verification, not an authorization token or client-supplied identity.
        """
        if not isinstance(record, MemoryRecord) or not re.fullmatch(r'[\w.:-]{1,128}', record.memory_id):
            raise ValueError('invalid memory record')
        if not re.fullmatch(r'[\w\u3400-\u9fff]{2,64}', record.term):
            raise ValueError('memory term must be a bounded literal')
        if len(encoded(asdict(record))) > 24000:
            raise ValueError('memory record too large')
        timestamp(record.valid_from)
        if record.valid_to and timestamp(record.valid_to) <= timestamp(record.valid_from):
            raise ValueError('invalid validity interval')
        with self.connect() as db:
            db.execute('INSERT INTO memories VALUES(?,?,?) ON CONFLICT(scope,memory_id) DO UPDATE SET payload=excluded.payload',
                       (record.scope.key, record.memory_id, encoded(asdict(record))))

    def scan(self, scope):
        with self.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM memories WHERE scope=?', (scope.key,)).fetchone()[0]
            if count > 512:
                raise ValueError('memory scan budget exceeded; do not select from an incomplete conflict set')
            rows = db.execute('SELECT payload FROM memories WHERE scope=? ORDER BY memory_id', (scope.key,)).fetchall()
            foreign = db.execute('SELECT COUNT(*) FROM memories WHERE scope!=?', (scope.key,)).fetchone()[0]
        result = []
        for row in rows:
            value = json.loads(row[0]); value['scope'] = TrustedScope(**value['scope'])
            result.append(MemoryRecord(**value))
        return result, foreign

    def append_event(self, scope, event):
        payload = encoded(event); digest = hashlib.sha256(payload.encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT payload_sha256 FROM memory_events WHERE scope=? AND event_id=?',
                             (scope.key, event['event_id'])).fetchone()
            if old:
                if old[0] != digest: raise ValueError('event id reused with different payload')
                return False
            return bool(db.execute('INSERT OR IGNORE INTO memory_events VALUES(?,?,?,?)',
                (scope.key, event['event_id'], payload, digest)).rowcount)

    def events(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT payload FROM memory_events ORDER BY rowid')]


def occurrences(question, term, protected_spans=()):
    pattern = re.escape(term)
    if term.isascii(): pattern = r'(?<!\w)' + pattern + r'(?!\w)'
    return [(m.start(), m.end()) for m in re.finditer(pattern, question)
            if not any(m.start() < b and m.end() > a for a, b in protected_spans)]


class MemoryCore:
    def __init__(self, store, *, scope=None, enabled=False):
        if enabled and not isinstance(scope, TrustedScope):
            raise ValueError('enabled memory requires trusted server scope')
        self.store, self.scope, self.enabled = store, scope, bool(enabled)

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        flag = env.get('ICT8_MEMORY_ENABLED', '0')
        if flag not in {'0','1'}: raise ValueError('ICT8_MEMORY_ENABLED must be 0 or 1')
        if flag == '0': return cls(None)
        scope = TrustedScope(env.get('ICT8_MEMORY_DEPLOYMENT',''), env.get('ICT8_MEMORY_PROJECT',''),
                             tuple(x.strip() for x in env.get('ICT8_MEMORY_SOURCES','').split(',') if x.strip()))
        path = env.get('ICT8_MEMORY_DB_PATH','')
        if not path: raise ValueError('enabled memory requires server ICT8_MEMORY_DB_PATH')
        if env.get('ICT8_MEMORY_DATABASE_SOURCE','') not in scope.data_sources:
            raise ValueError('memory database source must be authorized by server scope')
        try: store = MemoryStore(path)
        except (OSError, sqlite3.Error, ValueError): store = None
        return cls(store, scope=scope, enabled=True)

    @staticmethod
    def invalid_reason(record, context, scope):
        if record.scope != scope: return 'foreign_scope'
        if record.verification_state == 'revoked': return 'revoked'
        if record.verification_state != 'confirmed': return 'unverified'
        if record.memory_type != 'business_semantics': return 'unsupported_type'
        if not all(record.provenance.get(k) for k in ('authority','confirmation_id','evidence_id','verification_id')):
            return 'missing_confirmation'
        at = timestamp(context.now)
        if at < timestamp(record.valid_from): return 'not_yet_valid'
        if record.valid_to and at >= timestamp(record.valid_to): return 'expired'
        if record.source_version.get('schema') != context.schema_version: return 'schema_changed'
        sources = record.source_version.get('sources', {})
        if not sources: return 'source_unverified'
        if not set(sources) <= set(scope.data_sources): return 'source_unauthorized'
        if any(context.sources.get(k) != v or not v for k, v in sources.items()): return 'source_changed'
        b = record.binding
        if set(b) - {'table','column','function','filters','unit'}: return 'invalid_binding'
        if (b.get('table'),b.get('column'),b.get('function')) not in context.metrics: return 'invalid_metric'
        if not isinstance(b.get('filters', []), list) or len(b.get('filters', [])) > 4: return 'invalid_filter'
        for f in b.get('filters', []):
            if set(f) != {'column','value'} or (b['table'],f['column'],encoded(f['value'])) not in context.filter_values:
                return 'invalid_filter'
        return None

    def recall(self, context, scope, budget=Budget()):
        if not self.enabled: return RecallResult()
        if scope != self.scope: return RecallResult(decisions=[{'reason':'untrusted_scope'}])
        if self.store is None: return RecallResult(decisions=[{'reason':'store_unavailable'}], degraded=True)
        try:
            records, foreign = self.store.scan(scope)
            decisions = [{'reason':'foreign_scope','count':foreign}] if foreign else []
            valid = []
            for record in records:
                if not occurrences(context.question, record.term, context.protected_spans): continue
                reason = self.invalid_reason(record, context, scope)
                if reason: decisions.append({'memory_id':record.memory_id,'term':record.term,'reason':reason})
                else: valid.append(record)
            groups = {}
            for record in valid: groups.setdefault(record.term, []).append(record)
            eligible = []
            # Conflicts are resolved before ranking/budget truncation.
            for term, group in groups.items():
                if len({encoded(r.binding) for r in group}) > 1:
                    decisions.extend({'memory_id':r.memory_id,'term':term,'reason':'conflict'} for r in group)
                else: eligible.append(sorted(group,key=lambda r:r.memory_id)[0])
            selected, size = [], 0
            for record in sorted(eligible, key=lambda r:(-len(r.term),r.memory_id)):
                cost = len(record.term) + len(encoded(record.binding))
                if len(selected) >= budget.max_items or size+cost > budget.max_characters:
                    decisions.append({'memory_id':record.memory_id,'term':record.term,'reason':'budget_exceeded'})
                    continue
                selected.append(record); size += cost
                decisions.append({'memory_id':record.memory_id,'term':record.term,'reason':'selected'})
            return RecallResult(tuple(selected), decisions)
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return RecallResult(decisions=[{'reason':'store_or_context_unavailable'}], degraded=True)

    def observe(self, event, verified_outcome):
        if not self.enabled: return {'stored':False,'reason':'disabled'}
        if self.store is None: return {'stored':False,'reason':'event_store_unavailable','promotion':'none'}
        # Deliberate allowlist: no raw prompts, model text, credentials or rows.
        clean = {k:event[k] for k in ('event_id','question_sha256','category','selected','consumed','rejected','source_versions') if k in event}
        clean['verification'] = {k:bool(verified_outcome.get(k)) for k in ('execution_verified','independent_task_verified')}
        clean['promotion'] = 'none'
        try:
            return {'stored':self.store.append_event(self.scope, clean),'promotion':'none'}
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return {'stored':False,'reason':'event_store_unavailable','promotion':'none'}
