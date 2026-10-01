"""Fail-closed original native block continuation; no model or gold hints.

The caller must supply fresh page blocks and reading order pinned to the actual
PDF SHA. This prototype does not certify the PDF bytes or authorize arithmetic.
"""
from __future__ import annotations

import hashlib
import math
import re

from .chunk_cleaning import DocumentChunker


def continuation_state(text):
    """Recognize only explicitly verbal, alphabetic-ended open prose.

    Labels, names, years, amounts and numeric table rows retain their native
    block contract. This narrow gate makes no claim to general syntax parsing.
    """
    tail = text.rstrip()
    if not tail or not re.search(r'[a-z]$', tail) or tail.endswith((':', '：')):
        return 'native'
    # A verb in an already closed sentence cannot turn following role labels
    # or names into open prose. Inspect only the final unclosed segment.
    tail = re.split(r'[。！？!?]|\.(?=\s|$)', tail)[-1]
    words = re.findall(r'[A-Za-z]+', tail)
    # A printed block can stop just after the next sentence's noun phrase.
    # Without this gate, "... complete sentence. The power" looks like a
    # free-standing table label and loses the following requirement/condition.
    if 2 <= len(words) <= 4 and re.match(r'^\s*(?:the|this|these|those)\b', tail, re.I):
        return 'unknown'
    verbal = bool(re.search(r'\b(?:is|are|was|were|has|have|had|does|do|did|'
                          r'can|could|may|might|must|shall|should|will|would)\b', tail, re.I))
    if verbal:
        return 'open' if len(words) >= 4 else 'unknown'
    if (re.search(r'\b(?:not|of|to|for|with|without|from|by|and|or|unless|if)$', tail, re.I)
            or len(words) >= 2 and re.search(r'\b(?:excludes?|includes?|covers?|requires?|'
                                           r'provides?|supports?|applies?)\b', tail, re.I)):
        return 'unknown'
    return 'native'


def requires_continuation(text):
    return continuation_state(text) != 'native'


_LITERAL_UNIT = re.compile(r'(?:%|[$€£¥]|USD|EUR|GBP|CNY|RMB|dollars?|euros?|'
                           r'millions?|billions?|thousands?|mn|bn|m|k)(?![A-Za-z])', re.I)


def enrich_native_line_styles(blocks, page):
    """Attach fresh native font facts by exact text and bbox; never infer style.

    Uniqueness failure leaves style unavailable. Text extraction remains the
    established parser's output; these records only restrict continuation.
    """
    lines = [line for block in page.get_text('dict', sort=False).get('blocks', [])
             if block.get('type') == 0 for line in block.get('lines', [])]
    by_signature = {}
    for line in lines:
        if not _box(line.get('bbox')):
            continue
        signature = (''.join(span.get('text', '') for span in line.get('spans', [])),
                     tuple(round(value, 4) for value in line['bbox']))
        by_signature.setdefault(signature, []).append(line)
    for block in blocks:
        for native in block.get('native_lines', []):
            if not _box(native.get('bbox')):
                continue
            signature = (native['text'], tuple(round(value, 4) for value in native['bbox']))
            matches = by_signature.get(signature, [])
            if len(matches) == 1:
                native['native_direction'] = tuple(round(float(value), 6)
                                                   for value in matches[0].get('dir', ()))
                native['native_style'] = tuple(sorted({
                    (span.get('font'), round(float(span.get('size', 0)), 3), span.get('flags'))
                    for span in matches[0].get('spans', [])}))


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


