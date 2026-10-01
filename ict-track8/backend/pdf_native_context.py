"""Bounded original-PDF block context, without trusting stored chunk metadata."""
from __future__ import annotations

import hashlib
import re

from .chunk_cleaning import DocumentChunker
from .evidence_context import text_sha256
from .grounded_generation import (_ENGLISH_ACTUAL_HEADING, _ENGLISH_FORECAST_HEADING,
                                  _FORECAST_HEADING, _DECLARED_UNIT_HEADING,
                                  _DECLARED_CONDITION_HEADING, MAX_LOCAL_SCOPE_HEADINGS)
from .native_continuation import (choose_continuation, continuation_state,
                                  enrich_native_line_styles)

EXTRACTION_VERSION = 'original-native-complete-block-context-v3'
_UNIT = re.compile(r'(?:%|[$€£¥]|USD|EUR|GBP|CNY|RMB|dollars?|euros?|millions?|billions?|thousands?|mn|bn|m|k)', re.I)
_NUMERIC_END = re.compile(r'\d(?:[\d,.]*\d)?\s*$')


def _compact(text):
    return re.sub(r'\s+', '', DocumentChunker.clean_text(text))


def _member(block):
    return {**{key: block.get(key) for key in ('block_id', 'native_block_id', 'native_column_part',
            'bbox_fitz_unrotated_pt', 'text_sha256')},
            'lines': [{'line_index': i, 'bbox_fitz_unrotated_pt': line.get('bbox'),
                       'text_sha256': text_sha256(line['text']),
                       'native_style': line.get('native_style')}
                      for i, line in enumerate(block.get('native_lines', []))]}


def _column(block, order):
    box = block['bbox_fitz_unrotated_pt']
    bands = order.get('column_bands_pt', [])
    if not bands:
        return 0
    matches = [i for i, b in enumerate(bands) if box[0] >= b['x0'] - 5 and box[2] <= b['x1'] + 5]
    return matches[0] if len(matches) == 1 else None


