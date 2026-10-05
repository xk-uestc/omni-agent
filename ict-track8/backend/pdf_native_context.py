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
from .native_anchor import ANCHOR_POLICY_VERSION, anchor_ranges, literal_compact

EXTRACTION_VERSION = 'original-native-complete-block-context-v5'
_UNIT = re.compile(r'(?:%|[$€£¥]|USD|EUR|GBP|CNY|RMB|dollars?|euros?|millions?|billions?|thousands?|mn|bn|m|k)', re.I)
_NUMERIC_END = re.compile(r'\d(?:[\d,.]*\d)?\s*$')


def _compact(text):
    return literal_compact(DocumentChunker.clean_text(text))


def _anchor_members(parts, anchor_text):
    """Locate the union on the whole page before accepting one block/region."""
    source = '\n\n'.join(part['normalized_text'] for part in parts)
    ranges = anchor_ranges(source, DocumentChunker.clean_text(anchor_text))
    if len(ranges) != 1:
        return None
    left, right = ranges[0]
    offset, touched, bindings = 0, [], []
    for part in parts:
        end = offset + len(part['normalized_text'])
        if offset < right and end > left:
            touched.append(part)
            bindings.append({'block_id': part['block_id'],
                'native_block_id': part.get('native_block_id'),
                'native_column_part': part.get('native_column_part'),
                'source_range': [max(0, left - offset), min(end, right) - offset],
                'parent_source_range': part.get('source_range')})
        offset = end + 2
    return touched, {'policy_version': ANCHOR_POLICY_VERSION,
                     'page_reading_order_range': [left, right], 'members': bindings}


def _member(block):
    return {**{key: block.get(key) for key in ('block_id', 'native_block_id', 'native_column_part',
            'bbox_fitz_unrotated_pt', 'text_sha256', 'source_range', 'parent_text_sha256')},
            'lines': [{'line_index': i, 'bbox_fitz_unrotated_pt': line.get('bbox'),
                       'text_sha256': text_sha256(line['text']),
                       'native_style': line.get('native_style'),
                       'native_direction': line.get('native_direction')}
                      for i, line in enumerate(block.get('native_lines', []))]}


def _column(block, order):
    box = block['bbox_fitz_unrotated_pt']
    bands = order.get('column_bands_pt', [])
    if not bands:
        return 0
    matches = [i for i, b in enumerate(bands) if box[0] >= b['x0'] - 5 and box[2] <= b['x1'] + 5]
    return matches[0] if len(matches) == 1 else None


