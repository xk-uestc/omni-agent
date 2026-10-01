"""Static text-slot types; navigation projections are never fact evidence.

This inspects declared tool output shapes without resolving or executing refs.
Known scalar projections retain the original DAG and execution-time guards.
Unknown paths and nested text arrays fail closed instead of stringifying data.
"""
from __future__ import annotations

import math
from dataclasses import fields, is_dataclass
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from .nl2sql.models import QueryPlan


_SCALAR = 'string|number|null'
_JSON_SCALAR = 'string|number|boolean|null'
_LOCATION = {'source_uri': 'string', 'locator': 'string'}
_METRIC = {key: 'string' for key in ('id', 'table', 'column', 'function', 'label')}
_PARAMETER = {'value': 'number', 'unit': 'string', **_LOCATION}
_AGGREGATE = {'value': _SCALAR, 'unit': 'string', 'metric': _METRIC, **_LOCATION}
_CELL = {'value': _JSON_SCALAR, 'unit': 'string', 'sha256': 'string',
         'matched_conditions': {'$map': _JSON_SCALAR}, **_LOCATION}
_FACT = {'value': 'string', 'sha256': 'string', **_LOCATION}
_POLICY = {
    'value': 'string|number', 'raw_value': 'string', 'numeric_value': 'number',
    'unit': 'string', 'text': 'string', 'document_id': 'string', 'label': 'string',
    'evidence_type': 'string', 'valid_from': 'string', 'valid_to': 'string|null',
    'as_of': 'string', 'sha256': 'string', **_LOCATION,
}
_SEARCH_FACT_SOURCE = {
    'value': 'number', 'unit': 'string', 'chunk_id': 'string', 'sha256': 'string',
    'document_id': 'string', 'quote': 'string', **_LOCATION,
    'label_binding': {'method': 'string', 'requested_label': 'string',
                      'literal_label': 'string', 'normalized_clause_start': 'number',
                      'normalized_clause_end': 'number'},
}
_SEARCH_FACT = {**_SEARCH_FACT_SOURCE, 'scope': 'string', 'label': 'string',
                'sources': ('array', _SEARCH_FACT_SOURCE), 'validation': 'string'}
_CELL_METADATA = {'raw_value': _JSON_SCALAR, 'cached_value': _JSON_SCALAR,
                  'number_format': 'string', 'formula': 'string',
                  'cache_status': 'string', 'error': 'string'}
_SEARCH_METADATA = {
    **{key: 'string' for key in (
        'document_id', 'original_document_id', 'chunk_id', 'source_locator',
        'raw_text', 'raw_page_text', 'source_sha256', 'table_name', 'style',
        'retrieval_channel', 'text_normalization', 'embedding_model',
        'page_scope_method', 'evidence_role', 'source_row_cells_sha256',
        'header_decision')},
    'sheet_name': 'string|null', 'ocr_status': 'string|null',
    **{key: 'number|null' for key in (
        'page_no', 'row_start', 'row_end', 'part_no', 'source_row_index', 'header_row')},
    **{key: 'number' for key in (
        'quality', 'part_count', 'bm25_raw', 'bm25_relative', 'bm25_rank',
        'dense_cosine', 'dense_rank', 'rrf_rank', 'rrf_score', 'ranking_score',
        'anchor_match_count', 'page_scope_coverage', 'header_confidence')},
    **{key: ('array', 'string') for key in (
        'title_path', 'warnings', 'headers', 'source_headers', 'identifier_anchors',
        'hidden_columns', 'merged_ranges', 'retrieval_channels', 'ocr_selected_transforms')},
    'hidden_rows': ('array', 'number'), 'hidden_sheet': 'boolean',
    'values': ('array', ('union', _JSON_SCALAR, _CELL_METADATA)),
    'source_row_cells': ('array', ('union', _JSON_SCALAR, _CELL_METADATA)),
    'retrieval_language_policy': {'query_language': 'string',
                                  'encoder_languages': ('array', 'string'), 'strategy': 'string'},
    'question_shape_navigation': {'method': 'string', 'body_affinity': 'number'},
    'coordinate_evidence': {
        'bbox_status': 'string', 'layout_block_ids': ('array', 'number'),
        **{key: ('array', 'number') for key in (
            'source_bbox_pdf_user_pt', 'source_bbox_fitz_unrotated_pt',
            'page_bbox_pdf_user_pt', 'source_bbox_fitz_display_pt', 'source_bbox_render_px')},
        'page_geometry': {'rotation_degrees': 'number', 'rotation_source': 'string'},
    },
    'evidence_selection': {'method': 'string', 'semantic_sufficiency': 'string',
                           'new_lexical_facets': ('array', 'string'), 'body_sha256': 'string',
                           'utility': 'number', 'uncovered_lexical_facets': ('array', 'string'),
                           'selection_budget_exhausted': 'boolean'},
}


