"""Deterministic source contracts. Extraction never establishes business truth.

Only explicit `业务口径:{...}` lines are supported. The caller must obtain
source_evidence from the verified document tool, not from a user request body.
"""
from __future__ import annotations
from dataclasses import asdict
import hashlib
import json
import re
from .core import encoded

PREFIX=re.compile(r'^业务口径[：:]\s*(\{.*\})\s*$')


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def extract_candidate(source_event, source_evidence, current_schema, scope, now):
    if source_event.get('kind')!='verified_document_read' or not source_event.get('event_id'):
        raise ValueError('verified source read event required')
    if source_evidence['document_id'] not in scope.data_sources:
        raise ValueError('source outside trusted scope')
    line=source_evidence['quote']
    match=PREFIX.fullmatch(line)
    if not match:return None
    if len(line)>8000:raise ValueError('source contract too large')
    contract=json.loads(match[1])
    if set(contract)-{'term','definition','binding','valid_from','valid_to','supersedes'}:
        raise ValueError('unsupported source contract fields')
    if not re.fullmatch(r'[\w\u3400-\u9fff]{2,64}',contract.get('term','')):
        raise ValueError('invalid literal term')
    if not isinstance(contract.get('definition'),str) or not 1<=len(contract['definition'])<=1000:
        raise ValueError('explicit definition required')
    # Missing binding is retained for review, never guessed from a successful SQL.
    content={'memory_type':'business_semantics','term':contract['term'],'definition':contract['definition'],
        'binding':contract.get('binding'), 'scope':asdict(scope),'evidence':source_evidence,
        'source_version':current_schema,'valid_from':contract.get('valid_from'),
        'valid_to':contract.get('valid_to'),'supersedes':contract.get('supersedes'),
        'reason':'explicit_structured_source_contract_not_business_confirmation'}
    version=digest(content)
    return {**content,'candidate_id':'cand-'+version[:32],'digest':version,'created_at':now,
        'source_event_id':source_event['event_id'],'verification_state':'candidate'}
