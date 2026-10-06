"""Conservative native-word aligned tables; no inferred headers or OCR facts.

PDF text-strategy table proposals are not authority: words must independently
form continuous aligned rows and explicit column labels. Units remain literal,
unknown units/periods are disclosed, and facts are never calculator inputs.
"""
from __future__ import annotations
import hashlib
import re
from statistics import median

import fitz

from .native_fraction import parse_native_fraction_cell

_NUMBER=re.compile(r'[$€¥]?[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?')
_FRACTION_CELL=re.compile(r'[0-9]{1,12}/[0-9]{1,12}')
_MONEY=re.compile(r'([$€¥])([+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(m|k|bn|million|billion|thousand)?')
_SCALES={'m':'1000000','million':'1000000','k':'1000','thousand':'1000',
         'bn':'1000000000','billion':'1000000000'}


def _native_cell_token(text):
    # A fraction proposes geometry only. It becomes a distinct typed fact
    # only after the own-column count header and literal proof both validate.
    return bool(_NUMBER.fullmatch(text) or _FRACTION_CELL.fullmatch(text))


def _bbox(words):
    return fitz.Rect(min(w[0] for w in words),min(w[1] for w in words),max(w[2] for w in words),max(w[3] for w in words))


def _text(words):
    return ' '.join(w[4] for w in sorted(words,key=lambda w:w[0]))


def column_unit_declaration(path):
    """Only literal declarations inside one geometrically bounded column."""
    text = ' '.join(path)
    codes = set(re.findall(r'\b(?:USD|EUR|GBP|CNY|JPY|CAD|AUD|MYR|SGD|INR|RM)\b', text))
    symbols = set(re.findall(r'[$€£¥]', text))
    percent = bool(re.search(r'%|\bpercent(?:age)?\b', text, re.I))
    if len(codes) > 1 or len(symbols) > 1 or percent and (codes or symbols):
        raise ValueError('column_unit_declaration_conflict')
    code = next(iter(codes), None)
    symbol = next(iter(symbols), None)
    if code and symbol and (code, symbol) not in {
            ('USD','$'), ('CAD','$'), ('AUD','$'), ('EUR','€'), ('GBP','£'), ('CNY','¥'), ('JPY','¥')}:
        raise ValueError('column_unit_declaration_conflict')
    unit = 'percent' if percent else 'currency:'+code if code else 'currency_symbol:'+symbol if symbol else 'unknown'
    scales = {m.casefold().rstrip('s') for m in re.findall(r'\b(?:thousands?|millions?|billions?)\b', text, re.I)}
    if unit.startswith(('currency:', 'currency_symbol:')) and re.search(r'\bbn\b', text, re.I):
        scales.add('billion')
    # Common financial header notation is a literal multiplier declaration.
    # It must remain inside this column, never borrowed from another heading.
    if re.search(r"(?:['’]\s*000(?:s)?\b|\b000s\b)", text, re.I):
        scales.add('thousand')
    if len(scales) > 1 or scales and unit == 'unknown':
        raise ValueError('column_scale_declaration_unbound')
    scale = _SCALES[next(iter(scales))] if scales else None
    return {'unit':unit, 'currency':code or 'unknown', 'scale':scale, 'symbol':symbol}


