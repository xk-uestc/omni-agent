"""Source-bound choices for a local semantic clarification.

These records never become executed SQL history.  A reply can replace only a
currently offered ambiguity span; all remaining bytes of the original request
survive.  The caller must hold ``ConversationStore.turn`` and the engine's
``consistent_reads`` through resolution and eventual SQL execution.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, is_dataclass, replace
import hashlib
import json
import re
import sqlite3
import threading
import uuid


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _plain(value):
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


@dataclass(frozen=True)
class SemanticPendingResolution:
    status: str
    question: str
    reason: str
    message: str
    pending_id: str
    normalization: object | None = None
    original_question: str = ''
    selection: dict | None = None


class SemanticPendingStore:
    """One bounded, persistent side record per session, with no SQL authority."""
    def __init__(self, owner, *, ttl_seconds=None):
        self.owner = owner
        self.ttl_seconds = float(ttl_seconds if ttl_seconds is not None else owner.ttl_seconds)
        if self.ttl_seconds <= 0:
            raise ValueError('语义澄清有效期必须为正数')
        self._lock = threading.RLock()
        self._memory = None if owner.storage_path else sqlite3.connect(':memory:', check_same_thread=False)
        with self.connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS pending_semantic_choices (
                session_id TEXT PRIMARY KEY, pending_id TEXT NOT NULL,
                payload TEXT NOT NULL, checksum TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL
            )''')

    @contextmanager
    def connection(self):
        if self._memory is None:
            with self.owner._connect() as connection:
                yield connection
        else:
            with self._lock, self._memory:
                yield self._memory

    @staticmethod
    def _snapshot(engine):
        with engine._connect() as connection:
            tables, _, _ = engine._snapshot_for(connection)
            rules = engine.planner.linker.rules_for(tables)
            source_revision = engine.current_source_revision()
        catalogs = []
        for catalog in (getattr(engine, 'metric_catalog', None), getattr(engine.semantic_graph, 'metric_catalog', None)):
            catalogs.append(None if catalog is None else _plain({
                'digest': catalog.digest, 'payload': catalog.payload,
                'sources': catalog.sources, 'derived': catalog.derived,
                'aliases': catalog.aliases, 'query_aliases': catalog.query_aliases,
            }))
        return {'source_revision': source_revision,
                'schema_sha256': _digest(_plain(tables)),
                'aliases_sha256': _digest(_plain(rules)),
                'catalog_sha256': _digest(catalogs)}

    def _anchor(self, session_id):
        turns = self.owner.context(session_id)
        return _digest(_plain(turns[-1]) if turns else None)

    def save(self, session_id, raw_question, normalization, engine, *, history_hint=None):
        key = self.owner.validate_id(session_id)
        if not isinstance(raw_question, str) or not raw_question.strip() or len(raw_question) > 12000:
            raise ValueError('语义澄清问题无效')
        fresh = engine.normalize_question(raw_question, history_hint=history_hint)
        if (fresh is None or not fresh.ambiguities or normalization is None
                or _plain(fresh) != _plain(normalization)):
            raise ValueError('语义澄清必须来自当前真实 Schema 的完整重解析')
        now = self.owner._clock()
        payload = {'original_question': raw_question, 'question': raw_question,
                   'normalization': _plain(fresh), 'original_normalization': _plain(fresh), 'edits': [],
                   'history_hint': _plain(history_hint or {}),
                   'snapshot': self._snapshot(engine), 'anchor': self._anchor(key)}
        encoded = _json(payload)
        if len(encoded) > 64000:
            raise ValueError('语义澄清状态超过大小限制')
        identifier = 'semantic_' + uuid.uuid4().hex
        with self.connection() as connection:
            connection.execute('''INSERT INTO pending_semantic_choices VALUES(?,?,?,?,?,?)
                ON CONFLICT(session_id) DO UPDATE SET pending_id=excluded.pending_id,
                payload=excluded.payload,checksum=excluded.checksum,
                created_at=excluded.created_at,expires_at=excluded.expires_at''',
                (key, identifier, encoded, _digest(payload), now, now + self.ttl_seconds))
            # Retain expired records as refusal tombstones until a full new
            # request/reset clears them. A late short choice cannot query all rows.
            connection.execute('''DELETE FROM pending_semantic_choices WHERE session_id NOT IN
                (SELECT session_id FROM pending_semantic_choices ORDER BY created_at DESC,rowid DESC LIMIT ?)''',
                (self.owner.max_sessions,))
        return identifier

    def clear(self, session_id, connection=None):
        key = self.owner.validate_id(session_id)
        if connection is None:
            with self.connection() as current:
                self.clear(key, current)
            return
        connection.execute('DELETE FROM pending_semantic_choices WHERE session_id=?', (key,))

    def has_pending(self, session_id):
        """A side-store lookup only; never inspect the source DB or Schema."""
        if session_id is None:
            return False
        key = self.owner.validate_id(session_id)
        with self.connection() as connection:
            return connection.execute('SELECT 1 FROM pending_semantic_choices WHERE session_id=?', (key,)).fetchone() is not None

    @staticmethod
    def _reply(reply):
        text = str(reply).strip().rstrip('。？！!?').strip()
        text = re.sub(r'^(?:我(?:指|说)的是|我的意思是|意思是|选择|选用|选|就是|是|用)(?:\s*)', '', text)
        return text.strip().rstrip('。？！!?').strip()

    @staticmethod
    def _new_question(reply):
        # A complete independent request may replace pending work. Mixed
        # choice-and-edit text remains a refusal rather than partial execution.
        return bool(re.match(r'^(?:换个(?:问题|主题|话题)|换一个(?:问题|主题|话题)|新问题|重新查询|取消)', reply)
                    or re.match(r'^(?:请|帮我)?(?:查询|统计|查看|查|看)\s*\d{4}年.+', reply)
                    or re.search(r'^\d{4}年.+|\bSELECT\b|是什么意思|怎么计算|怎么算', reply, re.I))

    @staticmethod
    def _option(candidate):
        match = re.fullmatch(r'(.+)\.([^\.]+)（(.+)）', candidate)
        return {'field': match[1] + '.' + match[2], 'table': match[1],
                'column': match[2], 'label': match[3]} if match else None

    @staticmethod
    def _replay(payload):
        question = payload['original_question']
        for edit in payload['edits']:
            start, end = edit['start'], edit['end']
            if (not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(question)
                    or question[start:end] != edit['source'] or not isinstance(edit['replacement'], str)):
                raise ValueError('pending_semantic_invalid_edit')
            question = question[:start] + edit['replacement'] + question[end:]
        if question != payload['question']:
            raise ValueError('pending_semantic_scope_drift')
        return question

    @staticmethod
    def _remaining(original, question, edits):
        """Map the original ambiguity queue without guessing unchosen words."""
        consumed = {(edit['origin_start'], edit['origin_end']) for edit in edits}
        remaining = []
        for ambiguity in original.ambiguities:
            if (ambiguity.start, ambiguity.end) in consumed:
                continue
            offset = sum(len(edit['replacement']) - (edit['origin_end'] - edit['origin_start'])
                         for edit in edits if edit['origin_end'] <= ambiguity.start)
            start, end = ambiguity.start + offset, ambiguity.end + offset
            if question[start:end] != ambiguity.source_text:
                raise ValueError('pending_semantic_original_span_drift')
            remaining.append(replace(ambiguity, start=start, end=end))
        return tuple(remaining)

    @classmethod
    def _pending_normalization(cls, original, question, edits):
        from .nl2sql.semantic_graph import SemanticNormalization
        remaining = cls._remaining(original, question, edits)
        message = None
        if remaining:
            first = remaining[0]
            message = f'“{first.source_text}”的含义需要确认：{first.reason}。'
            if first.candidates:
                message += '可选字段：' + '、'.join(first.candidates) + '。'
        return SemanticNormalization(question, question, ambiguities=remaining, clarification=message)

    def _safe_return_to_executed_scope(self, session_id, reply, engine):
        """Preserve old region/time follow-ups after an unanswerable term.

        There must be no offered semantic target, and no metric language in
        the new reply. The established pure rule guard must independently
        prove the successful SQL record and every retained scope slot.
        """
        from .sql_history_scope import (_resolve_sql_followup_scope, VERIFIED_SQL_CONTEXT_MODES,
                                        _metric_semantics, _complete)
        history = self.owner.context(session_id)
        if not history or not (history[-1].state or {}).get('executed_sql_context'):
            return False
        base = history[-1].effective_question
        original = engine.extract_required_intent(base)
        slots = engine.analyze_slots(reply, preferred_tables=(original.table,) if original.table else ())
        if slots['metrics'] or not (slots['values'] or slots['time_spans']):
            return False
        scope, audit = _resolve_sql_followup_scope(reply, history, engine)
        if audit.get('mode') not in VERIFIED_SQL_CONTEXT_MODES or scope == reply:
            return False
        candidate = engine.extract_required_intent(scope)
        return bool(_complete(original) and _complete(candidate)
                    and _metric_semantics(original.to_dict()) == _metric_semantics(candidate.to_dict()))

    def resolve(self, session_id, reply, engine):
        key = self.owner.validate_id(session_id)
        with self.connection() as connection:
            row = connection.execute('''SELECT pending_id,payload,checksum,expires_at
                FROM pending_semantic_choices WHERE session_id=?''', (key,)).fetchone()
        if row is None:
            return None
        identifier, encoded, checksum, expires = row
        text = self._reply(reply)
        # Full independent requests never inherit a stale pending scope.
        if self._new_question(str(reply).strip()):
            self.clear(key)
            return SemanticPendingResolution('unrelated', str(reply), 'semantic_pending_new_question',
                                             '', identifier)
        original = ''
        def rejected(reason, message, normalization=None):
            return SemanticPendingResolution('clarification', original, reason, message,
                                             identifier, normalization, original)
        try:
            payload = json.loads(encoded)
            original = payload.get('original_question', '')
            if _digest(payload) != checksum:
                return rejected('semantic_pending_integrity_failed', '此前澄清记录的完整性校验失败，请重新输入完整问题。')
            self._replay(payload)
            if expires <= self.owner._clock():
                return rejected('semantic_pending_expired', '此前澄清已过期，请重新输入包含年份、地区和指标的完整问题。')
            if payload['snapshot'] != self._snapshot(engine):
                return rejected('semantic_pending_source_changed', '数据库或业务口径已变化，请重新输入完整问题，避免继承过期条件。')
            if payload['anchor'] != self._anchor(key):
                return rejected('semantic_pending_history_changed', '对话上下文已经变化，请重新输入完整问题。')
            fresh = engine.normalize_question(original, history_hint=payload['history_hint'])
            if fresh is None or not fresh.ambiguities or _plain(fresh) != payload['original_normalization']:
                return rejected('semantic_pending_binding_changed', '当前术语口径与此前选项不一致，请重新输入完整问题。')
            queued = self._pending_normalization(fresh, payload['question'], payload['edits'])
            if not queued.ambiguities:
                return rejected('semantic_pending_choices_completed', '此前术语已经确认，请重新输入完整问题。')
            ambiguity = queued.ambiguities[0]
            if not ambiguity.candidates and self._safe_return_to_executed_scope(key, str(reply), engine):
                self.clear(key)
                return SemanticPendingResolution('unrelated', str(reply),
                    'semantic_pending_verified_prior_scope_return',
                    '此前术语没有可用字段，已按已核验的成功查询继续修改时间或地区。', identifier)
            options = [self._option(value) for value in ambiguity.candidates]
            if any(option is None for option in options):
                return rejected('semantic_pending_option_invalid', '该术语没有可核验的唯一字段，请重新输入完整问题。', fresh)
            numbered = re.fullmatch(r'(?:第)?([1-8一二三四五六七八])(?:个|项)?', text)
            if numbered:
                digit = numbered[1]
                index = int(digit) if digit.isdigit() else '一二三四五六七八'.index(digit) + 1
                matches = [options[index - 1]] if 1 <= index <= len(options) else []
            else:
                matches = [option for option in options if text in
                           {option['field'], option['column'], option['label'],
                            option['field'] + '（' + option['label'] + '）'}]
            if len(matches) != 1:
                return rejected('semantic_pending_choice_not_unique',
                                queued.clarification or '请选择当前提供的一个具体字段；原问题的其他条件保持。', queued)
            selected = matches[0]
            with engine._connect() as connection:
                tables, _, _ = engine._snapshot_for(connection)
            fields = {(table.name, column.name) for table in tables for column in table.columns}
            is_physical = (selected['table'], selected['column']) in fields
            # Derived candidates use their verified catalog label, never an
            # invented physical column. Their catalog is snapshot-bound above.
            catalog = getattr(engine.semantic_graph, 'metric_catalog', None)
            derived_ids = []
            if catalog:
                for metric_id, metric in catalog.derived.items():
                    sources, _ = catalog.dependencies(metric_id)
                    if (metric.label == selected['label'] and sources
                            and (catalog.sources[sources[0]].table, catalog.sources[sources[0]].column)
                                == (selected['table'], selected['column'])):
                        derived_ids.append(metric_id)
            is_derived = len(derived_ids) == 1
            if not is_physical and not is_derived:
                return rejected('semantic_pending_target_missing', '所选指标没有对应的真实字段或已声明公式，请输入完整问题。', fresh)
            # Use the offered business alias, then independently verify its
            # physical binding below. Native names directly before “是多少”
            # can be interpreted as filter labels by the general linker.
            replacement = selected['label']
            start, end = ambiguity.start, ambiguity.end
            before = payload['question']
            if before[start:end] != ambiguity.source_text:
                return rejected('semantic_pending_span_changed', '待确认词语的位置无法核验，请重新输入完整问题。', fresh)
            candidate = before[:start] + replacement + before[end:]
            original_ambiguity = next(item for item in fresh.ambiguities
                                      if item.source_text == ambiguity.source_text
                                      and (item.start, item.end) not in
                                      {(edit['origin_start'], edit['origin_end']) for edit in payload['edits']})
            payload['edits'].append({'start': start, 'end': end, 'source': ambiguity.source_text,
                                     'replacement': replacement,
                                     'origin_start': original_ambiguity.start, 'origin_end': original_ambiguity.end})
            queued = self._pending_normalization(fresh, candidate, payload['edits'])
            if queued.ambiguities:
                payload['question'], payload['normalization'] = candidate, _plain(queued)
                with self.connection() as connection:
                    connection.execute('''UPDATE pending_semantic_choices SET payload=?,checksum=?
                        WHERE session_id=? AND pending_id=?''',
                        (_json(payload), _digest(payload), key, identifier))
                return SemanticPendingResolution('clarification', candidate,
                    'semantic_pending_more_choices', queued.clarification or '请继续确认剩余术语。',
                    identifier, queued, original, selected)
            resolved = engine.normalize_question(candidate, history_hint={
                **payload['history_hint'],
                'selected_metric': {'table': selected['table'], 'column': selected['column'],
                    'label': selected['label'], 'metric_id': derived_ids[0] if is_derived else None},
                'selection_span': start,
            })
            if resolved is not None and resolved.ambiguities:
                return rejected('semantic_pending_binding_changed', '所选指标仍存在新的口径歧义，请明确提供完整问题。', resolved)
            canonical = resolved.normalized_question if resolved is not None else candidate
            # Parsing the entire preserved question grants no execution
            # authority here. Missing grain/subject remains a normal SQL
            # clarification for the caller to handle.
            plan = engine.extract_required_intent(canonical)
            metrics = plan.metrics or []
            chosen = (any(metric.id == derived_ids[0] for metric in plan.derived_metrics) if is_derived else
                      ((plan.metric_table or plan.table, plan.metric_column) == (selected['table'], selected['column'])
                       or any((metric.table, metric.column) == (selected['table'], selected['column']) for metric in metrics)))
            if not chosen:
                return rejected('semantic_pending_selected_metric_not_verified',
                                '选择后的完整问题未能绑定该指标，请明确提供完整问题。', resolved)
            return SemanticPendingResolution('resolved', canonical, 'semantic_pending_explicit_choice',
                '已确认术语，原问题的时间、地区和其余条件保留。', identifier, resolved, original, selected)
        except (ValueError, KeyError, TypeError, AttributeError):
            return rejected('semantic_pending_integrity_failed', '此前澄清记录无法完整核验，请重新输入完整问题。')