def extract_native_context(raw, page_no, anchor_text, max_chars=1800):
    """Return a complete uniquely anchored native block and narrow provenance.

    Only a unit-only right-hand block with a unique geometrically touching
    numeric line may be inserted. This does not certify numeric units or
    table relations and must not authorize calculator inputs.
    """
    if (not isinstance(raw, bytes) or not raw or type(page_no) is not int or page_no < 1
            or type(max_chars) is not int or max_chars <= 0 or len(raw) > 20 * 1024 * 1024
            or not isinstance(anchor_text, str) or not _compact(anchor_text)):
        return None
    try:
        import fitz
        with fitz.open(stream=raw, filetype='pdf') as document:
            if page_no > len(document) or len(document) > 1000:
                return None
            page = document[page_no - 1]
            page.set_rotation(0)
            blocks = DocumentChunker._pdf_text_blocks(page)
            if len(blocks) > 20000:
                return None
            enrich_native_line_styles(blocks, page)
            ordered, order = DocumentChunker._pdf_reading_order(blocks, page.rect.width)
    except Exception:
        return None
    anchor = _compact(anchor_text)
    matches = [b for b in ordered if anchor in _compact(b['normalized_text'])]
    if len(matches) != 1 or _compact(matches[0]['normalized_text']).count(anchor) != 1:
        return None
    block = matches[0]
    text = block['normalized_text']
    if len(text) > max_chars:
        return None
    members = [block]
    continuation_members = None
    column_transition = None
    # Re-read native geometry above, then preserve the entire anchor before
    # adding only the unique successor's first explicitly closed sentence.
    # An open prose block cannot fall back to its incomplete old fragment.
    state = continuation_state(text)
    if state in ('open', 'unknown'):
        continuation = choose_continuation(block, ordered,
            {**order, 'source_sha256': hashlib.sha256(raw).hexdigest()}, max_chars=max_chars)
        if continuation['text'] is None:
            return None
        text = continuation['text']
        continuation_members = continuation['members']
        column_transition = continuation.get('column_transition')
    unit_insertions = []
    # All-page uniqueness: a unit cannot attach to two competing number lines.
    for unit in ordered:
        unit_text = unit['normalized_text'].strip()
        if unit is block or not _UNIT.fullmatch(unit_text):
            continue
        ub = unit['bbox_fitz_unrotated_pt']
        candidates = []
        for owner in ordered:
            if owner is unit:
                continue
            for line in owner.get('native_lines', []):
                lb = line.get('bbox')
                if (lb and _NUMERIC_END.search(line['text']) and abs(lb[3] - ub[3]) <= 2
                        and abs(lb[1] - ub[1]) <= 2 and 0 <= ub[0] - lb[2] <= 3):
                    candidates.append((owner, line))
        if len(candidates) != 1 or candidates[0][0] is not block:
            continue
        line_box = candidates[0][1]['bbox']
        competing_units = [other for other in ordered if other is not block
            and _UNIT.fullmatch(other['normalized_text'].strip())
            and abs(line_box[3] - other['bbox_fitz_unrotated_pt'][3]) <= 2
            and abs(line_box[1] - other['bbox_fitz_unrotated_pt'][1]) <= 2
            and 0 <= other['bbox_fitz_unrotated_pt'][0] - line_box[2] <= 3]
        if len(competing_units) != 1:
            continue
        numeric_line = candidates[0][1]
        line_text = numeric_line['text'].strip()
        # The parser merges physical native runs at one baseline into a
        # normalized line. A label and numeric run can therefore be separate
        # native_lines while sharing one authoritative normalized line.
        # Rebuild precisely that parser grouping, never search/splice the
        # numeric substring into an arbitrary sentence.
        groups = []
        for native in sorted(block.get('native_lines', []), key=lambda item: (
                (item.get('bbox') or block['bbox_fitz_unrotated_pt'])[1],
                (item.get('bbox') or block['bbox_fitz_unrotated_pt'])[0])):
            box = native.get('bbox') or block['bbox_fitz_unrotated_pt']
            if groups and abs(box[1] - (groups[-1][0].get('bbox') or block['bbox_fitz_unrotated_pt'])[1]) <= 2:
                groups[-1].append(native)
            else:
                groups.append([native])
        matching_groups = [group for group in groups if any(item is numeric_line for item in group)]
        if len(matching_groups) != 1:
            continue
        physical_runs = sorted(matching_groups[0], key=lambda item: (
            item.get('bbox') or block['bbox_fitz_unrotated_pt'])[0])
        if physical_runs[-1] is not numeric_line:
            continue
        normalized_line = DocumentChunker.clean_text(' '.join(item['text'].strip() for item in physical_runs))
        if not normalized_line.endswith(line_text):
            continue
        # Only the complete reconstructed physical line occurring once is
        # eligible. Preserve its left-hand label and all other literal runs.
        # Numeric native-line insertion cannot splice a reconstructed prose
        # continuation; its literal member ranges are independently pinned.
        if continuation_members is not None:
            continue
        lines = text.splitlines()
        indices = [i for i, line in enumerate(lines) if line.strip() == normalized_line]
        if len(indices) != 1:
            continue
        lines[indices[0]] += ' ' + unit_text
        candidate_text = '\n'.join(lines)
        if len(candidate_text) > max_chars:
            return None
        text = candidate_text
        members.append(unit)
        unit_insertions.append({'numeric_block_id': block['block_id'], 'unit_block_id': unit['block_id'],
                                'native_line_text_sha256': text_sha256(line_text),
                                'method': 'unique_numeric_line_right_touch_same_baseline'})
    # Preserve only an immediate chain of explicit condition/unit headings
    # whose fresh native geometry proves same-column paragraph adjacency.
    # Never search backwards past an intervening fact or section to borrow a
    # declaration. Unknown layout and an excessive chain fail closed.
    declared_headings = []
    owner = block
    for previous in reversed(ordered[:ordered.index(block)]):
        value = previous['normalized_text'].strip().rstrip('.。')
        if not (_DECLARED_UNIT_HEADING.fullmatch(value) or _DECLARED_CONDITION_HEADING.fullmatch(value)):
            break
        pb, ob = previous['bbox_fitz_unrotated_pt'], owner['bbox_fitz_unrotated_pt']
        pc, oc = _column(previous, order), _column(owner, order)
        native = previous.get('native_lines') or []
        heights = [line['bbox'][3] - line['bbox'][1] for line in native if line.get('bbox')]
        if (pc is None or pc != oc or not heights
                or not (0 <= ob[1] - pb[3] <= 2 * min(heights) + 4)
                or abs(ob[0] - pb[0]) > min(heights)):
            return None
        declared_headings.insert(0, previous)
        if len(declared_headings) > MAX_LOCAL_SCOPE_HEADINGS:
            return None
        owner = previous
    # Preserve a preceding explicit forecast heading in the same column.
    # Spanning headings apply only when they physically cover the anchor.
    column = _column(block, order)
    effective_heading = None
    for previous in ordered[:ordered.index(block)]:
        value = previous['normalized_text'].rstrip().rstrip('.。')
        forecast = bool(_ENGLISH_FORECAST_HEADING.search(value) or _FORECAST_HEADING.search(value))
        actual = bool(_ENGLISH_ACTUAL_HEADING.search(value))
        if not (forecast or actual):
            continue
        pb, bb = previous['bbox_fitz_unrotated_pt'], block['bbox_fitz_unrotated_pt']
        pc = _column(previous, order)
        same_column = column is not None and pc == column
        spanning = pc is None and pb[0] <= bb[0] and pb[2] >= bb[2] and pb[3] <= bb[1]
        if not same_column and not spanning:
            # Column inference absent: a horizontal disjoint heading is unrelated.
            if not order.get('column_bands_pt') and not (pb[0] < bb[2] and bb[0] < pb[2]):
                continue
            if pc is None or column is None:
                return None
            continue
        if not order.get('column_bands_pt') and not (pb[0] < bb[2] and bb[0] < pb[2]):
            continue
        effective_heading = previous if forecast else None
    prefix_members = [item for item in ordered
                      if item is effective_heading or any(item is declared for declared in declared_headings)]
    if prefix_members:
        text = '\n'.join(item['normalized_text'] for item in prefix_members) + '\n' + text
        members = prefix_members + members
    if len(text) > max_chars or anchor not in _compact(text):
        return None
    return {'text': text, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'page_no': page_no,
            'evidence_sha256': text_sha256(text), 'text_sha256': text_sha256(text),
            'evidence_chars': len(text), 'max_chars': max_chars,
            'extraction_version': EXTRACTION_VERSION,
            'mode': 'original_native_complete_continuation' if continuation_members else 'original_native_complete_block',
            'members': ([_member(item) for item in prefix_members] + continuation_members
                        if continuation_members else [_member(b) for b in members]), 'reading_order': order,
            'unit_insertions': unit_insertions, 'column_transition': column_transition,
            'calculator_input_eligible': False}
