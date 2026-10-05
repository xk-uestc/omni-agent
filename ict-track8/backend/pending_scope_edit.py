"""Atomic edits to an unfinished SQL task, without granting execution authority."""
from dataclasses import dataclass
import re
from .nl2sql.models import QueryPlan


@dataclass(frozen=True)
class PendingScopeEdit:
    scope: str
    plan: QueryPlan
    verified: bool
    reason: str
    message: str
    replacements: tuple


class PendingScopeEditAgent:
    """Replace explicit existing slots while retaining every other request span.

    This agent reparses actual schema/values, never executes SQL and never asks
    a model to guess an edit. Named multi-clause edits commit all or none.
    """
    _CLAUSE=re.compile(r'(?:把)?(?P<label>[A-Za-z_0-9.\u3400-\u9fff]{1,48}?)?(?:改成|改为|换成|换为)(?P<value>.+)')
    _START=re.compile(r'^(?:把)?(?:[A-Za-z_0-9.\u3400-\u9fff]{1,48}?)?(?:改成|改为|换成|换为)')
    _TIME_LABELS=(None,'时间','时间范围','年份','月份','日期')
    _METRIC_LABELS=(None,'指标','统计指标')

    def __init__(self,engine):self.engine=engine

    @classmethod
    def command(cls, question):
        if not isinstance(question,str):return None
        text=question.strip().rstrip('。？！!?')
        text=re.sub(r'^(?:那么|那)(?=把|改成|改为|换成|换为|时间|日期|年份|月份|地区|渠道|指标)','',text)
        if re.match(r'^(?:换个主题|换一个主题|换个问题|新问题|重新查询)',text):return None
        from .pending_filter_edit import PendingFilterEditAgent
        if not any(cls._START.match(clause.strip()) or PendingFilterEditAgent.START.match(clause.strip())
                   for clause in re.split(r'[，,；;]',text)):return None
        return text

    def run(self,question,history):
        text=self.command(question)
        if text is None or not history:return None
        previous=history[-1];state=previous.state or {};base=previous.effective_question
        if (state.get('route')!='sql' or not isinstance(base,str) or not base
                or state.get('pending_question')!=base or not state.get('clarification_code')):return None
        original=self.engine.extract_required_intent(base)
        if not original.clarification or original.clarification_code!=state['clarification_code']:return None
        def rejected(reason,message):
            return PendingScopeEdit(base,original,False,reason,message,())
        from .sql_history_scope import _filters,_saved_metrics_match
        original_plan=original.to_dict()
        if (_filters(state.get('filters',[]))!=_filters(original_plan['filters'])
                or state.get('dimensions',[])!=original_plan['dimensions']
                or not _saved_metrics_match(state,original_plan,self.engine)):
            return rejected('pending_edit_saved_scope_mismatch','此前保存条件与当前解析不一致，不能继承修改；请确认完整问题。')
        return self.edit_scope(text,base,original)

    def edit_scope(self,text,base,original):
        """Build an atomic candidate; callers must verify the history authority."""
        from .pending_filter_edit import PendingFilterEditAgent
        def rejected(reason,message):
            return PendingScopeEdit(base,original,False,reason,message,())
        if len(text)>160:
            return rejected('pending_edit_too_complex','修改内容较长，请分次明确要改的条件；原问题未改变。')
        clauses=re.split(r'[，,；;]',text)
        if not 1<=len(clauses)<=4 or any(not clause.strip() for clause in clauses):
            return rejected('pending_edit_too_complex','请明确要修改的条件及其新值；本次没有部分修改原问题。')
        scope=base;replacements=[];changed=set()
        for clause in clauses:
            mutation=PendingFilterEditAgent(self.engine).run(clause.strip(),scope)
            if mutation is not None:
                if not mutation.verified:
                    return rejected(mutation.reason,mutation.message)
                if mutation.replacement['slot'] in changed:
                    return rejected('pending_edit_duplicate_slot','同一条件出现多次操作，请确认一种；本次没有部分修改原问题。')
                scope=mutation.scope
                replacements.append(mutation.replacement)
                changed.add(mutation.replacement['slot'])
                continue
            match=self._CLAUSE.fullmatch(clause.strip())
            if match is None:
                # A compound command may finish the current clarification, but
                # only with a uniquely current offered choice. Recompute after
                # preceding edits; an old UI menu cannot authorize a new slot.
                from .clarification import normalize_clarification_reply,ClarificationResolver,ClarificationSelection
                pending=self.engine.extract_required_intent(scope)
                reply=normalize_clarification_reply(clause)
                grain=re.fullmatch(r'按(月|年)(?:统计|汇总|趋势)?',reply)
                value={'月':'monthly_trend','年':'yearly_trend'}.get(grain[1]) if grain else None
                options=[item for item in pending.clarification_options
                         if (pending.clarification_code=='missing_time_grain' and value and item.get('value')==value)
                         or item.get('label')==reply]
                if not pending.clarification or len(options)!=1:
                    return rejected('pending_edit_not_verified','请明确每项修改的条件和新值；原问题及已选条件保留。')
                code=pending.clarification_code
                slot='time_grain' if code=='missing_time_grain' else 'clarification_choice'
                if slot in changed:
                    return rejected('pending_edit_duplicate_slot','同一条件出现多个新值，请确认一种；本次没有部分修改原问题。')
                option=options[0]
                try:
                    scope=ClarificationResolver().apply(scope,ClarificationSelection(code,option['value'],option.get('label')))
                except (ValueError,KeyError,TypeError):
                    return rejected('pending_edit_choice_not_verified','这个选项仍需补充具体条件，原问题没有部分修改。')
                replacements.append({'slot':slot,'from':'','to':option.get('label',option['value'])})
                changed.add(slot)
                continue
            label,value=match['label'],re.sub(r'(?:吧|即可)$','',match['value']).strip()
            before=self.engine.analyze_slots(scope);after=self.engine.analyze_slots(value)
            # A slot value must consume the whole clause, not just a recognized
            # fragment of an exclusion, comparison or independent new question.
            kinds=[kind for kind in ('time_spans','values','metrics','dimensions') if after[kind]]
            if len(kinds)!=1 or len(after[kinds[0]])!=1:
                return rejected('pending_edit_ambiguous','这项修改不能唯一确定，请说明具体时间、筛选取值或指标；原问题未改变。')
            kind=kinds[0]
            if kind=='time_spans':
                if (label not in self._TIME_LABELS and label not in before[kind]
                        or after['normalized']!=after[kind][0] or len(before[kind])>1):
                    return rejected('pending_edit_time_not_verified','请填写一个明确有效的新时间范围；原问题未改变。')
                old,new=(before[kind][0] if before[kind] else ''),after[kind][0];slot='time'
            elif kind=='values':
                target=after[kind][0]
                existing=[item for item in before[kind] if (item.table,item.column)==(target.table,target.column)]
                if (label in ('时间','年份','月份','日期','指标','统计指标') or target.via!='exact' or target.negated
                        or after['normalized']!=target.span or len(existing)!=1 or existing[0].negated
                        or existing[0].via!='exact'):
                    return rejected('pending_edit_filter_not_verified','筛选值尚不能唯一对应原条件，请说明具体字段和取值；原问题未改变。')
                # Labels cannot turn one physical field into another by name.
                if label and label!=existing[0].span:
                    label_slots=self.engine.analyze_slots(label)['dimensions']
                    if not any((item.table,item.column)==(target.table,target.column) for item in label_slots):
                        return rejected('pending_edit_filter_label_mismatch','修改名称与实际字段不一致；原问题未改变。')
                old,new=existing[0].span,target.span;slot=f'{target.table}.{target.column}'
            elif kind=='metrics':
                target=after[kind][0]
                if (label not in self._METRIC_LABELS and label not in [item.matched_alias for item in before[kind]] or len(before[kind])!=1
                        or after['normalized']!=target.matched_alias):
                    return rejected('pending_edit_metric_not_verified','请选择一个明确的统计指标；原问题未改变。')
                old,new=before[kind][0].matched_alias,target.matched_alias;slot='metric'
            else:
                return rejected('pending_edit_dimension_not_verified','分组修改请使用当前分组选项或完整问题；原问题未改变。')
            if slot in changed:
                return rejected('pending_edit_duplicate_slot','同一条件出现多个新值，请确认一种；本次没有部分修改原问题。')
            if old and scope.count(old)!=1:
                return rejected('pending_edit_ambiguous_span','原条件出现多处，无法唯一修改；请直接输入完整的新问题。')
            changed.add(slot)
            scope=scope.replace(old,new,1) if old else f'{scope} {new}'
            replacements.append({'slot':slot,'from':old,'to':new})
        # The full normal intent parser runs against the atomic candidate.
        # Unmodified bytes are preserved, including negation, ranking and field
        # markers. Any newly introduced unresolved condition rejects the edit.
        candidate=self.engine.extract_required_intent(scope)
        if candidate.coverage.get('unresolved'):
            return rejected('pending_edit_unresolved_scope','修改后的条件仍有无法确认的内容；原问题未改变，请补充完整说明。')
        if candidate.clarification_code=='missing_data_subject':
            return rejected('pending_edit_subject_lost','修改后业务对象不能确认，原问题已保留。')
        return PendingScopeEdit(scope,candidate,True,'pending_scope_explicit_atomic_edit',
            '已修改明确指定的条件，其他条件保留。',tuple(replacements))