def _rotated_cell_row(block, rotation, matrix):
    """Recognize a complete rotated native row from fresh display geometry.

    Its label/condition cells remain literal, unbound source text. A rotated
    prose fragment or an ordinary page with a Rotate flag cannot use this
    exception to bypass continuation checks.
    """
    if rotation not in (90, 180, 270):
        return None
    import fitz
    native = block.get('native_lines') or []
    if not 3 <= len(native) <= 128:
        return None
    transformed = []
    for line in native:
        direction = line.get('native_direction')
        if not isinstance(direction, tuple) or len(direction) != 2:
            return None
        dx = direction[0] * matrix[0] + direction[1] * matrix[2]
        dy = direction[0] * matrix[1] + direction[1] * matrix[3]
        if abs(dx - 1) > .0001 or abs(dy) > .0001:
            return None
        box = list(fitz.Rect(line['bbox']) * fitz.Matrix(*matrix))
        if box[0] >= box[2] or box[1] >= box[3]:
            return None
        transformed.append(box)
    components = []
    for box in sorted(transformed, key=lambda value: value[0]):
        if components and box[0] - components[-1][2] < 8:
            previous = components[-1]
            previous[:] = [min(previous[0], box[0]), min(previous[1], box[1]),
                           max(previous[2], box[2]), max(previous[3], box[3])]
        else:
            components.append(box[:])
    if not 3 <= len(components) <= 8:
        return None
    if min(box[3] for box in components) <= max(box[1] for box in components):
        return None
    return {'method': 'fresh_native_direction_and_disjoint_display_cell_runs',
            'page_rotation_degrees': rotation, 'native_to_display_matrix': list(matrix),
            'display_cell_components_pt': components,
            'semantic_row_column_binding_verified': False}


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
            source_rotation = page.rotation
            source_display_matrix = tuple(page.rotation_matrix)
            page.set_rotation(0)
            blocks = DocumentChunker._pdf_text_blocks(page)
            if len(blocks) > 20000:
                return None
            enrich_native_line_styles(blocks, page)
            ordered, order = DocumentChunker._pdf_reading_order(blocks, page.rect.width)
    except Exception:
        return None
    located = _anchor_members(ordered, anchor_text)
    if located is None or len(located[0]) != 1:
        return None
    block, anchor_match = located[0][0], located[1]
    text = block['normalized_text']
    if len(text) > max_chars:
        return None
    members = [block]
    rotated_row = _rotated_cell_row(block, source_rotation, source_display_matrix)
    continuation_members = None
    column_transition = None
    continuation_policy_version = None
    # Re-read native geometry above, then preserve the entire anchor before
    # adding only the unique successor's first explicitly closed sentence.
    # An open prose block cannot fall back to its incomplete old fragment.
    state = continuation_state(text)
    if state in ('open', 'unknown') and rotated_row is None:
        continuation = choose_continuation(block, ordered,
            {**order, 'source_sha256': hashlib.sha256(raw).hexdigest()}, max_chars=max_chars)
        if continuation['text'] is None:
            return None
        text = continuation['text']
        continuation_members = continuation['members']
        column_transition = continuation.get('column_transition')
        continuation_policy_version = continuation.get('continuation_policy_version')
    unit_insertions = []
    # All-page uniqueness: a unit cannot attach to two competing number lines.
    for unit in ordered:
        # Right-touch in unrotated x/y is not a display baseline after Rotate.
        # Keep all original cell text, but never attach a neighbouring rotated
        # block as a unit using the horizontal-page heuristic.
        if source_rotation:
            break
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
    if (len(text) > max_chars
            or len(anchor_ranges(text, DocumentChunker.clean_text(anchor_text))) != 1):
        return None
    return {'text': text, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'page_no': page_no,
            'evidence_sha256': text_sha256(text), 'text_sha256': text_sha256(text),
            'evidence_chars': len(text), 'max_chars': max_chars,
            'extraction_version': EXTRACTION_VERSION,
            'mode': ('original_native_complete_rotated_row' if rotated_row else
                     'original_native_complete_continuation' if continuation_members else
                     'original_native_complete_block'),
            'members': ([_member(item) for item in prefix_members] + continuation_members
                        if continuation_members else [_member(b) for b in members]), 'reading_order': order,
            'unit_insertions': unit_insertions, 'column_transition': column_transition,
            'rotated_native_row': rotated_row,
            'anchor_match_policy': ANCHOR_POLICY_VERSION, 'anchor_match': anchor_match,
            'continuation_policy_version': continuation_policy_version,
            'calculator_input_eligible': False}


def _native_groups(block):
    groups = []
    for line in sorted(block.get('native_lines', []), key=lambda item: (
            item['bbox'][1], item['bbox'][0])):
        if groups and abs(line['bbox'][1] - groups[-1][0]['bbox'][1]) <= 2:
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _paragraph_parts(block):
    """Split only literal blank-line paragraphs independently replayed from runs.

    A giant MuPDF block may contain an entire page. A whitespace-only native
    line is the boundary; sentence or keyword windows never supply boundaries.
    """
    text = block['normalized_text']
    spans, start = [], 0
    for separator in re.finditer(r'\n\s*\n', text):
        spans.append((start, separator.start()))
        start = separator.end()
    spans.append((start, len(text)))
    if len(spans) == 1:
        return [block]
    groups, paragraphs, current = _native_groups(block), [], []
    for group in groups:
        if not any(line['text'].strip() for line in group):
            if current:
                paragraphs.append(current)
                current = []
        else:
            current.extend(group)
    if current:
        paragraphs.append(current)
    if len(paragraphs) != len(spans):
        return [block]
    result = []
    for (left, right), lines in zip(spans, paragraphs):
        literal = text[left:right]
        replay = '\n'.join(' '.join(line['text'].strip() for line in group)
                           for group in _native_groups({'native_lines': lines}))
        if _compact(literal) != _compact(replay):
            return [block]
        boxes = [line['bbox'] for line in lines]
        result.append({**block, 'normalized_text': literal,
            'text_sha256': text_sha256(literal), 'parent_text_sha256': block['text_sha256'],
            'source_range': [left, right], 'native_lines': lines,
            'bbox_fitz_unrotated_pt': [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                      max(b[2] for b in boxes), max(b[3] for b in boxes)]})
    return result


