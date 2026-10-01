"""Source-bearing SQL aggregate cells addressed by verified schema semantics.

Presentation labels and formula variable names are deliberately not addresses.
Ambiguous aggregates sharing table/column/function expose no selectable cell.
"""
from __future__ import annotations

from collections import Counter


def aggregate_evidence(result):
    plan = result.get('plan', {})
    metrics = plan.get('metrics', [])
    if not metrics and not plan.get('derived_metrics'):
        # The legacy single-metric compiler records these verified fields
        # directly on the plan. Unit stays unknown unless actually declared.
        metrics = [{'id': 'single_metric', 'table': plan.get('metric_table') or plan.get('table'),
                    'column': plan.get('metric_column'), 'function': plan.get('metric_function'),
                    'label': plan.get('metric_label') or plan.get('metric_column'), 'unit': 'unknown'}]
    rows = result.get('rows', [])
    query_hash = result.get('provenance', {}).get('query_hash')
    if not isinstance(query_hash, str) or not query_hash or not isinstance(rows, list):
        return {}, []
    descriptors = []
    for metric in metrics:
        if (not isinstance(metric, dict) or not all(isinstance(metric.get(key), str) and metric[key]
                for key in ('table', 'column', 'function', 'label', 'id'))):
            continue
        descriptors.append(metric)
    counts = Counter((metric['table'], metric['column'], metric['function']) for metric in descriptors)
    cells, ambiguous = {}, []
    for metric in descriptors:
        address = (metric['table'], metric['column'], metric['function'])
        if counts[address] != 1:
            if list(address) not in ambiguous:
                ambiguous.append(list(address))
            continue
        label = metric['label']
        if any(not isinstance(row, dict) or label not in row for row in rows):
            continue
        unit = metric.get('currency') if metric.get('unit') == 'currency' else metric.get('unit', 'unknown')
        values = [{'value': row[label], 'source_uri': 'sql://'+query_hash,
                   'locator': f'rows/{index}/{label}', 'unit': unit or 'unknown',
                   'metric': {key: metric[key] for key in ('id', 'table', 'column', 'function', 'label')}}
                  for index, row in enumerate(rows)]
        table, column, function = address
        cells.setdefault(table, {}).setdefault(column, {})[function] = values
    return cells, ambiguous
