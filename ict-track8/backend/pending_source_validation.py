"""Validate the data generation of an unfinished SQL context, without tools."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PendingSourceRejection:
    reason: str = 'pending_source_changed'
    message: str = '数据源或业务映射已经更新，旧待补条件不能直接沿用；请重新提交完整问题，使用当前数据重新确认条件。'
    turn: object = None
    plan: object = None
    followup: str = ''
    supported_followup: bool = False


class PendingSourceValidationAgent:
    def __init__(self, engine):
        self.engine = engine

    def matches(self, state):
        revision = state.get('pending_source_revision')
        # An unbound legacy context cannot establish which source was used.
        return (isinstance(revision, str) and revision == self.engine.current_source_revision())

    def run(self, question, history):
        if not history:
            return None
        state = history[-1].state or {}
        if state.get('route') != 'sql' or not state.get('pending_question') or self.matches(state):
            return None
        from .sql_history_scope import _self_contained, _NEW_QUERY, _FOLLOWUP
        from .clarification import normalize_clarification_reply,clarification_ordinal
        from .clarification_continuation import ClarificationContinuationAgent
        if _NEW_QUERY.search(question):
            return None
        if re.search(r'改成|改为|换成|换为|增加|添加|新增|加上|删除|移除|取消', question):
            # A compound edit can mention metric + date and superficially
            # look self-contained, while still inheriting an old filter.
            return PendingSourceRejection()
        slots = self.engine.analyze_slots(question)
        if _self_contained(question, slots, self.engine):
            return None
        reply = normalize_clarification_reply(question)
        dependent = (reply in ClarificationContinuationAgent._HELP
                     or clarification_ordinal(question) is not None
                     or reply in ClarificationContinuationAgent._UNDECIDED
                     or _FOLLOWUP.search(question)
                     or any(slots[key] for key in ('metrics', 'time_spans', 'values', 'dimensions'))
                     or re.search(r'改成|改为|换成|换为', question))
        return PendingSourceRejection() if dependent else None
