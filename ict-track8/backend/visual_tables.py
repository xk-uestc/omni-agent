"""Bounded line-grid PDF tables, with literal native-text layout provenance.

Layout verification establishes geometric row / column membership in a strict
native vector grid, not visible OCR accuracy or semantic truth. The supported
header convention is one internal first grid row and one first-column row key.
Merged, partial, external or ambiguous headers abstain instead of being guessed.
"""
from __future__ import annotations

import hashlib
from bisect import bisect_right
import json
import math
import re
from typing import Any

import fitz


class VisualTableError(ValueError):
    pass


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _coords(rect):
    return [float(item) for item in rect]


def _matrix(matrix):
    return [float(getattr(matrix, key)) for key in ('a', 'b', 'c', 'd', 'e', 'f')]


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise VisualTableError(f'{name} must be a positive integer')


def _contains(container, child, tolerance=0.05):
    return (container.x0 - tolerance <= child.x0 and container.y0 - tolerance <= child.y0
            and container.x1 + tolerance >= child.x1 and container.y1 + tolerance >= child.y1)


def _text(tokens):
    lines = {}
    for token in tokens:
        lines.setdefault((token['block_no'], token['line_no']), []).append(token)
    ordered = sorted(lines.values(), key=lambda line: (min(w['bbox_fitz_unrotated_pt'][1] for w in line),
                                                       min(w['bbox_fitz_unrotated_pt'][0] for w in line)))
    return '\n'.join(' '.join(w['text'] for w in sorted(line, key=lambda word: word['word_no'])) for line in ordered)


def _label(text):
    return ' '.join(text.split())


def _number_like(text):
    return bool(re.fullmatch(r'[+−-]?(?:\d[\d,.]*|\(\d[\d,.]*\))\s*[%％]?', text.strip()))


