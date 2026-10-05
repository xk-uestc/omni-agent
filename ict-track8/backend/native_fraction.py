"""Pinned native count fractions, never decimal cells or physical inputs.

The column nominates a count-completion domain, not semantic truth. The caller
must still verify the requested entity, period and whole question independently.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
import hashlib
import math
import re

import fitz


_LITERAL = re.compile(r'([0-9]{1,12})/([0-9]{1,12})')
_COUNT_HEADER = re.compile(
    r'\b(?:attendance|meetings? attended|sessions? attended|'
    r'(?:tasks?|items?|sessions?|meetings?) completed|completion count)\b|'
    r'出席(?:次数|情况)?|到会次数|完成次数|已完成(?:任务|项目)数', re.I)
_CONFLICT_HEADER = re.compile(
    r'\b(?:date|version|odds|price|currency|percent|percentage|rate|ratio|'
    r'USD|EUR|GBP|CNY|JPY|CAD|AUD|thousands?|millions?|billions?)\b|'
    r'日期|版本|赔率|价格|货币|百分比|完成率|出席率|[$€£¥%]', re.I)


def _box(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(n) not in (int, float) or not math.isfinite(n) for n in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        raise ValueError('native_fraction_bbox_invalid')
    return list(value)


def parse_native_fraction_cell(raw_value, *, column_header_path,
                               column_header_bboxes_display_pt, bbox_display_pt,
                               source_sha256, page_no):
    """Bind one complete native token to its explicit count column and source.

    No spaces, signs, mixed numbers, dates, decimals or OCR normalization.
    Zero attendance is allowed; a denominator must be positive.
    """
    match = _LITERAL.fullmatch(raw_value) if isinstance(raw_value, str) else None
    if not match:
        raise ValueError('native_fraction_literal_invalid')
    numerator, denominator = map(int, match.groups())
    if denominator <= 0 or numerator > denominator:
        raise ValueError('native_fraction_count_bounds_invalid')
    if (not isinstance(column_header_path, list) or not column_header_path
            or len(column_header_path) > 3
            or any(not isinstance(t, str) or not t.strip() or len(t) > 200
                   for t in column_header_path)):
        raise ValueError('native_fraction_header_invalid')
    header = ' '.join(column_header_path)
    if not _COUNT_HEADER.search(header) or _CONFLICT_HEADER.search(header):
        raise ValueError('native_fraction_count_header_unbound')
    if (not isinstance(column_header_bboxes_display_pt, list)
            or len(column_header_bboxes_display_pt) != len(column_header_path)):
        raise ValueError('native_fraction_header_bbox_invalid')
    boxes = [_box(b) for b in column_header_bboxes_display_pt]
    cell_box = _box(bbox_display_pt)
    # The own-column header must overlap the cell lane horizontally and precede
    # it vertically. Table extraction still proves unique empty gutters.
    if any(min(b[2], cell_box[2]) <= max(b[0], cell_box[0])
           or b[3] > cell_box[1] for b in boxes):
        raise ValueError('native_fraction_header_geometry_unbound')
    if (not isinstance(source_sha256, str)
            or re.fullmatch(r'[0-9a-f]{64}', source_sha256) is None
            or type(page_no) is not int or page_no < 1):
        raise ValueError('native_fraction_source_binding_invalid')
    return {'schema_version': 'native-count-fraction-v1',
            'value_kind': 'native_count_fraction_literal',
            'raw_value': raw_value, 'numerator': str(numerator), 'denominator': str(denominator),
            'unit': 'count_fraction', 'scale': None,
            'column_header_path': list(column_header_path),
            'column_header_bboxes_display_pt': boxes, 'bbox_display_pt': cell_box,
            'source_sha256': source_sha256, 'page_no': page_no,
            'binding': 'own_explicit_count_column_and_complete_native_token',
            'calculator_input_eligible': False, 'physical_calculator_input_eligible': False,
            'validation_scope': 'native_literal_geometry_not_semantic_truth'}


def validate_native_fraction_proof(proof):
    """Reject forged type/unit/operands or stale fields before arithmetic."""
    if not isinstance(proof, dict):
        raise ValueError('native_fraction_proof_invalid')
    try:
        fresh = parse_native_fraction_cell(**{k: proof[k] for k in (
            'raw_value', 'column_header_path', 'column_header_bboxes_display_pt',
            'bbox_display_pt', 'source_sha256', 'page_no')})
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('native_fraction_proof_invalid') from exc
    if fresh != proof:
        raise ValueError('native_fraction_proof_invalid')
    return fresh


def _same_box(a, b):
    return all(abs(x-y) <= 0.001 for x, y in zip(a, b))


def replay_native_fraction_cell(raw, proof):
    """Re-read pinned original bytes and exact cell/header native word boxes.

    This proves the literal token and its printed header, not table row identity,
    empty gutters, document completeness or model-reviewed semantic scope.
    """
    proof = validate_native_fraction_proof(proof)
    if (not isinstance(raw, bytes) or not raw or len(raw) > 20*1024*1024
            or hashlib.sha256(raw).hexdigest() != proof['source_sha256']):
        raise ValueError('native_fraction_source_replay_mismatch')
    with fitz.open(stream=raw, filetype='pdf') as pdf:
        if pdf.needs_pass or len(pdf) > 1000 or proof['page_no'] > len(pdf):
            raise ValueError('native_fraction_page_replay_invalid')
        page = pdf[proof['page_no']-1]
        transform = page.rotation_matrix
        words = [(list(fitz.Rect(w[:4])*transform), w[4]) for w in page.get_text('words')]
        cells = [(b, t) for b, t in words if _same_box(b, proof['bbox_display_pt'])]
        if len(cells) != 1 or cells[0][1] != proof['raw_value']:
            raise ValueError('native_fraction_cell_replay_mismatch')
        for text, box in zip(proof['column_header_path'], proof['column_header_bboxes_display_pt']):
            enclosed = [(b, t) for b, t in words if b[0] >= box[0]-.001
                        and b[1] >= box[1]-.001 and b[2] <= box[2]+.001 and b[3] <= box[3]+.001]
            if not enclosed:
                raise ValueError('native_fraction_header_replay_mismatch')
            enclosing = [min(b[0] for b, _ in enclosed), min(b[1] for b, _ in enclosed),
                         max(b[2] for b, _ in enclosed), max(b[3] for b, _ in enclosed)]
            printed = ' '.join(t for b, t in sorted(enclosed, key=lambda item: item[0][0]))
            if not _same_box(enclosing, box) or printed != text:
                raise ValueError('native_fraction_header_replay_mismatch')
    return proof


def fraction_percentage(proof, *, decimal_places=2):
    """Server rational arithmetic with explicit half-up display rounding."""
    proof = validate_native_fraction_proof(proof)
    if type(decimal_places) is not int or not 0 <= decimal_places <= 6:
        raise ValueError('native_fraction_percentage_precision_unsupported')
    fraction = Fraction(int(proof['numerator']), int(proof['denominator'])) * 100
    digits, remainder = divmod(fraction.numerator * 10**decimal_places, fraction.denominator)
    if remainder * 2 >= fraction.denominator:
        digits += 1
    value = Decimal((0, tuple(map(int, str(digits))), -decimal_places))
    rounded = Fraction(value) != fraction
    return {'operation': 'fraction_percentage', 'operands': [proof['raw_value']],
            'answer': ('≈' if rounded else '') + format(value, f'.{decimal_places}f') + '%',
            'numeric_result': format(value, 'f'), 'unit': 'percent', 'scale': None,
            'exact_fraction': {'numerator': str(fraction.numerator), 'denominator': str(fraction.denominator)},
            'display_decimal_places': decimal_places, 'rounding': 'ROUND_HALF_UP', 'rounded': rounded,
            'computation_domain': 'one_native_count_fraction_not_literal_source_percentage',
            'calculator_input_eligible': False, 'physical_calculator_input_eligible': False}
