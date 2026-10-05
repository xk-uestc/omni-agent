"""Atomic explicit edits of a verified successful SQL conversation scope."""
from .pending_scope_edit import PendingScopeEdit, PendingScopeEditAgent


class ExecutedScopeEditAgent:
    """Reuse pending-task edits only after proving successful source authority.

    Does not execute or reuse result rows. The full query engine executes the
    new scope; rejected commands leave the successful conversation untouched.
    """

    def __init__(self, engine):
        self.engine = engine

    def run(self, question, history):
        text = PendingScopeEditAgent.command(question)
        if text is None or not history:
            return None
        import re
        from .pending_filter_edit import PendingFilterEditAgent
        clauses=re.split(r'[，,；;]',text)
        named=PendingScopeEditAgent._CLAUSE.fullmatch(text)
        if (len(clauses)==1 and not PendingFilterEditAgent.START.match(text)
                and (named is None or named['label'] is None)):
            # Existing simple "改成2024年" follow-ups retain their established
            # verifier; this branch adds named or compound scope operations.
            return None
        previous = history[-1]
        state = previous.state or {}
        base = previous.effective_question
        if (state.get('route') != 'sql' or state.get('pending_question') is not None
                or 'pending_question' not in state or state.get('clarification_code')
                or not isinstance(base, str) or not base):
            return None
        from .sql_history_scope import (_complete, _confirmed_replacement_context_valid,
            _filters, _saved_metrics_match, _physical_metrics, _unchanged_query_controls)
        original = self.engine.extract_required_intent(base)
        def reject(reason, message):
            return PendingScopeEdit(base, original, False, reason, message, ())
        if not _confirmed_replacement_context_valid(question,base,state,self.engine):
            return reject('executed_edit_context_invalid',
                '原查询的执行记录或数据版本已变化，请重新查询完整问题后再修改。')
        before = original.to_dict()
        if (not _complete(original)
                or _filters(state.get('filters', [])) != _filters(before['filters'])
                or not _saved_metrics_match(state,before,self.engine)
                or state.get('dimensions', []) != before['dimensions']):
            return reject('executed_edit_scope_unverified',
                '原查询的字段和条件尚不能完整核对，请提供完整的新问题；本次未执行。')
        edited = PendingScopeEditAgent(self.engine).edit_scope(text,base,original)
        if not edited.verified:
            return edited
        after = edited.plan.to_dict()
        # Slots explicitly changed are the only ones allowed to differ.
        slots = {r['slot'] for r in edited.replacements}
        columns = {tuple(slot.split('.',1)) for slot in slots if '.' in slot}
        if 'time' in slots:
            spans = (self.engine.analyze_slots(base)['time_spans']
                     + self.engine.analyze_slots(edited.scope)['time_spans'])
            columns.update((item.get('table'),item['column'])
                for item in before['filters']+after['filters']
                if any(span in item.get('source_text','') for span in spans))
        def preserved(plan):
            return _filters([item for item in plan['filters']
                if (item.get('table') or plan['table'],item['column']) not in columns])
        if (before['table'] != after['table']
                or before['dimensions'] != after['dimensions']
                or preserved(before) != preserved(after)
                or 'metric' not in slots and _physical_metrics(before) != _physical_metrics(after)
                or not _unchanged_query_controls(before,after)):
            return reject('executed_edit_unmentioned_scope_changed',
                '无法确认未修改的条件仍完整保留，整条修改未执行；请提供完整的新问题。')
        return edited
