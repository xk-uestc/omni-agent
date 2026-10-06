"""Revalidate text-table proposals against native financial header/cell geometry.

Grouping is literal layout evidence, not semantic truth. Missing dash cells are
never zero. Repeated labels retain distinct row identities. No OCR is inferred.
"""
import hashlib
import re
import fitz

from .native_text_tables import _bbox, _text, column_unit_declaration

YEAR = re.compile(r'(?:19|20)\d{2}')
VALUE = re.compile(r'(?:[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|\((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\))')


def _rows(words):
    result = []
    for word in sorted(words, key=lambda w: ((w[1]+w[3])/2, w[0])):
        cy = (word[1]+word[3])/2
        if result and abs(result[-1]['cy']-cy) <= 2:
            result[-1]['words'].append(word)
        else:
            result.append({'cy':cy, 'words':[word]})
    for row in result:
        row['words'].sort(key=lambda w:w[0])
    return result


def extract_candidates(page, words, *, source_sha256, page_no, display_matrix):
    """Return verified local tables only with explicit grouped year/unit headers."""
    rows = _rows(words)
    headers = []
    for row in rows:
        years = [w for w in row['words'] if YEAR.fullmatch(w[4])]
        if not 2 <= len(years) <= 8:
            continue
        centers = [(w[0]+w[2])/2 for w in years]
        gaps = [b-a for a,b in zip(centers,centers[1:])]
        if min(gaps) < 25:
            continue
        cuts = [(a+b)/2 for a,b in zip(centers,centers[1:])]
        bounds = [centers[0]-gaps[0]/2, *cuts, centers[-1]+gaps[-1]/2]
        unit_rows = [r for r in rows if 0 < r['cy']-row['cy'] <= 24]
        units = None
        for unit_row in unit_rows:
            proposed = [[w for w in unit_row['words'] if bounds[c] <= w[0] and w[2] <= bounds[c+1]] for c in range(len(years))]
            if any(not cell for cell in proposed):
                continue
            try:
                declared = [column_unit_declaration([_text(cell)]) for cell in proposed]
            except ValueError:
                continue
            if all(d['unit'].startswith(('currency:', 'currency_symbol:')) for d in declared):
                units = (unit_row, proposed, declared)
                break
        if units is None:
            continue
        # Repeated years require the actual spanning group labels, not the
        # nearest arbitrary page title. Each group must physically overlap at
        # least two contiguous column lanes and contain distinct year tokens.
        groups = None
        for above in reversed([r for r in rows if 0 < row['cy']-r['cy'] <= 30]):
            segments = []
            for word in above['words']:
                if segments and word[0]-segments[-1][-1][2] < 12:
                    segments[-1].append(word)
                else:
                    segments.append([word])
            assignment = [None]*len(years)
            valid = True
            for segment in segments:
                box = _bbox(segment)
                columns = [c for c in range(len(years)) if min(box.x1,bounds[c+1])-max(box.x0,bounds[c]) > 1]
                if not columns:
                    continue
                label = _text(segment)
                if (len(columns) < 2 or columns != list(range(columns[0],columns[-1]+1))
                        or any(assignment[c] is not None for c in columns)
                        or len({years[c][4] for c in columns}) != len(columns)
                        or any(VALUE.fullmatch(w[4]) for w in segment)):
                    valid = False
                    break
                for c in columns:
                    assignment[c] = (label, segment)
            if valid and all(assignment) and len({a[0] for a in assignment}) >= 2:
                groups = assignment
                break
        if groups is not None:
            headers.append((row, years, bounds, units, groups))
    if not headers:
        return []
    # Existing upstream detector supplies geometry proposals only. No cell
    # string returned by find_tables() is accepted as a monetary fact.
    proposals = page.find_tables(strategy='text', min_words_vertical=3, min_words_horizontal=1).tables
    if len(proposals) > 16 or len(headers) > 16:
        return []
    result = []
    for header, years, bounds, units, groups in headers:
        proposed = [t for t in proposals if t.bbox[1]-3 <= header['cy'] <= t.bbox[3]
                    and t.bbox[0] <= years[0][0] and t.bbox[2] >= years[-1][2]-1]
        if len(proposed) != 1:
            continue
        proposal = proposed[0]
        if sum(proposal.bbox[1]-3 <= h[0]['cy'] <= proposal.bbox[3] for h in headers) != 1:
            # One proposal covering several independent header scopes cannot
            # silently assign the lower table's values to the upper headers.
            continue
        if any(tuple(line.get('dir',(1.,0.))) != (1.,0.)
                for block in page.get_text('dict')['blocks'] if 'lines' in block
                for line in block['lines']
                if fitz.Rect(line['bbox']).intersects(fitz.Rect(proposal.bbox))):
            continue
        unit_row, unit_cells, declarations = units
        # Header text centers do not define number cell gutters: a long
        # right-aligned parenthesized decimal can cross that midpoint while
        # the actual neighboring values remain far apart. First bind complete
        # numeric rows to their printed year/right-edge anchors, then compute
        # empty gutters from ALL full original tokens, including parentheses.
        candidate_rows = [r for r in rows if unit_row['cy']+3 < r['cy'] <= proposal.bbox[3]+1]
        aligned = []
        for row in candidate_rows:
            cells = [[w for w in row['words'] if abs(w[2]-year[2]) <= 8
                      and (VALUE.fullmatch(w[4]) or w[4] in ('-', '—', '–'))] for year in years]
            if all(len(cell)==1 for cell in cells) and len({tuple(cell[0]) for cell in cells})==len(years):
                aligned.append(cells)
        if len(aligned) < 3:
            continue
        minima = [min(cells[c][0][0] for cells in aligned) for c in range(len(years))]
        maxima = [max(cells[c][0][2] for cells in aligned) for c in range(len(years))]
        if any(b-a < 8 for a,b in zip(maxima,minima[1:])):
            continue
        bounds = [minima[0]-2,*[(a+b)/2 for a,b in zip(maxima,minima[1:])],maxima[-1]+2]
        identity = hashlib.sha256(f'{source_sha256}:{page_no}:grouped-financial:{proposal.bbox}:{header["cy"]}'.encode()).hexdigest()[:24]
        note_words = [w for w in header['words'] if w[2] < bounds[0] and re.fullmatch(r'Notes?|附注',w[4],re.I)]
        note_box = _bbox(note_words) if len(note_words) == 1 else None
        facts, missing, labels = [], [], []
        candidates = candidate_rows
        for row_index, row in enumerate(candidates):
            cells = [[w for w in row['words'] if bounds[c] <= w[0] and w[2] <= bounds[c+1]] for c in range(len(years))]
            if any(len(cell) != 1 or not (VALUE.fullmatch(cell[0][4]) or cell[0][4] in ('-', '—', '–')) for cell in cells):
                continue
            numeric_words = [cell[0] for cell in cells]
            left = [w for w in row['words'] if w[2] < bounds[0]-2]
            note = [w for w in left if note_box is not None and re.fullmatch(r'\d{1,3}',w[4])
                    and note_box.x0-3 <= w[0] and w[2] <= note_box.x1+3]
            label_words = [w for w in left if w not in note]
            if (not label_words or any(VALUE.fullmatch(w[4]) for w in label_words)
                    or len(note) > 1 or len(label_words)+len(note)+len(numeric_words) != len(row['words'])
                    or any(a[2] > b[0] for a,b in zip(row['words'],row['words'][1:]))):
                continue
            label = _text(label_words)
            row_id = f'{identity}:row:{row_index}'
            labels.append(label)
            for c,word in enumerate(numeric_words):
                path = [groups[c][0],years[c][4],_text(unit_cells[c])]
                boxes = [list(_bbox(groups[c][1])*display_matrix), list(fitz.Rect(years[c][:4])*display_matrix),list(_bbox(unit_cells[c])*display_matrix)]
                bbox = list(fitz.Rect(word[:4])*display_matrix)
                if word[4] in ('-', '—', '–'):
                    missing.append({'row_id':row_id,'row_header':label,'column_header_path':path,
                        'raw_value':word[4],'bbox_display_pt':bbox,'meaning':'missing_or_not_applicable_not_zero'})
                    continue
                declaration = declarations[c]
                unit_proof = {'binding':'own_explicit_column_header_only','header_path':path,
                    'header_bboxes_display_pt':boxes,'unit':declaration['unit'],
                    'currency':declaration['currency'],'multiplier':declaration['scale']}
                fact = {'fact_id':f'{row_id}:column:{c}', 'table_id':identity,'native_row_id':row_id,
                    'row_header':label,'column_header_path':path,'column_header_bboxes_display_pt':boxes,
                    'row_header_bbox_display_pt':list(_bbox(label_words)*display_matrix),
                    'bbox_display_pt':bbox,'raw_value':word[4],'source_sha256':source_sha256,'page_no':page_no,
                    'period':years[c][4],'period_status':'explicit_column_year','unit':declaration['unit'],
                    'currency':declaration['currency'],'scale':declaration['scale'],
                    'unit_evidence':unit_proof,'scale_evidence':unit_proof if declaration['scale'] else None,
                    'value_kind':'native_grouped_financial_cell_literal','calculator_input_eligible':False,
                    'physical_calculator_input_eligible':False,
                    'validation_scope':'native_geometry_and_literal_headers_not_ocr_or_semantic_truth'}
                if word[4].startswith('('):
                    fact['sign_evidence'] = {'binding':'own_complete_parenthesized_cell',
                        'raw_value':word[4],'bbox_display_pt':bbox,'negative':True}
                facts.append(fact)
        if len(labels) < 3 or not facts:
            continue
        if len(facts) > 512:
            continue
        result.append({'table_id':identity,'table_kind':'native_grouped_financial_statement',
            'status':'native_alignment_verified','source_sha256':source_sha256,'page_no':page_no,
            'bbox_display_pt':list(fitz.Rect(proposal.bbox)*display_matrix),
            'column_header_paths':[[groups[c][0],years[c][4],_text(unit_cells[c])] for c in range(len(years))],
            'row_headers':labels,'facts':facts,'missing_cells':missing,
            'complete_scope':'individually_complete_native_rows_not_complete_financial_statement',
            'scope_status':'explicit_group_year_unit_headers','calculator_input_eligible':False,
            'local_geometry_proposal':'pymupdf_text_strategy_revalidated_native_word_geometry'})
    return result
