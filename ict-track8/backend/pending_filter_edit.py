"""Explicit filter addition/removal with full before/after intent proofs."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PendingFilterMutation:
    scope: str
    verified: bool
    reason: str
    message: str
    replacement: dict | None = None


class PendingFilterEditAgent:
    START = re.compile(r'^(?:增加|添加|新增|加上|删除|移除|取消)')
    ADD = re.compile(r'^(?:增加|添加|新增|加上)(?P<body>.+)$')
    REMOVE = re.compile(r'^(?:删除|移除|取消)(?P<body>.+)$')

    def __init__(self, engine):
        self.engine = engine

    def run(self, clause, scope):
        if not self.START.match(clause):
            return None
        def reject(reason, message):
            return PendingFilterMutation(scope, False, reason, message)
        add = self.ADD.fullmatch(clause)
        remove = self.REMOVE.fullmatch(clause)
        body = (add or remove)['body'].strip()
        before = self.engine.analyze_slots(scope)
        plan_before = self.engine.extract_required_intent(scope).to_dict()
        if add:
            parts = re.split(r'(?:为|=|：|:)', body, maxsplit=1)
            label = re.sub(r'(?:筛选条件|筛选|条件)$', '', parts[0]).strip() if len(parts)==2 else None
            value = parts[-1].strip()
            if len(parts)==1:
                value = re.sub(r'(?:筛选条件|筛选|条件)$', '', value).strip()
            supplied = self.engine.analyze_slots(value)
            if (len(supplied['values'])!=1 or supplied['normalized']!=supplied['values'][0].span
                    or any(supplied[key] for key in ('metrics','time_spans','dimensions'))):
                return reject('pending_filter_add_ambiguous','请给出一个明确筛选字段和实际取值；本次没有部分修改。')
            target = supplied['values'][0]
            if target.via!='exact' or target.negated or label is not None and not self._label_matches(label,target):
                return reject('pending_filter_add_not_verified','筛选字段与取值不能唯一对应，请确认字段名称及实际取值。')
            existing = [item for item in before['values'] if (item.table,item.column)==(target.table,target.column)]
            if existing:
                if (len(existing)==1 and existing[0].value==target.value and not existing[0].negated
                        and not self._negative_matches(scope,existing[0].span)):
                    return PendingFilterMutation(scope,True,'pending_filter_already_present','这项筛选已存在，保留原条件。',
                        {'slot':f'{target.table}.{target.column}','from':existing[0].span,'to':target.span,'operation':'retain'})
                return reject('pending_filter_already_constrained','这个字段已有筛选条件；请使用“改成”明确替换，原条件未变。')
            candidate = f'{scope} {target.span}'
            replacement = {'slot':f'{target.table}.{target.column}','from':'','to':target.span,'operation':'add'}
        else:
            label = re.sub(r'(?:筛选条件|筛选|条件)$', '', body).strip()
            candidates = [item for item in before['values'] if
                label==item.span or self._label_matches(label,item)]
            if len(candidates)!=1 or candidates[0].via!='exact':
                return reject('pending_filter_remove_ambiguous','请明确要删除的现有筛选字段；缺失或多个取值时不会自动删除。')
            target = candidates[0]
            if len([item for item in before['values'] if (item.table,item.column)==(target.table,target.column)])!=1:
                return reject('pending_filter_remove_multiple','这个字段存在多个取值，请提交完整的新筛选条件。')
            span = target.span
            negative_matches = self._negative_matches(scope,span)
            if target.negated or negative_matches:
                # analyze_slots exposes raw ValueIndex matches; the rule
                # planner applies negation later, so raw negated may be false.
                matches = negative_matches
                if len(matches)!=1:
                    return reject('pending_filter_remove_negation_unbound','排除条件的完整范围不能确认，原条件保留。')
                start,end = matches[0].span()
            else:
                matches = list(re.finditer(re.escape(span),scope))
                if len(matches)!=1:
                    return reject('pending_filter_remove_span_ambiguous','原取值出现多处，不能唯一删除；请提交完整问题。')
                start,end = matches[0].span()
            candidate = (scope[:start]+scope[end:]).strip()
            replacement = {'slot':f'{target.table}.{target.column}','from':target.span,'to':'','operation':'remove'}
        from .sql_history_scope import _filters, _physical_metrics, _unchanged_query_controls
        plan_after = self.engine.extract_required_intent(candidate).to_dict()
        key = (target.table,target.column)
        field = lambda item:(item.get('table') or plan_before['table'],item.get('column'))
        old = plan_before['filters']
        new = plan_after['filters']
        target_filters = [item for item in (new if add else old) if field(item)==key]
        unchanged = [item for item in (new if add else old) if field(item)!=key]
        expected_unchanged = old if add else new
        if (len(candidate)>1000 or len(target_filters)!=1
                or add and (target_filters[0].get('operator')!='=' or target_filters[0].get('value')!=target.value)
                or _filters(unchanged)!=_filters(expected_unchanged)
                or _physical_metrics(plan_before)!=_physical_metrics(plan_after)
                or plan_before['dimensions']!=plan_after['dimensions']
                or plan_before['table']!=plan_after['table']
                or not _unchanged_query_controls(plan_before,plan_after)
                or plan_after.get('coverage',{}).get('unresolved')):
            return reject('pending_filter_edit_scope_unverified','无法证明其余查询条件保持一致，本次没有部分修改。')
        return PendingFilterMutation(candidate,True,'pending_filter_explicit_mutation',
            '已修改指定筛选，其余查询条件保持一致。',replacement)

    @staticmethod
    def _negative_matches(scope,span):
        return list(re.finditer(r'(?:不包括|不含|排除|除了|不是|不等于|不为)\s*'+re.escape(span)
            +'|'+re.escape(span)+r'\s*(?:以外|之外)',scope))

    def _label_matches(self, label, target):
        if not label:
            return False
        if label in {target.column,f'{target.table}.{target.column}'}:
            return True
        slots=self.engine.analyze_slots(label)
        return (not any(slots[key] for key in ('metrics','time_spans','values'))
            and len(slots['dimensions'])==1
            and (slots['dimensions'][0].table,slots['dimensions'][0].column)==(target.table,target.column)
            and slots['normalized']==slots['dimensions'][0].matched_alias)