def _column_wrap(block, following, ordered, order, column, next_column, height):
    """Certify only a bottom-to-top transition between adjacent native lanes.

    This supplies layout evidence, not inferred text or semantic entailment.
    The caller still retains the whole anchor, rejects headings/style changes,
    and requires the successor's first explicit sentence closure.
    """
    bands = order.get('column_bands_pt', [])
    if (order.get('mode') != 'native_columns' or not 2 <= len(bands) <= 4
            or column is None or next_column != column + 1):
        return None
    left, right = bands[column], bands[next_column]
    if any(not _box([band.get(key) for key in ('x0', 'y0', 'x1', 'y1')]) for band in bands):
        return None
    bb, fb = block['bbox_fitz_unrotated_pt'], following['bbox_fitz_unrotated_pt']
    if (left['x1'] >= right['x0']
            or abs(left['y0'] - right['y0']) > 2 * height
            or abs(left['y1'] - right['y1']) > 2 * height
            or min(left['y1'] - left['y0'], right['y1'] - right['y0']) < 4 * height
            or not 0 <= left['y1'] - bb[3] <= height
            or not 0 <= fb[1] - right['y0'] <= height):
        return None
    assigned = [_column(item, order) for item in ordered]
    known = [value for value in assigned if value is not None]
    if known != sorted(known):
        return None
    in_left = [item for item, lane in zip(ordered, assigned) if lane == column]
    in_right = [item for item, lane in zip(ordered, assigned) if lane == next_column]
    if not in_left or not in_right or in_left[-1] is not block or in_right[0] is not following:
        return None
    previous = sorted(block.get('native_lines', []), key=lambda line: (line['bbox'][1], line['bbox'][0]))
    current = sorted(following.get('native_lines', []), key=lambda line: (line['bbox'][1], line['bbox'][0]))
    if (not previous or not current or previous[-1].get('native_style') is None
            or current[0].get('native_style') is None
            or previous[-1]['native_style'] != current[0]['native_style']):
        return None
    return {'method': 'adjacent_native_lane_bottom_to_top', 'from_column': column,
            'to_column': next_column, 'from_band': dict(left), 'to_band': dict(right),
            'same_boundary_font_style': True, 'intervening_lane_blocks': 0}


