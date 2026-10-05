"""Source-only multi-page table navigation prototype; no answers or model calls.

Repeated headers and aligned physical cells identify candidate page chains.
They do not prove sample identity, exhaustive closure, or numeric semantics.
"""
import hashlib
import json

import fitz

from .chunk_cleaning import DocumentChunker
from .pdf_native_context import _aligned_native_cells, _member


def probe(raw, *, max_pages=64, max_chars=12000):
    pages=[]
    with fitz.open(stream=raw,filetype='pdf') as document:
        if len(document)>max_pages:
            return {'status':'budget_exceeded','candidates':[]}
        for index,page in enumerate(document):
            page.set_rotation(0)
            blocks=DocumentChunker._pdf_text_blocks(page)
            if len(blocks)>20000:
                return {'status':'budget_exceeded','candidates':[]}
            physical=sorted([b for b in blocks if b['normalized_text'].strip()],
                key=lambda b:(b['bbox_fitz_unrotated_pt'][1],b['bbox_fitz_unrotated_pt'][0]))
            tables=[]
            for position,header in enumerate(physical):
                cells=_aligned_native_cells(header)
                if not cells or any(any(c.isdigit() for c in cell['text']) for cell in cells):
                    continue
                rows=[];annotations=[];bottom=header['bbox_fitz_unrotated_pt'][3]
                height=max(c['bbox'][3]-c['bbox'][1] for c in cells)
                for block in physical[position+1:]:
                    box=block['bbox_fitz_unrotated_pt']
                    if box[1]<bottom-.5 or box[1]-bottom>2*height+4:
                        break
                    current=_aligned_native_cells(block)
                    if current and len(current)==len(cells) and all(
                            abs(a['bbox'][0]-b['bbox'][0])<=5 for a,b in zip(cells,current)):
                        rows.append(block)
                    elif (box[0]>=cells[0]['bbox'][0]-5
                            and box[2]<cells[1]['bbox'][0]-8):
                        annotations.append(block)
                    else:
                        break
                    bottom=box[3]
                if len(rows)<2:
                    continue
                tables.append({'header':header,'cells':cells,'rows':rows,'annotations':annotations,
                    'preceding':physical[:position],
                    'following':[b for b in physical if b['bbox_fitz_unrotated_pt'][1]>bottom]})
            pages.append({'page_no':index+1,'size':[page.rect.width,page.rect.height],'tables':tables})
    chains=[]
    for left,right in zip(pages,pages[1:]):
        matches=[]
        for a in left['tables']:
            for b in right['tables']:
                if (left['size']==right['size'] and len(a['cells'])==len(b['cells'])
                        and all(' '.join(x['text'].split())==' '.join(y['text'].split())
                            and abs(x['bbox'][0]-y['bbox'][0])<=5
                            for x,y in zip(a['cells'],b['cells']))):
                    matches.append((a,b))
        if len(matches)!=1:
            continue
        a,b=matches[0];parts=[]
        for page,table in ((left,a),(right,b)):
            # Keep ALL preceding and following scope, not only matching rows.
            members=table['preceding']+[table['header']]+table['rows']+table['annotations']+table['following']
            members=sorted(members,key=lambda block:(block['bbox_fitz_unrotated_pt'][1],block['bbox_fitz_unrotated_pt'][0]))
            text='\n\n'.join(block['normalized_text'] for block in members)
            parts.append({'page_no':page['page_no'],'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),
                'members':[_member(block) for block in members],
                'header_text':table['header']['normalized_text'],
                'header_block_id':table['header']['block_id'],
                'row_block_ids':[block['block_id'] for block in table['rows']]})
        if sum(len(part['text']) for part in parts)>max_chars:
            continue
        chains.append({'pages':parts,'header_exact_and_lanes_aligned':True,
            'semantic_sample_identity_verified':False,'exhaustive_table_closure_verified':False,
            'calculator_input_eligible':False})
    return {'status':'candidate' if chains else 'no_unique_candidate',
        'source_sha256':hashlib.sha256(raw).hexdigest(),'candidates':chains,
        'model_calls':0,'production_answer_authority':False}


def page_context(raw,page_no,anchor_text,max_chars):
    from .native_anchor import ANCHOR_POLICY_VERSION,anchor_ranges
    result=probe(raw)
    candidates=[(chain,page) for chain in result['candidates'] for page in chain['pages']
        if page['page_no']==page_no and len(anchor_ranges(page['text'],DocumentChunker.clean_text(anchor_text)))==1]
    if len(candidates)!=1:
        return None
    chain,page=candidates[0]
    text=page['text']
    if len(text)>max_chars:
        return None
    return {'text':text,'source_sha256':result['source_sha256'],'page_no':page_no,
        'evidence_sha256':page['text_sha256'],'text_sha256':page['text_sha256'],
        'evidence_chars':len(text),'max_chars':max_chars,
        'extraction_version':'original-native-table-chain-page-v1',
        'mode':'original_native_candidate_table_chain_page','members':page['members'],
        'table_chain':{'pages':[p['page_no'] for p in chain['pages']],
            'header_exact_and_lanes_aligned':True,'semantic_sample_identity_verified':False,
            'exhaustive_table_closure_verified':False},
        'anchor_match_policy':ANCHOR_POLICY_VERSION,
        'anchor_match':{'policy_version':ANCHOR_POLICY_VERSION,'members':page['members']},
        'calculator_input_eligible':False}
