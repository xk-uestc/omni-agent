"""Conservative server-owned fusion follow-up scopes, never model rewrites."""
from __future__ import annotations

from copy import deepcopy
import json
import re

from .fusion_constraints import SourceConstraintError
from .knowledge_store import SourceIntegrityError


_FOLLOWUP = re.compile(r'^(?:那么|那|改成|换成|再看|如果|同样算法|相同算法)|呢[？?]?$')
_RESET = re.compile(r'^(?:换个主题|换一个主题|换个问题|新问题)')
_INHERITED_REFERENCE = re.compile(r'同一|同样|照旧|仍按|还是同|上(?:一)?次|刚才|之前|上述|'
                                  r'该|这个|此(?:地区|客户|产品|公式|文档)|基准(?:还是|仍是)|增长率取')


def _self_contained_sql(question, engine):
    """Prove fresh SQL scope before treating conversational 那/呢 as context.

    A metric-only fragment still depends on history. An explicit temporal
    scope (or a fully specified grouping request) plus a server-verified plan
    does not acquire old documents merely because it ends with 呢.
    """
    if _INHERITED_REFERENCE.search(question):
        return False
    slots = engine.analyze_slots(question)
    if not slots.get('metrics'):
        return False
    if not slots.get('time_spans') and not (slots.get('dimensions') and re.search(r'各|按|每个|分别|分组', question)):
        return False
    required = engine.extract_required_intent(question)
    return (not required.clarification and not required.coverage.get('unresolved')
            and bool(required.metrics or required.metric_column and required.metric_function))


