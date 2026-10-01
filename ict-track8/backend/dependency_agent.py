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
from typing import Any

from .document_analysis import DocumentAnalyzer
from .formula_binding import FormulaBinder, ParameterEvidence
from .knowledge_store import SourceIntegrityError


class DependencyPlanError(ValueError):
    pass


class DependencyAgent:
    TOOLS = {'sql', 'search', 'document_formula', 'document_cell', 'document_fact', 'calculate', 'policy_select', 'compare'}

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

    def run(self, tasks, *, on_event=None):
        ordered, dependencies = self.validate(tasks)
        results, trace = {}, []
        trace_id = hashlib.sha256(json.dumps(tasks, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        for task in ordered:
            started = time.perf_counter()
            try:
                args = self.resolve(task['args'], results)
                result = self.execute(task['tool'], args, task['args'], results)
                results[task['id']] = result
                event = {'trace_id': trace_id, 'task_id': task['id'], 'tool': task['tool'], 'dependencies': sorted(dependencies[task['id']]), 'status': 'complete', 'latency_ms': round((time.perf_counter()-started)*1000, 3)}
                trace.append(event)
                if on_event:
                    on_event(dict(event))
            except (ValueError, KeyError, TypeError, SyntaxError, OverflowError, OSError, sqlite3.DatabaseError) as exc:
                code = 'evidence_integrity_failed' if isinstance(exc,SourceIntegrityError) else 'storage_unavailable' if isinstance(exc,(OSError,sqlite3.DatabaseError)) else 'tool_contract_failed'
                message = '数据源暂不可读取，请恢复文件或解除数据库锁后重新执行。' if code=='storage_unavailable' else str(exc)[:200]
                event = {'trace_id': trace_id, 'task_id': task['id'], 'tool': task['tool'], 'dependencies': sorted(dependencies[task['id']]), 'status': 'failed', 'error': message, 'error_code':code, 'latency_ms': round((time.perf_counter()-started)*1000, 3)}
                trace.append(event)
                if on_event:
                    on_event(dict(event))
                return {'status': 'incomplete', 'trace_id': trace_id, 'results': results, 'trace': trace, 'failed_task': task['id'], 'error': message,
                        'error_code':code,'skipped_tasks':[later['id'] for later in ordered[len(trace):]],
                        'edges':[{'from':dependency,'to':key} for key,refs in dependencies.items() for dependency in sorted(refs)]}
        return {'status': 'ok', 'trace_id': trace_id, 'results': results, 'trace': trace, 'edges': [{'from': dependency, 'to': key} for key, refs in dependencies.items() for dependency in sorted(refs)]}

    def execute(self, tool, args, original_args, results):
        if tool == 'policy_select':
            from .policy_evidence import select_policy
            if set(args) != {'document_id', 'as_of', 'label'}:
                raise DependencyPlanError('版本选择必须含文档、适用日期和政策要素')
            return select_policy(self.knowledge_store.document(args['document_id']), as_of=args['as_of'], label=args['label'])
        if tool == 'compare':
            if set(args) != {'left', 'right'}:
                raise DependencyPlanError('证据比较必须有两个来源')
            for key in ('left', 'right'):
                ref = original_args[key]
                if not isinstance(ref, dict) or ref.get('path') != [] or ref.get('ref') not in results:
                    raise DependencyPlanError('比较对象必须直接引用工具证据')
                if not isinstance(args[key], dict) or not {'value', 'source_uri', 'locator'} <= args[key].keys():
                    raise DependencyPlanError('比较对象缺少可定位证据')
            left, right = args['left'], args['right']
            unit_left, unit_right = left.get('unit', 'unknown'), right.get('unit', 'unknown')
            values = (left['value'], right['value'])
            if unit_left != unit_right:
                raise DependencyPlanError('不同单位不能直接比较，请先完成单位换算')
            return {'status': 'equal' if values[0] == values[1] else 'different', 'left': left, 'right': right,
                    'unit': unit_left, 'difference': values[0]-values[1] if all(type(v) in (int, float) for v in values) else None}
        if tool == 'sql':
            if set(args) != {'question'}:
                raise DependencyPlanError('SQL 工具只接受自然语言 question')
            result = self.sql_engine.answer(self.text(args['question'])).to_dict()
            if result['status'] != 'ok' or not result['rows']:
                raise DependencyPlanError(result.get('clarification') or '结构化查询未产生可用结果')
            return result
        if tool == 'search':
            if set(args) != {'query'}:
                raise DependencyPlanError('检索工具只接受 query')
            hits = self.knowledge_store.search(self.text(args['query']), top_k=4)
            if not hits:
                raise DependencyPlanError('没有文档证据')
            return {'hits': [hit.to_dict() for hit in hits]}
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
                unique = {item['expression']: item for item in candidates}
                if len(unique) != 1:
                    raise DependencyPlanError('公式缺失或存在多个冲突版本')
                selected = next(iter(unique.values()))
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
                if set(args) != {'document_id', 'where', 'column'} or not isinstance(args['where'], dict) or not args['where']:
                    raise DependencyPlanError('表格取值必须明确匹配条件和列')
                candidates = []
                for chunk in document['chunks']:
                    metadata = chunk['metadata']
                    cells = dict(zip(metadata.get('headers', []), metadata.get('values', [])))
                    if all(key in cells and cells[key].get('raw_value') == value for key, value in args['where'].items()) and args['column'] in cells:
                        cell = cells[args['column']]
                        if cell.get('formula'):
                            raise DependencyPlanError('未经重算验证的 Excel 公式不能作为数值输入')
                        unit = 'ratio' if '%' in str(cell.get('number_format', '')) else 'CNY' if '人民币元' in args['column'] else '小时' if '小时' in args['column'] else 'unknown'
                        candidates.append({'value': cell.get('raw_value'), 'unit': unit, 'source_uri': f'/api/v1/knowledge/documents/{document["document_id"]}/original', 'locator': chunk['source_locator'] + '/column:' + args['column'], 'sha256': document['sha256'], 'matched_conditions': dict(args['where'])})
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
                if growth_result.get('matched_conditions', {}).get('年份') != temporal['target_year']:
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
                    metrics = result['plan'].get('metrics', [])
                    metric = next((m for m in metrics if m['label'] == reference['path'][2]), {})
                    unit = metric.get('currency') if metric.get('unit') == 'currency' else metric.get('unit', 'unknown')
                else:
                    raise DependencyPlanError('参数来源没有结构化单元格证据')
                if isinstance(numeric, bool) or not isinstance(numeric, (int, float)) or not math.isfinite(numeric):
                    raise DependencyPlanError('参数不是有限数字')
                bindings[name] = ParameterEvidence(numeric, source, locator, unit or 'unknown')
            calculated = FormulaBinder().calculate(formula['expression'], bindings, formula_source=formula['source_uri'], formula_locator=formula['locator'])
            calculated['temporal_validation'] = {'status': 'verified', **temporal} if temporal else {'status': 'not_inferred'}
            return calculated
        raise DependencyPlanError('未授权工具')
