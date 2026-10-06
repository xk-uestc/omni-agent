"""Explicit native 'label, $amount' pairs; no pixel reading or inferred units.

Wrapped labels may span uniquely overlapping adjacent native lines. A page
collection is not proof of one chart/period: only independently reviewed entity
comparison can consume it. Native text, geometry and source bytes are replayed.
"""
import hashlib
import re

import fitz

_AMOUNT = re.compile(r'[$€¥][+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')


def extract_candidates(words, *, source_sha256, page_no, display_matrix, existing):
    """Propose at least three literal pairs without borrowing chart headings."""
    def box(group):
        return fitz.Rect(min(w[0] for w in group),min(w[1] for w in group),
                         max(w[2] for w in group),max(w[3] for w in group))
    def text(group):
        return ' '.join(w[4] for w in sorted(group,key=lambda w:w[0]))
    rows=[]
    for w in sorted(words,key=lambda w:((w[1]+w[3])/2,w[0])):
        cy=(w[1]+w[3])/2
        if rows and abs(cy-rows[-1]['cy'])<=2:rows[-1]['words'].append(w)
        else:rows.append({'cy':cy,'words':[w]})
    segments=[]
    for row in rows:
        groups=[]
        for w in sorted(row['words'],key=lambda w:w[0]):
            if groups and w[0]-groups[-1][-1][2]<18:groups[-1].append(w)
            else:groups.append([w])
        segments.extend({'cy':row['cy'],'words':g,'box':box(g)} for g in groups)
    covered={tuple(f['bbox_display_pt']) for f in existing}
    pairs=[]
    for segment in segments:
        group=segment['words'];amount=group[-1]
        if not _AMOUNT.fullmatch(amount[4]) or tuple(fitz.Rect(amount[:4])*display_matrix) in covered:
            continue
        if any(re.search(r'[$€¥]|\d',w[4]) for w in group[:-1]):continue
        lines=[group[:-1]] if group[:-1] else []
        current=segment
        for _ in range(3-len(lines)):
            candidates=[s for s in segments if 0<current['cy']-s['cy']<=1.6*max(
                current['box'].height,s['box'].height) and
                min(current['box'].x1,s['box'].x1)>max(current['box'].x0,s['box'].x0)]
            if not candidates:break
            distance=min(current['cy']-s['cy'] for s in candidates)
            nearest=[s for s in candidates if abs(current['cy']-s['cy']-distance)<=2]
            if len(nearest)!=1:break
            previous=nearest[0];literal=text(previous['words'])
            if re.search(r'[$€¥\d:.!?;]|[。！？；：]',literal):break
            # Only the terminal label line can have the comma separator.
            if lines and ',' in literal:break
            lines.insert(0,previous['words']);current=previous
        if not lines or not text(lines[-1]).endswith(','):continue
        label=' '.join(text(line) for line in lines)[:-1].strip()
        if not label or len(label)>180 or len(label.split())>18 or ',' in label:continue
        label_words=[w for line in lines for w in line]
        bounds=box(label_words+[amount])
        pairs.append({'label':label,'words':label_words+[amount], 'label_words':label_words,
                      'amount':amount,'bounds':bounds,'label_lines':[text(line) for line in lines]})
    if not 3<=len(pairs)<=64 or len({p['label'] for p in pairs})!=len(pairs):return []
    if len({p['amount'][4][0] for p in pairs})!=1:return []
    # An amount cannot belong to two pairs, nor can one label borrow another
    # pair's amount/label words through overlapping wrapped-text geometry.
    identities=[[(w[0],w[1],w[2],w[3],w[4]) for w in p['words']] for p in pairs]
    if any(set(a).intersection(b) for i,a in enumerate(identities) for b in identities[i+1:]):return []
    identity=hashlib.sha256(f'{source_sha256}:{page_no}:explicit-inline-pairs'.encode()).hexdigest()[:24]
    facts=[]
    for i,p in enumerate(pairs):
        amount=p['amount'];raw=amount[4]
        proof={'binding':'explicit_comma_label_native_amount_pair',
               'literal_label_lines':p['label_lines'],
               'native_words':[{'text':w[4],'bbox_display_pt':list(fitz.Rect(w[:4])*display_matrix)} for w in p['words']]}
        facts.append({'fact_id':f'{identity}:pair:{i}','table_id':identity,
            'row_header':p['label'],'row_header_bbox_display_pt':list(box(p['label_words'])*display_matrix),
            'raw_value':raw,'bbox_display_pt':list(fitz.Rect(amount[:4])*display_matrix),
            'column_header_path':[],'unit':'currency_symbol:'+raw[0],'currency':'unknown',
            'scale':None,'period':None,'period_status':'unknown_or_external_scope',
            'source_sha256':source_sha256,'page_no':page_no,
            'value_kind':'native_inline_label_amount_literal','inline_pair_proof':proof,
            'calculator_input_eligible':False,'physical_calculator_input_eligible':False,
            'validation_scope':'literal_native_pair_not_chart_membership_or_unit_scale_proof'})
    return [{'table_id':identity,'table_kind':'explicit_inline_label_collection_not_grid',
        'source_sha256':source_sha256,'page_no':page_no,'facts':facts,
        'bbox_display_pt':list(box([w for p in pairs for w in p['words']])*display_matrix),
        'complete_scope':'all_detected_explicit_pairs_on_page_not_exhaustive_chart',
        'scope_status':'chart_membership_requires_independent_complete_page_review',
        'calculator_input_eligible':False,'physical_calculator_input_eligible':False}]
