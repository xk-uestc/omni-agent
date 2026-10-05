"""Validate archived comparison work before restoring its original operands."""
from copy import deepcopy
from dataclasses import dataclass
import re
from .conversation_comparison import ComparisonError, MESSAGES, unseal, seal, _verify, parse_request


@dataclass(frozen=True)
class PendingComparisonSelection:
    reason: str
    message: str
    turn: object = None
    preview: dict | None = None
    followup: str = ''
    supported_followup: bool = False


class PendingComparisonResumeAgent:
    def __init__(self, engine): self.engine = engine

    def inspect(self, turn, followup=''):
        state = turn.state or {}
        try:
            if state.get('route') != 'comparison' or not state.get('pending_question'):
                raise ComparisonError('pending_reference_not_pending')
            preview = unseal(state.get('pending_comparison_result'))
            if preview.get('status') != 'clarification' or preview.get('rows'):
                raise ComparisonError('comparison_snapshot_invalid')
            context = unseal(state.get('comparison_context'))
            revision = self.engine.current_source_revision()
            if len(context.get('sources', [])) != 2:
                raise ComparisonError('comparison_snapshot_invalid')
            for source in context['sources']: _verify(source, self.engine, revision)
            waiting = state.get('comparison_pending_query')
            batch = state.get('comparison_pending_batch')
            staged = deepcopy(context)
            if batch:
                batch = unseal(batch)
                if (batch.get('version') != 1 or batch.get('source_revision') != revision
                    or batch.get('context_sha256') != seal(context)['sha256']
                    or type(batch.get('cursor')) is not int or batch['cursor'] not in (0, 1, 2)
                    or len(batch.get('commands', [])) != 2):
                    raise ComparisonError('comparison_snapshot_invalid')
                for index, source in batch.get('staged_sources', {}).items():
                    if index not in ('0', '1'): raise ComparisonError('comparison_snapshot_invalid')
                    _verify(source, self.engine, revision)
                    staged['sources'][int(index)] = deepcopy(source)
                for key, value in batch.get('comparison_settings', {}).items():
                    if key not in ('reverse', 'month_alignment') or type(value) is not bool:
                        raise ComparisonError('comparison_snapshot_invalid')
                    staged[key] = value
            if waiting:
                waiting = unseal(waiting)
                if (waiting.get('version') != 1 or type(waiting.get('index')) is not int
                    or waiting['index'] not in (0, 1) or waiting.get('source_revision') != revision
                    or waiting.get('context_sha256') != seal(staged)['sha256']):
                    raise ComparisonError('comparison_snapshot_invalid')
                required = self.engine.extract_required_intent(waiting['question'])
                if not required.clarification or required.clarification_code != preview.get('clarification_code'):
                    raise ComparisonError('comparison_snapshot_invalid')
                preview['clarification_options'] = list(required.clarification_options)
            supported = False
            if followup:
                supported = bool(parse_request(followup) or re.fullmatch(r'(?:基准值|比较值|基准查询|比较查询|取消修改|放弃修改)', followup)
                    or re.match(r'^(?:把)?(?:基准值?|比较值|基准查询|比较查询|较早查询|较晚查询)(?:的条件)?(?:改成|换成|改为)', followup))
                if waiting and not supported:
                    from .session import ConversationTurn
                    from .sql_history_scope import resolve_sql_followup_scope, VERIFIED_SQL_CONTEXT_MODES
                    pending = ConversationTurn(waiting['question'], waiting['question'], 0, waiting['state'])
                    _, audit = resolve_sql_followup_scope(followup, (pending,), self.engine)
                    supported = audit.get('mode') in VERIFIED_SQL_CONTEXT_MODES
            return PendingComparisonSelection('pending_comparison_restored',
                '已恢复这项比较任务，原基准、比较对象及已补条件保持不变。', turn, preview, followup, supported)
        except (ComparisonError, ValueError, TypeError, KeyError, IndexError) as error:
            code = getattr(error, 'code', 'comparison_snapshot_invalid')
            return PendingComparisonSelection(code, MESSAGES.get(code, '这项比较任务无法可靠恢复，请重新确认两项查询。'))
