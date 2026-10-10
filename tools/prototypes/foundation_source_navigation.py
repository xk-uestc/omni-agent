"""Body-only source navigation. Scores nominate sources; never answer facts."""
from collections import Counter
from math import log
from backend.cross_source import _tokenize

VERSION = 'body-source-navigation-v1'


def navigate_sources(query, records, *, limit=6):
    if type(limit) is not int or not 1 <= limit <= 6:
        raise ValueError('source navigation limit must be 1..6')
    from backend.knowledge_store import _RETRIEVAL_STOP_WORDS
    terms = set(_tokenize(query)) - _RETRIEVAL_STOP_WORDS
    groups = {}
    for record in records:
        did = record.metadata['document_id']
        group = groups.setdefault(did, {'terms': set(), 'chunks': 0, 'sha': set()})
        group['terms'].update(_tokenize(record.content))
        group['chunks'] += 1
        group['sha'].add(record.metadata.get('source_sha256'))
    frequencies = Counter(t for group in groups.values() for t in group['terms'] if t in terms)
    weights = {t: log(1+(len(groups)+.5)/(frequencies[t]+.5)) for t in terms}
    denominator = sum(weights.values()) or 1
    ranked = []
    for did, group in groups.items():
        matched = sorted(terms.intersection(group['terms']))
        score = sum(weights[t] for t in matched)/denominator
        if score > 0:
            ranked.append({'document_id': did, 'source_sha256': sorted(x for x in group['sha'] if x),
                           'body_coverage': score, 'matched_body_terms': matched,
                           'chunk_count': group['chunks']})
    ranked.sort(key=lambda row: (-row['body_coverage'], row['document_id']))
    selected = ranked[:limit]
    return [r['document_id'] for r in selected], {
        'version': VERSION, 'navigation_only': True, 'semantic_sufficiency': 'not_evaluated',
        'source_limit': limit, 'corpus_sources': len(groups), 'selected_sources': selected,
        'source_budget_exhausted': len(ranked) > limit,
        'index_version': VERSION, 'title_as_evidence': False}
