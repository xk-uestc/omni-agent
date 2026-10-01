"""Bounded explicit user operations, independent of benchmark IDs or answers."""
from __future__ import annotations

import re
from datetime import date


def requested_operations(question):
    dates = list(dict.fromkeys(re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)', question)))
    dates.extend(f'{int(year):04d}-{int(month):02d}-{int(day):02d}'
                 for year, month, day in re.findall(r'(\d{4})年(\d{1,2})月(\d{1,2})日', question))
    dates = list(dict.fromkeys(dates))
    valid = []
    for value in dates:
        try:
            date.fromisoformat(value)
            valid.append(value)
        except ValueError:
            pass
    version_comparison = (len(valid) >= 2 and bool(re.search(r'政策|版本|生效', question))
                          and bool(re.search(r'比较|对比|变化|差异|区别|不同', question)))
    return {'version_comparison_dates': valid if version_comparison else []}


def completion_errors(requirements, route, tasks):
    dates = requirements['version_comparison_dates']
    if not dates:
        return []
    if route != 'fusion':
        return ['explicit_version_comparison_requires_fusion']
    if not isinstance(tasks, list):
        return ['explicit_version_comparison_requires_policy_selection_and_comparison']
    selected = {}
    identities = {}
    for task in tasks:
        if isinstance(task, dict) and task.get('tool') == 'policy_select' and isinstance(task.get('args'), dict):
            if task['args'].get('as_of') in dates:
                selected[task.get('id')] = task['args']['as_of']
                identities[task.get('id')] = (task['args'].get('document_id'), task['args'].get('label'))
    if set(selected.values()) != set(dates):
        return ['requested_effective_dates_not_selected']
    compared = set()
    compared_identities = set()
    for task in tasks:
        if not isinstance(task, dict) or task.get('tool') != 'compare' or not isinstance(task.get('args'), dict):
            continue
        args = task['args']
        left, right = args.get('left'), args.get('right')
        if (isinstance(left, dict) and isinstance(right, dict) and left.get('ref') in selected
                and right.get('ref') in selected and selected[left['ref']] != selected[right['ref']]):
            identity = identities[left['ref']]
            if (identity != identities[right['ref']] or not all(isinstance(part, str) and part for part in identity)):
                return ['version_comparison_requires_same_policy_element']
            if left.get('path') != [] or right.get('path') != []:
                return ['version_comparison_requires_complete_policy_evidence']
            compared_identities.add(identity)
            compared.update((selected[left['ref']], selected[right['ref']]))
    if len(compared_identities) > 1:
        return ['version_comparison_requires_same_policy_element']
    return [] if compared == set(dates) else ['selected_versions_not_actually_compared']
