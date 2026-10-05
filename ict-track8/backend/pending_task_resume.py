"""Explicit selection of retained, unexecuted SQL clarification snapshots."""
from dataclasses import dataclass
import re
from .nl2sql.models import QueryPlan


@dataclass(frozen=True)
class PendingTaskSelection:
    reason: str
    message: str
    turn: object = None
    plan: QueryPlan | None = None
    followup: str = ''
    supported_followup: bool = False


class PendingTaskResumeAgent:
    _REQUEST=re.compile(r'^(?:继续|恢复|回到)待补查询编号\s*(q_[a-f0-9]{32})(?:[，,:：]\s*(.+))?\s*$',re.I)
    _PREFIX=re.compile(r'^(?:继续|恢复|回到)待补查询',re.I)

    def __init__(self,engine):self.engine=engine

    def run(self,question,history,*,resolver=None):
        if not isinstance(question,str) or not self._PREFIX.match(question.strip()):return None
        match=self._REQUEST.fullmatch(question.strip())
        if match is None:
            return PendingTaskSelection('pending_reference_invalid','请使用完整待补查询编号；不会自动选择其他未完成问题。')
        identifier,followup=match[1].lower(),(match[2] or '').strip()
        matches=[turn for turn in history if turn.turn_id==identifier]
        if resolver is not None:
            archived, status = resolver(identifier)
            if status == 'completed':
                return PendingTaskSelection('pending_reference_completed','这项待补查询已经完成，不能再次作为待补任务恢复；请引用已完成查询或重新提问。')
            if archived is not None:
                matches = [archived]
            elif matches and (matches[0].state or {}).get('pending_question'):
                matches = []
        if len(matches)!=1:
            return PendingTaskSelection('pending_reference_unavailable','这个待补编号不可用，可能已过期、被清空或不属于当前会话；请重新提交完整问题。')
        turn=matches[0];state=turn.state or {};scope=turn.effective_question
        if (state.get('route')!='sql' or not isinstance(scope,str) or not scope
                or state.get('pending_question')!=scope or not state.get('clarification_code')
                or state.get('executed_sql_context') or state.get('comparison_snapshot')):
            return PendingTaskSelection('pending_reference_not_pending','指定记录不是尚未执行的SQL待补问题；已完成查询请使用“引用这次查询”。')
        from .pending_source_validation import PendingSourceValidationAgent
        if not PendingSourceValidationAgent(self.engine).matches(state):
            return PendingTaskSelection('pending_reference_source_changed','数据源或业务映射已经更新，不能恢复旧待补条件；请重新提交完整问题。')
        plan=self.engine.extract_required_intent(scope)
        from .sql_history_scope import _filters,_saved_metrics_match,resolve_sql_followup_scope,VERIFIED_SQL_CONTEXT_MODES
        parsed=plan.to_dict()
        if (not plan.clarification or plan.clarification_code!=state['clarification_code']
                or _filters(state.get('filters',[]))!=_filters(parsed['filters'])
                or state.get('dimensions',[])!=parsed['dimensions']
                or not _saved_metrics_match(state,parsed,self.engine)):
            return PendingTaskSelection('pending_reference_changed','待补条件与当前数据源解析不一致，不能直接恢复；请重新确认完整问题。')
        supported=False
        if followup:
            from .pending_scope_edit import PendingScopeEditAgent
            from .clarification_continuation import ClarificationContinuationAgent
            supported=(PendingScopeEditAgent(self.engine).run(followup,(turn,)) is not None
                or ClarificationContinuationAgent(self.engine).run(followup,(turn,)) is not None)
            if not supported:
                _,audit=resolve_sql_followup_scope(followup,(turn,),self.engine)
                supported=audit.get('mode') in VERIFIED_SQL_CONTEXT_MODES
        return PendingTaskSelection('pending_reference_restored',
            '已恢复指定轮次的待补条件；原问题和当前可选条件如下。' if not followup or supported else
            '已恢复指定待补问题，但这次补充不能可靠合并；请使用当前选项或明确修改条件。',
            turn,plan,followup,supported)
