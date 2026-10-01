"""Shared search-tool protocol; explicit source scope must never be discarded."""
from __future__ import annotations

import re
import math


class SearchScopeError(ValueError):
    pass


def validate_search_args(args, store=None, *, resolved=False):
    if not isinstance(args,dict) or 'query' not in args or not set(args) <= {'query','document_id','page_no'}:
        raise SearchScopeError('search_args_invalid')
    query=args['query']
    parts=query if isinstance(query,list) else [query]
    if not parts or len(parts)>100:
        raise SearchScopeError('search_query_invalid')
    for part in parts:
        if isinstance(part,str):
            if len(part)>1000: raise SearchScopeError('search_query_over_budget')
        elif type(part) in (int,float) and isinstance(query,list):
            if not math.isfinite(part): raise SearchScopeError('search_query_nonfinite')
        elif (not resolved and isinstance(part,dict) and set(part)=={'ref','path'}
              and isinstance(part['ref'],str) and isinstance(part['path'],list) and len(part['path'])<=8):
            pass
        else:
            raise SearchScopeError('search_query_invalid')
    if all(isinstance(p,str) for p in parts) and not ' '.join(parts).strip():
        raise SearchScopeError('search_query_empty')
    kwargs={}
    scope={'mode':'all_documents','document_id':None,'page_no':None}
    if 'page_no' in args and 'document_id' not in args:
        raise SearchScopeError('search_page_requires_document')
    if 'document_id' in args:
        identity=args['document_id']
        if not isinstance(identity,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',identity):
            raise SearchScopeError('search_document_id_invalid')
        kwargs['document_id']=identity
        scope={'mode':'explicit_document_all_pages','document_id':identity,'page_no':None}
        document=None
        if store is not None:
            try: document=store.document(identity)
            except KeyError as exc: raise SearchScopeError('search_document_not_found') from exc
            scope['source_sha256']=document['sha256']
        if 'page_no' in args:
            page=args['page_no']
            if type(page) is not int or page<1:
                raise SearchScopeError('search_page_no_invalid')
            if document is not None and (document['modality']!='pdf' or page>int(document['stats'].get('page_count') or 1)):
                raise SearchScopeError('search_page_outside_pdf_scope')
            kwargs['page_no']=page
            scope.update(mode='explicit_document_page',page_no=page)
    return kwargs,scope


def verify_search_hits(hits,scope):
    """Recheck tool output scope before any dependant consumes it."""
    if scope['document_id'] is None: return
    for hit in hits:
        metadata=hit.metadata
        if (metadata.get('document_id')!=scope['document_id']
            or metadata.get('source_sha256')!=scope['source_sha256']
            or scope['page_no'] is not None and metadata.get('page_no')!=scope['page_no']):
            raise SearchScopeError('search_result_source_scope_mismatch')
