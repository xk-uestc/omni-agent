"""Bounded parser observations with source geometry; recognition is not approval."""
from __future__ import annotations

import hashlib
import json
import math
import re
from html.parser import HTMLParser

MAX_ELEMENTS = 20000
MAX_CELLS = 50000
MAX_TEXT = 2_000_000


def observation_digest(element):
    keys = ('kind','page_no','order','bbox','text','html','cells','native_text','source_parts',
            'source_sha256','coordinate_frame','context_candidates','context_relation_status')
    value = {k:element[k] for k in keys if k in element}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class TableCells(HTMLParser):
    """Read text/spans only. Never render HTML, follow links or execute markup."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cells, self.row, self.col, self.current = [], -1, 0, None
        self.occupied = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            if self.current is not None:
                raise ValueError('nested_table_cell')
            self.row += 1
            self.col = 0
        elif tag in {'td', 'th'}:
            if self.row < 0 or self.current is not None:
                raise ValueError('invalid_table_structure')
            attrs = dict(attrs)
            spans = [int(attrs.get(k, '1')) for k in ('rowspan', 'colspan')]
            if any(v < 1 or v > 1000 for v in spans) or self.row > 5000:
                raise ValueError('table_span_budget')
            while (self.row, self.col) in self.occupied:
                self.col += 1
            self.current = {'row': self.row, 'column': self.col,
                            'row_span': spans[0], 'column_span': spans[1],
                            'is_header': tag == 'th', 'text': '', 'bbox': None}
        elif tag == 'br' and self.current is not None:
            self.current['text'] += '\n'

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data
            if len(self.current['text']) > 10000:
                raise ValueError('cell_text_budget')

    def handle_endtag(self, tag):
        if tag in {'td', 'th'}:
            if self.current is None:
                raise ValueError('invalid_table_close')
            cell = self.current
            for row in range(cell['row'], cell['row'] + cell['row_span']):
                for col in range(cell['column'], cell['column'] + cell['column_span']):
                    if (row, col) in self.occupied:
                        raise ValueError('overlapping_table_cells')
                    self.occupied.add((row, col))
            if len(self.occupied) > MAX_CELLS or len(self.cells) >= MAX_CELLS:
                raise ValueError('table_cell_budget')
            cell['text'] = cell['text'].strip()
            self.cells.append(cell)
            self.col += cell['column_span']
            self.current = None


def html_cells(text):
    parser = TableCells()
    parser.feed(text)
    parser.close()
    if parser.current is not None or not parser.cells:
        raise ValueError('incomplete_table')
    return parser.cells


def _bbox(box, width, height, *, normalized=False):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise ValueError('missing_element_geometry')
    if any(type(v) not in {int, float} or not math.isfinite(v) for v in box):
        raise ValueError('invalid_element_geometry')
    x0, y0, x1, y1 = box
    if normalized:
        x0, x1, y0, y1 = x0 * width, x1 * width, y0 * height, y1 * height
    if not (0 <= x0 < x1 <= width + .1 and 0 <= y0 < y1 <= height + .1):
        raise ValueError('element_outside_page')
    return [round(v, 4) for v in (x0, y0, min(x1, width), min(y1, height))]


def _inline(content):
    if isinstance(content, str):
        return content
    return ''.join(str(s.get('content', '')) for s in content or []
                   if s.get('type') in {'text', 'equation', 'inline_equation'})


def normalize_mineru(raw, geometry, sha256):
    """MinerU 4 docvortex normalized coordinates -> explicit display points."""
    if raw.get('schema') != 'docvortex.middle' or raw.get('metadata', {}).get('producer', {}).get('name') != 'mineru':
        raise ValueError('unsupported_parser_schema')
    dimensions = {p['page_idx']: p for p in raw.get('extensions', {}).get('docvortex_layout', {}).get('pages', [])}
    pages = raw.get('pages', [])
    if len(pages) != len(geometry) or {p.get('page_idx') for p in pages} != set(range(len(geometry))):
        raise ValueError('incomplete_parser_pages')
    elements = []
    for page in pages:
        no = page['page_idx'] + 1
        width, height = geometry[no - 1]
        declared = dimensions.get(no - 1, {})
        if abs(declared.get('width_pt', 0) - width) > .2 or abs(declared.get('height_pt', 0) - height) > .2:
            raise ValueError('parser_coordinate_frame_mismatch')
        for block in page.get('blocks', []):
            kind = block.get('type')
            children = block.get('content', [])
            if kind in {'table', 'image', 'chart'}:
                if not isinstance(children, list):
                    raise ValueError('invalid_compound_block')
                for child in children:
                    ck = child.get('type')
                    if ck == 'table_body':
                        elements.append({'kind': 'table', 'page_no': no, 'order': child['index'],
                            'bbox': _bbox(child.get('bbox'), width, height, normalized=True),
                            'text': '', 'html': child['content'], 'cells': html_cells(child['content'])})
                    elif ck and (ck.endswith('caption') or ck.endswith('footnote')):
                        elements.append({'kind': ck, 'page_no': no, 'order': child['index'],
                            'bbox': _bbox(child.get('bbox'), width, height, normalized=True), 'text': _inline(child.get('content'))})
                if kind != 'table':
                    elements.append({'kind': kind, 'page_no': no, 'order': block['index'],
                        'bbox': _bbox(block.get('bbox'), width, height, normalized=True), 'text': ''})
            else:
                elements.append({'kind': 'formula' if kind == 'equation' else kind,
                    'page_no': no, 'order': block['index'],
                    'bbox': _bbox(block.get('bbox'), width, height, normalized=True),
                    'text': _inline(block.get('content'))})
    return seal(elements, geometry, sha256, raw['metadata']['producer'])


def seal(elements, geometry, sha256, parser):
    if len(elements) > MAX_ELEMENTS:
        raise ValueError('document_element_budget')
    elements.sort(key=lambda e: (e['page_no'], e['order']))
    if sum(len(json.dumps(e, ensure_ascii=False)) for e in elements) > MAX_TEXT:
        raise ValueError('document_structure_budget')
    for n, element in enumerate(elements):
        element['element_id'] = f'e{n:05d}'
        element['source_sha256'] = sha256
        element['coordinate_frame'] = 'fitz_display_pt'
        element['recognition_status'] = 'parser_observation'
        element['review_status'] = 'requires_review'
        element['confidence'] = None
        element['calculator_input_eligible'] = False
        element['memory_promotion_eligible'] = False
    for element in elements:
        if element['kind'] != 'formula':
            continue
        x0,y0,x1,y1 = element['bbox']
        candidates = []
        for other in elements:
            if other['page_no'] != element['page_no'] or other['kind'] not in {'text','paragraph'}:
                continue
            a,b,c,d = other['bbox']
            gap = max(y0-d, b-y1, 0)
            overlap = max(0, min(x1,c)-max(x0,a)) / max(1,min(x1-x0,c-a))
            if gap <= 100 and overlap >= .5:
                candidates.append((gap,other['element_id']))
        element['context_candidates'] = [e for _,e in sorted(candidates)[:3]]
        element['context_relation_status'] = 'nearby_same_page_region_not_parameter_binding'
        from .formula_conversion import convert_latex_arithmetic
        expression = re.sub(r'\\tag\{\d{1,4}\}\s*$', '', element['text'].strip().strip('$')).strip()
        if expression.count('=') == 1:
            expression = expression.split('=',1)[1].strip()
        try:
            element['conversion_candidate'] = convert_latex_arithmetic(expression)
        except (ValueError, SyntaxError, IndexError) as exc:
            element['conversion_candidate'] = {'status':'unsupported_or_incomplete', 'reason':str(exc),
                                               'calculator_input_eligible':False}
    for element in elements:
        element['observation_sha256'] = observation_digest(element)
    return {'schema': 'omni.structured-document.v1', 'source_sha256': sha256,
            'parser': parser, 'pages': [{'page_no': n + 1, 'width_pt': g[0], 'height_pt': g[1]} for n, g in enumerate(geometry)],
            'elements': elements, 'automatic_formula_execution': False}


def normalize_docling(raw, geometry, sha256, version):
    if raw.get('schema_name') != 'DoclingDocument' or len(raw.get('pages', {})) != len(geometry):
        raise ValueError('unsupported_or_incomplete_docling_document')
    def box(value, width, height):
        if value.get('coord_origin') == 'BOTTOMLEFT':
            values = [value['l'], height - value['t'], value['r'], height - value['b']]
        elif value.get('coord_origin') == 'TOPLEFT':
            values = [value['l'], value['t'], value['r'], value['b']]
        else:
            raise ValueError('unknown_coordinate_origin')
        return _bbox(values, width, height)
    items = {i['self_ref']: i for key in ('texts', 'tables', 'pictures', 'groups') for i in raw.get(key, [])}
    ordered, seen = [], set()
    def visit(ref, depth=0):
        if depth > 30 or ref in seen or ref not in items:
            raise ValueError('invalid_document_reference')
        seen.add(ref)
        item = items[ref]
        if ref.startswith('#/groups/'):
            for child in item.get('children', []):
                visit(child['$ref'], depth + 1)
        else:
            ordered.append(item)
    for child in raw.get('body', {}).get('children', []):
        visit(child['$ref'])
    elements = []
    for order, item in enumerate(ordered):
        prov = item.get('prov', [])
        if not prov or len({p['page_no'] for p in prov}) != 1:
            raise ValueError('ambiguous_element_page')
        no = prov[0]['page_no']
        if type(no) is not int or not 1 <= no <= len(geometry):
            raise ValueError('invalid_element_page')
        w, h = geometry[no - 1]
        size = raw['pages'][str(no)]['size']
        if abs(size['width'] - w) > .2 or abs(size['height'] - h) > .2:
            raise ValueError('parser_coordinate_frame_mismatch')
        kind = item.get('label')
        parts = [box(p['bbox'], w, h) for p in prov]
        bounds = [min(p[0] for p in parts), min(p[1] for p in parts), max(p[2] for p in parts), max(p[3] for p in parts)]
        e = {'kind': 'formula' if kind == 'formula' else kind, 'page_no': no, 'order': order,
             'bbox': bounds, 'source_parts': parts, 'text': item.get('text', ''), 'native_text': item.get('orig', '')}
        if kind == 'table':
            cells = item['data']['table_cells']
            if len(cells) > MAX_CELLS:
                raise ValueError('table_cell_budget')
            e['cells'] = [{'row': c['start_row_offset_idx'], 'column': c['start_col_offset_idx'],
                'row_span': c['row_span'], 'column_span': c['col_span'], 'text': c['text'],
                'is_header': c.get('column_header', False), 'bbox': box(c['bbox'], w, h) if c.get('bbox') else None}
                for c in cells]
        elements.append(e)
    return seal(elements, geometry, sha256, {'name': 'docling', 'version': version})


def chunks_from_structure(structure, document_id, chunker):
    """Index independent formulas and bounded table rows with their cell identity."""
    chunks = []
    for e in structure['elements']:
        base = {'structured_element_id': e['element_id'], 'parser': structure['parser'],
                'recognition_status': e['recognition_status'], 'review_status': e['review_status'],
                'calculator_input_eligible': False, 'memory_promotion_eligible': False,
                'source_bbox_fitz_display_pt': e['bbox'], 'coordinate_frame': e['coordinate_frame'],
                'observation_sha256': e['observation_sha256']}
        locator = f'page:{e["page_no"]}/element:{e["element_id"]}'
        if e['kind'] == 'table':
            rows = sorted({c['row'] for c in e['cells']})
            for row in rows:
                cells = [c for c in e['cells'] if c['row'] == row]
                text = ' | '.join(c['text'] for c in cells)
                meta = {**base, 'structured_table_cells': cells, 'table_id': e['element_id'],
                        'row_index': row, 'table_structure_status': 'recognized_requires_review'}
                # No headers are guessed; rowspan/colspan remain in the table record.
                chunks.extend(chunker._make_chunks(document_id, 'pdf', 'table_row', text,
                    locator=locator + f'/row:{row}', page_no=e['page_no'], quality=.8,
                    warnings=('parser_observation_requires_review',), metadata=meta))
        elif e['text'].strip():
            kind = 'formula' if e['kind'] == 'formula' else 'heading' if e['kind'] in {'title', 'heading'} else 'paragraph'
            chunks.extend(chunker._make_chunks(document_id, 'pdf', kind, e['text'],
                locator=locator, page_no=e['page_no'], quality=.8,
                warnings=('parser_observation_requires_review',), metadata=base))
    return [c.to_dict() for c in chunks]
