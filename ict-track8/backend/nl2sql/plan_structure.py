"""Presentation-only canonicalization and bounded, value-redacted diagnostics."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import unicodedata

from .model_contract import ModelPlanError


def descriptor(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda v: {'type': type(v).__name__})
    return {'type': type(value).__name__, 'length': len(value) if isinstance(value, (str, list, dict)) else None,
            'sha256': hashlib.sha256(encoded.encode()).hexdigest(),
            'identifier_valid': value.isidentifier() if isinstance(value, str) else False}


def diagnose(payload, tables):
    """No filter values, labels, source text, credentials or arbitrary keys."""
    columns = {t.name: {c.name for c in t.columns} for t in tables}
    def field(item):
        if not isinstance(item, dict):
            return {'invalid_type': type(item).__name__}
        table = item.get('table')
        owner = table if isinstance(table, str) and table in columns else descriptor(table)
        column = item.get('column')
        native = column if isinstance(table, str) and table in columns and isinstance(column, str) and column in columns[table] else descriptor(column)
        result = {'table': owner, 'column': native}
        for key in ('id', 'label', 'value'):
            if key in item:
                result[key] = descriptor(item[key])
        for key in ('function', 'operator', 'transform'):
            if key in item:
                result[key] = item[key] if isinstance(item[key], str) and item[key] in {'SUM','AVG','MIN','MAX','COUNT','COUNT_DISTINCT',
                    '=','!=','>','>=','<','<=','LIKE','RANGE','BETWEEN','IN','NOT IN','raw','month','year'} else descriptor(item[key])
        if 'filters' in item:
            result['filters'] = [field(v) for v in item['filters'][:16]] if isinstance(item['filters'], list) else descriptor(item['filters'])
        return result
    if not isinstance(payload, dict):
        return {'payload': descriptor(payload)}
    result = {'payload': descriptor(payload), 'version': payload.get('version') if type(payload.get('version')) is int else None}
    for key in ('metrics', 'dimensions', 'filters', 'derived_metrics'):
        raw = payload.get(key, [])
        result[key] = [field(v) for v in raw[:16]] if isinstance(raw, list) else descriptor(raw)
    if isinstance(payload.get('metric'), dict):
        result['metric'] = field(payload['metric'])
    result['order_metric'] = descriptor(payload.get('order_metric'))
    result['output_metrics'] = descriptor(payload.get('output_metrics'))
    return result


def canonicalize(payload):
    """Change internal handles/display labels only, never semantic slots.

    Duplicate IDs are NOT repaired: their references are ambiguous. Valid
    catalogue IDs remain untouched. Invalid unique opaque IDs are rebound
    consistently through a bounded expression graph and output references.
    """
    if not isinstance(payload, dict) or payload.get('version') != 2:
        return payload, []
    if len(json.dumps(payload, ensure_ascii=False)) > 50000:
        raise ModelPlanError('模型计划超过大小限制')
    result = deepcopy(payload)
    metrics, derived, dimensions = (result.get(k, []) for k in ('metrics','derived_metrics','dimensions'))
    if any(not isinstance(v, list) or len(v) > 16 or any(not isinstance(i, dict) for i in v)
           for v in (metrics, derived, dimensions)):
        return result, []
    items = metrics + derived
    ids = [v.get('id') for v in items]
    if any(not isinstance(v, str) or not v or len(v) > 128
           or any(unicodedata.category(c) == 'Cc' for c in v) for v in ids):
        raise ModelPlanError('指标 ID 无效')
    if len(ids) != len(set(ids)):
        raise ModelPlanError('指标 ID 重复，引用无法无歧义绑定')
    mapping, changes, occupied = {}, [], set(ids)
    for index, item in enumerate(items):
        old = item['id']
        if old.isidentifier():
            continue
        counter = 0
        new = f'metric_{index}'
        while new in occupied:
            counter += 1
            new = f'metric_{index}_{counter}'
        occupied.add(new); mapping[old] = new; item['id'] = new
        changes.append({'kind':'logical_id', 'before':descriptor(old), 'after':new})
    nodes = 0
    def expression(node, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 128 or depth > 12:
            raise ModelPlanError('表达式结构超过预算')
        if not isinstance(node, dict):
            return
        if isinstance(node.get('ref'), str):
            node['ref'] = mapping.get(node['ref'], node['ref'])
        for key in ('left','right'):
            if key in node:
                expression(node[key], depth+1)
    for item in derived:
        expression(item.get('expression'))
    if isinstance(result.get('order_metric'), str):
        result['order_metric'] = mapping.get(result['order_metric'], result['order_metric'])
    if isinstance(result.get('output_metrics'), list):
        result['output_metrics'] = [mapping.get(v, v) if isinstance(v, str) else v for v in result['output_metrics']]
    occupied_labels = set()
    for index, item in enumerate(dimensions + items):
        label = item.get('label')
        if not isinstance(label, str) or not 1 <= len(label) <= 80:
            continue  # Validator rejects missing/invalid labels.
        if label in occupied_labels:
            suffix = 1
            new = f'{label[:64]} [{suffix}]'
            while new in occupied_labels:
                suffix += 1
                new = f'{label[:64]} [{suffix}]'
            item['label'] = new
            changes.append({'kind':'display_label', 'item_index':index, 'before':descriptor(label), 'after':descriptor(new)})
            label = new
        occupied_labels.add(label)
    return result, changes
