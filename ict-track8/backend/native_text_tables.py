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


def _bbox(words):
    return fitz.Rect(min(w[0] for w in words),min(w[1] for w in words),max(w[2] for w in words),max(w[3] for w in words))


def _text(words):
    return ' '.join(w[4] for w in sorted(words,key=lambda w:w[0]))


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
            bounds=[rect.x0-40,*cuts,rect.x1+40]
            labels=[_text(row['labels']) for row in data]
            if len(set(labels))!=len(labels):reject('row_header_not_unique');continue
            header_rows=[]
            for row in reversed(rows[:indices[0]]):
                if data[0]['cy']-row['cy']>65 or len(header_rows)>=3:break
                if any(re.match(r'[$€¥]',w[4]) for w in row['words']):break
                cells=[[] for _ in range(n)];invalid=False
                for w in row['words']:
                    candidates=[c for c in range(n) if bounds[c]<=w[0] and w[2]<=bounds[c+1]]
                    if len(candidates)!=1:invalid=True;break
                    cells[candidates[0]].append(w)
                if invalid or any(not cell for cell in cells):break
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
            paths=[[_text(row[c]) for row in header_rows] for c in range(n)]
            if len({tuple(path) for path in paths})!=n:reject('column_header_paths_not_unique');continue
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
                    unit='percent' if w[4].endswith('%') else 'currency_symbol:'+w[4][0] if w[4][0] in '$€¥' else 'unknown'
                    facts.append({'fact_id':f'{identity}:row:{r}:column:{c}','table_id':identity,
                        'row_header':labels[r],'column_header_path':paths[c],
                        'row_header_bbox_display_pt':list(_bbox(row['labels'])*matrix),
                        'column_header_bboxes_display_pt':[list(_bbox(h[c])*matrix) for h in header_rows],
                        'bbox_display_pt':list(fitz.Rect(w[:4])*matrix),'raw_value':w[4],
                        'source_sha256':sha,'page_no':page_no,'unit':unit,'scale':None,
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
    report['status']='native_alignment_verified' if report['tables'] else 'incomplete'
    return report