def _declared_shape(annotation):
    """Use QueryPlan's declared dataclass fields; Any stays unknown."""
    if annotation is Any:
        return 'unknown'
    if annotation in {str, int, float, bool, type(None)}:
        return {str: 'string', int: 'number', float: 'number',
                bool: 'boolean', type(None): 'null'}[annotation]
    origin, args = get_origin(annotation), get_args(annotation)
    if origin in {Union, UnionType}:
        shapes = [_declared_shape(arg) for arg in args if arg is not type(None)]
        if len(shapes) == 1 and not isinstance(shapes[0], str):
            return shapes[0]  # Optional container: presence is checked by resolve.
        if all(isinstance(shape, str) for shape in shapes):
            return '|'.join(dict.fromkeys('|'.join(shapes).split('|') +
                                         (['null'] if type(None) in args else [])))
        return 'unknown'
    if origin is list:
        return ('array', _declared_shape(args[0]))
    if origin is dict and args[0] is str:
        return {'$map': _declared_shape(args[1])}
    if is_dataclass(annotation):
        hints = get_type_hints(annotation)
        return {field.name: _declared_shape(hints[field.name]) for field in fields(annotation)}
    return 'unknown'


_SCHEMAS = {
    'search': {
        'hits': ('array', {
            'document_id': 'string', 'title': 'string', 'snippet': 'string',
            'source_uri': 'string', 'score': 'number',
            # DocumentHit.to_dict() retains this tuple. resolve() cannot
            # index tuples, and text() cannot consume them as flat lists.
            'matched_terms': 'tuple<string>', 'metadata': _SEARCH_METADATA,
        }),
        'search_scope': {'mode': 'string', 'document_id': 'string|null',
                         'page_no': 'number|null', 'source_sha256': 'string'},
    },
    'document_formula': {
        'expression': 'string', 'parameters': ('array', 'string'),
        'label': 'string', 'source_uri': 'string', 'locator': 'string',
        'chunk_id': 'string', 'sha256': 'string',
        'parameter_contracts': {'$map': {key: 'string' for key in ('table', 'column', 'function')}},
        'parameter_contract_sources': ('array', {key: 'string' for key in
            ('parameter', 'quote', 'quote_basis', 'locator', 'sha256')}),
        'temporal_constraints': {'base_year': 'number', 'target_year': 'number'},
    },
    'document_cell': _CELL,
    'document_fact': _FACT,
    'policy_select': _POLICY,
    'search_fact': _SEARCH_FACT,
    'sql': {
        'question': 'string', 'rewritten_question': 'string', 'status': 'string',
        'result_state': 'string', 'sql': 'string|null',
        'columns': ('array', 'string'), 'explanation': ('array', 'string'),
        'notices': ('array', 'string'), 'parameters': ('array', _SCALAR),
        'clarification': 'string|null', 'clarification_code': 'string|null',
        'clarification_options': ('array', {'$map': 'string'}),
        'rows': ('array', {'$map': _SCALAR}),
        'dimension_values': {'$map': {'$map': ('array', _SCALAR)}},
        'aggregate_cells': {'$map': {'$map': {'$map': ('array', _AGGREGATE)}}},
        'aggregate_cell_contract': {'address': ('array', 'string'), 'source': 'string',
                                    'ambiguous_addresses_not_exposed': ('array', ('array', 'string'))},
        'plan': _declared_shape(QueryPlan),
        'provenance': {'source_type': 'string', 'database': 'string',
                       'table': 'string', 'query_hash': 'string', 'consistency': 'string',
                       'source_revision': 'string', 'source_revision_kind': 'string',
                       'result_completeness': 'string', 'row_count': 'number', 'row_limit': 'number'},
    },
    'calculate': {
        'value': 'number', 'status': 'string', 'expression': 'string',
        'formula_source': 'string', 'formula_locator': 'string', 'result_unit': 'string',
        'unit_validation': 'string', 'result_dimension': {'$map': 'number'},
        'parameters': {'$map': _PARAMETER}, 'normalized_parameters': {'$map': 'number'},
        'temporal_validation': {'status': 'string', 'base_year': 'number', 'target_year': 'number'},
        'parameter_semantics_validation': {'status': 'string', 'scope': 'string',
            'bindings': ('array', {**{key: 'string' for key in (
                'parameter', 'source_type', 'label', 'source_uri', 'source_sha256', 'locator',
                'task_id', 'table', 'column', 'function', 'verification')}, 'row': 'number'})},
        'trace': ('array', {'stage': 'string', 'status': 'string', 'value': 'number',
                            'parameters': ('array', 'string'), 'sources': ('array', 'string')}),
    },
    'compare': {'matched': 'boolean', 'difference': 'number|null',
                'status': 'string', 'operator': 'string', 'unit': 'string',
                'left': ('union', _AGGREGATE, _CELL, _FACT, _POLICY, _SEARCH_FACT),
                'right': ('union', _AGGREGATE, _CELL, _FACT, _POLICY, _SEARCH_FACT)},
}


