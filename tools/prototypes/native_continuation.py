"""Isolated, fail-closed native block continuation prototype; no model/gold.

The caller must supply fresh page blocks and reading order pinned to the actual
PDF SHA. This prototype does not certify the PDF bytes or authorize arithmetic.
"""
from __future__ import annotations

import hashlib
import math
import re


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _box(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and all(type(x) in (int, float) and math.isfinite(x) for x in value)
            and value[0] < value[2] and value[1] < value[3])


def _column(block, order):
    box = block['bbox_fitz_unrotated_pt']
    bands = order.get('column_bands_pt', [])
    if not bands:
        return 0 if order.get('column_count') == 1 else None
    matches = [i for i, band in enumerate(bands)
               if box[0] >= band['x0'] - 3 and box[2] <= band['x1'] + 3]
    return matches[0] if len(matches) == 1 else None


def _first_closed_end(text):
    """Only explicit sentence punctuation; decimals/acronyms are not closure.

    Deliberately rejects ambiguous period endings after short words, initials,
    and common abbreviations. No lexical completion/inference is attempted.
    """
    abbreviations = {'mr', 'mrs', 'ms', 'dr', 'prof', 'inc', 'ltd', 'corp',
                     'dept', 'fig', 'no', 'vs', 'etc', 'st'}
    for match in re.finditer(r'[。！？!?]|\.', text):
        i = match.start()
        if text[i] == '.':
            after = text[i + 1:i + 2]
            if after and not after.isspace():
                continue
            token = re.search(r'[A-Za-z.]+$', text[max(0, i - 80):i])
            word = token.group() if token else ''
            if word and (len(word) <= 2 or '.' in word or word.lower() in abbreviations):
                continue
        return match.end()
    return None


def _member(block, start, end):
    text = block['normalized_text']
    lines = []
    cursor = 0
    for index, line in enumerate(block.get('native_lines', [])):
        value = line.get('text', '')
        if not isinstance(value, str) or not value or not _box(line.get('bbox')):
            return None
        position = text.find(value, cursor)
        if position < 0 or text.find(value, position + 1) >= 0 or text[cursor:position].strip():
            return None
        finish = position + len(value)
        if position < end and finish > start:
            lines.append({'line_index': index, 'bbox_fitz_unrotated_pt': line['bbox'],
                          'source_range': [position, finish],
                          'selected_range': [max(start, position), min(end, finish)],
                          'text_sha256': _sha(value)})
        cursor = finish
    if not lines or text[cursor:].strip():
        return None
    return {'block_id': block['block_id'], 'native_block_id': block.get('native_block_id'),
            'bbox_fitz_unrotated_pt': block['bbox_fitz_unrotated_pt'],
            'text_sha256': _sha(text), 'source_range': [start, end],
            'selected_text_sha256': _sha(text[start:end]), 'lines': lines}


def choose_continuation(block, ordered, order, max_chars=1800):
    """Return {text,members,...} or {text:None,reason}; use literal first closure.

    Entire leading block survives, including condition/forecast prefixes.
    Only the immediate unique same-column geometric successor can contribute
    its first explicitly closed sentence, even when its full block is huge.
    """
    def refuse(reason):
        return {'text': None, 'reason': reason}

    if type(max_chars) is not int or max_chars < 1 or not isinstance(order, dict):
        return refuse('invalid_input')
    sha = order.get('source_sha256')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        return refuse('source_sha_missing')
    if not isinstance(ordered, list) or not 2 <= len(ordered) <= 20000:
        return refuse('invalid_order')
    ids = [item.get('block_id') for item in ordered]
    if any(x is None for x in ids) or len(set(ids)) != len(ids) or order.get('block_ids') != ids:
        return refuse('ambiguous_order')
    matches = [i for i, item in enumerate(ordered) if item is block]
    if len(matches) != 1 or matches[0] + 1 >= len(ordered):
        return refuse('no_unique_successor')
    following = ordered[matches[0] + 1]
    for item in ordered:
        if not _box(item.get('bbox_fitz_unrotated_pt')):
            return refuse('geometry_unavailable')
        if item.get('source_sha256', sha) != sha:
            return refuse('source_sha_mismatch')
    for item in (block, following):
        text = item.get('normalized_text')
        if not isinstance(text, str) or not text.strip() or item.get('text_sha256') != _sha(text):
            return refuse('text_pin_invalid')
    text, suffix = block['normalized_text'], following['normalized_text']
    if _first_closed_end(text.rstrip()) == len(text.rstrip()):
        return refuse('anchor_already_closed')
    # Earlier complete sentences may precede the final open sentence.
    if text.rstrip().endswith(tuple('.。!?！？')):
        return refuse('ambiguous_anchor_boundary')
    column = _column(block, order)
    if column is None or _column(following, order) != column:
        return refuse('cross_column_or_ambiguous')
    first_line = suffix.strip().splitlines()[0]
    if (re.match(r'^(?:#{1,6}\s|\[[^\]]+\])', first_line)
            or first_line.endswith((':', '：'))
            or len(first_line) <= 80 and first_line.isupper()
            or '\n' in suffix and len(first_line) <= 80 and first_line.istitle()
            and not _first_closed_end(first_line)
            or following.get('is_heading') is True):
        return refuse('new_heading')
    bb, fb = block['bbox_fitz_unrotated_pt'], following['bbox_fitz_unrotated_pt']
    native = block.get('native_lines') or []
    heights = [line['bbox'][3] - line['bbox'][1] for line in native if _box(line.get('bbox'))]
    if not heights:
        return refuse('line_geometry_unavailable')
    height = sorted(heights)[len(heights) // 2]
    gap = fb[1] - bb[3]
    if not (0 <= gap <= 2 * height + 4 and abs(fb[0] - bb[0]) <= height
            and bb[0] < fb[2] and fb[0] < bb[2]):
        return refuse('not_geometrically_adjacent')
    competitors = [item for item in ordered if item is not block and item is not following
                   and _column(item, order) == column
                   and bb[3] <= item['bbox_fitz_unrotated_pt'][1] <= fb[3]
                   and item['bbox_fitz_unrotated_pt'][0] < fb[2]
                   and fb[0] < item['bbox_fitz_unrotated_pt'][2]]
    if competitors:
        return refuse('ambiguous_geometric_successor')
    end = _first_closed_end(suffix)
    if end is None:
        return refuse('no_explicit_sentence_closure')
    combined = text + '\n' + suffix[:end]
    if len(combined) > max_chars:
        return refuse('complete_continuation_over_budget')
    members = [_member(block, 0, len(text)), _member(following, 0, end)]
    if any(member is None for member in members):
        return refuse('line_ranges_unverified')
    return {'text': combined, 'reason': None, 'source_sha256': sha,
            'evidence_sha256': _sha(combined), 'members': members,
            'column': column, 'max_chars': max_chars,
            'mode': 'unique_native_successor_first_closed_sentence_prototype',
            'calculator_input_eligible': False}
