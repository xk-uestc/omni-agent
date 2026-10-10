"""Typed, bounded cross-source dependency execution with real source bindings.

Plans contain tool arguments and references to earlier results, never raw SQL.
References determine the DAG; failed/missing/ambiguous evidence stops dependants.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from copy import deepcopy
from contextlib import nullcontext
from typing import Any

from .document_analysis import DocumentAnalyzer
from .formula_binding import FormulaBinder, ParameterEvidence
from .knowledge_store import SourceIntegrityError, SourceRevisionError
from .sql_evidence import aggregate_evidence, dimension_evidence
from .fusion_constraints import SourceConstraintError, VerifiedFormulaTarget, bind_source_constraints


class DependencyPlanError(ValueError):
    pass


class DependencyAgent:
    TOOLS = {'sql', 'search', 'search_fact', 'document_formula', 'document_cell', 'document_fact', 'calculate', 'policy_select', 'compare'}

    def __init__(self, sql_engine, knowledge_store):
        self.sql_engine = sql_engine
        self.knowledge_store = knowledge_store

    @staticmethod
    def references(value):
        if isinstance(value, dict):
            if 'ref' in value:
                if set(value) != {'ref', 'path'} or not isinstance(value['ref'], str) or not isinstance(value['path'], list) or len(value['path']) > 8:
                    raise DependencyPlanError('引用必须含 ref 和有界 path')
                return {value['ref']}
            return set().union(*(DependencyAgent.references(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(DependencyAgent.references(v) for v in value))
        return set()

    def validate(self, tasks):
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 16:
            raise DependencyPlanError('任务数必须为 1—16')
        by_id = {}
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {'id', 'tool', 'args'} or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,40}', str(task['id'])):
                raise DependencyPlanError('任务结构或 ID 非法')
            if task['id'] in by_id or task['tool'] not in self.TOOLS or not isinstance(task['args'], dict):
                raise DependencyPlanError('任务重复或工具未授权')
            if task['tool'] == 'search':
                from .search_scope import validate_search_args, SearchScopeError
                try:
                    validate_search_args(task['args'], self.knowledge_store)
                except SearchScopeError as exc:
                    raise DependencyPlanError(str(exc)) from exc
            by_id[task['id']] = task
        dependencies = {key: self.references(task['args']) for key, task in by_id.items()}
        if any(not refs <= by_id.keys() for refs in dependencies.values()):
            raise DependencyPlanError('引用了不存在的任务')
        ordered, visited, visiting = [], set(), set()
        def visit(key):
            if key in visiting:
                raise DependencyPlanError('依赖图存在循环')
            if key in visited:
                return
            visiting.add(key)
            for dependency in sorted(dependencies[key]):
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
            ordered.append(by_id[key])
        for key in by_id:
            visit(key)
        return ordered, dependencies

    @staticmethod
    def resolve(value, results):
        if isinstance(value, dict) and 'ref' in value:
            try:
                current = results[value['ref']]
                for key in value['path']:
                    if isinstance(key, int) and not isinstance(key, bool) and key >= 0 and isinstance(current, list):
                        current = current[key]
                    elif isinstance(key, str) and isinstance(current, dict):
                        current = current[key]
                    else:
                        raise KeyError(key)
                return current
            except (KeyError, IndexError, TypeError) as exc:
                raise DependencyPlanError('引用位置无可用结果') from exc
        if isinstance(value, dict):
            return {key: DependencyAgent.resolve(v, results) for key, v in value.items()}
        if isinstance(value, list):
            return [DependencyAgent.resolve(v, results) for v in value]
        return value

    @staticmethod
    def text(value):
        parts = value if isinstance(value, list) else [value]
        if any(not isinstance(p, (str, int, float)) or isinstance(p, bool) for p in parts):
            raise DependencyPlanError('查询文本只能引用字符串或数字')
        text = ' '.join(str(p) for p in parts)
        if not text.strip() or len(text) > 1000:
            raise DependencyPlanError('查询文本为空或过长')
        return text

    def run(self, tasks, *, on_event=None, original_question=None, source_constraints=None):
        # Server-bound reference normalization never mutates the caller graph.
        tasks = deepcopy(tasks)
        from .nl2sql.security import unsafe_request_reason
        questions = [original_question] if original_question is not None else []
        questions.extend(t.get('args', {}).get('question') for t in tasks
                         if isinstance(t, dict) and t.get('tool') == 'sql' and isinstance(t.get('args'), dict))
        if any(unsafe_request_reason(q) for q in questions):
            event = {'stage': 'request_safety', 'status': 'rejected', 'executed': False,
                     'failure_category': 'safety', 'error_code': 'read_only_query_required'}
            if on_event:
                on_event(dict(event))
            return {'status': 'clarification', 'results': {}, 'trace': [event],
                    'clarification': '整条请求包含写入或绕过规则意图，未执行任何子任务。',
                    'clarification_code': 'read_only_query_required', 'error_code': 'read_only_query_required',
                    'skipped_tasks': [t.get('id') for t in tasks if isinstance(t, dict)], 'edges': []}
        ordered, dependencies = self.validate(tasks)
        read_scope = getattr(self.sql_engine, 'consistent_reads', None)
        with read_scope() if read_scope else nullcontext():
            audit, initial_versions, formula_results = [], {}, {}
            search_bindings = {'source_ranges': [], 'bindings': []}
            try:
                if original_question is not None:
                    from .sql_document_binding import authorize_sql_document_search, normalize_sql_document_references
                    search_bindings = authorize_sql_document_search(original_question, tasks, self.sql_engine)
                    normalize_sql_document_references(search_bindings, tasks)
                    proofs = []
                    for task in tasks:
                        if task['tool'] != 'document_formula' or self.references(task['args']):
                            continue
                        try:
                            evidence = self.execute('document_formula', task['args'], task['args'], {})
                            formula_results[task['id']] = evidence
                            for document_id, digest in self._document_versions('document_formula', evidence):
                                if document_id in initial_versions and initial_versions[document_id] != digest:
                                    raise SourceRevisionError('公式来源发生版本变化')
                                initial_versions[document_id] = digest
                                proofs.append(VerifiedFormulaTarget(evidence['label'], document_id, digest, task['id']))
                            self._verify_versions(initial_versions)
                        except (ValueError, KeyError, TypeError, OSError) as exc:
                            raise SourceConstraintError('formula_target_evidence_unverified') from exc
                    source_constraints, audit = bind_source_constraints(
                        original_question, tasks, self.sql_engine, verified_formula_targets=tuple(proofs),
                        verified_document_search_ranges=tuple(search_bindings['source_ranges']))
                    formula_consumption = self._formula_consumption_contracts(
                        tasks, dependencies, audit, formula_results)
                else:
                    formula_consumption = {}
            except SourceConstraintError as exc:
                trace_id = hashlib.sha256(json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
                return {'status': 'clarification', 'trace_id': trace_id, 'results': {}, 'trace': [],
                        'clarification': str(exc), 'clarification_code': exc.code, 'error_code': exc.code,
                        'skipped_tasks': [task['id'] for task in ordered], 'edges': [],
                        'user_constraint_validation': {'status': 'unverified', 'error_code': exc.code}}
            result = self._run_ordered(tasks, ordered, dependencies, on_event=on_event,
                                       source_constraints=source_constraints or {}, original_question=original_question,
                                       initial_versions=initial_versions, search_bindings=search_bindings,
                                       formula_consumption=formula_consumption)
            result['user_constraint_validation'] = {'status': ('verified' if source_constraints else 'not_applicable')
                                                   if original_question is not None else 'verified' if source_constraints else 'not_provided',
                                                   'scope': 'explicit_server_bound_sql_source_clauses', 'bindings': audit}
            if search_bindings['bindings']:
                result['user_constraint_validation']['document_search_bindings'] = search_bindings['bindings']
            if result['status'] != 'ok' and (original_question is not None or source_constraints):
                result['user_constraint_validation']['status'] = 'incomplete'
            return result

    @staticmethod
    def _document_versions(tool, result):
        # Only provenance emitted by our document tools is considered. SQL
        # cell values that happen to contain source_uri/sha256 are plain data.
        if tool == 'search':
            return [(hit['metadata']['document_id'], hit['metadata']['source_sha256']) for hit in result['hits']]
        if tool in {'document_formula','document_cell','document_fact','policy_select','search_fact'}:
            items = result.get('sources', [result])
            sources = []
            for item in items:
                uri = item.get('source_uri', '')
                match = re.fullmatch(r'/api/v1/knowledge/documents/([A-Za-z0-9_.-]+)/original', uri)
                if match is None or not re.fullmatch(r'[a-f0-9]{64}', item.get('sha256', '')):
                    raise DependencyPlanError('文档工具未提供有效的原文件版本依据')
                sources.append((match.group(1), item['sha256']))
            return sources
        return []

    def _verify_versions(self, versions):
        for document_id, digest in versions.items():
            self.knowledge_store.verify_source(document_id, expected_sha256=digest)

    @staticmethod
    def _formula_consumption_contracts(tasks, dependencies, bindings, formula_results):
        targets = [binding['target_binding'] for binding in bindings if binding.get('target_binding')]
        if not targets:
            return {}
        def ancestors(task_id, visited=None):
            visited = set() if visited is None else visited
            for dependency in dependencies[task_id]:
                if dependency not in visited:
                    visited.add(dependency)
                    ancestors(dependency, visited)
            return visited
        calculations = [task for task in tasks if task['tool'] == 'calculate']
        terminals = [task for task in calculations
                     if not any(task['id'] in ancestors(other['id']) for other in calculations if other != task)]
        if not terminals:
            raise SourceConstraintError('formula_target_not_consumed')
        contracts, consumed_targets = {}, set()
        for task in terminals:
            ref = task['args'].get('formula')
            if not isinstance(ref, dict) or set(ref) != {'ref', 'path'} or ref['path'] != []:
                raise SourceConstraintError('formula_target_not_consumed')
            evidence = formula_results.get(ref['ref'])
            if evidence is None:
                raise SourceConstraintError('formula_target_not_consumed')
            matched = []
            for target in targets:
                if target['mode'] in {'verified_document_formula_exact_label',
                                      'verified_document_formula_catalog_target'}:
                    if (ref['ref'] in target.get('formula_task_ids', [target.get('task_id')])
                            and evidence.get('label') == target['label']
                            and evidence.get('sha256') == target['source_sha256']
                            and evidence.get('source_uri') == f"/api/v1/knowledge/documents/{target['document_id']}/original"):
                        matched.append(target)
                elif (target['mode'] == 'server_semantic_catalog_complete_target'
                      and ref['ref'] in target.get('formula_task_ids', ())
                      and evidence.get('label') in target['derived_labels']):
                    matched.append(target)
            if not matched:
                raise SourceConstraintError('formula_target_not_consumed')
            target_ids = {target.get('target_id') for target in matched}
            required_sql = {binding['task_id'] for binding in bindings
                            if (binding.get('target_binding') or {}).get('target_id') in target_ids}
            actual_sql = {node['id'] for node in tasks
                          if node['tool'] == 'sql' and node['id'] in ancestors(task['id'])}
            if not required_sql or actual_sql != required_sql:
                raise SourceConstraintError('formula_target_source_not_consumed')
            consumed_targets.update(target_ids)
            contracts[task['id']] = {'formula_task_id': ref['ref'], 'label': evidence['label'],
                                      'source_uri': evidence['source_uri'], 'sha256': evidence['sha256'],
                                      'expression': evidence['expression']}
        if {target.get('target_id') for target in targets} != consumed_targets:
            raise SourceConstraintError('formula_target_not_consumed')
        return contracts

    def _run_ordered(self, tasks, ordered, dependencies, *, on_event=None, source_constraints=None, original_question=None,
                     initial_versions=None, search_bindings=None, formula_consumption=None):
        results, trace = {}, []
        versions = dict(initial_versions or {})
        trace_id = hashlib.sha256(json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        for task in ordered:
            started = time.perf_counter()
            try:
                self._verify_versions(versions)
                args = self.resolve(task['args'], results)
                expected_formula = (formula_consumption or {}).get(task['id'])
                if expected_formula is not None:
                    ref = task['args'].get('formula', {})
                    actual_formula = args.get('formula', {})
                    if (ref != {'ref': expected_formula['formula_task_id'], 'path': []}
                            or not isinstance(actual_formula, dict)
                            or any(actual_formula.get(key) != expected_formula[key]
                                   for key in ('label', 'source_uri', 'sha256', 'expression'))):
                        raise SourceConstraintError('formula_target_not_consumed')
                search_validation = None
                if task['tool'] == 'search' and search_bindings and search_bindings['bindings']:
                    from .sql_document_binding import validate_sql_document_search
                    search_validation = validate_sql_document_search(
                        search_bindings, task=task, tasks=tasks, results=results, engine=self.sql_engine)
                required = (source_constraints or {}).get(task['id'])
                if task['tool'] == 'sql' and original_question is not None:
                    from .dynamic_source_binding import bind_dynamic_source_filters
                    required = bind_dynamic_source_filters(self, task=task, tasks=tasks, results=results,
                        required_intent=required, original_question=original_question)
                result = (self.execute(task['tool'], args, task['args'], results, original_question=original_question)
                          if task['tool'] == 'calculate' else
                          self.execute(task['tool'], args, task['args'], results, required_intent=required)
                          if required is not None else self.execute(task['tool'], args, task['args'], results))
                if search_validation is not None:
                    result['dependency_reference_validation'] = search_validation
                if expected_formula is not None:
                    result['target_formula_validation'] = {
                        'status': 'verified', 'consumed_task_id': expected_formula['formula_task_id'],
                        'label': expected_formula['label'], 'source_uri': expected_formula['source_uri'],
                        'sha256': expected_formula['sha256'],
                        'scope': 'actual_terminal_calculation_formula_and_bound_sql_ancestors',
                    }
                for document_id, digest in self._document_versions(task['tool'], result):
                    if document_id in versions and versions[document_id] != digest:
                        raise SourceRevisionError('同一资料在一个任务中出现了不同版本，请重新执行。')
                    versions[document_id] = digest
                self._verify_versions(versions)
                results[task['id']] = result
                event = {'trace_id': trace_id, 'task_id': task['id'], 'tool': task['tool'], 'dependencies': sorted(dependencies[task['id']]), 'status': 'complete', 'latency_ms': round((time.perf_counter()-started)*1000, 3)}
                trace.append(event)
                if on_event:
                    on_event(dict(event))
            except (ValueError, KeyError, TypeError, SyntaxError, OverflowError, OSError, sqlite3.DatabaseError) as exc:
                code = 'source_constraint_mismatch' if isinstance(exc,SourceConstraintError) else 'evidence_revision_changed' if isinstance(exc,SourceRevisionError) else 'evidence_integrity_failed' if isinstance(exc,SourceIntegrityError) else 'storage_unavailable' if isinstance(exc,(OSError,sqlite3.DatabaseError)) else 'tool_contract_failed'
                message = '数据源暂不可读取，请恢复文件或解除数据库锁后重新执行。' if code=='storage_unavailable' else str(exc)[:200]
                event = {'trace_id': trace_id, 'task_id': task['id'], 'tool': task['tool'], 'dependencies': sorted(dependencies[task['id']]), 'status': 'failed', 'error': message, 'error_code':code, 'latency_ms': round((time.perf_counter()-started)*1000, 3)}
                if isinstance(exc, SourceConstraintError):
                    event['source_constraint_code'] = exc.code
                    event['constraint_error_codes'] = list(getattr(exc, 'constraint_error_codes', ()))
                trace.append(event)
                if on_event:
                    on_event(dict(event))
                return {'status': 'incomplete', 'trace_id': trace_id, 'results': results, 'trace': trace, 'failed_task': task['id'], 'error': message,
                        'error_code':code,'skipped_tasks':[later['id'] for later in ordered[len(trace):]],
                        'edges':[{'from':dependency,'to':key} for key,refs in dependencies.items() for dependency in sorted(refs)]}
        return {'status': 'ok', 'trace_id': trace_id, 'results': results, 'trace': trace,
                'source_validation': {'status': 'verified', 'documents': versions,
                                      'scope': 'selected_original_hash_and_logical_version_not_semantic_truth'},
                'edges': [{'from': dependency, 'to': key} for key, refs in dependencies.items() for dependency in sorted(refs)]}

    def execute(self, tool, args, original_args, results, *, required_intent=None, original_question=None):
        if tool == 'policy_select':
            from .policy_evidence import select_policy
            if set(args) != {'document_id', 'as_of', 'label'}:
                raise DependencyPlanError('版本选择必须含文档、适用日期和政策要素')
            evidence = select_policy(self.knowledge_store.document(args['document_id']), as_of=args['as_of'], label=args['label'])
            if 'numeric_value' in evidence:
                evidence = {**evidence, 'value': evidence['numeric_value']}
            return evidence
        if tool == 'compare':
            if set(args) not in ({'left', 'right'}, {'left', 'right', 'operator'}):
                raise DependencyPlanError('证据比较必须有两个来源')
            operator = args.get('operator', 'eq')
            if operator not in {'eq', 'ne', 'lt', 'le', 'gt', 'ge'}:
                raise DependencyPlanError('比较运算符未受支持')
            for key in ('left', 'right'):
                ref = original_args[key]
                path = ref.get('path') if isinstance(ref, dict) else None
                source_result = results.get(ref.get('ref'), {}) if isinstance(ref, dict) else {}
                aggregate_ref = (isinstance(path, list) and len(path) == 5 and path[0] == 'aggregate_cells'
                    and all(isinstance(part, str) for part in path[1:4]) and type(path[4]) is int and path[4] >= 0
                    and 'provenance' in source_result and 'aggregate_cells' in source_result)
                if not isinstance(ref, dict) or ref.get('ref') not in results or not (path == [] or aggregate_ref):
                    raise DependencyPlanError('比较对象必须直接引用工具证据')
                calculated_ref=(path==[] and args[key]==source_result
                    and source_result.get('parameter_semantics_validation',{}).get('status')=='verified'
                    and source_result.get('unit_validation')=='validated'
                    and isinstance(source_result.get('formula_source'),str)
                    and isinstance(source_result.get('formula_locator'),str))
                if calculated_ref:
                    args[key]={**source_result,'source_uri':source_result['formula_source'],
                        'locator':source_result['formula_locator'],'unit':source_result['result_unit']}
                if not isinstance(args[key], dict) or not {'value', 'source_uri', 'locator'} <= args[key].keys():
                    raise DependencyPlanError('比较对象缺少可定位证据')
            left, right = args['left'], args['right']
            if left.get('evidence_type') == right.get('evidence_type') == 'policy':
                if (left.get('document_id'), left.get('label')) != (right.get('document_id'), right.get('label')):
                    raise DependencyPlanError('政策版本比较必须使用同一文档的同一政策要素')
            unit_left, unit_right = left.get('unit', 'unknown'), right.get('unit', 'unknown')
            values = (left['value'], right['value'])
            if unit_left != unit_right:
                raise DependencyPlanError('不同单位不能直接比较，请先完成单位换算')
            if any(isinstance(v, bool) or type(v) in (int, float) and not math.isfinite(v) for v in values):
                raise DependencyPlanError('比较不能使用布尔值或非有限数值')
            numeric = all(type(v) in (int, float) and math.isfinite(v) for v in values)
            if operator not in {'eq', 'ne'} and (not numeric or unit_left == 'unknown'):
                raise DependencyPlanError('大小比较必须使用单位明确的有限数值事实')
            matched = {'eq': lambda: values[0] == values[1], 'ne': lambda: values[0] != values[1],
                       'lt': lambda: values[0] < values[1], 'le': lambda: values[0] <= values[1],
                       'gt': lambda: values[0] > values[1], 'ge': lambda: values[0] >= values[1]}[operator]()
            return {'status': 'equal' if values[0] == values[1] else 'different', 'left': left, 'right': right,
                    'unit': unit_left, 'operator': operator, 'matched': matched,
                    'difference': values[0]-values[1] if numeric else None}
        if tool == 'sql':
            if set(args) != {'question'}:
                raise DependencyPlanError('SQL 工具只接受自然语言 question')
            result = (self.sql_engine.answer(self.text(args['question']), required_intent=required_intent)
                      if required_intent is not None else self.sql_engine.answer(self.text(args['question']))).to_dict()
            if result.get('clarification_code') == 'source_constraint_mismatch':
                error = SourceConstraintError('source_constraint_mismatch')
                audit = result.get('provenance', {}).get('source_constraint_validation', {})
                error.constraint_error_codes = tuple(
                    code for code in audit.get('error_codes', [])[:16]
                    if isinstance(code, str) and re.fullmatch(r'[a-z0-9_]{1,100}', code))
                raise error
            if result['status'] != 'ok' or not result['rows']:
                raise DependencyPlanError(result.get('clarification') or '结构化查询未产生可用结果')
            result['aggregate_cells'], ambiguous = aggregate_evidence(result)
            result['dimension_values'] = dimension_evidence(result)
            result['aggregate_cell_contract'] = {
                'address': ['table', 'column', 'function', 'row'],
                'source': 'actual_verified_plan_and_executed_rows',
                'ambiguous_addresses_not_exposed': ambiguous}
            return result
        if tool == 'search':
            from .search_scope import validate_search_args, verify_search_hits, SearchScopeError
            try:
                kwargs, scope = validate_search_args(args, self.knowledge_store, resolved=True)
                hits = self.knowledge_store.search(self.text(args['query']), top_k=4, **kwargs)
                verify_search_hits(hits, scope)
                if scope['document_id'] is not None:
                    self.knowledge_store.verify_source(scope['document_id'], expected_sha256=scope['source_sha256'])
            except SearchScopeError as exc:
                raise DependencyPlanError(str(exc)) from exc
            if not hits:
                raise DependencyPlanError('没有文档证据')
            return {'hits': [hit.to_dict() for hit in hits], 'search_scope': scope}
        if tool == 'search_fact':
            from .evidence_fact import extract_search_fact
            if set(args) != {'evidence', 'scope', 'label', 'unit'}:
                raise DependencyPlanError('检索事实定位参数非法')
            ref = original_args['evidence']
            if not isinstance(ref, dict) or ref.get('path') != [] or ref.get('ref') not in results:
                raise DependencyPlanError('事实必须直接引用前步检索证据，不能填入literal')
            return extract_search_fact(self.knowledge_store, args['evidence'],
                                       scope=args['scope'], label=args['label'], unit=args['unit'])
        if tool in {'document_formula', 'document_cell', 'document_fact'}:
            document = self.knowledge_store.document(args['document_id'])
            if tool == 'document_formula':
                if set(args) != {'document_id', 'label'}:
                    raise DependencyPlanError('公式定位参数非法')
                candidates = []
                for chunk in document['chunks']:
                    formulas = DocumentAnalyzer().analyze(chunk['text']).formulas
                    for formula in formulas:
                        if formula.label == args['label'] and formula.status != 'rejected':
                            candidates.append({'expression': formula.normalized_expression, 'parameters': list(formula.parameters), 'label': formula.label,
                                'source_uri': f'/api/v1/knowledge/documents/{document["document_id"]}/original', 'locator': chunk['source_locator'], 'chunk_id': chunk['chunk_id'], 'sha256': document['sha256']})
                if document['modality'] == 'pdf':
                    from .native_formula_pages import extract_cross_page_formulas
                    raw = self.knowledge_store.verify_source(document['document_id'], expected_sha256=document['sha256']).read_bytes()
                    cross_page = extract_cross_page_formulas(raw, label=args['label'], expected_sha256=document['sha256'])
                    candidates.extend({**item, 'source_uri':f'/api/v1/knowledge/documents/{document["document_id"]}/original',
                                       'chunk_id':None} for item in cross_page)
                    self.knowledge_store.verify_source(document['document_id'], expected_sha256=document['sha256'])
                unique = {item['expression']: item for item in candidates}
                if len(unique) != 1:
                    raise DependencyPlanError('公式缺失或存在多个冲突版本')
                selected = next(iter(unique.values()))
                contracts, declarations = {}, []
                declaration_pattern = re.compile(
                    r'^参数绑定\s*[:：]\s*(?P<parameter>[\w\u3400-\u9fff]{1,64})\s*=\s*'
                    r'(?P<function>SUM|AVG|MIN|MAX|COUNT|COUNT_DISTINCT)\s*\(\s*'
                    r'(?P<table>[\w\u3400-\u9fff]{1,64})\s*\.\s*'
                    r'(?P<column>[\w\u3400-\u9fff]{1,64})\s*\)\s*$', re.I)
                for chunk in document['chunks']:
                    for line in chunk['text'].splitlines():
                        if not re.match(r'^\s*参数绑定\s*[:：]', line):
                            continue
                        match = declaration_pattern.fullmatch(line.strip())
                        if match is None:
                            raise DependencyPlanError('文档参数绑定声明格式不完整')
                        name = match.group('parameter')
                        if name not in selected['parameters']:
                            continue
                        contract = {'table': match.group('table'), 'column': match.group('column'),
                                    'function': match.group('function').upper()}
                        if name in contracts and contracts[name] != contract:
                            raise DependencyPlanError('文档参数绑定声明存在冲突')
                        contracts[name] = contract
                        declarations.append({'parameter': name, 'quote': line.strip(),
                                             'quote_basis': 'normalized_extracted_document_chunk',
                                             'locator': chunk['source_locator'], 'sha256': document['sha256']})
                if contracts:
                    selected['parameter_contracts'] = contracts
                    selected['parameter_contract_sources'] = declarations
                if {'基准销售额', '目标增长率'} <= set(selected['parameters']):
                    text = '\n'.join(chunk['text'] for chunk in document['chunks'])
                    years = set(re.findall(r'(20\d{2})年(?:的)?目标增长率.{0,80}?基准为(20\d{2})年', text, re.S))
                    if len(years) > 1:
                        raise DependencyPlanError('预测公式存在冲突的适用年份')
                    if years:
                        target, base = next(iter(years))
                        selected['temporal_constraints'] = {'base_year': int(base), 'target_year': int(target)}
                return selected
            if tool == 'document_cell':
                from .excel_layout import literal_matches,query_literal
                if set(args) != {'document_id', 'where', 'column'} or not isinstance(args['where'], dict) or not args['where']:
                    raise DependencyPlanError('表格取值必须明确匹配条件和列')
                candidates = []
                seen_cells = set()
                for chunk in document['chunks']:
                    metadata = chunk['metadata']
                    cells = dict(zip(metadata.get('headers', []), metadata.get('values', [])))
                    if all(isinstance(cells.get(key), dict) and literal_matches(cells[key],value) for key, value in args['where'].items()) and args['column'] in cells:
                        cell = cells[args['column']]
                        if not isinstance(cell, dict) or query_literal(cell) is None:
                            raise DependencyPlanError('Excel目标单元格为空或被合并覆盖，不能作为数值输入')
                        if cell.get('formula'):
                            raise DependencyPlanError('未经重算验证的 Excel 公式不能作为数值输入')
                        if cell.get('error'):
                            raise DependencyPlanError('Excel目标单元格为错误值，不能作为数值输入')
                        for key in args['where']:
                            if cells[key].get('formula') or cells[key].get('error'):
                                raise SourceConstraintError('excel_selector_unverified')
                        physical=(chunk.get('sheet_name'),cell.get('coordinate'))
                        if physical[1] and physical in seen_cells:continue
                        seen_cells.add(physical)
                        unit = 'ratio' if '%' in str(cell.get('number_format', '')) else 'CNY' if '人民币元' in args['column'] else '小时' if '小时' in args['column'] else 'unknown'
                        if cell.get('declared_unit'):unit=cell['declared_unit']
                        candidates.append({'value': query_literal(cell), 'unit': unit, 'source_uri': f'/api/v1/knowledge/documents/{document["document_id"]}/original', 'locator': chunk['source_locator'] + '/column:' + args['column'], 'sha256': document['sha256'], 'matched_conditions': dict(args['where']),
                            'cell_coordinate':cell.get('coordinate'), 'merged_anchor':cell.get('merged_anchor'),
                            'condition_coordinates':{key:cells[key].get('merged_anchor') or cells[key].get('coordinate') for key in args['where']}})
                if len(candidates) != 1:
                    raise DependencyPlanError('表格取值缺失或歧义；必须明确唯一记录')
                return candidates[0]
            if set(args) != {'document_id', 'label'} or not isinstance(args['label'], str) or len(args['label']) > 50:
                raise DependencyPlanError('文档参数定位非法')
            candidates = []
            pattern = re.compile(r'^\s*' + re.escape(args['label']) + r'\s*[:：=＝]\s*([^\r\n]+)$', re.MULTILINE)
            for chunk in document['chunks']:
                for match in pattern.finditer(chunk['text']):
                    candidates.append({'value': match.group(1).strip(), 'source_uri': f'/api/v1/knowledge/documents/{document["document_id"]}/original', 'locator': chunk['source_locator'], 'sha256': document['sha256']})
            unique = {item['value']: item for item in candidates}
            if len(unique) != 1:
                raise DependencyPlanError('文档参数缺失或冲突')
            return next(iter(unique.values()))
        if tool == 'calculate':
            if set(args) != {'formula', 'parameters'} or not isinstance(args['formula'], dict) or not isinstance(args['parameters'], dict):
                raise DependencyPlanError('计算必须引用文档公式和参数')
            formula_reference = original_args['formula']
            if not isinstance(formula_reference, dict) or formula_reference.get('ref') not in results or formula_reference.get('path') != []:
                raise DependencyPlanError('公式必须直接引用已执行的文档定位结果')
            formula = args['formula']
            if not {'expression', 'source_uri', 'locator'} <= formula.keys():
                raise DependencyPlanError('公式没有有效文档来源')
            from .formula_parameter_binding import validate_formula_sql_parameters
            parameter_validation = validate_formula_sql_parameters(
                self.sql_engine, formula, original_args['parameters'], args['parameters'], results,
                original_question=original_question, knowledge=self.knowledge_store)
            temporal = formula.get('temporal_constraints')
            if temporal:
                base_ref = original_args['parameters'].get('基准销售额', {})
                growth_ref = original_args['parameters'].get('目标增长率', {})
                base_result = results.get(base_ref.get('ref'), {})
                growth_result = results.get(growth_ref.get('ref'), {})
                expected_window = [f"{temporal['base_year']}-01-01", f"{temporal['base_year'] + 1}-01-01"]
                actual_window = base_result.get('plan', {}).get('intent_audit', {}).get('time_range')
                if actual_window != expected_window:
                    raise DependencyPlanError('预测基准年份必须严格匹配文档规定的全年统计区间')
                # A literal scenario in the very same version of the formula
                # document inherits its unique source-declared target year.
                # Rates from other documents still need an explicit year cell.
                same_document_fact = (
                    growth_result.get('validation') == 'literal_scoped_numeric_fact_not_general_entailment'
                    and growth_result.get('source_uri') == formula.get('source_uri')
                    and growth_result.get('sha256') == formula.get('sha256')
                    and set(map(int, re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)', growth_result.get('quote', ''))))
                        <= {temporal['target_year']})
                if (growth_result.get('matched_conditions', {}).get('年份') != temporal['target_year']
                        and not same_document_fact):
                    raise DependencyPlanError('目标增长率适用年份与文档预测年份不一致')
            bindings = {}
            for name, value in args['parameters'].items():
                reference = original_args['parameters'][name]
                if not isinstance(reference, dict) or 'ref' not in reference:
                    raise DependencyPlanError('参数必须引用已执行工具结果，不能臆造数值')
                result = results[reference['ref']]
                if isinstance(value, dict) and {'value', 'source_uri', 'locator'} <= value.keys():
                    numeric, source, locator = value['value'], value['source_uri'], value['locator']
                    unit = value.get('unit', 'unknown')
                elif 'provenance' in result and 'rows' in result and len(reference['path']) == 3 and reference['path'][0] == 'rows':
                    numeric = value
                    source = 'sql://' + str(result['provenance']['query_hash'])
                    locator = '/'.join(str(x) for x in reference['path'])
                    # Single-metric plans do not populate plan.metrics. Use
                    # the same executed physical slot already certified by
                    # the parameter validator, rather than its display name.
                    proof=next(p for p in parameter_validation['bindings'] if p['parameter']==name)
                    cell=aggregate_evidence(result)[0][proof['table']][proof['column']][proof['function']][proof['row']]
                    unit=cell.get('unit','unknown')
                else:
                    raise DependencyPlanError('参数来源没有结构化单元格证据')
                if unit == 'unknown':
                    proof=next(p for p in parameter_validation['bindings'] if p['parameter']==name)
                    catalog=getattr(self.sql_engine,'metric_catalog',None)
                    declared={m.currency if m.unit=='currency' else m.unit
                        for m in catalog.sources.values()
                        if proof.get('source_type')=='sql' and
                        (m.table,m.column,m.function)==(proof.get('table'),proof.get('column'),proof.get('function'))} if catalog else set()
                    if len(declared)==1 and next(iter(declared)):
                        unit=next(iter(declared))
                if isinstance(numeric, bool) or not isinstance(numeric, (int, float)) or not math.isfinite(numeric):
                    raise DependencyPlanError('参数不是有限数字')
                bindings[name] = ParameterEvidence(numeric, source, locator, unit or 'unknown')
            calculated = FormulaBinder().calculate(formula['expression'], bindings, formula_source=formula['source_uri'], formula_locator=formula['locator'])
            calculated['parameter_semantics_validation'] = parameter_validation
            calculated['temporal_validation'] = {'status': 'verified', **temporal} if temporal else {'status': 'not_inferred'}
            if temporal:
                calculated['result_interpretation']='预测目标，不能当作实际销售额。'
            return calculated
        raise DependencyPlanError('未授权工具')