def _projection_type(tool, path):
    def project(node, remaining):
        if node is None:
            return None
        if isinstance(node, tuple) and node[0] == 'union':
            kinds = [project(branch, remaining) for branch in node[1:]]
            return '|'.join(dict.fromkeys(kind for kind in kinds if kind is not None)) or None
        if not remaining:
            if isinstance(node, dict):
                return 'object'
            if isinstance(node, tuple):
                leaf = project(node[1], [])
                return 'array<' + (leaf or 'unknown') + '>'
            return node
        key, rest = remaining[0], remaining[1:]
        if isinstance(node, dict) and isinstance(key, str):
            return project(node.get(key, node.get('$map')), rest)
        if (isinstance(node, tuple) and node[0] == 'array' and isinstance(key, int)
                and not isinstance(key, bool) and key >= 0):
            return project(node[1], rest)
        return None
    return project(_SCHEMAS.get(tool), path) or 'unknown'


def _text_scalar(kind):
    types = set(kind.split('|'))
    # Known scalar unions preserve legitimate cell values; bool/null still
    # need the existing execution-time eligibility check. Unknown is not a
    # guessable scalar, and a possible object/array is never flattened.
    return bool(types & {'string', 'number'}) and types <= {'string', 'number', 'boolean', 'null'}


def _available_paths(tool):
    """Bounded schema hints, never snippets, provider text or inferred facts."""
    schema = _SCHEMAS.get(tool, {})
    result = [{'path': [key], 'type': value}
              for key, value in schema.items() if isinstance(value, str)
              and _text_scalar(value)][:6]
    if tool == 'search':
        result = [
            {'path': ['hits', 0, 'snippet'], 'type': 'string'},
            {'path': ['hits', 0, 'title'], 'type': 'string'},
            {'path': ['hits', 0, 'metadata', 'document_id'], 'type': 'string'},
            {'path': ['hits', 0, 'metadata', 'source_locator'], 'type': 'string'},
        ]
    for hint in result:
        hint['use'] = 'text_navigation_only_not_verified_fact'
        hint['availability'] = 'must_be_rechecked_at_execution'
    return result


def text_reference_errors(tasks):
    """Check normalized SQL/search text arguments, retaining all references.

    A known nullable scalar remains subject to resolve/text at execution.
    A top-level reference to a scalar array matches text()'s existing flat
    list contract; an array inside a text list is always invalid.
    """
    tools = {task['id']: task['tool'] for task in tasks}
    errors = []
    for task in tasks:
        if task['tool'] not in {'sql', 'search'}:
            continue
        field = 'question' if task['tool'] == 'sql' else 'query'
        if field not in task['args']:
            continue
        value = task['args'][field]
        parts = list(enumerate(value)) if isinstance(value, list) else [(None, value)]
        for index, part in parts:
            error = {'task_id': task['id'],
                     'argument_path': [field] if index is None else [field, index]}
            if isinstance(part, dict) and set(part) == {'ref', 'path'}:
                tool = tools.get(part['ref'])
                kind = _projection_type(tool, part['path'])
                scalar = _text_scalar(kind)
                if task['tool'] == 'search' and index is None:
                    # validate_search_args permits numbers only inside an
                    # actual query list, even though SQL text accepts them.
                    scalar = scalar and 'string' in kind.split('|')
                scalar_array = index is None and kind in {
                    'array<string>', 'array<number>', 'array<' + _SCALAR + '>',
                    'array<' + _JSON_SCALAR + '>'}
                if scalar or scalar_array:
                    continue
                code = ('text_reference_unknown_type' if 'unknown' in kind.split('|') else
                        'text_reference_incompatible_type' if _text_scalar(kind) else
                        'text_reference_non_scalar')
                allowed = ['string', 'number']
                if index is None:
                    allowed = (['string'] if task['tool'] == 'search' else allowed) + ['flat_array<string|number>']
                error.update({'code': code,
                              'source_task_id': part['ref'], 'source_tool': tool,
                              'resolved_type': kind, 'allowed_types': allowed,
                              'available_paths': _available_paths(tool)})
            elif (isinstance(part, (str, int, float)) and not isinstance(part, bool)
                  and (not isinstance(part, float) or math.isfinite(part))):
                continue
            else:
                error.update({'code': 'text_argument_non_scalar',
                              'resolved_type': 'array' if isinstance(part, list) else
                              'object' if isinstance(part, dict) else
                              'boolean' if isinstance(part, bool) else 'null',
                              'allowed_types': ['string', 'number']})
            errors.append(error)
            if len(errors) >= 16:
                return errors
    return errors