def _explicit_amount_lanes(words, layout_width):
    """Propose local rows only from native explicit monetary column headings.

    Headers nominate geometry, never values. Empty vertical gutters bound each
    local panel using ALL intervening words, including competing chart text.
    The ordinary table validator still owns headers, units and row uniqueness.
    """
    lines=[]
    for word in sorted(words,key=lambda w:((w[1]+w[3])/2,w[0])):
        cy=(word[1]+word[3])/2
        if lines and abs(cy-lines[-1]['cy'])<=2:lines[-1]['words'].append(word)
        else:lines.append({'cy':cy,'words':[word]})
    segments=[]
    for line in lines:
        groups=[]
        for word in sorted(line['words'],key=lambda w:w[0]):
            if groups and word[0]-groups[-1][-1][2]<18:groups[-1].append(word)
            else:groups.append([word])
        segments.extend({'cy':line['cy'],'words':group,'box':_bbox(group)} for group in groups)
    seeds=[]
    for segment in segments:
        if any(_MONEY.fullmatch(w[4]) or _NUMBER.fullmatch(w[4]) and
               not re.fullmatch(r'(?:19|20)\d{2}',w[4]) for w in segment['words']):continue
        try:declaration=column_unit_declaration([_text(segment['words'])])
        except ValueError:continue
        if declaration['unit'].startswith(('currency:', 'currency_symbol:')):seeds.append(segment)
    if len(seeds)>16:return []
    candidates=[]
    for seed in seeds:
        header=[seed]
        # A unit-only second line is attached to its own overlapping heading.
        for _ in range(2):
            top=header[-1]
            above=[s for s in segments if 0<top['cy']-s['cy']<=26 and
                   min(top['box'].x1,s['box'].x1)>max(top['box'].x0,s['box'].x0)]
            if not above:break
            nearest=min(top['cy']-s['cy'] for s in above)
            above=[s for s in above if abs(top['cy']-s['cy']-nearest)<=2]
            if len(above)!=1:break
            preceding=above[0]
            if any(_NUMBER.fullmatch(w[4]) or _MONEY.fullmatch(w[4]) for w in preceding['words']):break
            header.append(preceding)
        top=header[-1]
        left_headers=[s for s in segments if abs(s['cy']-top['cy'])<=2 and s['box'].x1+8<top['box'].x0]
        if not left_headers:continue
        label_header=max(left_headers,key=lambda s:s['box'].x1)
        try:
            if column_unit_declaration([_text(label_header['words'])])['unit']!='unknown':continue
        except ValueError:continue
        if not any(not _NUMBER.fullmatch(w[4]) for w in label_header['words']):continue
        headbox=_bbox([w for s in header for w in s['words']])
        # A later competing monetary heading ends the vertical scope, even
        # when its values happen to share the same right-aligned amount lane.
        next_heads=[s['box'].y0 for s in seeds if s['cy']>seed['cy']+4 and
                    min(s['box'].x1,headbox.x1)>max(s['box'].x0,headbox.x0)]
        stop=min(next_heads,default=float('inf'))
        anchors=[w for w in words if _NUMBER.fullmatch(w[4]) and
                 (w[1]+w[3])/2>seed['cy']+2 and w[1]<stop and
                 headbox.x0-16<=(w[0]+w[2])/2<=headbox.x1+16]
        clusters=[]
        for word in sorted(anchors,key=lambda w:w[2]):
            if clusters and word[2]-clusters[-1][0][2]<=5:clusters[-1].append(word)
            else:clusters.append([word])
        clusters=[group for group in clusters if len(group)>=3]
        if len(clusters)!=1:continue
        anchors=sorted(clusters[0],key=lambda w:(w[1]+w[3])/2)
        if (anchors[0][1]+anchors[0][3])/2-seed['cy']>65:continue
        run=[]
        for word in anchors:
            if run and (word[1]+word[3]-run[-1][1]-run[-1][3])/2>36:break
            run.append(word)
        if len(run)<3 or len(run)>256:continue
        y0=min(w[1] for w in run);y1=max(w[3] for w in run)
        band=[w for w in words if w[1]<y1 and w[3]>y0]
        spans=[]
        for w in sorted(band,key=lambda w:w[0]):
            if spans and w[0]<=spans[-1][1]:spans[-1][1]=max(spans[-1][1],w[2])
            else:spans.append([w[0],w[2]])
        gaps=[(a[1],b[0]) for a,b in zip([[0,0],*spans], [*spans,[layout_width,layout_width]]) if b[0]-a[1]>=12]
        left_gaps=[g for g in gaps if g[0]<label_header['box'].x0-2 and g[1]<=label_header['box'].x1]
        right_gaps=[g for g in gaps if g[0]>=max(w[2] for w in run)-.01 and g[1]>headbox.x1+2]
        if not left_gaps or not right_gaps:continue
        left_gap=max(left_gaps,key=lambda g:g[1]);right_gap=min(right_gaps,key=lambda g:g[0])
        lower=min(sum(left_gap)/2,label_header['box'].x0-2)
        upper=max(sum(right_gap)/2,headbox.x1+2)
        if not left_gap[0]<lower<left_gap[1] or not right_gap[0]<upper<right_gap[1]:continue
        if any(w[0]<lower<w[2] or w[0]<upper<w[2] for w in band):continue
        local=[w for w in words if lower<=w[0] and w[2]<=upper and top['box'].y0-1<=w[1] and w[3]<=y1+.01]
        lane_rows=[]
        for w in sorted(local,key=lambda w:((w[1]+w[3])/2,w[0])):
            cy=(w[1]+w[3])/2
            if lane_rows and abs(cy-lane_rows[-1]['cy'])<=2:lane_rows[-1]['words'].append(w)
            else:lane_rows.append({'cy':cy,'words':[w]})
        indices=[];bad=False
        for i,row in enumerate(lane_rows):
            row['words'].sort(key=lambda w:w[0])
            amount=[w for w in run if w in row['words']]
            row['numbers']=amount
            row['labels']=[w for w in row['words'] if w not in amount]
            if not amount:continue
            labels=row['labels']
            if len(amount)!=1 or not labels or max(w[2] for w in labels)+8>=amount[0][0]:bad=True;break
            # A label may literally contain “Top 20” or an identifier number.
            # A distant, unheaded numeric column is never absorbed as a label.
            for pos,w in enumerate(labels):
                if _NUMBER.fullmatch(w[4]) and not any(not _NUMBER.fullmatch(n[4]) and
                    0<=max(w[0]-n[2],n[0]-w[2])<=6 for n in labels[max(0,pos-1):pos+2] if n is not w):
                    bad=True
            if not any(not _NUMBER.fullmatch(w[4]) for w in labels):bad=True
            indices.append(i)
        if bad or len(indices)!=len(run):continue
        # No unassigned prose/chart row can be silently skipped inside a table.
        if indices!=list(range(indices[0],indices[-1]+1)):continue
        candidates.append((lane_rows,indices,(lower,upper,headbox.x0)))
    return candidates