def _member(block, start, end):
    text = block['normalized_text']
    lines = []
    cursor = 0
    natives = list(enumerate(block.get('native_lines', [])))
    if any(not _box(line.get('bbox')) for _, line in natives):
        return None
    groups = []
    for index, line in sorted(natives, key=lambda item: (item[1]['bbox'][1], item[1]['bbox'][0])):
        if (block.get('native_column_part') is None and groups
                and abs(line['bbox'][1] - groups[-1][0][1]['bbox'][1]) <= 2):
            groups[-1].append((index, line))
        else:
            groups.append([(index, line)])
    groups = [sorted(group, key=lambda item: item[1]['bbox'][0]) for group in groups]
    reconstructed = DocumentChunker.clean_text('\n'.join(
        ' '.join(line['text'].strip() for _, line in group) for group in groups))
    if reconstructed != text:
        return None
    for index, line in [item for group in groups for item in group]:
        value = line.get('text', '')
        if not isinstance(value, str) or not value or not _box(line.get('bbox')):
            return None
        normalized = DocumentChunker.clean_text(value)
        if not normalized:
            continue
        position = text.find(normalized, cursor)
        if position < 0 or text[cursor:position].strip():
            return None
        finish = position + len(normalized)
        if position < end and finish > start:
            lines.append({'line_index': index, 'bbox_fitz_unrotated_pt': line['bbox'],
                          'source_range': [position, finish],
                          'selected_range': [max(start, position), min(end, finish)],
                          'text_sha256': _sha(value), 'normalized_text_sha256': _sha(normalized),
                          'native_style': line.get('native_style')})
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
    column, next_column = _column(block, order), _column(following, order)
    native = block.get('native_lines') or []
    heights = [line['bbox'][3] - line['bbox'][1] for line in native if _box(line.get('bbox'))]
    if not heights:
        return refuse('line_geometry_unavailable')
    height = sorted(heights)[len(heights) // 2]
    column_transition = None
    if column != next_column:
        column_transition = _column_wrap(block, following, ordered, order, column, next_column, height)
    if column is None or next_column != column and column_transition is None:
        return refuse('cross_column_or_ambiguous')
    first_line = suffix.strip().splitlines()[0]
    if (re.match(r'^(?:#{1,6}\s|\[[^\]]+\])', first_line)
            or first_line.endswith((':', '：'))
            or len(first_line) <= 80 and first_line.isupper() and not re.search(r'\d', first_line)
            or '\n' in suffix and len(first_line) <= 80 and first_line.istitle()
            and not _first_closed_end(first_line)
            or following.get('is_heading') is True):
        return refuse('new_heading')
    # A new independently capitalized sentence has no proven syntactic
    # dependency on the previous fragment. Named-object continuations remain
    # unsupported rather than being silently treated as a sentence join.
    if re.match(r'[A-Z]', first_line):
        return refuse('independent_or_unverified_sentence')
    # A lowercase standalone label followed by a capitalized sentence is not
    # proved to be a wrapped prose line. This also catches unstyled headings.
    native_lines = following.get('native_lines', [])
    if ('\n' in suffix and _first_closed_end(first_line) is None
            and len(re.findall(r'[A-Za-z]+', first_line)) <= 4):
        return refuse('unverified_heading_or_paragraph_boundary')
    previous_lines = sorted(block.get('native_lines', []), key=lambda line: (line['bbox'][1], line['bbox'][0]))
    next_lines = sorted(native_lines, key=lambda line: (line['bbox'][1], line['bbox'][0]))
    if (previous_lines and next_lines and previous_lines[-1].get('native_style') is not None
            and next_lines[0].get('native_style') is not None
            and previous_lines[-1]['native_style'] != next_lines[0]['native_style']):
        return refuse('native_font_style_boundary')
    bb, fb = block['bbox_fitz_unrotated_pt'], following['bbox_fitz_unrotated_pt']
    gap = fb[1] - bb[3]
    if column_transition is None and not (0 <= gap <= 2 * height + 4 and abs(fb[0] - bb[0]) <= height
            and bb[0] < fb[2] and fb[0] < bb[2]):
        return refuse('not_geometrically_adjacent')
    competitors = [item for item in ordered if item is not block and item is not following
                   and _column(item, order) == column and column_transition is None
                   and bb[3] <= item['bbox_fitz_unrotated_pt'][1] <= fb[3]
                   and item['bbox_fitz_unrotated_pt'][0] < fb[2]
                   and fb[0] < item['bbox_fitz_unrotated_pt'][2]]
    if competitors:
        return refuse('ambiguous_geometric_successor')
    end = _first_closed_end(suffix)
    if end is None:
        return refuse('no_explicit_sentence_closure')
    # A numeric terminal followed immediately by a literal unit run is not
    # allowed to crop the unit away. Preserve the actual unit and its range;
    # certify only matching same-line/adjacent-line native layout.
    if re.search(r'\d\.$', suffix[:end]):
        unit = re.match(r'\s*(' + _LITERAL_UNIT.pattern + r')[.]?', suffix[end:], re.I)
        if unit:
            extended_end = end + unit.end()
            proof = _member(following, 0, extended_end)
            if proof is None:
                return refuse('unit_line_mapping_unverified')
            selected = proof['lines']
            if len(selected) > 1:
                before, after = selected[-2]['bbox_fitz_unrotated_pt'], selected[-1]['bbox_fitz_unrotated_pt']
                line_height = before[3] - before[1]
                same_line = abs(before[1] - after[1]) <= 2 and 0 <= after[0] - before[2] <= 3
                adjacent_line = (0 <= after[1] - before[3] <= line_height + 2
                                 and abs(after[0] - before[0]) <= 3)
                if not same_line and not adjacent_line:
                    return refuse('unit_layout_unverified')
            end = extended_end
    combined = text + '\n' + suffix[:end]
    if len(combined) > max_chars:
        return refuse('complete_continuation_over_budget')
    members = [_member(block, 0, len(text)), _member(following, 0, end)]
    if any(member is None for member in members):
        return refuse('line_ranges_unverified')
    return {'text': combined, 'reason': None, 'source_sha256': sha,
            'evidence_sha256': _sha(combined), 'members': members,
            'column': column, 'max_chars': max_chars,
            'column_transition': column_transition,
            'mode': 'unique_native_successor_first_closed_sentence_prototype',
            'calculator_input_eligible': False}
