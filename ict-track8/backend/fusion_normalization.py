"""Recorded, bounded restoration of whole-document evidence references.

This adapts specific model protocol mistakes, not user intent or tool results.
Execution and all existing evidence/source guards remain DependencyAgent's job.
Normalized success must not be reported as raw model protocol accuracy.
"""
from __future__ import annotations

from copy import deepcopy
import math

from .dependency_agent import DependencyAgent, DependencyPlanError


def _check_structure(tasks):
    """Reject non-JSON/cyclic/oversized structures with a bounded traversal."""
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 16:
        raise DependencyPlanError('规范化任务数必须为1至16')
    stack, nodes = [(tasks, 0)], 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > 5000 or depth > 20:
            raise DependencyPlanError('规范化任务结构超过有界限制')
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise DependencyPlanError('规范化对象字段必须为字符串')
            if 'ref' in value:
                if set(value) != {'ref', 'path'} or not isinstance(value['ref'], str) or not isinstance(value['path'], list):
                    raise DependencyPlanError('规范化引用必须含ref和path')
                if len(value['path']) > 8 or any(not isinstance(key, (str, int)) or isinstance(key, bool)
                                                or isinstance(key, int) and key < 0 for key in value['path']):
                    raise DependencyPlanError('规范化引用路径非法')
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise DependencyPlanError('规范化任务必须是JSON数据')
        elif isinstance(value, float) and not math.isfinite(value):
            raise DependencyPlanError('规范化任务不得包含非有限值')
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {'id', 'tool', 'args'}:
            raise DependencyPlanError('规范化任务字段非法')
        if not isinstance(task['id'], str) or not isinstance(task['tool'], str) or not isinstance(task['args'], dict):
            raise DependencyPlanError('规范化任务ID、工具或参数类型非法')
    # These methods inspect only protocol and DAG. They do not run any tool.
    DependencyAgent(None, None).validate(tasks)


def normalize_fusion_tasks(tasks):
    """Return ``(deep_copied_tasks, normalization_notes)`` without execution.

    Only an exact one-leaf projection at an explicitly typed argument is
    restored to the complete source tool result. Unknown refs/tools and bad
    DAGs reject; valid but unsupported projections stay unchanged.
    """
    _check_structure(tasks)
    normalized = deepcopy(tasks)
    source_tools = {task['id']: task['tool'] for task in normalized}
    notes = []

    def restore(task, reference, argument_path, allowed_sources, leaf):
        if (not isinstance(reference, dict) or set(reference) != {'ref', 'path'}
                or reference['path'] != [leaf] or source_tools.get(reference['ref']) not in allowed_sources):
            return reference
        notes.append({'task_id': task['id'], 'argument_path': argument_path,
                      'source_task_id': reference['ref'], 'source_tool': source_tools[reference['ref']],
                      'old_path': list(reference['path']), 'new_path': [],
                      'reason': 'restore_whole_document_evidence_without_changing_intent'})
        # Replace only this typed argument. deepcopy preserves aliases in
        # Python inputs; mutating the ref in place could otherwise alter an
        # unrelated SQL question sharing the same reference object.
        return {'ref': reference['ref'], 'path': []}

    for task in normalized:
        args = task['args']
        if task['tool'] in {'sql', 'search'}:
            argument = 'question' if task['tool'] == 'sql' else 'query'
            text = args.get(argument)
            def scalar_reference(reference, path):
                if (not isinstance(reference, dict) or set(reference) != {'ref', 'path'}
                        or reference['path'] != []
                        or source_tools.get(reference['ref']) != 'document_cell'):
                    return reference
                notes.append({'task_id': task['id'], 'argument_path': path,
                    'source_task_id': reference['ref'], 'source_tool': 'document_cell',
                    'old_path': [], 'new_path': ['value'],
                    'reason': 'project_cell_value_for_text_with_dependency_provenance_retained'})
                return {'ref': reference['ref'], 'path': ['value']}
            if isinstance(text, list):
                args[argument] = [scalar_reference(part, [argument, index]) for index, part in enumerate(text)]
            elif isinstance(text, dict):
                args[argument] = scalar_reference(text, [argument])
        if task['tool'] == 'calculate':
            if 'formula' in args:
                args['formula'] = restore(task, args['formula'], ['formula'], {'document_formula'}, 'expression')
            parameters = args.get('parameters')
            if isinstance(parameters, dict):
                for name, reference in parameters.items():
                    parameters[name] = restore(task, reference, ['parameters', name], {'document_cell'}, 'value')
        elif task['tool'] == 'compare':
            for side in ('left', 'right'):
                if side in args:
                    args[side] = restore(task, args[side], [side], {'policy_select', 'search_fact', 'document_cell', 'document_fact'}, 'value')
    return normalized, notes
