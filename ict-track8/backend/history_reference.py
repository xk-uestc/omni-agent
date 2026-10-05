"""Select explicit conversation references before any model or query execution.

Backward positions are relative to retained SQL attempts, not all chat turns.
Absolute positions are deliberately unresolved without absolute turn identities.
Selection does not certify execution; SQL scope validation owns that boundary.
"""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class HistoryReference:
    kind: str
    followup: str
    successful_only: bool = False
    offset: int = 1
    question: str = ''
    turn_id: str = ''


@dataclass(frozen=True)
class ReferenceSelection:
    request: HistoryReference
    history_index: int | None = None
    reason: str | None = None


def reference_clarification(reason):
    return {
        'requested_sql_reference_expression_unsupported':'请用“回到倒数第二次SQL查询，那华南呢”，或引用完整问题。当前历史窗口不能可靠解释从会话开头计数的查询序号。',
        'requested_sql_reference_ambiguous':'这段完整问题在保留的历史中对应多次查询，请使用查询编号或倒数第几次SQL查询指定具体一次。',
        'requested_sql_reference_not_available':'指定的SQL查询不在当前会话保留的历史中，请重新提交完整问题。不会改用最近一次结果。',
        'requested_sql_reference_not_verified':'指定的SQL查询未成功、来源已变化或记录无法核验，请重新执行完整问题。',
        'requested_sql_reference_rewrite_unverified':'已找到指定查询，但本次修改尚不能可靠合并，请明确完整的时间、指标和筛选条件。',
    }.get(reason,'追问中的原有约束尚未核验，请完整说明查询指标、筛选条件和时间范围。')


class ConversationReferenceAgent:
    _identifier=re.compile(r'^\s*回到(成功的)?SQL查询编号\s*(q_[a-f0-9]{32})[，,:：\s]+(.+)$',re.I)
    _previous = re.compile(r'^\s*回到(?:上一次|上一条)(成功的)?SQL查询[，,:：\s]+(.+)$', re.I)
    _backward = re.compile(r'^\s*回到倒数第([0-9]{1,3}|[一二三四五六七八九十两]{1,3})(?:次|条)(成功的)?SQL查询[，,:：\s]+(.+)$', re.I)
    _quote = re.compile(r'^\s*回到(成功的)?SQL查询(?:“([^”]+)”|「([^」]+)」|"([^"]+)")[，,:：\s]+(.+)$', re.I)
    _prefix = re.compile(r'^\s*回到.*SQL查询', re.I)

    @staticmethod
    def _number(value):
        if value.isascii() and value.isdigit():return int(value)
        digits={key:index+1 for index,key in enumerate('一二三四五六七八九')}
        digits['两']=2
        if value in digits:return digits[value]
        if value.count('十')!=1:return 0
        tens,ones=value.split('十')
        if tens and tens not in digits or ones and ones not in digits:return 0
        return (digits[tens] if tens else 1)*10+(digits[ones] if ones else 0)

    def parse(self, text):
        if not isinstance(text,str):return None
        matched=self._identifier.fullmatch(text)
        if matched:return HistoryReference('explicit_id_sql',matched[3],bool(matched[1]),turn_id=matched[2].lower())
        matched=self._previous.fullmatch(text)
        if matched:return HistoryReference('explicit_previous_sql',matched[2],bool(matched[1]))
        matched=self._backward.fullmatch(text)
        if matched:
            return HistoryReference('explicit_backward_sql',matched[3],bool(matched[2]),self._number(matched[1]))
        matched=self._quote.fullmatch(text)
        if matched:return HistoryReference('explicit_quoted_sql',matched[5],bool(matched[1]),
                                           question=next(value for value in matched.groups()[1:4] if value is not None))
        if self._prefix.match(text):return HistoryReference('unresolved_sql_reference','')
        return None

    def select(self, text, history):
        request=self.parse(text)
        if request is None:return None
        if (request.kind=='unresolved_sql_reference' or not request.followup.strip()
                or self.parse(request.followup) is not None):
            return ReferenceSelection(request,reason='requested_sql_reference_expression_unsupported')
        return self.select_source(request,history)

    def select_source(self,request,history):
        """Shared source selection for followups and two-source comparisons."""
        if request.kind not in {'explicit_previous_sql','explicit_backward_sql','explicit_quoted_sql','explicit_id_sql'}:
            return ReferenceSelection(request,reason='requested_sql_reference_expression_unsupported')
        candidates=[(index,turn) for index,turn in enumerate(history)
                    if (turn.state or {}).get('route')=='sql'
                    and (not request.successful_only or (turn.state or {}).get('executed_sql_context'))]
        if request.kind=='explicit_id_sql':
            candidates=[(index,turn) for index,turn in candidates if turn.turn_id==request.turn_id]
            if len(candidates)>1:return ReferenceSelection(request,reason='requested_sql_reference_ambiguous')
        elif request.kind=='explicit_quoted_sql':
            candidates=[(index,turn) for index,turn in candidates
                        if request.question in (turn.question,turn.effective_question)]
            if len(candidates)>1:return ReferenceSelection(request,reason='requested_sql_reference_ambiguous')
        elif request.offset<1 or request.offset>len(candidates):
            return ReferenceSelection(request,reason='requested_sql_reference_not_available')
        else:
            candidates=[candidates[-request.offset]]
        if not candidates:return ReferenceSelection(request,reason='requested_sql_reference_not_available')
        return ReferenceSelection(request,history_index=candidates[0][0])