def extract_pdf_tables(
    pdf_bytes: bytes,
    *,
    page_no: int,
    expected_source_sha256: str,
    crop_display_pt: list[float] | tuple[float, ...] | None = None,
    max_source_bytes: int = 20 * 1024 * 1024,
    max_pages: int = 1000,
    max_native_words: int = 20_000,
    max_drawing_items: int = 20_000,
    max_tables: int = 32,
    max_cells: int = 4000,
) -> dict[str, Any]:
    """Extract only line-strict grids from pinned bytes; never read a path/URL.

    PDF Rotate is temporarily normalized in an in-memory document for reading
    row order. Original rotation is used to map all output to displayed points.
    Cropping is selection only: a partial table cannot lose its header and then
    masquerade as another complete table. No rasterization or OCR is performed.
    """
    for value, name in ((max_source_bytes, 'max_source_bytes'), (max_pages, 'max_pages'),
                        (max_native_words, 'max_native_words'), (max_drawing_items, 'max_drawing_items'),
                        (max_tables, 'max_tables'), (max_cells, 'max_cells')):
        _positive(value, name)
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes or len(pdf_bytes) > max_source_bytes:
        raise VisualTableError('PDF bytes empty, invalid or over source budget')
    if (not isinstance(expected_source_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_source_sha256)
            or _sha(pdf_bytes) != expected_source_sha256):
        raise VisualTableError('source_sha256_mismatch')
    if type(page_no) is not int or page_no < 1:
        raise VisualTableError('page_no must be a 1-based integer')
    try:
        document = fitz.open(stream=pdf_bytes, filetype='pdf')
    except Exception as exc:
        raise VisualTableError('PDF cannot be parsed') from exc
    with document:
        if document.needs_pass:
            raise VisualTableError('encrypted PDF not accepted')
        if len(document) > max_pages or page_no > len(document):
            raise VisualTableError('page bounds or page-count budget exceeded')
        page = document[page_no - 1]
        rotation = page.rotation
        rotated_rect = fitz.Rect(page.rect)
        rotation_matrix = fitz.Matrix(page.rotation_matrix)
        clip = fitz.Rect(rotated_rect)
        if crop_display_pt is not None:
            if (not isinstance(crop_display_pt, (list, tuple)) or len(crop_display_pt) != 4
                    or any(type(v) not in (float, int) or not math.isfinite(v) for v in crop_display_pt)):
                raise VisualTableError('crop must contain four finite display points')
            clip = fitz.Rect(crop_display_pt)
            if not clip.is_valid or clip.is_empty or clip.is_infinite or not rotated_rect.contains(clip):
                raise VisualTableError('crop outside displayed page bounds')
        # The zero-rotation matrix includes true CropBox offsets. Using the
        # rotated page's transformation matrix can lose offsets in PyMuPDF.
        page.set_rotation(0)
        pdf_to_native = fitz.Matrix(page.transformation_matrix)
        words = page.get_text('words', sort=False)
        if len(words) > max_native_words:
            raise VisualTableError('native word budget exceeded')
        drawings = page.get_drawings()
        drawing_items = sum(len(item.get('items', [])) for item in drawings)
        if drawing_items > max_drawing_items:
            raise VisualTableError('drawing item budget exceeded before table detection')
        try:
            found = page.find_tables(strategy='lines_strict', paths=drawings)
        except Exception as exc:
            raise VisualTableError('strict grid detection failed') from exc
        if len(found.tables) > max_tables:
            raise VisualTableError('table budget exceeded')
        total_cells = sum(table.row_count * table.col_count for table in found.tables)
        if total_cells > max_cells:
            raise VisualTableError('cell budget exceeded')
        native = []
        for index, word in enumerate(words):
            rect = fitz.Rect(word[:4])
            displayed = rect * rotation_matrix
            native.append({'word_id': f'page:{page_no}:word:{index}', 'text': str(word[4]),
                           'block_no': int(word[5]), 'line_no': int(word[6]), 'word_no': int(word[7]),
                           'bbox_fitz_unrotated_pt': _coords(rect),
                           'bbox_display_pt': _coords(displayed),
                           'bbox_pdf_user_pt': _coords(rect * ~pdf_to_native),
                           'fully_contained_in_crop': clip.contains(displayed),
                           'provenance': 'native_text_layer_not_visible_ocr'})
        identity = {'source_sha256': expected_source_sha256, 'page_no': page_no,
                    'extractor': f'PyMuPDF:{fitz.VersionBind}:lines_strict:unrotated',
                    'crop_display_pt': _coords(clip), 'rotation': rotation,
                    'header_convention': 'one_internal_first_grid_row_and_first_column_row_key'}
        evidence_id = _sha(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode())
        result = {'schema_version': 'native-grid-table-v1', 'evidence_id': evidence_id,
                  'source_sha256': expected_source_sha256, 'page_no': page_no,
                  'page_count': len(document), 'rotation_degrees': rotation,
                  'page_display_rect_pt': _coords(rotated_rect), 'crop_display_pt': _coords(clip),
                  'crop_box_pdf_pt': _coords(page.cropbox),
                  'coordinate_frame': {'origin': 'top_left', 'unit': 'pt', 'rotation_applied': True},
                  'mappings': {'pdf_user_to_fitz_unrotated': _matrix(pdf_to_native),
                               'fitz_unrotated_to_display': _matrix(rotation_matrix)},
                  'extractor': identity['extractor'], 'header_convention': identity['header_convention'],
                  'native_words': native, 'tables': [], 'rejected_tables': [],
                  'warnings': ['native_text_layer_not_visible_ocr',
                               'layout_membership_not_general_semantic_or_visible_truth',
                               'single_internal_header_convention_not_arbitrary_multilevel_header_inference'],
                  'budgets': {'max_source_bytes': max_source_bytes, 'max_pages': max_pages,
                              'max_native_words': max_native_words, 'max_drawing_items': max_drawing_items,
                              'max_tables': max_tables, 'max_cells': max_cells}}

        def reject(table_id, bbox, reasons):
            result['rejected_tables'].append({'table_id': table_id, 'bbox_display_pt': _coords(bbox * rotation_matrix),
                                             'status': 'relation_unverified', 'reasons': sorted(set(reasons))})

        for index, table in enumerate(found.tables):
            bbox = fitz.Rect(table.bbox)
            displayed = bbox * rotation_matrix
            if not displayed.intersects(clip):
                continue
            table_id = f'{evidence_id}:table:{index}'
            reasons = []
            if not clip.contains(displayed):
                reasons.append('partial_table_crop')
            if table.row_count < 2 or table.col_count < 2:
                reasons.append('insufficient_grid_rows_or_columns')
            if table.header.external:
                reasons.append('external_header_not_bound')
            rows = table.rows
            if any(len(row.cells) != table.col_count or any(cell is None for cell in row.cells) for row in rows):
                reasons.append('merged_or_missing_cell')
            if reasons:
                reject(table_id, bbox, reasons)
                continue
            grid = [[fitz.Rect(cell) for cell in row.cells] for row in rows]
            xedges = [grid[0][0].x0, *(cell.x1 for cell in grid[0])]
            yedges = [grid[0][0].y0, *(row[0].y1 for row in grid)]
            for r, row in enumerate(grid):
                for c, rect in enumerate(row):
                    expected = fitz.Rect(xedges[c], yedges[r], xedges[c + 1], yedges[r + 1])
                    if any(abs(a - b) > 0.05 for a, b in zip(rect, expected)):
                        reasons.append('irregular_or_merged_grid')
            # Partition native tokens once per table. Avoid cells × words
            # quadratic work on otherwise valid maximum-budget inputs.
            cell_tokens = {}
            for word in native:
                word_rect = fitz.Rect(word['bbox_fitz_unrotated_pt'])
                if not word_rect.intersects(bbox):
                    continue
                c = bisect_right(xedges, (word_rect.x0 + word_rect.x1) / 2) - 1
                r = bisect_right(yedges, (word_rect.y0 + word_rect.y1) / 2) - 1
                if (not 0 <= r < len(grid) or not 0 <= c < len(grid[0])
                        or not _contains(grid[r][c], word_rect) or not word['fully_contained_in_crop']):
                    reasons.append('partial_or_cross_cell_word')
                    continue
                cell_tokens.setdefault((r, c), []).append(word)
            cells = []
            for r, row in enumerate(grid):
                cell_row = []
                for c, rect in enumerate(row):
                    tokens = cell_tokens.get((r, c), [])
                    cell_row.append({'cell_id': f'{table_id}:r:{r}:c:{c}', 'row_index': r, 'column_index': c,
                                     'raw_text': _text(tokens), 'word_ids': [word['word_id'] for word in tokens],
                                     'bbox_fitz_unrotated_pt': _coords(rect), 'bbox_display_pt': _coords(rect * rotation_matrix),
                                     'bbox_pdf_user_pt': _coords(rect * ~pdf_to_native)})
                cells.append(cell_row)
            headers = [_label(cell['raw_text']) for cell in cells[0]]
            if (any(not header or _number_like(header) for header in headers)
                    or len(set(headers)) != len(headers)):
                reasons.append('ambiguous_or_missing_column_header')
            row_keys = [_label(row[0]['raw_text']) for row in cells[1:]]
            if any(not key for key in row_keys) or len(set(row_keys)) != len(row_keys):
                reasons.append('ambiguous_or_missing_row_header')
            # Extra text-only leading grid rows can be a multirow header. This
            # numeric-fact adapter abstains rather than silently treating them
            # as a data row. Text-only tables are outside this first version.
            if any(not any(_number_like(cell['raw_text']) for cell in row[1:]) for row in cells[1:]):
                reasons.append('text_only_row_or_multilevel_header_unresolved')
            if (all(re.fullmatch(r'(?:19|20)\d{2}', cell['raw_text'].strip()) for cell in cells[1][1:])
                    and not any(re.search(r'(?:19|20)\d{2}', header) for header in headers)):
                # A second grid row made only of years may be a child header
                # below Actual / Forecast. Do not guess it is a data row.
                reasons.append('possible_year_subheader_unresolved')
            if reasons:
                reject(table_id, bbox, reasons)
                continue
            facts = []
            for r, row in enumerate(cells[1:], start=1):
                for c, cell in enumerate(row[1:], start=1):
                    if not cell['raw_text'].strip():
                        continue
                    facts.append({'fact_id': cell['cell_id'], 'fact_type': 'native_grid_cell_literal',
                                  'status': 'layout_verified', 'validation_scope': 'native_text_layer_not_visible_ocr',
                                  'source_sha256': expected_source_sha256, 'evidence_id': evidence_id,
                                  'page_no': page_no, 'table_id': table_id,
                                  'fact_key': {'table_id': table_id, 'row_index': r, 'column_index': c,
                                               'row_header': row_keys[r - 1], 'column_header_path': [headers[c]]},
                                  'row_header_cell_id': row[0]['cell_id'], 'column_header_cell_ids': [cells[0][c]['cell_id']],
                                  'value_cell_id': cell['cell_id'], 'raw_value': cell['raw_text'],
                                  'row_header_word_ids': row[0]['word_ids'], 'column_header_word_ids': cells[0][c]['word_ids'],
                                  'value_word_ids': cell['word_ids'], 'bbox_display_pt': cell['bbox_display_pt'],
                                  'unit': 'unknown', 'scale': None,
                                  'numeric_validation': 'not_parsed_or_unit_bound'})
            result['tables'].append({'table_id': table_id, 'status': 'layout_verified',
                                     'bbox_fitz_unrotated_pt': _coords(bbox), 'bbox_display_pt': _coords(displayed),
                                     'bbox_pdf_user_pt': _coords(bbox * ~pdf_to_native),
                                     'row_count': table.row_count, 'column_count': table.col_count,
                                     'header_bbox_display_pt': _coords(fitz.Rect(table.header.bbox) * rotation_matrix),
                                     'column_header_paths': [[header] for header in headers], 'row_headers': row_keys,
                                     'row_bboxes_display_pt': [_coords(fitz.Rect(row.bbox) * rotation_matrix) for row in rows],
                                     'cells': cells, 'facts': facts})
        result['status'] = 'layout_verified' if result['tables'] else 'relation_unverified'
        if not found.tables:
            result['warnings'].append('no_strict_line_grid_detected')
        return result


def lookup_table_fact(manifest, *, table_id, row_header, column_header_path):
    """Select a whole unique semantic key; never nearest value or fuzzy header.

    This consumes a trusted server extractor result, not a model-supplied
    manifest. Unit/Decimal interpretation must be a separate validated layer.
    """
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 'native-grid-table-v1'
            or not isinstance(table_id, str) or not isinstance(row_header, str)
            or not isinstance(column_header_path, list) or not column_header_path
            or any(not isinstance(part, str) or not part for part in column_header_path)):
        raise VisualTableError('invalid_literal_fact_key')
    tables = [table for table in manifest.get('tables', []) if table['table_id'] == table_id]
    if len(tables) != 1 or tables[0].get('status') != 'layout_verified':
        raise VisualTableError('table_not_uniquely_layout_verified')
    facts = [fact for fact in tables[0]['facts'] if fact['fact_key']['row_header'] == row_header
             and fact['fact_key']['column_header_path'] == column_header_path]
    if len(facts) != 1:
        raise VisualTableError('cell_header_path_not_uniquely_bound')
    return json.loads(json.dumps(facts[0]))