def _currency_panels(rows, sha, page_no, matrix, existing):
    """Repeated amount lanes, independently bounded labels, no page-wide scale.

    This supplements (never rewrites) existing explicit-header tables. An
    adjacent printed currency suffix binds ONLY its own amount. Department
    headings break table identity; separate panels cannot be summed merely
    because their annotations use the same currency symbol.
    """
    anchors=[]
    for index,row in enumerate(rows):
        words=row['words']
        for pos,w in enumerate(words):
            match=_MONEY.fullmatch(w[4])
            if not match:continue
            token=w[4];parts=[w];suffix=match.group(3)
            if not suffix and pos+1<len(words):
                nxt=words[pos+1]
                if nxt[4] in _SCALES and 0<=nxt[0]-w[2]<=2 and abs((nxt[1]+nxt[3])/2-row['cy'])<=2:
                    suffix=nxt[4];token+=suffix;parts.append(nxt)
            anchors.append({'row':index,'word':w,'parts':parts,'raw':token,
                            'symbol':match.group(1),'numeric':match.group(2), 'suffix':suffix})
    if len(anchors)>512:return []
    clusters=[]
    for anchor in sorted(anchors,key=lambda a:a['word'][2]):
        if clusters and anchor['word'][2]-clusters[-1][0]['word'][2]<=6:
            clusters[-1].append(anchor)
        else:clusters.append([anchor])
    clusters=[c for c in clusters if len({a['row'] for a in c})>=3]
    if len(clusters)>16:return []
    covered={tuple(f['bbox_display_pt']) for f in existing}
    tables=[]
    for cluster in clusters:
        y0=min(rows[a['row']]['cy'] for a in cluster);y1=max(rows[a['row']]['cy'] for a in cluster)
        edge=max(a['word'][2] for a in cluster)
        neighbors=[c for c in clusters if max(a['word'][2] for a in c)<min(a['word'][0] for a in cluster)-12
                   and min(rows[a['row']]['cy'] for a in c)<=y1 and max(rows[a['row']]['cy'] for a in c)>=y0]
        lower=max((max(_bbox(a['parts']).x1 for a in c)+8 for c in neighbors),default=0)
        # Cluster baselines AFTER bounding the lane. A bold total in another
        # panel must not split this panel's normal-font label from its value.
        lane_native=[w for row in rows for w in row['words'] if w[0]>=lower and w[2]<=edge+14]
        lane_rows=[]
        for w in sorted(lane_native,key=lambda w:((w[1]+w[3])/2,w[0])):
            cy=(w[1]+w[3])/2
            if lane_rows and abs(cy-lane_rows[-1]['cy'])<=2:lane_rows[-1]['words'].append(w)
            else:lane_rows.append({'cy':cy,'words':[w]})
        runs=[];run=[];headings=[]
        def flush():
            nonlocal run
            if run:runs.append((run,headings[-1:]));run=[]
        for i,row in enumerate(lane_rows):
            local=row['words']
            aligned=[a for a in cluster if a['word'] in local]
            anchor=aligned[0] if len(aligned)==1 else None
            if anchor:
                before=[w for w in local if w[2]<=anchor['word'][0]-2]
                remaining=[w for w in local if w not in before and w not in anchor['parts']]
                good=bool(before and not remaining and all((not _NUMBER.fullmatch(w[4]) or re.fullmatch(r'(?:19|20)\d{2}',w[4])) and not _MONEY.fullmatch(w[4]) for w in before))
                # Two adjacent numeric columns with only one row label are
                # an unheaded multi-column table, not two monetary panels.
                cy=(anchor['word'][1]+anchor['word'][3])/2
                band=[w for source_row in rows for w in source_row['words']
                      if abs((w[1]+w[3])/2-cy)<=2 and w[2]>lower and w[0]<edge+14]
                if any(w[0]<lower or w[2]>edge+14 for w in band):good=False
                ordered=sorted(local,key=lambda w:w[0])
                if any(a[2]>b[0] for a,b in zip(ordered,ordered[1:])):good=False
                right=[a for a in anchors if abs((a['word'][1]+a['word'][3])/2-cy)<=2 and a['word'][0]>edge+14]
                if any(not any(w[0]>=_bbox(anchor['parts']).x1+8 and w[2]<=a['word'][0]-2
                               and not _NUMBER.fullmatch(w[4]) and not _MONEY.fullmatch(w[4])
                               for source_row in rows for w in source_row['words']
                               if abs((w[1]+w[3])/2-cy)<=2) for a in right):good=False
                if good and run and row['cy']-lane_rows[run[-1][0]]['cy']>36:flush()
                if good:run.append((i,before,anchor));continue
                flush();continue
            flush()
            # Local headings are source text, not fabricated column labels.
            # A second monetary amount or truncated spanning text cannot head
            # an independently bounded panel.
            if not any(_MONEY.fullmatch(w[4]) or _NUMBER.fullmatch(w[4]) for w in local):
                headings.append({'text':_text(local),'bbox_display_pt':list(_bbox(local)*matrix),
                                 '_cy':row['cy']})
        flush()
        for run,heading in runs:
            # Keep a total even if the native label changed font/indentation,
            # but never certify isolated prose as a table.
            if len(run)<3:continue
            if len({_text(label) for _,label,_ in run})!=len(run):continue
            if len({a['symbol'] for _,_,a in run})!=1:continue
            runwords=[w for _,label,a in run for w in label+a['parts']]
            rect=_bbox(runwords)
            identity=hashlib.sha256(f'{sha}:{page_no}:currency-panel:{list(rect)}'.encode()).hexdigest()[:24]
            title=[{k:v for k,v in h.items() if k!='_cy'} for h in heading
                   if 0<lane_rows[run[0][0]]['cy']-h['_cy']<65]
            facts=[]
            for r,(i,label,a) in enumerate(run):
                box=list(_bbox(a['parts'])*matrix)
                if tuple(box) in covered:continue
                suffix=a['suffix'];scale=_SCALES.get(suffix)
                proof={'text':a['raw'],'bbox_display_pt':box,'binding':'own_adjacent_currency_suffix_only',
                       'suffix':suffix,'multiplier':scale} if suffix else None
                facts.append({'fact_id':f'{identity}:row:{r}:annotation','table_id':identity,
                    'row_header':_text(label),'column_header_path':[], 'column_role':'native_currency_annotation',
                    'row_header_bbox_display_pt':list(_bbox(label)*matrix),'bbox_display_pt':box,
                    'raw_value':a['raw'],'source_sha256':sha,'page_no':page_no,
                    'currency_symbol':a['symbol'],'currency':'unknown','unit':'currency_symbol:'+a['symbol'],
                    'scale':scale,'scale_evidence':proof,'period':None,'title_context':title,
                    'value_kind':'native_currency_annotation_only','calculator_input_eligible':False,
                    'physical_calculator_input_eligible':False,
                    'native_amount_domain':a['numeric'].replace(',','').replace('−','-'),
                    'validation_scope':'native_word_alignment_not_visible_ocr_or_semantic_truth'})
                covered.add(tuple(box))
            if not facts:continue
            tables.append({'table_id':identity,'status':'native_alignment_verified','table_kind':'monetary_panel_list',
                'source_sha256':sha,'page_no':page_no,'bbox_display_pt':list(rect*matrix),
                'column_header_paths':[],'row_headers':[_text(label) for _,label,_ in run],
                'facts':facts,'title_context':title,'panel_x_bounds_unrotated_pt':[lower,edge+14],
                'complete_scope':'continuous_local_native_rows_not_whole_document',
                'calculator_input_eligible':False,'physical_calculator_input_eligible':False})
    return tables