def resolve_fusion_followup(question, history, engine, knowledge):
    audit = {'mode': 'independent', 'actual_question': question}
    if history and not _RESET.search(question):
        from .fusion_parameter_followup import resolve_parameter_operation
        operation=resolve_parameter_operation(question,(history[-1].state or {}).get('fusion_context'),engine,knowledge)
        if operation:return operation
    if _RESET.search(question) or not history or not _FOLLOWUP.search(question):
        return question, audit, None
    previous = history[-1]
    state = previous.state or {}
    pending = state.get('pending_fusion_scope')
    recovering = pending is not None
    if state.get('route') != 'fusion' and not recovering:
        return question, audit, None
    if (state.get('route')=='fusion' and not state.get('fusion_context') and not recovering
            and not engine.analyze_slots(question)['metrics']):
        from .unified_routing import source_hint
        if source_hint(question,engine)=='document':
            return question,{**audit,'reason':'independent_document_question_after_document_workflow'},None
    if _self_contained_sql(question, engine):
        audit['reason'] = 'server_verified_self_contained_sql'
        return question, audit, None
    if recovering:
        # A planning/execution failure still has a literal user request. It
        # supplies text for conservative slot replacement, never evidence,
        # document contracts, SQL defaults or an earlier successful turn.
        if (not isinstance(pending, dict) or set(pending) != {'status', 'scope_question'}
                or pending.get('status') != 'user_text_only'
                or not isinstance(pending.get('scope_question'), str)
                or not 1 <= len(pending['scope_question']) <= 1000
                or pending['scope_question'] != previous.effective_question):
            raise SourceConstraintError('fusion_history_unverified')
        saved = {'scope_question': pending['scope_question'], 'documents': {}, 'source_bindings': []}
    else:
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
                       or prefix.endswith(('数据库', '数据表','算出','计算','核算')))
            right_ok = (not suffix or suffix[0] in '0123456789的）)：:、，,。;； \n'
                        or any(suffix.startswith(alias) for alias in aliases if alias))
            if not left_ok or not right_ok:
                raise SourceConstraintError('fusion_followup_ambiguous')
            return new_value
        scope = re.sub(re.escape(old_value), replace_entity, scope)
        remaining = remaining.replace(new_value, '', 1)
        replacements.append({'slot': '.'.join(key), 'from': old_value, 'to': new_value})
    metric_aliases={link.matched_alias for link in previous_slots['metrics']}
    year_suffix='年|增长|目标'+''.join('|'+re.escape(alias) for alias in metric_aliases)
    old_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?='+year_suffix+')', scope))
    new_years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?='+year_suffix+')', question))
    if new_years:
        # A target year and a baseline year are different source slots. Merely
        # naming two years in a short turn cannot authorize reassignment.
        if not new_years <= old_years:
            raise SourceConstraintError('fusion_followup_time_ambiguous')
        if len(old_years) > 1:
            if recovering:
                # No executed bindings exist to distinguish baseline and
                # target slots. Their assignment cannot be guessed.
                raise SourceConstraintError('fusion_followup_time_ambiguous')
            baseline_years = {year for binding in saved.get('source_bindings', [])
                              for year in re.findall(r'(?:19|20)\d{2}(?=年)', binding['text'])}
            target_years = old_years - baseline_years
            baseline = re.findall(r'基准(?:还是|仍是|为|是)?((?:19|20)\d{2})年', question)
            reverse=re.fullmatch(r'.*?(?:继续)?用((?:19|20)\d{2})年?(.+?)为基准(?:[，,].*)?',question)
            if reverse and reverse[2] in metric_aliases:baseline.append(reverse[1])
            target = re.findall(r'((?:19|20)\d{2})年(?:的)?(?:目标|预测)', question)
            if (len(baseline) + len(target) != len(re.findall(r'(?:19|20)\d{2}(?='+year_suffix+')', question))
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
    # Fully covered same-algorithm wording may confirm a pinned source and
    # metric. Source switches and new conditions remain outside this grammar.
    remaining=re.sub(r'^(?:同样|相同)算法(?:改成|换成)', '',remaining)
    remaining=re.sub(r'(?:继续)?用(?:年)?('+ '|'.join(re.escape(a) for a in metric_aliases)+r')为基准','',remaining) if metric_aliases else remaining
    for identifier in documents:
        title=knowledge.document(identifier)['title']
        remaining=re.sub(r'(?:并)?(?:使用|用)'+re.escape(title)+r'对应增长率','',remaining)
    for word in sorted(fillers, key=len, reverse=True):
        remaining = remaining.replace(word, '')
    if remaining or not replacements and not new_years:
        raise SourceConstraintError('fusion_followup_unsupported')
    if len(scope) > 1000:
        raise SourceConstraintError('fusion_history_unverified')
    audit = {'mode': ('server_resolved_pending_fusion_scope' if recovering else 'server_verified_fusion_followup'),
             'actual_question': question,
             'previous_actual_question': previous.question, 'base_scope_question': saved['scope_question'],
             'scope_question': scope, 'replacements': replacements, 'document_versions': documents,
             'preserved_temporal_scope': sorted(old_years)}
    if recovering:
        audit['verification'] = 'user_text_slot_replacement_only_requires_fresh_source_validation'
        return scope, audit, None
    inherited = deepcopy(saved)
    record=saved.get('task_template')
    if record is not None:
        from .conversation_comparison import unseal,ComparisonError
        try:
            template=unseal(record)
            if any(template.get(key)!=saved.get(key) for key in ('scope_question','documents','source_bindings','document_tasks','formula_contracts')):
                raise SourceConstraintError('fusion_history_unverified')
            graph=deepcopy(template['tasks'])
            for task in graph:
                if task['tool']=='sql' and isinstance(task['args'].get('question'),str):
                    for replacement in replacements:
                        task['args']['question']=task['args']['question'].replace(replacement['from'],replacement['to'])
                if task['tool']=='document_cell':
                    for column,value in task['args']['where'].items():
                        for replacement in replacements:
                            if value==replacement['from']:task['args']['where'][column]=replacement['to']
            inherited['resolved_tasks']=graph
        except (ComparisonError,KeyError,TypeError) as exc:
            raise SourceConstraintError('fusion_history_unverified') from exc
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
    if saved.get('parameter_operation'):
        actual={task['args']['document_id'] for task in tasks if 'document_id' in task.get('args',{})}
        formulas=[{'tool':task['tool'],'args':task['args']} for task in tasks if task['tool']=='document_formula']
        canonical=lambda items:sorted(json.dumps(item,ensure_ascii=False,sort_keys=True) for item in items)
        if (not set(saved['required_documents'])<=actual<=set(saved['documents'])
                or canonical(formulas)!=canonical(saved['formula_tasks'])
                or any(task['tool']=='search' and 'document_id' not in task['args'] for task in tasks)
                or saved.get('parameter_source_id') and any(task['tool'] in {'search','document_cell','document_fact'}
                    and task['args'].get('document_id')!=saved['parameter_source_id'] for task in tasks)):
            raise SourceConstraintError('fusion_followup_document_binding_changed')
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
    saved={'status': 'verified', 'scope_question': scope, 'documents': documents,
            'source_bindings': deepcopy(validation['bindings']),
            'document_tasks': deepcopy([{'tool': task['tool'], 'args': task['args']} for task in tasks
                                         if 'document_id' in task.get('args', {})])}
    saved['formula_contracts']=[{**task['args'],'temporal_constraints':deepcopy(result['results'][task['id']].get('temporal_constraints',{}))}
        for task in tasks if task['tool']=='document_formula']
    from .conversation_comparison import seal
    saved['task_template']=seal({**{key:saved[key] for key in ('scope_question','documents','source_bindings','document_tasks','formula_contracts')},
        'tasks':deepcopy(tasks)})
    return saved
