"""Conservative SQL history scopes, checked before model intent planning."""
from __future__ import annotations

import json
import re


_FOLLOWUP = re.compile(r'^(?:那么|那|改成|换成|再看)|呢[？?]?$')
_UNSAFE = re.compile(r'排除|不含|不包括|除了|大于|小于|超过|不足|至少|至多|不要|但|或者|如果|同一|之前|刚才|上次')


def _filters(items):
    canonical = []
    for item in items:
        operator, value = item.get('operator', '').upper(), item.get('value')
        if operator == '<>':
            operator = '!='
        if operator in {'IN', 'NOT IN'} and isinstance(value, list):
            if len(value) == 1:
                operator, value = ('=' if operator == 'IN' else '!='), value[0]
            else:
                value = sorted(value, key=lambda entry: json.dumps(entry, sort_keys=True))
        canonical.append(json.dumps({'table': item.get('table'), 'column': item.get('column'),
                                      'operator': operator, 'value': value}, ensure_ascii=False, sort_keys=True))
    return sorted(set(canonical))


def _metrics(items):
    return sorted((item.get('table'), item.get('column'), item.get('function')) for item in items)


def _physical_metrics(plan):
    if plan['metrics']:
        return _metrics(plan['metrics'])
    return [(plan.get('metric_table') or plan.get('table'), plan.get('metric_column'), plan.get('metric_function'))]


def _saved_metrics_match(state, required, engine):
    """Legacy/v2 equality is physical; catalog metadata remains authoritative."""
    saved = state.get('metrics', [])
    if not saved:
        # Historical legacy state serializes no v2 metrics. Its successful
        # server-owned scope remains independently parsed, never model text.
        return not required['metrics']
    if _metrics(saved) != _physical_metrics(required):
        return False
    catalog = getattr(engine, 'metric_catalog', None)
    for metric in saved:
        identity = (metric.get('table'), metric.get('column'), metric.get('function'))
        expected = [entry for entry in required['metrics']
                    if (entry['table'], entry['column'], entry['function']) == identity]
        if not expected and catalog:
            expected = [vars(entry) for entry in catalog.sources.values()
                        if (entry.table, entry.column, entry.function) == identity]
        if len(expected) > 1:
            return False
        contract = expected[0] if expected else {'unit': 'unknown', 'currency': None, 'missing': 'null', 'filters': []}
        if (metric.get('missing', 'null') != contract.get('missing', 'null')
                or metric.get('unit', 'unknown') != contract.get('unit', 'unknown')
                or metric.get('currency') != contract.get('currency')
                or _filters(metric.get('filters', [])) != _filters([
                    item.to_dict() if hasattr(item, 'to_dict') else item for item in contract.get('filters', [])])):
            return False
    return True


def _complete(plan):
    return (not plan.clarification and not plan.coverage.get('unresolved')
            and bool(plan.metrics or plan.metric_column and plan.metric_function))


def resolve_sql_followup_scope(question, history, engine):
    """Return a proven slot rewrite or the unchanged question plus its audit.

    This does not choose a route, execute SQL or declare a model plan valid.
    Unknown/new constraints remain the model/planner's clarification problem.
    """
    unchanged = {'mode': 'independent', 'actual_question': question}
    if not isinstance(question, str) or len(question) > 80 or not history or not _FOLLOWUP.search(question):
        return question, unchanged
    previous = history[-1]
    state = previous.state or {}
    base = previous.effective_question
    if (state.get('route') != 'sql' or 'pending_question' not in state
            or state['pending_question'] is not None or state.get('clarification_code')
            or not isinstance(base, str) or not base or _UNSAFE.search(question)):
        return question, unchanged
    try:
        current_slots = engine.analyze_slots(question)
        # Complete questions start a fresh scope, even with 那/呢 punctuation.
        if current_slots['metrics'] and (current_slots['time_spans'] or current_slots['values']):
            return question, {**unchanged, 'reason': 'self_contained_sql'}
        if (len(current_slots['metrics']) > 1 or len(current_slots['time_spans']) > 1
                or not any(current_slots[key] for key in ('metrics', 'values', 'time_spans', 'dimensions'))):
            return question, unchanged
        previous_slots = engine.analyze_slots(base)
        old_value_keys = {(value.table, value.column) for value in previous_slots['values']}
        if any(getattr(value, 'via', '') != 'exact' or (value.table, value.column) not in old_value_keys
               for value in current_slots['values']):
            return question, unchanged
        required_base = engine.extract_required_intent(base)
        if not _complete(required_base):
            return question, unchanged
        base_plan = required_base.to_dict()
        # Compare saved executed scope with the independent original scope;
        # a clarification or a different old query is never successful history.
        if (_filters(state.get('filters', [])) != _filters(base_plan['filters'])
                or not _saved_metrics_match(state, base_plan, engine)
                or state.get('dimensions', []) != base_plan['dimensions']):
            return question, unchanged
        scope, resolution = engine.contextualize(base, question)
        if (resolution.get('mode') != 'merged' or resolution.get('appended')
                or not resolution.get('replaced') or scope == base):
            return question, unchanged
        required = engine.extract_required_intent(scope)
        if not _complete(required):
            return question, unchanged
        new_plan = required.to_dict()
        changed_slots = {item['slot'] for item in resolution['replaced']}
        allowed_columns = {tuple(slot.split('.', 1)) for slot in changed_slots if '.' in slot}
        if 'time' in changed_slots:
            # Identify temporal filters from independently parsed source text;
            # unrelated exclusions/customer filters cannot disappear.
            spans = previous_slots['time_spans'] + current_slots['time_spans']
            for item in base_plan['filters'] + new_plan['filters']:
                if any(span and span in item.get('source_text', '') for span in spans):
                    allowed_columns.add((item.get('table'), item['column']))
        def retained_filters(plan):
            return _filters([item for item in plan['filters']
                             if (item.get('table'), item['column']) not in allowed_columns])
        if (retained_filters(base_plan) != retained_filters(new_plan)
                or base_plan['dimensions'] != new_plan['dimensions']
                or 'metric' not in changed_slots and _physical_metrics(base_plan) != _physical_metrics(new_plan)
                or 'metric' not in changed_slots and
                (required_base.metric_column, required_base.metric_function) != (required.metric_column, required.metric_function)):
            return question, unchanged
    except (ValueError, KeyError, TypeError, AttributeError):
        return question, unchanged
    return scope, {'mode': 'server_verified_sql_followup', 'actual_question': question,
                   'previous_actual_question': previous.question, 'base_scope_question': base,
                   'scope_question': scope, 'replacements': resolution['replaced'],
                   'verification': 'independent_rule_scope_and_saved_sql_slots'}
