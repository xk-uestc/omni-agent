"""Bounded recovery of an unanswered clarification, without SQL execution."""
from dataclasses import dataclass
import re
from datetime import date
from .clarification import normalize_clarification_reply,clarification_ordinal
from .nl2sql.models import QueryPlan


@dataclass(frozen=True)
class ClarificationContinuation:
    scope: str
    plan: QueryPlan
    reason: str
    message: str
    retry_count: int


class ClarificationContinuationAgent:
    """Keep a pending task only when the reply clearly is not a new question.

    This agent does not invent defaults, rewrite constraints or execute tools.
    Options come from a new parse of the original pending scope, not UI text.
    """
    _UNDECIDED = frozenset({'不知道', '我不知道', '不确定', '随便', '都可以',
        '你决定', '暂时不知道', '不懂', '没看懂', '再解释一下', '解释一下', '什么意思'})
    _TIME_CODES = frozenset({'missing_time_range', 'missing_comparison_period',
                            'missing_comparison_scope'})
    _HELP = frozenset({'什么意思', '不懂', '没看懂', '解释一下', '再解释一下',
                       '怎么选', '应该怎么选', '有什么区别', '选项有什么区别',
                       '这是什么意思', '这些选项是什么意思', '请解释一下', '能解释一下吗'})
    _ACKNOWLEDGEMENTS = frozenset({'好', '好的', '嗯', '明白了', '知道了',
                                  '继续', '继续吧', '就这样', '可以', '没问题', '确认'})

    @staticmethod
    def _explain(code):
        return {
            'missing_time_range': '时间范围是要查询哪个年份或月份，例如2025年、2025年3月；它决定哪些记录会参与统计。',
            'missing_comparison_period': '比较期间是要比较哪些年份或月份，请明确具体期间；不会自动把缺失期间设为今年。',
            'missing_comparison_scope': '请说明比较哪些对象或期间，例如地区、渠道或具体年份；只能选择当前提供的条件。',
            'missing_time_grain': '时间粒度是结果按多长时间汇总：按月会逐月列出，按年会逐年列出。查询时间范围仍按原问题保留；支持的粒度以下方选项为准。',
            'missing_metric': '统计指标是想计算什么，例如金额、数量或记录数；请选择下方当前数据库提供的指标。',
            'ambiguous_metric': '同一名称对应多个统计字段，请确认要计算哪个表的字段；表名.字段名用于区分它们。',
            'ambiguous_dimension': '分组字段决定结果按什么对象分别展示；同名字段可能来自不同表，请选择具体表的字段。',
            'ambiguous_value': '当前名称匹配多个实际取值，请选择你指的具体对象；不会自动取第一个。',
            'missing_analysis_dimension': '分组方式决定按哪个类别分别统计，例如地区或渠道；请选择当前提供的选项。',
        }.get(code, '当前问题还有条件需要确认，请选择下方选项或说明具体条件。')

    def __init__(self, engine):
        self.engine = engine

    @classmethod
    def reply_kind(cls, question):
        """Recognize only a whole, bounded help or acknowledgement reply."""
        text = normalize_clarification_reply(question)
        if text in cls._HELP:
            return 'clarification_help_requested'
        if text in cls._ACKNOWLEDGEMENTS:
            return 'condition_not_supplied'
        if text in cls._UNDECIDED:
            return 'selection_not_confirmed'
        return None

    def run(self, question, history):
        if not history or not isinstance(question, str):
            return None
        previous = history[-1]
        state = previous.state or {}
        scope = previous.effective_question
        if (state.get('route') != 'sql' or not isinstance(scope, str) or not scope
                or state.get('pending_question') != scope or not state.get('clarification_code')):
            return None
        text = normalize_clarification_reply(question)
        code = state['clarification_code']
        reason = None
        if text in self._HELP:
            reason = 'clarification_help_requested'
        elif text in self._ACKNOWLEDGEMENTS:
            reason = 'condition_not_supplied'
        elif text in self._UNDECIDED:
            reason = 'selection_not_confirmed'
        elif code in self._TIME_CODES and re.fullmatch(r'[0-9]{4}(?:年|(?:年|-)[0-9]{1,2}月?)?', text):
            # Valid time replies belong to the normal resolver. Retain only
            # dates whose month is invalid; never turn them into new queries.
            month = re.search(r'(?:年|-)([0-9]{1,2})月?$', text)
            try:
                date(int(text[:4]), int(month[1]) if month else 1, 1)
            except ValueError:
                reason = 'invalid_time_reply'
        elif code == 'missing_time_grain' and re.fullmatch(
                r'按(?:月|年)(?:统计|趋势|汇总)?(?:或|或者|和|、)(?:按)?(?:月|年)(?:统计|趋势|汇总)?', text):
            reason = 'multiple_grains_not_confirmed'
        plan = self.engine.extract_required_intent(scope)
        if not plan.clarification or plan.clarification_code != code:
            return None
        ordinal=clarification_ordinal(question)
        if ordinal is not None:
            options=plan.clarification_options
            if state.get('clarification_options')!=options:
                reason='option_list_changed'
            elif not options or ordinal!=-1 and not 1<=ordinal<=len(options):
                reason='option_ordinal_out_of_range'
            elif options[-1 if ordinal==-1 else ordinal-1].get('value') in {'year','month','time_range'}:
                reason='selected_time_requires_value'
        if code == 'missing_time_grain' and re.fullmatch(
                r'按(?:日|天|周|星期|季度|季|半年|月|年)(?:统计|趋势|汇总)?', text):
            grain = re.sub(r'(?:统计|趋势|汇总)$', '', text)
            offered = {item.get('value') for item in plan.clarification_options}
            value = {'按月':'monthly_trend', '按年':'yearly_trend'}.get(grain)
            if value is None or value not in offered:
                reason = 'unsupported_time_grain'
        matches = [item for item in plan.clarification_options if item.get('label') == text]
        if len(matches) > 1:
            reason = 'duplicate_option_label'
        if reason is None:
            return None
        messages = {
            'clarification_help_requested': self._explain(code),
            'selection_not_confirmed':'可以点击下方选项；如果不确定，请说明想比较或统计什么。当前不会替你选择条件。',
            'invalid_time_reply':'请输入有效年份和1到12的月份，例如2025年或2025-03。原问题已保留，请重新填写。',
            'multiple_grains_not_confirmed':'这次趋势查询需要选择一种时间粒度，请从下方选项中确认一种。',
            'unsupported_time_grain':'当前查询未提供这种时间粒度，请选择下方支持的选项。原问题和时间范围已保留，不会自动改用其他粒度。',
            'duplicate_option_label':'这个名称对应多个字段，请点击具体选项或输入完整的表名.字段名。',
            'condition_not_supplied':'收到，但还没有获得需要补充的具体条件。原问题和已确认条件已保留，请选择下方选项或填写具体条件后再继续。',
            'option_list_changed':'此前选项无法与当前查询核对，请根据本次展示的选项重新选择；原问题和已确认条件已保留。',
            'option_ordinal_out_of_range':'这个序号不在当前选项范围内，请选择下方列出的选项；原问题未改变。',
            'selected_time_requires_value':'已了解你选择时间类型，但仍需要具体年份或月份，例如2025年、2025年3月；本次尚未执行查询。',
        }
        if reason == 'clarification_help_requested':
            labels = [item.get('label') for item in plan.clarification_options
                      if isinstance(item, dict) and isinstance(item.get('label'), str)]
            if labels:
                messages[reason] += ' 当前可选：' + '；'.join(labels[:12]) + '。'
        retries = state.get('clarification_retry_count', 0)
        retries = retries if isinstance(retries, int) and not isinstance(retries, bool) and retries >= 0 else 0
        return ClarificationContinuation(scope, plan, reason, messages[reason], min(retries + 1, 100))