def _tabular_runs(block):
    # A superscript, unit or separate font run inside prose is not a cell.
    # Require repeated, aligned numeric-only runs with an actual empty gutter
    # to another literal run. This still certifies geometry, never row meaning.
    numeric = re.compile(r'[$€£¥]?[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?')
    lanes = []
    for group in _native_groups(block):
        row = sorted(group, key=lambda line: line['bbox'][0])
        for index, line in enumerate(row):
            if not numeric.fullmatch(line['text'].strip()):
                continue
            before = row[index - 1] if index else None
            after = row[index + 1] if index + 1 < len(row) else None
            if not (before and line['bbox'][0] - before['bbox'][2] >= 8
                    or after and after['bbox'][0] - line['bbox'][2] >= 8):
                continue
            edge = line['bbox'][2]
            lane = next((values for values in lanes if abs(values[0][0] - edge) <= 5), None)
            if lane is None:
                lane = []
                lanes.append(lane)
            lane.append((edge, line['bbox'][1]))
    return any(len({round(y, 1) for _, y in lane}) >= 3 for lane in lanes)


_TABLE_CAPTION = re.compile(r'^(?:Table\s+\d+(?:[.-]\d+)*\b|表\s*\d+\b)', re.I)


def _captioned_table_regions(ordered):
    """Whole, contiguous native regions below an explicit printed caption.

    MuPDF can split a table's caption, labels, header and data into separate
    blocks. Preserve all literal blocks in the one geometric region, including
    wrapped labels and subtotal rows. No model, question terms or gold define
    its boundary; numeric cell meanings and cross-page relations stay unknown.
    """
    regions = []
    physical = sorted(ordered, key=lambda item: (item['bbox_fitz_unrotated_pt'][1],
                                                item['bbox_fitz_unrotated_pt'][0]))
    for caption in physical:
        if not _TABLE_CAPTION.match(caption['normalized_text'].strip()):
            continue
        cb = caption['bbox_fitz_unrotated_pt']
        native = caption.get('native_lines') or []
        heights = [line['bbox'][3] - line['bbox'][1] for line in native if line.get('bbox')]
        if not heights or len(caption['normalized_text']) > 300:
            continue
        height = sorted(heights)[len(heights) // 2]
        # A caption can be much narrower than its table. Inspect the complete
        # following horizontal section; a competing caption or narrative block
        # rejects the proposal rather than trimming away distant literal cells.
        x0 = min(block['bbox_fitz_unrotated_pt'][0] for block in physical)
        x1 = max(block['bbox_fitz_unrotated_pt'][2] for block in physical)
        bottom, members = cb[3], [caption]
        for block in physical:
            if block is caption:
                continue
            bb = block['bbox_fitz_unrotated_pt']
            if bb[1] < cb[3] or bb[0] < x0 or bb[2] > x1:
                continue
            gap = bb[1] - bottom
            if gap > (40 if len(members) == 1 else 2 * height + 4):
                break
            if _TABLE_CAPTION.match(block['normalized_text'].strip()):
                break
            members.append(block)
            bottom = max(bottom, bb[3])
        if len(members) < 2:
            continue
        merged = {'native_lines': [line for block in members[1:]
                                   for line in block.get('native_lines', [])]}
        if (not _tabular_runs(merged) or len(members) < 4
                or any(_tabular_runs(block) for block in members[1:])):
            continue
        if any(len(re.findall(r'[A-Za-z]+', block['normalized_text'])) >= 16
               and re.search(r'\b(?:is|are|was|were|has|have|must|shall|will|would)\b',
                             block['normalized_text'], re.I) for block in members[1:]):
            continue
        # An intersecting native block cannot be silently cropped out of the
        # purported complete region, including a competing second caption.
        if any(block not in members and block['bbox_fitz_unrotated_pt'][1] < bottom
               and block['bbox_fitz_unrotated_pt'][3] > cb[3]
               and block['bbox_fitz_unrotated_pt'][0] < x1
               and block['bbox_fitz_unrotated_pt'][2] > x0 for block in physical):
            continue
        # The caption can be narrower than its numeric columns. Bound those
        # columns from literal numeric runs, with a fixed font-height margin
        # for their printed headers/labels. A distant short sidebar must reject
        # expansion, rather than being interleaved as another alleged cell.
        number = re.compile(r'[$€£¥]?[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?')
        numeric_right = max((line['bbox'][2] for line in merged['native_lines']
                             if number.fullmatch(line['text'].strip())), default=cb[2])
        bounds = [cb[0] - 40, max(cb[2], numeric_right) + 4 * height + 4]
        supported = all(bounds[0] <= block['bbox_fitz_unrotated_pt'][0]
                        and block['bbox_fitz_unrotated_pt'][2] <= bounds[1]
                        for block in members)
        regions.append({'members': members, 'supported_horizontal_scope': supported,
                        'horizontal_bounds_unrotated_pt': bounds})
    return regions


def _aligned_native_cells(block):
    """A physical row of disjoint native runs, without interpreting its cells."""
    groups = _native_groups(block)
    if len(groups) != 1:
        return None
    cells = sorted(groups[0], key=lambda line: line['bbox'][0])
    if not 3 <= len(cells) <= 8:
        return None
    if any(right['bbox'][0] - left['bbox'][2] < 8
           for left, right in zip(cells, cells[1:])):
        return None
    return cells


def _uncaptioned_table_region(ordered, touched):
    """Return a closed, header-led row-block region, or False on ambiguity.

    None means this new shape does not apply. False must never fall back to
    a smaller row/header excerpt. Alignment is only a geometry witness, not
    certification of any semantic relation or of uninspected other pages.
    """
    physical = sorted((block for block in ordered if block['normalized_text'].strip()),
                      key=lambda block: (block['bbox_fitz_unrotated_pt'][1],
                                         block['bbox_fitz_unrotated_pt'][0]))
    signatures = {block['block_id']: _aligned_native_cells(block) for block in physical}
    touched_ids = {part['block_id'] for part in touched}
    # Multiple row blocks are necessary: existing single-block table and
    # paragraph closure policies remain authoritative for their own shapes.
    def aligned(cells, other):
        return (other is not None and len(cells) == len(other)
                and all(abs(a['bbox'][0] - b['bbox'][0]) <= 5
                        for a, b in zip(cells, other)))
    active = [block for block in physical if block['block_id'] in touched_ids
              and signatures[block['block_id']] is not None]
    if not active:
        # A wrapped/interleaved row block beside aligned row peers is not a
        # safe paragraph fallback. Its full table geometry remains unknown.
        for part in touched:
            pb = part['bbox_fitz_unrotated_pt']
            aligned_peers = [block for block in physical
                if signatures[block['block_id']] is not None
                and abs(block['bbox_fitz_unrotated_pt'][0] - pb[0]) <= 5
                and block['bbox_fitz_unrotated_pt'][2] >= pb[2] - 5]
            if len(aligned_peers) >= 2 and any(len(group) >= 3 for group in _native_groups(part)):
                return False
        return None
    reference = signatures[active[0]['block_id']]
    peers = [block for block in physical if aligned(reference, signatures[block['block_id']])]
    if len(peers) < 2:
        return None
    headers = [block for block in peers
               if not any(re.search(r'\d', line['text']) for line in signatures[block['block_id']])]
    if len(headers) != 1:
        return False
    header = headers[0]
    if any(block['bbox_fitz_unrotated_pt'][1] < header['bbox_fitz_unrotated_pt'][1]
           for block in peers):
        return False
    hb = header['bbox_fitz_unrotated_pt']
    height = max(line['bbox'][3] - line['bbox'][1] for line in reference)
    x0, x1 = hb[0] - 5, max(block['bbox_fitz_unrotated_pt'][2] for block in peers) + 5
    members, rows, annotations, boundary = [header], [], [], None
    bottom = hb[3]
    for block in physical[physical.index(header) + 1:]:
        bb = block['bbox_fitz_unrotated_pt']
        if bb[1] < bottom - .5 or bb[1] - bottom > 2 * height + 4:
            return False
        # Any neighbouring column/side note is a competing scope, not a
        # record that may be silently excluded from a complete table claim.
        if bb[0] < x0 or bb[2] > x1:
            # A full-width closed prose paragraph is an explicit boundary.
            if (bb[0] >= x0 and len(_native_groups(block)) >= 2
                    and re.search(r'[.。!?！？]$', block['normalized_text'].strip())):
                boundary = block
                break
            return False
        cells = signatures[block['block_id']]
        if aligned(reference, cells):
            if not any(re.search(r'\d', line['text']) for line in cells):
                return False
            rows.append(block)
            members.append(block)
        elif (cells is None and len(_native_groups(block)) == 1
              and abs(bb[0] - hb[0]) <= 5 and bb[2] < reference[1]['bbox'][0] - 8):
            # Whole first-column annotations remain with the surrounding
            # records (including qualifications), never used as a cut point.
            annotations.append(block)
            members.append(block)
        elif (cells is None and bb[0] >= x0
              and bb[2] > reference[1]['bbox'][0]
              and re.search(r'[.。!?！？]$', block['normalized_text'].strip())):
            boundary = block
            break
        else:
            return False
        bottom = bb[3]
    if boundary is None or len(rows) < 2 or not touched_ids.issubset(
            {block['block_id'] for block in members}):
        return False
    # Retain the whole preceding page section as literal scope. This avoids
    # borrowing a bare header while deleting sample/entity/date declarations.
    # Large or competing preceding sections fail the existing member/char cap.
    scope = physical[:physical.index(header)]
    if any(block['bbox_fitz_unrotated_pt'][0] < x0
           or block['bbox_fitz_unrotated_pt'][2] > x1
           or block['bbox_fitz_unrotated_pt'][3] > hb[1]
           for block in scope):
        return False
    if any(block not in members and block is not boundary
           and block['bbox_fitz_unrotated_pt'][1] < boundary['bbox_fitz_unrotated_pt'][1]
           and block['bbox_fitz_unrotated_pt'][3] > hb[1] for block in physical):
        return False
    return {'members': scope + members + [boundary], 'witness': {
        'version': 'native-uncaptioned-aligned-region-v1',
        'horizontal_bounds_unrotated_pt': [x0, x1],
        'header_block_id': header['block_id'],
        'row_block_ids': [block['block_id'] for block in rows],
        'scope_block_ids': [block['block_id'] for block in scope] + [boundary['block_id']],
        'annotation_block_ids': [block['block_id'] for block in annotations],
        'bottom_boundary': {'kind': 'native_prose_block', 'block_id': boundary['block_id'],
                            'bbox_fitz_unrotated_pt': boundary['bbox_fitz_unrotated_pt']},
        'page_local_only': True, 'semantic_row_column_binding_verified': False}}


def extract_native_page_context(raw, page_no, anchor_text, max_chars=1800):
    """Bounded literal paragraph/table region on one freshly read original page.

    All anchor characters must occur once in native reading order. Preserve
    whole touched paragraphs/blocks; include a unique adjacent native table
    and its geometric headers, never infer row relations or authorize math.
    """
    original = extract_native_context(raw, page_no, anchor_text, max_chars=max_chars)
    if (not isinstance(raw, bytes) or not raw or len(raw) > 20 * 1024 * 1024
            or type(page_no) is not int or page_no < 1 or type(max_chars) is not int
            or not 0 < max_chars <= 12000 or not isinstance(anchor_text, str)
            or not _compact(anchor_text)):
        return None
    try:
        import fitz
        with fitz.open(stream=raw, filetype='pdf') as document:
            if len(document) > 1000 or page_no > len(document):
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
    pieces = [part for block in ordered for part in _paragraph_parts(block)]
    located = _anchor_members(pieces, anchor_text)
    if located is None:
        return None
    touched, anchor_match = located
    if not touched or len(touched) > 6:
        return None
    captioned = [region for region in _captioned_table_regions(ordered)
                 if all(any(part['block_id'] == block['block_id'] for block in region['members'])
                        for part in touched)]
    if len(captioned) > 1 or captioned and not captioned[0]['supported_horizontal_scope']:
        return None
    uncapped = None if captioned else _uncaptioned_table_region(ordered, touched)
    if uncapped is False:
        from .native_table_chain import page_context
        return page_context(raw,page_no,anchor_text,max_chars)
    # A small title/header hit can recover one adjacent table block. Competing
    # tables, distant tables and unrelated column blocks do not expand it.
    tables = [block for block in ordered if _tabular_runs(block)]
    candidates = []
    for table in tables:
        tb = table['bbox_fitz_unrotated_pt']
        if any(part['block_id'] == table['block_id'] for part in touched):
            candidates.append(table)
        elif len(touched) == 1 and len(touched[0]['normalized_text']) <= 180:
            ab = touched[0]['bbox_fitz_unrotated_pt']
            if (0 <= tb[1] - ab[3] <= 40 and tb[0] - 8 <= ab[0] < tb[2]
                    and ab[2] <= tb[2] + 8):
                candidates.append(table)
    table_mode = len(candidates) == 1
    captioned_mode = len(captioned) == 1
    if uncapped:
        members = uncapped['members']
    elif captioned_mode:
        members = captioned[0]['members']
    elif table_mode:
        table = candidates[0]
        tb = table['bbox_fitz_unrotated_pt']
        region = [table]
        for block in ordered:
            bb = block['bbox_fitz_unrotated_pt']
            if (block is not table and len(block['normalized_text']) <= 300
                    and tb[1] - 60 <= bb[1] <= tb[1] + 25 and bb[3] <= tb[1] + 30
                    and tb[0] - 8 <= bb[0] and bb[2] <= tb[2] + 32):
                region.append(block)
        members = [block for block in ordered if any(block is item for item in region)]
    else:
        if original is not None:
            return original
        members = touched
        # No relaxation of incomplete prose closure; the old continuation
        # extractor remains authoritative for an open single native block.
        if any(continuation_state(part['normalized_text']) != 'native' for part in members):
            return None
        for part in members:
            value = part['normalized_text'].strip()
            if ('source_range' in part and len(re.findall(r'[A-Za-z]+', value)) >= 12
                    and (not re.search(r'[.。;；!?！？]$', value)
                         or re.match(r'[a-z]', value))):
                # A native block can end halfway through a paragraph at the
                # page bottom. Blank lines do not certify that final closure.
                return None
        for before, after in zip(members, members[1:]):
            if before['block_id'] == after['block_id']:
                continue  # Literal blank-line paragraphs of one native block.
            bb, ab = before['bbox_fitz_unrotated_pt'], after['bbox_fitz_unrotated_pt']
            heights = [line['bbox'][3] - line['bbox'][1] for line in before['native_lines']]
            if (_column(before, order) != _column(after, order)
                    or _column(before, order) is None or not heights
                    or not 0 <= ab[1] - bb[3] <= 2 * min(heights) + 4
                    or not (bb[0] < ab[2] and ab[0] < bb[2])):
                return None
    # Splitting a giant native block must not strip its literal declaration.
    # Inspect fresh preceding paragraphs, not stored chunk scope metadata.
    first = members[0]
    first_box = first['bbox_fitz_unrotated_pt']
    first_column = _column(first, order)
    preceding = [part for part in pieces if
                 (part['bbox_fitz_unrotated_pt'][3] <= first['bbox_fitz_unrotated_pt'][1]
                  and first_column is not None and _column(part, order) == first_column
                  and part['bbox_fitz_unrotated_pt'][0] < first_box[2]
                  and first_box[0] < part['bbox_fitz_unrotated_pt'][2])]
    prefixes, forecast = [], None
    for part in preceding:
        value = part['normalized_text'].strip().rstrip('.。')
        if _ENGLISH_FORECAST_HEADING.search(value) or _FORECAST_HEADING.search(value):
            forecast = part
        elif _ENGLISH_ACTUAL_HEADING.search(value):
            forecast = None
    for part in reversed(preceding):
        value = part['normalized_text'].strip().rstrip('.。')
        if not (_DECLARED_UNIT_HEADING.fullmatch(value) or _DECLARED_CONDITION_HEADING.fullmatch(value)):
            break
        prefixes.insert(0, part)
        if len(prefixes) > MAX_LOCAL_SCOPE_HEADINGS:
            return None
    if forecast is not None and not any(part is forecast for part in prefixes):
        prefixes.insert(0, forecast)
    if captioned_mode and original is not None:
        # The narrow extractor already proves immediate declarations and
        # spanning forecast headings for this caption. A table expansion must
        # keep them even when the caption spans inferred body columns.
        retained_ids = {part['block_id'] for part in members + prefixes}
        original_ids = {part['block_id'] for part in original['members']}
        prefixes = [part for part in ordered if part['block_id'] in original_ids
                    and part['block_id'] not in retained_ids] + prefixes
    members = prefixes + members
    source_member_text = '\n\n'.join(part['normalized_text'] for part in members)
    row_layout = []
    if captioned_mode or uncapped:
        # Render all native runs in physical row order, retaining geometry
        # rather than inventing header:value, pipes or calculator bindings.
        # A source block's anchor can be interleaved with the neighbouring
        # cells; its complete literal member remains independently pinned.
        native = {'native_lines': [line for part in members for line in part['native_lines']]}
        groups = _native_groups(native)
        text = DocumentChunker.clean_text('\n'.join(' '.join(
            line['text'].strip() for line in sorted(group, key=lambda line: line['bbox'][0]))
            for group in groups))
        row_layout = [[{'bbox_fitz_unrotated_pt': line['bbox'],
                        'text_sha256': text_sha256(line['text'])}
                       for line in sorted(group, key=lambda line: line['bbox'][0])]
                      for group in groups]
    else:
        text = source_member_text
    if (len(members) > (16 if captioned_mode or uncapped else 8) or len(text) > max_chars
            or len(anchor_ranges(source_member_text, DocumentChunker.clean_text(anchor_text))) != 1):
        return None
    return {'text': text, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'page_no': page_no,
        'evidence_sha256': text_sha256(text), 'text_sha256': text_sha256(text),
        'evidence_chars': len(text), 'max_chars': max_chars,
        'extraction_version': ('original-native-bounded-page-region-v4' if uncapped else
                               'original-native-bounded-page-region-v3'),
        'mode': ('original_native_complete_uncaptioned_table_region' if uncapped else
                 'original_native_complete_captioned_table_region' if captioned_mode else
                 'original_native_complete_table_region' if table_mode else
                 'original_native_complete_paragraph_region'),
        'members': [_member(part) for part in members], 'reading_order': order,
        'native_row_layout': row_layout,
        'captioned_native_region': ({'horizontal_bounds_unrotated_pt':
                                     captioned[0]['horizontal_bounds_unrotated_pt'],
                                     'semantic_row_column_binding_verified': False}
                                    if captioned_mode else None),
        **({'uncaptioned_native_region': uncapped['witness']} if uncapped else {}),
        'anchor_match_policy': ANCHOR_POLICY_VERSION, 'anchor_match': anchor_match,
        'unit_insertions': [], 'column_transition': None, 'calculator_input_eligible': False}
