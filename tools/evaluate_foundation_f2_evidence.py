"""Bounded A/B/C evidence-scope audit on F2 development table tasks.

Candidate selection is made from the query, retrieved chunks and parsed source
structure. Frozen scoring literals are read only after each candidate scope is
constructed and are used exclusively for independent measurement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import tempfile
import time
from pathlib import Path

import fitz
from foundation_common import ROOT, dump, environment, sha, source_hashes
from backend.knowledge_store import KnowledgeStore
from backend.evidence_scope import page_scope, row_scope
from backend.source_answer_dossier import build_dossier
from evaluate_foundation_f2 import raw_file
from freeze_foundation_f2 import BENCH

ROW = re.compile(r'^\s*(?P<id>[A-Z]-\d{2}-\d{2})\s*\|\s*(?P<area>[^|]+?)\s*\|\s*(?P<status>[^|]+?)\s*\|\s*(?P<reading>[^|]+?)\s*\|\s*(?P<date>\d{4}-\d{2}-\d{2})\s*$')
HEADER = '编号 | 区域 | 结论 | 读数(毫克/升) | 日期'


def read_table_chain(store, hits, question, *, page_budget=6):
    """Resolve an explicit/query-bound batch ID, then follow its table chain."""
    strategy_started=time.perf_counter()
    records = {record.document_id: record for record in store.records()}
    query_codes = set(re.findall(r'(?<![A-Z0-9])[A-Z]-\d{2}(?!-\d)', question))
    entity_pages=[]
    resolution = {'method':'explicit_query_code' if query_codes else 'source_entity_to_batch_code',
        'source_document_ids':[], 'derived_codes':sorted(query_codes), 'identifier_lookup_hits':[]}
    if not query_codes:
        org_match=re.search(r'([\u4e00-\u9fffA-Za-z0-9]+)(?:批次验收记录|批次验收)',question)
        organization=org_match.group(1) if org_match else None
        codes=set()
        if organization:
            seen_sources=set()
            for hit in hits:
                did=hit.metadata.get('document_id')
                if not did or did in seen_sources: continue
                seen_sources.add(did);doc=store.document(did)
                if doc['modality']!='pdf': continue
                pinned=hit.metadata.get('source_sha256') or doc['sha256']
                raw=store.verify_source(did,expected_sha256=pinned)
                with fitz.open(raw) as source_pdf:
                    # Inspect a fixed first-page candidate receipt per nominated
                    # source; do not let a tail chunk hide a document's identity line.
                    number=1
                    text=source_pdf[number-1].get_text('text',sort=True)
                    receipt={'document_id':did,'source_sha256':doc['sha256'],
                        'page_no':number,'text':text,'role':'candidate_entity_page'}
                    entity_pages.append(receipt)
                    code=re.search(r'对象代码\s*([A-Z]-\d{2})',text)
                    if organization in text and code and '不适用' not in text:
                        codes.add(code.group(1));resolution['source_document_ids'].append(did)
                store.verify_source(did,expected_sha256=doc['sha256'])
                if codes: break
        if len(codes)==1:
            query_codes=codes;resolution['derived_codes']=sorted(codes)
        else:
            return {'status':'incomplete','reason':'entity_to_code_unresolved_or_ambiguous',
                    'rows':[],'pages':[],'scope':None,'elapsed_ms':0.0,'resolution':resolution}
    if resolution['method']=='explicit_query_code':
        lookup_hits,lookup_audit=hits,{'index':'reuse_initial_top_k'}
        resolution['identifier_query_count']=1
    else:
        lookup_hits,lookup_audit=store.search(' '.join(sorted(query_codes)),top_k=4,with_audit=True)
        resolution['identifier_query_count']=2
    resolution['identifier_lookup_hits']=[{'chunk_id':h.document_id,
        'document_id':h.metadata.get('document_id'),'source_sha256':h.metadata.get('source_sha256')}
        for h in lookup_hits]
    resolution['identifier_lookup_index']=lookup_audit.get('index')
    candidate_ids = list(dict.fromkeys(hit.metadata.get('document_id') for hit in lookup_hits))
    chosen = None
    # A candidate must be represented in the bounded Top-K and expose the
    # asked batch code plus a repeated-header table, without looking at Gold.
    table_candidates=[]
    for did in candidate_ids:
        if not did:
            continue
        excerpt = '\n'.join(records[h.document_id].content for h in lookup_hits
            if h.metadata.get('document_id') == did and h.document_id in records)
        row_codes = {match[:4] for match in re.findall(r'(?<![A-Z0-9])[A-Z]-\d{2}-\d{2}(?![A-Z0-9])', excerpt)}
        if query_codes and query_codes.intersection(row_codes):
            table_candidates.append(did)
    if len(table_candidates)==1:
        chosen=table_candidates[0]
    if chosen is None:
        return {'status': 'incomplete', 'reason': 'no_unique_retrieved_table_chain', 'rows': [],
                'pages': [], 'scope': None, 'elapsed_ms': 0.0,'resolution':resolution,
                'candidate_table_documents':table_candidates}

    document = store.document(chosen)
    candidate_hashes={hit.metadata.get('source_sha256') for hit in lookup_hits
        if hit.metadata.get('document_id')==chosen and hit.metadata.get('source_sha256')}
    if candidate_hashes!={document['sha256']}:
        from backend.knowledge_store import SourceRevisionError
        raise SourceRevisionError('table-chain candidate revision changed after retrieval')
    path = store.verify_source(chosen, expected_sha256=document['sha256'])
    scan_started = time.perf_counter()
    pages=list(entity_pages)
    table_scanned=0
    omitted, rows, headers, units = [], [], [], []
    table_page_budget=max(0,page_budget-len(pages))
    with fitz.open(path) as pdf:
        for number, page in enumerate(pdf, start=1):
            if table_scanned >= table_page_budget:
                omitted.append({'document_id': chosen, 'page_no': number, 'reason': 'page_budget'})
                continue
            text = page.get_text('text', sort=True)
            pages.append({'document_id': chosen, 'source_sha256': document['sha256'],
                          'page_no': number, 'text': text})
            table_scanned+=1
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith('编号 |'):
                    headers.append(stripped)
                    match = re.search(r'读数\(([^)]+)\)', stripped)
                    if match:
                        units.append(match.group(1))
                match = ROW.fullmatch(line)
                if match:
                    item = match.groupdict()
                    rows.append({'row_id': item['id'], 'document_id': chosen,
                        'source_sha256': document['sha256'],
                        'page_no': number, 'text': ' | '.join(item.values()), **item})
    store.verify_source(chosen, expected_sha256=document['sha256'])
    codes_present = {row['id'][:4] for row in rows}
    entity_verified = len(query_codes) == 1 and codes_present == query_codes
    headers_verified = bool(headers) and len(set(headers)) == 1
    units_verified = bool(units) and len(set(units)) == 1
    unique_rows = len({row['row_id'] for row in rows}) == len(rows)
    page_count=int(document['stats'].get('page_count') or 1)
    complete_pages = table_scanned == page_count
    matching = [row for row in rows if
        ('北区' not in question or row['area'].strip() == '北区') and
        ('合格' not in question or row['status'].strip() == '合格')]
    candidate_lines = [line for page in pages for line in page['text'].splitlines()
        if query_codes and any(re.search(re.escape(code)+r'-\d{2}(?!\d)', line) for code in query_codes)]
    inventory_parsed = bool(candidate_lines) and len(candidate_lines) == len(rows)
    filters_bound_to_columns = (bool(rows) and all(row['area'].strip() and row['status'].strip() and row['date'] for row in rows)
        and (('北区' not in question or all('区域' in header for header in headers))
             and ('合格' not in question or all('结论' in header for header in headers))))
    exclusion_verified = ('隔离' not in question or
        any('隔离记录不纳入' in page['text'] or '隔离项不计入' in page['text'] for page in pages))
    conditions = {'entity_code': entity_verified, 'repeated_header': headers_verified,
        'unit': units_verified, 'record_ids_unique': unique_rows,
        'row_inventory_parsed': inventory_parsed, 'query_fields_available': filters_bound_to_columns,
        'declared_pages_scanned': complete_pages, 'exclusion_policy': exclusion_verified}
    declarations = []
    for did in dict.fromkeys(page['document_id'] for page in entity_pages):
        selected=[page for page in entity_pages if page['document_id']==did]
        declarations.append({'document_id':did,'source_sha256':selected[0]['source_sha256'],
            'pages':[page['page_no'] for page in selected]})
    declarations.append({'document_id': chosen, 'source_sha256': document['sha256'],
        'pages': list(range(1, page_count+1))})
    scope = page_scope(declarations, pages, pages, omitted=omitted,
        required_conditions=conditions, verified_conditions=[k for k,v in conditions.items() if v])
    # row_scope independently checks stable row identities and revision binding.
    native_rows = [{'row_id': r['row_id'], 'document_id': chosen,
        'source_sha256': document['sha256'], 'page_no': r['page_no']} for r in rows]
    matching_ids = {row['row_id'] for row in matching}
    matching_native_rows = [row for row in native_rows if row['row_id'] in matching_ids]
    row_audit = row_scope({'document_id': chosen, 'source_sha256': document['sha256']},
                          native_rows, matching_native_rows)
    if not unique_rows:
        scope['status'] = 'ambiguous'
    return {'status': scope['status'], 'reason': None if scope['status']=='complete_for_declared_scope' else 'scope_conditions_or_budget_unresolved',
        'document_id': chosen, 'source_sha256': document['sha256'], 'rows': rows,
        'matching_rows': matching,
        'pages': pages, 'scope': scope, 'row_scope': row_audit,
        'table_headers': headers, 'units': units, 'conditions': conditions,
        'budget_pages': page_budget, 'scanned_pages': len(pages),
        'omitted': omitted, 'resolution':resolution,
        'scan_ms': (time.perf_counter()-scan_started)*1000,
        'elapsed_ms': (time.perf_counter()-strategy_started)*1000}


def score_evidence(literals, text):
    found = [literal for literal in literals if literal in text]
    return {'required_count': len(literals), 'recovered_count': len(found),
        'evidence_recall': len(found)/len(literals) if literals else None,
        'complete_required_evidence': len(found)==len(literals),
        'missing_literals': [literal for literal in literals if literal not in text]}


def run(output, split='dev'):
    before = source_hashes()
    tasks = json.loads((BENCH/f'{split}.json').read_text())
    families = {task['source_family'] for task in tasks}
    documents = json.loads((BENCH/'documents.json').read_text())
    relevant_docs = [d for d in documents if d['family'] in families]
    scoring = json.loads((BENCH/'scoring.json').read_text())
    baseline_path = ROOT/f'docs/foundation/runs/f2-A-{split}-before.json'
    baseline = {row['id']: row for row in json.loads(baseline_path.read_text())['records']}
    rows_out = []
    with tempfile.TemporaryDirectory(prefix='f2-evidence-scope-') as temp:
        store = KnowledgeStore(temp)
        for doc in relevant_docs:
            store.ingest(raw_file(doc), document_id=doc['id'], title=doc['title'],
                modality=doc['type'], filename=doc['id']+'.'+doc['type'])
        for task in tasks:
            if task['kind'] not in {'complete_table_enumeration','cross_page_header_unit'}:
                continue
            hits, retrieval_audit = store.search(task['query'], top_k=task['top_k'], with_audit=True)
            a_text = '\n'.join(baseline[task['id']]['hit_evidence'].values())
            b_started=time.perf_counter()
            b_pages, b_scope = build_dossier(store, task['query'], hits)
            b_elapsed=(time.perf_counter()-b_started)*1000
            b_text = '\n'.join(page['text'] for page in b_pages)
            c = read_table_chain(store, hits, task['query'], page_budget=6)
            # Freeze the source selection and row scan before consulting Gold.
            c_text = '\n'.join(page['text'] for page in c.get('pages', []))
            required = scoring[task['id']]['required']
            required_source = next(iter(required))
            literals = required[required_source]
            a = score_evidence(literals, a_text)
            b = score_evidence(literals, b_text)
            c_metrics = score_evidence(literals, c_text)
            matching = c.get('matching_rows', [])
            c['matched_record_count'] = len(matching)
            rows_out.append({'id':task['id'],'family':task['source_family'],'kind':task['kind'],
                'query':task['query'],'required_source':required_source,
                'top_k_chunk_ids':[hit.document_id for hit in hits],
                'top_k_sources':list(dict.fromkeys(hit.metadata.get('document_id') for hit in hits)),
                'A_top_k':a,'A_hit_count':len(hits),
                'A_query_ms':baseline[task['id']]['query_ms'],'A_retrieved_chars':len(a_text),
                'B_existing_page_dossier':b,'B_pages_included':len(b_pages),'B_elapsed_ms':b_elapsed,
                'B_supplied_chars':len(b_text),
                'B_pages_scanned':b_scope['scanned_pages'],'B_scope':b_scope['evidence_scope'],
                'B_total_strategy_ms':baseline[task['id']]['query_ms']+b_elapsed,
                'C_supplied_chars':len(c_text),
                'C_total_strategy_ms':baseline[task['id']]['query_ms']+c.get('elapsed_ms',0),
                'C_bounded_row_chain':c,'A_original_baseline_score':baseline[task['id']]['evidence_recall_at_k'],
                'retrieval_audit_index':retrieval_audit.get('index')})
    after = source_hashes()
    if before != after:
        raise RuntimeError('F2 source hashes changed during scope experiment')
    summary = {}
    for label, field in [('A','A_top_k'),('B','B_existing_page_dossier'),('C','C_bounded_row_chain')]:
        vals = [row[field]['evidence_recall'] if label!='C' else
                score_evidence(scoring[row['id']]['required'][row['required_source']],
                    '\n'.join(p['text'] for p in row[field].get('pages', [])))['evidence_recall']
                for row in rows_out]
        complete = sum(value == 1 for value in vals)
        summary[label] = {'task_count':len(vals),'mean_evidence_recall':sum(vals)/len(vals) if vals else None,
            'complete_evidence_tasks':complete,'page_budget':{'A':'top-k only','B':'existing dossier max 6 pages supplied','C':'max 6 table-chain pages'}}
    report={'environment':environment(),'split':split,'task_count':len(rows_out),
        'cases':[r['id'] for r in rows_out],'summary':summary,'cases_detail':rows_out,
        'source_before':before,'source_after':after,'script_sha256':sha(__file__),
        'rss_max_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'gold_used_for_candidate_selection':False,'models_called':0,
        'scope_contract':'complete_for_declared_scope never proves global corpus or whole-answer sufficiency',
        'claim':'F2 dev table-scope mechanism audit only; no public benchmark or end-to-end answer result'}
    dump(output, report)
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == '__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--split',choices=['dev','retained'],default='dev')
    parser.add_argument('--output',required=True);args=parser.parse_args();run(args.output,args.split)
