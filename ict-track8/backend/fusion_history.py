"""Conservative server-owned fusion follow-up scopes, never model rewrites."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .fusion_constraints import SourceConstraintError
from .knowledge_store import SourceIntegrityError


_FOLLOWUP = re.compile(r'^(?:那么|那|改成|换成|再看|如果)|呢[？?]?$')
_RESET = re.compile(r'^(?:换个主题|换一个主题|换个问题|新问题)')


def resolve_fusion_followup(question, history, engine, knowledge):
    audit = {'mode': 'independent', 'actual_question': question}
    if _RESET.search(question) or not history or not _FOLLOWUP.search(question):
        return question, audit, None
    previous = history[-1]
    state = previous.state or {}
    if state.get('route') != 'fusion':
        return question, audit, None
    saved = state.get('fusion_context')
    if not saved or saved.get('status') != 'verified':
        raise SourceConstraintError('fusion_history_unverified')
    scope = saved['scope_question']
    documents = saved.get('documents', {})
    for document_id, digest in documents.items():
        try:
            knowledge.verify_source(document_id, expected_sha256=digest)
        except (SourceIntegrityError, KeyError, OSError) as exc:
            raise SourceConstraintError('fusion_history_source_changed') from exc
    current = engine.analyze_slots(question)
    previous_slots = engine.analyze_slots(scope)
    replacements = []
    grouped = {}
    for value in current['values']:
        if getattr(value, 'via', '') != 'exact':
            raise SourceConstraintError('fusion_followup_ambiguous')
        grouped.setdefault((value.table, value.column), []).append(value)
    remaining = question
    for key, values in grouped.items():
        old = [value for value in previous_slots['values'] if (value.table, value.column) == key]
        if len(values) != 1 or len({value.value for value in old}) != 1:
            raise SourceConstraintError('fusion_followup_ambiguous')
        old_value, new_value = old[0].span, values[0].span
        # Region/entity replacement applies to the whole explicitly shared
        # scope, including document parameters, while source IDs stay pinned.
        aliases = {link.matched_alias for link in (*previous_slots['metrics'], *previous_slots['dimensions'])}
        def replace_entity(match):
            prefix, suffix = scope[:match.start()], scope[match.end():]
            left_ok = (not prefix or prefix[-1] in '年中内里为是（(：:、，,。;； \n'
                       or prefix.endswith(('数据库', '数据表')))
            right_ok = (not suffix or suffix[0] in '0123456789的）)：:、，,。;； \n'
                        or any(suffix.startswith(alias) for alias in aliases if alias))
            if not left_ok or not right_ok:
                raise SourceConstraintError('fusion_followup_ambiguous')
            return new_value
        scope = re.sub(re.escape(old_value), replace_entity, scope)
        remaining = remaining.replace(new_value, '', 1)
        replacements.append({'slot': '.'.join(key), 'from': old_value, 'to': new_value})
    old_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?=年|增长)', scope))
    new_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?=年)', question))
    if new_years:
        # A target year and a baseline year are different source slots. Merely
        # naming two years in a short turn cannot authorize reassignment.
        if not new_years <= old_years:
            raise SourceConstraintError('fusion_followup_time_ambiguous')
        if len(old_years) > 1:
            baseline_years = {year for binding in saved.get('source_bindings', [])
                              for year in re.findall(r'(?:19|20)\d{2}(?=年)', binding['text'])}
            target_years = old_years - baseline_years
            baseline = re.findall(r'基准(?:还是|仍是|为|是)?((?:19|20)\d{2})年', question)
            target = re.findall(r'((?:19|20)\d{2})年(?:的)?(?:目标|预测)', question)
            if (len(baseline) + len(target) != len(re.findall(r'(?:19|20)\d{2}年', question))
                    or any(year not in baseline_years for year in baseline)
                    or any(year not in target_years for year in target)):
                raise SourceConstraintError('fusion_followup_time_ambiguous')
        for match in re.finditer(r'(?<!\d)(?:19|20)\d{2}年?', remaining):
            remaining = remaining.replace(match.group(), '', 1)
    # Only demonstrably redundant reference syntax may disappear. Unknown
    # metrics, exclusions, source changes, operators or new years clarify.
    if ('目标' in remaining and '目标' not in scope or '增长率' in remaining and '增长率' not in scope
            or '公式' in remaining and '公式' not in scope):
        raise SourceConstraintError('fusion_followup_unsupported')
    fillers = ('仍按同一公式', '按同一公式', '还是同一公式', '同一公式',
               '基准还是', '基准仍是', '增长率取该地区Excel', '增长率取该地区excel',
               '该地区Excel', '该地区excel', '的目标', '目标', '那么', '那', '呢',
               '年的', '年', '仍', '还是', '？', '?', '，', ',', '。', ' ', '地区')
    for word in sorted(fillers, key=len, reverse=True):
        remaining = remaining.replace(word, '')
    if remaining or not replacements and not new_years:
        raise SourceConstraintError('fusion_followup_unsupported')
    if len(scope) > 1000:
        raise SourceConstraintError('fusion_history_unverified')
    audit = {'mode': 'server_verified_fusion_followup', 'actual_question': question,
             'previous_actual_question': previous.question, 'base_scope_question': saved['scope_question'],
             'scope_question': scope, 'replacements': replacements, 'document_versions': documents,
             'preserved_temporal_scope': sorted(old_years)}
    inherited = deepcopy(saved)
    for contract in inherited.get('document_tasks', []):
        if contract['tool'] == 'document_cell':
            for column, value in contract['args']['where'].items():
                for replacement in replacements:
                    if value == replacement['from']:
                        contract['args']['where'][column] = replacement['to']
    return scope, audit, inherited


def verify_inherited_document_tasks(tasks, saved):
    if saved is None:
        return
    expected = saved.get('documents', {})
    actual = {task['args']['document_id'] for task in tasks if 'document_id' in task.get('args', {})}
    if not actual or actual != set(expected):
        raise SourceConstraintError('fusion_followup_source_changed')
    actual_contracts = [{'tool': task['tool'], 'args': task['args']} for task in tasks
                        if 'document_id' in task.get('args', {})]
    # IDs alone do not preserve the original formula or parameter selector.
    # A model may not switch 客单价 to another formula in the same file.
    canonical = lambda items: sorted(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items)
    if canonical(actual_contracts) != canonical(saved.get('document_tasks', [])):
        raise SourceConstraintError('fusion_followup_document_binding_changed')


def verified_fusion_context(scope, tasks, result):
    from .dependency_agent import DependencyAgent
    validation = result.get('user_constraint_validation', {})
    if result.get('status') != 'ok' or validation.get('status') != 'verified':
        return None
    documents = {}
    for task in tasks:
        for document_id, digest in DependencyAgent._document_versions(
                task['tool'], result['results'][task['id']]):
            documents[document_id] = digest
    if not documents or not validation.get('bindings'):
        return None
    return {'status': 'verified', 'scope_question': scope, 'documents': documents,
            'source_bindings': deepcopy(validation['bindings']),
            'document_tasks': deepcopy([{'tool': task['tool'], 'args': task['args']} for task in tasks
                                         if 'document_id' in task.get('args', {})])}
