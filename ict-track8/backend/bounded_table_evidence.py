"""Production port of F2's bounded original-row/header/source chain receipts.

Supports explicit pipe-delimited native PDF tables with repeated exact headers.
No answers, arithmetic, model calls, whole-corpus closure or inferred blank cells.
"""
import re
import time

import fitz
from .evidence_scope import page_scope, row_scope
from .source_binding import bind_sources, identifiers, normalize

ENUMERATION = re.compile(r'完整列出|逐条列出|(?:所有|全部|每条|每个).{0,25}(?:编号|记录|样本|项目)|\b(?:list|enumerate)\b.{0,40}\b(?:all|every)\b', re.I)
ROW_ID = re.compile(r'(?<![A-Za-z0-9_-])([A-Za-z]+-\d+)-\d+(?![A-Za-z0-9_-])')
HEADER_ID = re.compile(r'^(?:编号|对象编号|样本编号|记录编号|id|record id)$', re.I)


def applicable(question):
    return bool(ENUMERATION.search(question))


def prepare_table_bundle(store, question, hits, *, document_id=None, page_no=None,
                         page_budget=6, char_budget=32000):
    started = time.perf_counter()
    bundle = {'version': 'bounded-native-table-bundle-v1', 'status': 'not_applicable',
        'pages': [], 'rows': [], 'matching_rows': [], 'extra_retrieval_calls': 0,
        'extra_native_page_reads': 0, 'page_budget': page_budget, 'char_budget': char_budget,
        'calculator_input_eligible': False, 'answer_authority': False}
    def finish(status, reason=None):
        bundle.update(status=status, reason=reason, elapsed_ms=(time.perf_counter()-started)*1000)
        return bundle
    if page_no is not None or not applicable(question):
        return finish('not_applicable')
    if type(page_budget) is not int or not 1 <= page_budget <= 6 or not 1 <= char_budget <= 32000:
        raise ValueError('table_bundle_budget_invalid')
    codes = set(identifiers(question))
    resolution_pages, resolution_receipts = [], []
    catalog = {d['document_id']: d for d in store.list_documents()}
    nominated = [document_id] if document_id else list(dict.fromkeys(h.metadata['document_id'] for h in hits))
    if len(nominated)>4:
        return finish('budget_exhausted', 'candidate_source_budget')
    for did in nominated:
        pins={h.metadata['source_sha256'] for h in hits if h.metadata['document_id']==did}
        if len(pins)>1:
            from .knowledge_store import SourceRevisionError
            raise SourceRevisionError('table bundle mixed source revisions')
        if pins:
            store.verify_source(did,expected_sha256=next(iter(pins)))
    from .source_answer_dossier import native_page
    if not codes:
        from .source_binding import ORG_PATTERN
        orgs = set(re.findall(ORG_PATTERN, normalize(question)))
        if len(orgs)!=1:
            return finish('ambiguous', 'query_object_or_organization_not_unique')
        org = next(iter(orgs))
        for did in nominated:
            d = catalog.get(did)
            if not d or d['modality']!='pdf':
                continue
            pinned = {h.metadata['source_sha256'] for h in hits if h.metadata['document_id']==did}
            if pinned and pinned!={d['sha256']}:
                from .knowledge_store import SourceRevisionError
                raise SourceRevisionError('table bundle entity source changed')
            path=store.verify_source(did, expected_sha256=d['sha256'])
            if bundle['extra_native_page_reads'] >= page_budget:
                return finish('budget_exhausted', 'entity_page_budget')
            with fitz.open(path) as pdf:
                p=native_page(d,pdf[0],1,len(pdf))
                bundle['extra_native_page_reads']+=1
            store.verify_source(did, expected_sha256=d['sha256'])
            if sum(len(x['text']) for x in resolution_pages)+len(p['text'])>char_budget:
                return finish('budget_exhausted', 'entity_char_budget')
            # Exact body institution and object self-declaration; never an alias,
            # filename or mere substring in an affiliate's name. Inspect every
            # bounded nominee, not the first positive candidate.
            positive = bool(re.search(re.escape(org)+r'(?!附属组|分院|分公司)(?:季度|年度|运行|公报|[。.,:：])', normalize(p['text'])))
            code_matches = re.findall(r'对象代码\s*[:：]?\s*([A-Za-z]+-\d+)(?![A-Za-z0-9_-])', normalize(p['text']))
            if positive and len(set(code_matches))==1 and not re.search(r'本文件不适用|该版本已撤销', normalize(p['text'])):
                resolution_pages.append(p)
                codes.update(code_matches)
                resolution_receipts.append({'document_id':did,'source_sha256':d['sha256'],
                    'page_no':1,'quote':p['text'],'objects':code_matches})
        if len(codes)!=1:
            return finish('ambiguous', 'entity_to_object_unresolved_or_ambiguous')
    if len(codes)!=1:
        return finish('ambiguous', 'multiple_query_objects')
    code = next(iter(codes))
    # One additional bounded exact-ID retrieval, with the caller's document
    # restriction preserved. No Gold or caller-selected source may nominate it.
    records=store.records(document_id)
    records, original_binding=bind_sources(question,records)
    requested=original_binding['requested']
    lookup_query=' '.join([code, *requested['years'], *requested['organizations'],
        *('版本'+v for v in requested['versions'])])
    lookup,audit=store.search(lookup_query,top_k=20,document_id=document_id,with_audit=True)
    bundle['extra_retrieval_calls']=1
    binding=audit['source_binding']
    if binding['status']=='requires_clarification':
        return finish('ambiguous','source_version_unspecified')
    # Count competing indexed table sources before truncating candidates. This
    # uses indexed original row IDs, not unbounded original-PDF scans.
    candidate_ids={r.metadata['document_id'] for r in records
        if r.metadata['document_id'] not in binding['rejected_sources']
        and any(m.group(1).casefold()==code.casefold() for m in ROW_ID.finditer(r.content))
        and '|' in r.content}
    if len(candidate_ids)!=1:
        return finish('ambiguous','no_unique_current_table_source')
    did=next(iter(candidate_ids));d=catalog[did]
    if d['modality']!='pdf' or not any(h.metadata['document_id']==did for h in lookup):
        return finish('incomplete','table_source_not_in_bounded_lookup')
    initial_pins={h.metadata['source_sha256'] for h in hits if h.metadata['document_id']==did}
    pins={h.metadata['source_sha256'] for h in lookup if h.metadata['document_id']==did}|initial_pins
    if pins!={d['sha256']}:
        from .knowledge_store import SourceRevisionError
        raise SourceRevisionError('table bundle source replaced after retrieval')
    page_count=int(d['stats'].get('page_count') or 1)
    bundle.update(document_id=did, source_sha256=d['sha256'],
        resolution_receipts=resolution_receipts, lookup_chunk_ids=[h.document_id for h in lookup])
    # Budget is shared by identity reads and the table inventory. Reject whole
    # scope up front; no prefix can impersonate the full enumeration.
    if page_count+bundle['extra_native_page_reads']>page_budget:
        return finish('budget_exhausted','declared_table_page_budget')
    rows, headers, pages, size = [], [], [], sum(len(p['text']) for p in resolution_pages)
    path=store.verify_source(did, expected_sha256=d['sha256'])
    with fitz.open(path) as pdf:
        for number,page in enumerate(pdf,1):
            p=native_page(d,page,number,len(pdf));bundle['extra_native_page_reads']+=1
            if size+len(p['text'])>char_budget:
                return finish('budget_exhausted','complete_native_page_char_budget')
            size+=len(p['text']);pages.append(p)
            header=None; page_rows=[]
            for line in page.get_text('text',sort=True).splitlines():
                cells=[s.strip() for s in line.split('|')]
                if len(cells)<2:
                    continue
                if HEADER_ID.fullmatch(cells[0]) and len(set(cells))==len(cells):
                    if header is not None:
                        return finish('ambiguous','multiple_tables_per_page')
                    header=cells;headers.append(cells);continue
                if ROW_ID.fullmatch(cells[0]):
                    if header is None or len(cells)!=len(header) or not all(cells):
                        return finish('incomplete','row_header_or_field_alignment')
                    m=ROW_ID.fullmatch(cells[0])
                    if m.group(1).casefold()!=code.casefold():
                        return finish('ambiguous','other_sample_in_table_chain')
                    # The literal native line is preserved including signs,
                    # dates, units and qualifiers; fields are navigation only.
                    if p['text'].count(line)!=1:
                        return finish('ambiguous','native_row_quote_not_unique')
                    row={'row_id':cells[0],'document_id':did,'source_sha256':d['sha256'],
                        'page_no':number,'text':line,'fields':cells,'header':header,
                        'offsets':[p['text'].index(line),p['text'].index(line)+len(line)],
                        'calculator_input_eligible':False}
                    rows.append(row);page_rows.append(row)
                elif header is not None:
                    return finish('incomplete','unparsed_table_row')
            if not header or not page_rows:
                return finish('incomplete','missing_header_or_rows_on_declared_page')
    store.verify_source(did, expected_sha256=d['sha256'])
    for p in resolution_pages:
        store.verify_source(p['document_id'], expected_sha256=p['source_sha256'])
    if not rows or any(h!=headers[0] for h in headers):
        return finish('ambiguous','repeated_header_or_unit_mismatch')
    if len({r['row_id'] for r in rows})!=len(rows):
        return finish('ambiguous','duplicate_record_identity')
    if requested['years']:
        dates={value[:4] for row in rows for value in row['fields'] if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value)}
        if dates and not dates <= set(requested['years']):
            return finish('ambiguous','native_record_date_scope_conflict')
    # Literal categorical predicates from the requested enumeration clause.
    # Numeric/range/negative predicates require a semantic compiler and are
    # refused here. Full pages remain supplied for independent answer review.
    clause=re.split(r'使用什么|并带|并说明|逐条列出|并带上',question,maxsplit=1)[0]
    if re.search(r'大于|小于|超过|至少|不含|排除|不包括|\b(?:greater|less|excluding)\b',clause,re.I):
        return finish('incomplete','unsupported_selection_predicate')
    predicates=[]
    for index in range(1,len(headers[0])):
        values={r['fields'][index] for r in rows}
        values={v for v in values if not re.search(r'\d',v)}
        selected={v for v in values if v in clause and not any(v in longer and longer!=v and longer in clause for longer in values)}
        if len(selected)>1:
            return finish('ambiguous','competing_literal_predicates')
        if selected:
            predicates.append({'column_index':index,'header':headers[0][index],'literal':next(iter(selected))})
    matching=[r for r in rows if all(r['fields'][p['column_index']]==p['literal'] for p in predicates)]
    all_pages=resolution_pages+pages
    # If entity and table receipts use the same page, deduplicate by identity,
    # while accounting for every actual read in the cost counter.
    unique={(p['document_id'],p['page_no']):p for p in all_pages};all_pages=list(unique.values())
    declarations=[]
    for source in dict.fromkeys(p['document_id'] for p in all_pages):
        ps=[p for p in all_pages if p['document_id']==source]
        declarations.append({'document_id':source,'source_sha256':ps[0]['source_sha256'], 'pages':[p['page_no'] for p in ps]})
    conditions=('exact_object','repeated_headers_units','unique_rows','native_inventory','current_sources','bounded_pages')
    scope=page_scope(declarations,all_pages,all_pages,required_conditions=conditions,verified_conditions=conditions)
    for i,p in enumerate(all_pages,1):p['evidence_id']='P'+str(i)
    bundle.update(pages=all_pages,rows=rows,matching_rows=matching,headers=headers,predicates=predicates,
        scope=scope,row_scope=row_scope({'document_id':did,'source_sha256':d['sha256']},rows,matching),
        native_chars=sum(len(p['text']) for p in all_pages), semantic_sufficiency='requires_independent_whole_question_review')
    return finish('complete_for_declared_scope')
