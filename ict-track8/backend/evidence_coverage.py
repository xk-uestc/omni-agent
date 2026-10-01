"""Query-aware retrieval candidate selector; lexical coverage is NOT factual proof.

The caller must still verify source versions, reconstruct native closures, and
validate generated claims. No gold answers, model calls, or corpus-specific rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import unicodedata
from typing import Any, Callable, Mapping, Sequence

from .text_quality import simplify_for_retrieval

_STOP = set('a an the and or of to in on at for from by with what which who how is are was were be been do does did would could please tell me total much many'.split())
_ZH_STOP = {'什么', '多少', '哪个', '哪些', '如何', '请问', '的是', '以及', '我们'}
_QUALIFIERS = re.compile(r'\b(?:including|excluding|without|before|after|between|during|except)\b|包含|包括|不含|排除|之前|之后|期间', re.I)
_IDS = re.compile(r'(?<![\w-])(?=[A-Za-z\d_-]*[A-Za-z])(?=[A-Za-z\d_-]*\d)[A-Za-z][A-Za-z\d_-]{2,63}(?![\w-])')


def _normalize(text: str) -> str:
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def _terms(text: str) -> set[str]:
    normalized = _normalize(simplify_for_retrieval(text))
    terms = {t for t in re.findall(r'[a-z][a-z0-9_-]*|\d+(?:\.\d+)?', normalized) if t not in _STOP}
    for run in re.findall(r'[\u4e00-\u9fff]+', normalized):
        terms.update(run[i:i+2] for i in range(len(run)-1) if run[i:i+2] not in _ZH_STOP)
    return terms


def _facets(query: str) -> dict[str, float]:
    query = _normalize(simplify_for_retrieval(query))
    result = {term: 1.0 for term in _terms(query)}
    for number in re.findall(r'(?<!\d)\d{4}(?!\d)', query):
        result[number] = 2.0
    for identifier in _IDS.findall(query):
        result[_normalize(identifier)] = 3.0
    for match in _QUALIFIERS.finditer(query):
        result[_normalize(match.group())] = 2.0
    return result


@dataclass(frozen=True)
class CoverageSelection:
    hits: list[Any]
    audit: dict[str, Any]


def select_coverage_hits(query: str, hits: Sequence[Any], contents: Mapping[str, str],
                         ranking: Callable[[Any], float | tuple], top_k: int = 4) -> CoverageSelection:
    """Pick at most twenty actual-body candidates using soft source diversity.

    ``contents`` keys are hit.document_id (chunk IDs), not source IDs. Missing
    bodies are skipped: snippets/titles must never impersonate body coverage.
    ``ranking`` can return a score or the backend's (score, anchor_count).
    Results preserve original hit objects; the separate audit is navigation
    evidence only. A nonempty selection does not establish answer sufficiency.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError('query must be nonempty')
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError('top_k must be between 1 and 20')
    facets = _facets(query)
    candidates, skipped = [], []
    for position, hit in enumerate(hits):
        body = contents.get(hit.document_id)
        if not isinstance(body, str) or not body.strip():
            skipped.append({'chunk_id': hit.document_id, 'reason': 'body_missing'})
            continue
        value = ranking(hit)
        score = float(value[0] if isinstance(value, tuple) else value)
        if not math.isfinite(score):
            skipped.append({'chunk_id': hit.document_id, 'reason': 'nonfinite_score'})
            continue
        metadata = getattr(hit, 'metadata', {})
        # Equal words on distinct pages/rows can have different scope or units.
        # Only equivalent native anchors are eligible for body deduplication;
        # parent-context reconstruction is handled by the generation pipeline.
        locator = metadata.get('source_locator', '')
        context_locator = re.sub(r':part:\d+$', '', locator)
        normalized = _normalize(simplify_for_retrieval(body))
        # Deduplication is much stricter than retrieval normalization. Preserve
        # signs, operators, punctuation, identifier case and unit case. Only
        # whitespace layout may differ; no fuzzy/body-token comparison is safe.
        body_key = ' '.join(body.split())
        terms = _terms(body)
        coverage = set(facets).intersection(terms)
        # Exact qualifier and identifier boundaries, not substring matches.
        coverage.update(f for f in facets if re.search(r'(?<!\w)' + re.escape(f) + r'(?!\w)', normalized))
        if score <= 0 and not coverage:
            skipped.append({'chunk_id': hit.document_id, 'reason': 'zero_score_no_query_match'})
            continue
        candidates.append({'hit': hit, 'score': max(0.0, score), 'position': position,
                           'source': metadata.get('document_id', hit.document_id),
                           'version': metadata.get('source_sha256'), 'body': body_key,
                           'context': (metadata.get('page_no'), context_locator),
                           'coverage': coverage, 'terms': terms,
                           'hash': hashlib.sha256(body.encode('utf-8')).hexdigest()})
    candidates.sort(key=lambda c: (-c['score'], c['position']))
    unique = []
    for candidate in candidates:
        duplicate = None
        for previous in unique:
            if (candidate['source'], candidate['version'], candidate['context']) != (previous['source'], previous['version'], previous['context']):
                continue
            a, b = candidate['body'], previous['body']
            if a == b:
                duplicate = previous['hit'].document_id
                break
        if duplicate is not None:
            skipped.append({'chunk_id': candidate['hit'].document_id, 'reason': 'duplicate_actual_body', 'duplicate_of': duplicate})
        else:
            unique.append(candidate)
    best = max((c['score'] for c in unique), default=0.0)
    total = sum(facets.values()) or 1.0
    covered, selected, steps, counts = set(), [], [], {}
    remaining = list(unique)
    while remaining and len(selected) < top_k:
        def utility(candidate):
            relevance = candidate['score'] / best if best else 0.0
            marginal = sum(facets[f] for f in candidate['coverage'] - covered) / total
            # Tiny retrieval scores cannot be rescued merely by lexical overlap.
            gain = marginal * min(1.0, relevance / .15)
            repeat_penalty = .025 * counts.get(candidate['source'], 0)
            return .70 * relevance + .80 * gain - repeat_penalty
        chosen = max(remaining, key=lambda c: (utility(c), c['score'], -c['position']))
        remaining.remove(chosen)
        added = sorted(chosen['coverage'] - covered)
        steps.append({'chunk_id': chosen['hit'].document_id, 'source_id': chosen['source'],
                      'ranking_score': chosen['score'], 'utility': round(utility(chosen), 6),
                      'new_lexical_facets': added, 'body_sha256': chosen['hash']})
        covered.update(chosen['coverage'])
        counts[chosen['source']] = counts.get(chosen['source'], 0) + 1
        selected.append(chosen['hit'])
    available = set().union(*(c['coverage'] for c in unique)) if unique else set()
    return CoverageSelection(selected, {
        'method': 'query_body_lexical_coverage_navigation_only', 'semantic_sufficiency': 'not_evaluated',
        'top_k': top_k, 'candidate_count': len(hits), 'unique_body_count': len(unique),
        'query_facets': facets, 'covered_lexical_facets': sorted(covered),
        'uncovered_lexical_facets': sorted(set(facets) - covered),
        'available_but_unselected_facets': sorted(available - covered),
        'selection_budget_exhausted': bool(remaining and len(selected) == top_k),
        'source_counts': counts, 'steps': steps, 'skipped': skipped})
