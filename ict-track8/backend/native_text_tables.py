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

_NUMBER=re.compile(r'[$€¥]?[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?')
_MONEY=re.compile(r'([$€¥])([+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(m|k|bn|million|billion|thousand)?')
_SCALES={'m':'1000000','million':'1000000','k':'1000','thousand':'1000',
         'bn':'1000000000','billion':'1000000000'}


def _bbox(words):
    return fitz.Rect(min(w[0] for w in words),min(w[1] for w in words),max(w[2] for w in words),max(w[3] for w in words))


def _text(words):
    return ' '.join(w[4] for w in sorted(words,key=lambda w:w[0]))


def column_unit_declaration(path):
    """Only literal declarations inside one geometrically bounded column."""
    text = ' '.join(path)
    codes = set(re.findall(r'\b(?:USD|EUR|GBP|CNY|JPY|CAD|AUD)\b', text))
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
    # Common financial header notation is a literal multiplier declaration.
    # It must remain inside this column, never borrowed from another heading.
    if re.search(r"(?:['’]\s*000(?:s)?\b|\b000s\b)", text, re.I):
        scales.add('thousand')
    if len(scales) > 1 or scales and unit == 'unknown':
        raise ValueError('column_scale_declaration_unbound')
    scale = _SCALES[next(iter(scales))] if scales else None
    return {'unit':unit, 'currency':code or 'unknown', 'scale':scale, 'symbol':symbol}


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
        p=doc[page_no-1];matrix=fitz.Matrix(p.rotation_matrix);p.set_rotation(0)
        words=p.get_text('words')
        if len(words)>20000:raise ValueError('native_table_word_budget')
        if not words:
            report['rejected_tables'].append({'reason':'no_native_words_ocr_not_supported'})
            return report
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
            row['numbers']=[w for w in row['words'] if _NUMBER.fullmatch(w[4])]
            row['labels']=[w for w in row['words'] if not _NUMBER.fullmatch(w[4])]
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
        for group_no,indices in enumerate(groups):
            if len(indices)<3:continue
            data=[rows[i] for i in indices];n=len(data[0]['numbers'])+1
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
            # Outer headers can be longer than their right-aligned numeric
            # cells. Interior gutters remain bounded by the actual data rows.
            bounds=[0,*cuts,p.rect.width]
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
                if any(all(_NUMBER.fullmatch(w[4]) for w in cell) and not all(re.fullmatch(r'(?:19|20)\d{2}',w[4]) for w in cell) for cell in cells):break
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
                    literal_unit='percent' if w[4].endswith('%') else 'currency_symbol:'+w[4][0] if w[4][0] in '$€¥' else 'unknown'
                    declared = declarations[c]
                    if literal_unit != 'unknown' and declared['unit'] != 'unknown':
                        symbol = w[4][0] if w[4][0] in '$€¥' else None
                        expected = {'USD':'$', 'CAD':'$', 'AUD':'$', 'EUR':'€', 'GBP':'£', 'CNY':'¥', 'JPY':'¥'}.get(declared['currency'])
                        if ((literal_unit == 'percent') != (declared['unit'] == 'percent')
                                or symbol and (expected or declared['symbol']) != symbol):
                            reject('cell_and_column_unit_conflict');facts=[];break
                    unit = declared['unit'] if declared['unit'] != 'unknown' else literal_unit
                    header_boxes=[list(_bbox(h[c])*matrix) for h in header_rows if h[c]]
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
            report['tables'].append(table);report['facts'].extend(facts)
        for table in _currency_panels(rows,sha,page_no,matrix,report['facts']):
            report['tables'].append(table);report['facts'].extend(table['facts'])
    report['status']='native_alignment_verified' if report['tables'] else 'incomplete'
    return report
