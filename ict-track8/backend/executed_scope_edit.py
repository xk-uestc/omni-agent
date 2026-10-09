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
        if not history:
            return None
        from .conversational_sql_edit import conversational_edit
        previous = history[-1]
        state = previous.state or {}
        base = previous.effective_question
        natural = conversational_edit(question, base, self.engine)
        text = natural.command if natural is not None else PendingScopeEditAgent.command(question)
        if text is None and natural is None:
            return None
        import re
        from .pending_filter_edit import PendingFilterEditAgent
        clauses=re.split(r'[，,；;]',text or '')
        named=PendingScopeEditAgent._CLAUSE.fullmatch(text or '')
        if (natural is None and len(clauses)==1 and not PendingFilterEditAgent.START.match(text)
                and not re.fullmatch(r'.+?(?:一起给出|一起查询|都给出|同时给出)',text)
                and (named is None or named['label'] is None)):
            # Existing simple "改成2024年" follow-ups retain their established
            # verifier; this branch adds named or compound scope operations.
            return None
        if (state.get('route') != 'sql' or state.get('pending_question') is not None
                or 'pending_question' not in state or state.get('clarification_code')
                or not isinstance(base, str) or not base):
            return None
        from .sql_history_scope import (_complete, _confirmed_replacement_context_valid,
            _filters, _saved_metrics_match, _physical_metrics, _unchanged_query_controls,
            _unchanged_grouping, _metric_alias_groups_verified, _metric_semantics)
        original = self.engine.extract_required_intent(base)
        def reject(reason, message):
            return PendingScopeEdit(base, original, False, reason, message, ())
        if not _confirmed_replacement_context_valid(question,base,state,self.engine):
            return reject('executed_edit_context_invalid',
                '原查询的执行记录或数据版本已变化，请重新查询完整问题后再修改。')
        if natural is not None and natural.command is None:
            return reject(natural.reason,
                '追问中的条件或修改对象不能完整确认，请明确要改的字段和取值；原查询保留。')
        before = original.to_dict()
        if not _complete(original):
            # A successful relational query may not fit the rule planner's
            # slots. That is missing representation, not a rejected edit.
            # Defer a short follow-up to the existing independent rewrite and
            # review channel; it rechecks source authority before any API call.
            # Complete rule scopes and mismatched saved slots still fail closed.
            from .sql_history_scope import _FOLLOWUP
            provider = getattr(self.engine, 'model_plan_provider', None)
            if (getattr(provider, 'supports_complex_queries', False) is True
                    and _FOLLOWUP.search(question)):
                return None
        if (not _complete(original)
                or _filters(state.get('filters', [])) != _filters(before['filters'])
                or not _saved_metrics_match(state,before,self.engine)
                or state.get('dimensions', []) != before['dimensions']):
            return reject('executed_edit_scope_unverified',
                '原查询的字段和条件尚不能完整核对，请提供完整的新问题；本次未执行。')
        # A cancellation followed by a fully specified new grouped query is
        # an explicit replacement, rather than an incomplete atomic edit.
        full=re.fullmatch(r'(?:取消|删除|移除)(?:单一)?(.+?)(?:限制|筛选)[，,](.+)',text)
        if full:
            scope=full[2];candidate=self.engine.extract_required_intent(scope)
            slots=self.engine.analyze_slots(scope)
            labels=self.engine.analyze_slots(full[1],preferred_tables=(original.table,))['dimensions']
            old_fields={(f.get('table') or before['table'],f['column']) for f in before['filters']}
            group_fields={(candidate.dimension_tables.get(d,candidate.table),d) for d in candidate.dimensions}
            field={(d.table,d.column) for d in labels if (d.table,d.column) in old_fields & group_fields}
            new_fields={(f.table or candidate.table,f.column) for f in candidate.filters}
            if (_complete(candidate) and slots['time_spans'] and slots['metrics'] and len(field)==1
                    and field<=old_fields and not field & new_fields
                    and field<={(candidate.dimension_tables.get(d,candidate.table),d) for d in candidate.dimensions}
                    and not re.search(r'不变|沿用|刚才|同一',scope)):
                return PendingScopeEdit(scope,candidate,True,'executed_explicit_complete_replacement',
                    '已取消指定筛选并核验完整的新查询。',({'slot':'.'.join(next(iter(field))),'operation':'remove'},))
        edited = PendingScopeEditAgent(self.engine).edit_scope(text,base,original)
        if not edited.verified:
            return edited
        after = edited.plan.to_dict()
        # Slots explicitly changed are the only ones allowed to differ.
        slots = {r['slot'] for r in edited.replacements}
        if 'metric' in slots:
            metric_text = next(item['to'] for item in edited.replacements if item['slot'] == 'metric')
            requested = self.engine.extract_required_intent(metric_text)
            supplied = self.engine.analyze_slots(metric_text, preferred_tables=(original.table,))
            if (not _complete(requested) or not _metric_alias_groups_verified(supplied, self.engine)
                    or _metric_semantics(requested.to_dict()) != _metric_semantics(after)):
                return reject('executed_edit_metric_definition_unverified',
                    '新指标的物理依赖、公式或输出尚不能完整核验，原查询保留。')
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
                or not _unchanged_grouping(before, after)
                or preserved(before) != preserved(after)
                or 'metric' not in slots and _metric_semantics(before) != _metric_semantics(after)
                or not _unchanged_query_controls(before,after, metric_replacement='metric' in slots)):
            return reject('executed_edit_unmentioned_scope_changed',
                '无法确认未修改的条件仍完整保留，整条修改未执行；请提供完整的新问题。')
        return edited