def extract_native_text_tables(raw:bytes,*,page_no:int,expected_source_sha256=None):
    if not isinstance(raw,bytes) or not raw or len(raw)>20*1024*1024:
        raise ValueError('native_table_source_budget_invalid')
    sha=hashlib.sha256(raw).hexdigest()
    if expected_source_sha256 is not None and expected_source_sha256!=sha:
        raise ValueError('native_table_source_sha256_mismatch')
    if type(page_no) is not int or page_no<1:raise ValueError('native_table_page_invalid')
    report={'schema_version':'native-aligned-text-table-v1','source_sha256':sha,'page_no':page_no,
            'tables':[],'facts':[],'rejected_tables':[],'calculator_input_eligible':False,
            'validation_scope':'native_word_alignment_not_visible_ocr_or_semantic_truth'}
    with fitz.open(stream=raw,filetype='pdf') as doc:
        if doc.needs_pass or len(doc)>1000 or page_no>len(doc):raise ValueError('native_table_page_bounds')
        p=doc[page_no-1];matrix=fitz.Matrix(p.rotation_matrix)
        rotation, display_width = p.rotation, p.rect.width
        p.set_rotation(0)
        words=p.get_text('words')
        if len(words)>20000:raise ValueError('native_table_word_budget')
        if not words:
            report['rejected_tables'].append({'reason':'no_native_words_ocr_not_supported'})
            return report
        # Some original PDFs store vertical native glyph directions and use
        # the declared page rotation to display a horizontal table. Align in
        # that actual display plane, then emit display boxes only once.
        # A normal horizontal text layer on a rotated page keeps the existing
        # native grid; arbitrary tilted/mixed text is never normalized.
        expected_direction = {90:(0.,-1.),180:(-1.,0.),270:(0.,1.)}.get(rotation)
        if expected_direction is not None:
            lines = [line for block in p.get_text('dict')['blocks'] if 'lines' in block
                     for line in block['lines']]
            weighted = [(sum(len(span.get('text','')) for span in line.get('spans',[])),
                         line.get('dir',(1.,0.))) for line in lines]
            total = sum(weight for weight,_ in weighted)
            aligned = sum(weight for weight,direction in weighted if
                          all(abs(a-b)<0.001 for a,b in zip(direction,expected_direction)))
            if total and aligned/total >= 0.9:
                words = [(*tuple(fitz.Rect(word[:4])*matrix),*word[4:]) for word in words]
                matrix = fitz.Matrix(1,1)
                layout_width = display_width
                report['native_layout_orientation'] = {
                    'mode':'declared_rotation_restores_horizontal_native_lines',
                    'page_rotation_degrees':rotation,
                    'coordinate_system':'pdf_display_points_top_left'}
            else:
                layout_width = p.rect.width
        else:
            layout_width = p.rect.width
        # Baselines cluster only geometrically aligned native words, preserving
        # row text; no string whitespace splitting defines column membership.
        rows=[]
        for word in sorted(words,key=lambda w:((w[1]+w[3])/2,w[0])):
            cy=(word[1]+word[3])/2
            if rows and abs(cy-rows[-1]['cy'])<=2:
                rows[-1]['words'].append(word)
            else:rows.append({'cy':cy,'words':[word]})
        for row in rows:
            row['words'].sort(key=lambda w:w[0])
            row['numbers']=[w for w in row['words'] if _native_cell_token(w[4])]
            row['labels']=[w for w in row['words'] if not _native_cell_token(w[4])]
        groups=[];current=[]
        for index,row in enumerate(rows):
            valid=bool(row['labels'] and row['numbers'] and max(w[2] for w in row['labels'])+8<min(w[0] for w in row['numbers']))
            if valid and current:
                previous=rows[current[-1]]
                valid=(row['cy']-previous['cy']<=max(36,3*median(w[3]-w[1] for w in row['words']))
                       and len(row['numbers'])==len(previous['numbers'])
                       and all(abs(a[2]-b[2])<=5 for a,b in zip(row['numbers'],previous['numbers'])))
                if not valid:
                    groups.append(current);current=[]
                    valid=bool(row['labels'] and row['numbers'] and max(w[2] for w in row['labels'])+8<min(w[0] for w in row['numbers']))
            if valid:current.append(index)
            elif current:groups.append(current);current=[]
        if current:groups.append(current)
        page_rows=rows
        candidates=[(page_rows,indices,None) for indices in groups]
        candidates.extend(_explicit_amount_lanes(words,layout_width))
        for group_no,(rows,indices,lane_bounds) in enumerate(candidates):
            if len(indices)<3:continue
            data=[rows[i] for i in indices];n=len(data[0]['numbers'])+1
            if lane_bounds and any(tuple(fitz.Rect(w[:4])*matrix)==tuple(f['bbox_display_pt'])
                                   for row in data for w in row['numbers'] for f in report['facts']):continue
            allwords=[w for row in data for w in row['words']];rect=_bbox(allwords)
            identity=hashlib.sha256(f'{sha}:{page_no}:{group_no}:{list(rect)}'.encode()).hexdigest()[:24]
            def reject(reason):
                report['rejected_tables'].append({'table_id':identity,'reason':reason,
                    'bbox_display_pt':list(rect*matrix),'aligned_row_count':len(data),
                    'candidate_column_count':n,'candidate_rows_unverified':
                    [{'row_label':_text(row['labels']),'raw_values':[w[4] for w in row['numbers']]} for row in data],
                    'calculator_input_eligible':False})
            if n>8:reject('column_budget_exceeded');continue
            # Boundaries live in empty vertical gutters across every data row.
            maxima=[max(w[2] for row in data for w in row['labels'])]
            minima=[min(w[0] for row in data for w in row['labels'])]
            for c in range(n-1):
                minima.append(min(row['numbers'][c][0] for row in data))
                maxima.append(max(row['numbers'][c][2] for row in data))
            if any(b-a<8 for a,b in zip(maxima,minima[1:])):reject('columns_overlap_or_no_unique_gutters');continue
            cuts=[(a+b)/2 for a,b in zip(maxima,minima[1:])]
            if lane_bounds:
                # Keep the complete nominated monetary heading inside its
                # column, without moving a cut into any actual row label.
                cuts[0]=min(cuts[0],lane_bounds[2]-2)
                if not maxima[0]<cuts[0]<minima[1]:
                    reject('local_header_crosses_data_gutter');continue
            # Outer headers can be longer than their right-aligned numeric
            # cells. Interior gutters remain bounded by the actual data rows.
            bounds=[lane_bounds[0] if lane_bounds else 0,*cuts,lane_bounds[1] if lane_bounds else layout_width]
            labels=[_text(row['labels']) for row in data]
            if len(set(labels))!=len(labels):reject('row_header_not_unique');continue
            header_rows=[]
            for row in reversed(rows[:indices[0]]):
                if data[0]['cy']-row['cy']>65 or len(header_rows)>=3:break
                cells=[[] for _ in range(n)];invalid=False
                for w in row['words']:
                    candidates=[c for c in range(n) if bounds[c]<=w[0] and w[2]<=bounds[c+1]]
                    if len(candidates)!=1:invalid=True;break
                    cells[candidates[0]].append(w)
                # A second header line often prints only years or units above
                # numeric columns, leaving the row-label lane empty.
                if invalid or any(not cell for cell in cells[1:]):break
                if any(all(_native_cell_token(w[4]) for w in cell) and not all(re.fullmatch(r'(?:19|20)\d{2}',w[4]) for w in cell) for cell in cells):break
                header_rows.append(cells)
            header_rows.reverse()
            if not header_rows:
                # A headless monetary list has an explicit annotation role,
                # never a fabricated Amount column or inferred currency code.
                monetary = n==2 and all(re.fullmatch(r'[$€¥][+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?',row['numbers'][0][4]) for row in data)
                if not monetary:reject('missing_explicit_column_headers');continue
                if len({row['numbers'][0][4][0] for row in data})!=1:
                    reject('monetary_symbols_conflict');continue
                nearby=[row for row in rows[:indices[0]] if 0<data[0]['cy']-row['cy']<100]
                scope=[{'text':_text(row['words']),'bbox_display_pt':list(_bbox(row['words'])*matrix)} for row in nearby]
                period=[s for s in scope if re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)',s['text'])]
                titles=[s for s in scope if s not in period]
                facts=[]
                for r,row in enumerate(data):
                    w=row['numbers'][0]
                    facts.append({'fact_id':f'{identity}:row:{r}:annotation','table_id':identity,
                        'row_header':labels[r],'column_header_path':[], 'column_role':'native_currency_annotation',
                        'row_header_bbox_display_pt':list(_bbox(row['labels'])*matrix),
                        'bbox_display_pt':list(fitz.Rect(w[:4])*matrix),'raw_value':w[4],
                        'source_sha256':sha,'page_no':page_no,'currency_symbol':w[4][0],'currency':'unknown',
                        'unit':'currency_symbol:'+w[4][0],'scale':None,'period':None,
                        'period_scope_text':period,'title_context':titles[-1:] if titles else [],
                        'value_kind':'native_currency_annotation_only','calculator_input_eligible':False,
                        'physical_calculator_input_eligible':False,
                        'native_amount_domain':w[4][1:].replace(',','').replace('−','-'),
                        'validation_scope':report['validation_scope']})
                table={'table_id':identity,'status':'native_alignment_verified','table_kind':'monetary_key_value_list',
                    'source_sha256':sha,'page_no':page_no,'bbox_display_pt':list(rect*matrix),
                    'column_header_paths':[],'row_headers':labels,'facts':facts,'title_context':titles[-1:] if titles else [],
                    'period_scope_text':period,'complete_scope':'continuous_local_native_rows_not_whole_document',
                    'calculator_input_eligible':False,'physical_calculator_input_eligible':False}
                report['tables'].append(table);report['facts'].extend(facts);continue
            paths=[[_text(row[c]) for row in header_rows if row[c]] for c in range(n)]
            if len({tuple(path) for path in paths})!=n:reject('column_header_paths_not_unique');continue
            try:
                declarations = [column_unit_declaration(path) for path in paths]
            except ValueError as exc:
                reject(str(exc));continue
            # Every intervening native word must be inside its unique cell;
            # narrative text cannot be swallowed as a table row or header.
            if any(a[2]>b[0] for row in data for a,b in zip(row['words'],row['words'][1:])):
                reject('native_words_overlap');continue
            prefix=[row for row in rows[:indices[0]] if 0<data[0]['cy']-row['cy']<100 and row not in data]
            scope=[{'text':_text(row['words']),'bbox_display_pt':list(_bbox(row['words'])*matrix)} for row in prefix
                   if re.search(r'(?<!\d)(?:19|20)\d{2}(?!\d)',_text(row['words']))]
            years=set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',' '.join(s['text'] for s in scope)))
            facts=[]
            for r,row in enumerate(data):
                for c,w in enumerate(row['numbers'],1):
                    header=' '.join(paths[c]);header_years=re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',header)
                    if len(set(header_years))>1:reject('column_period_scope_ambiguous');facts=[];break
                    header_boxes=[list(_bbox(h[c])*matrix) for h in header_rows if h[c]]
                    if _FRACTION_CELL.fullmatch(w[4]):
                        try:
                            proof=parse_native_fraction_cell(w[4], column_header_path=paths[c],
                                column_header_bboxes_display_pt=header_boxes,
                                bbox_display_pt=list(fitz.Rect(w[:4])*matrix),
                                source_sha256=sha, page_no=page_no)
                        except ValueError as exc:
                            reject(str(exc));facts=[];break
                        facts.append({'fact_id':f'{identity}:row:{r}:column:{c}','table_id':identity,
                            'row_header':labels[r],'column_header_path':paths[c],
                            'row_header_bbox_display_pt':list(_bbox(row['labels'])*matrix),
                            'column_header_bboxes_display_pt':header_boxes,
                            'bbox_display_pt':proof['bbox_display_pt'],'raw_value':w[4],
                            'source_sha256':sha,'page_no':page_no,'unit':'count_fraction','scale':None,
                            'currency':'unknown','unit_evidence':None,'scale_evidence':None,
                            'period':header_years[0] if header_years else None,
                            'period_status':'explicit_column_year' if header_years else 'unknown_or_external_scope',
                            'value_kind':'native_count_fraction_literal','fraction_proof':proof,
                            'calculator_input_eligible':False,'physical_calculator_input_eligible':False,
                            'validation_scope':report['validation_scope']})
                        continue
                    literal_unit='percent' if w[4].endswith('%') else 'currency_symbol:'+w[4][0] if w[4][0] in '$€¥' else 'unknown'
                    declared = declarations[c]
                    if literal_unit != 'unknown' and declared['unit'] != 'unknown':
                        symbol = w[4][0] if w[4][0] in '$€¥' else None
                        expected = {'USD':'$', 'CAD':'$', 'AUD':'$', 'EUR':'€', 'GBP':'£', 'CNY':'¥', 'JPY':'¥'}.get(declared['currency'])
                        if ((literal_unit == 'percent') != (declared['unit'] == 'percent')
                                or symbol and (expected or declared['symbol']) != symbol):
                            reject('cell_and_column_unit_conflict');facts=[];break
                    unit = declared['unit'] if declared['unit'] != 'unknown' else literal_unit
                    unit_proof = ({'binding':'own_explicit_column_header_only', 'header_path':paths[c],
                                   'header_bboxes_display_pt':header_boxes, 'unit':unit,
                                   'currency':declared['currency'], 'multiplier':declared['scale']}
                                  if declared['unit'] != 'unknown' else None)
                    facts.append({'fact_id':f'{identity}:row:{r}:column:{c}','table_id':identity,
                        'row_header':labels[r],'column_header_path':paths[c],
                        'row_header_bbox_display_pt':list(_bbox(row['labels'])*matrix),
                        'column_header_bboxes_display_pt':header_boxes,
                        'bbox_display_pt':list(fitz.Rect(w[:4])*matrix),'raw_value':w[4],
                        'source_sha256':sha,'page_no':page_no,'unit':unit,'scale':declared['scale'],
                        'currency':declared['currency'], 'unit_evidence':unit_proof,
                        'scale_evidence':unit_proof if declared['scale'] else None,
                        'period':header_years[0] if header_years else None,
                        'period_status':'explicit_column_year' if header_years else 'unknown_or_external_scope',
                        'value_kind':'native_aligned_cell_literal','calculator_input_eligible':False,
                        'validation_scope':report['validation_scope']})
                if not facts:break
            if not facts:continue
            table={'table_id':identity,'status':'native_alignment_verified','source_sha256':sha,'page_no':page_no,
                'bbox_display_pt':list(rect*matrix),'column_header_paths':paths,'row_headers':labels,'facts':facts,
                'external_scope_text':scope,'external_scope_years':sorted(years),
                'scope_status':'multiple_years_not_assigned_to_cells' if len(years)>1 else 'external_scope_only',
                'complete_scope':'continuous_local_native_rows_not_whole_document','calculator_input_eligible':False}
            if lane_bounds:
                table['local_geometry_proposal']='explicit_native_currency_header_and_empty_outer_gutters'
                table['panel_x_bounds_native_layout_pt']=list(lane_bounds[:2])
            report['tables'].append(table);report['facts'].extend(facts)
        for table in _currency_panels(page_rows,sha,page_no,matrix,report['facts']):
            report['tables'].append(table);report['facts'].extend(table['facts'])
        if 'native_layout_orientation' not in report:
            from .native_financial_tables import extract_candidates
            for table in extract_candidates(p, words, source_sha256=sha, page_no=page_no, display_matrix=matrix):
                if any(f['bbox_display_pt'] == old['bbox_display_pt'] for f in table['facts'] for old in report['facts']):
                    continue
                report['tables'].append(table);report['facts'].extend(table['facts'])
    report['status']='native_alignment_verified' if report['tables'] else 'incomplete'
    return report
