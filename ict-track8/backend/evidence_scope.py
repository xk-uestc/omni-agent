"""Bounded evidence ledger. Mechanical coverage never proves semantic truth."""
from copy import deepcopy

STATES = frozenset({'complete_for_declared_scope', 'incomplete', 'ambiguous',
                    'source_changed', 'budget_exhausted'})


def page_scope(declared_sources, scanned_pages, supplied_pages, *, omitted=(),
               required_conditions=(), verified_conditions=(), ambiguous=False,
               source_changed=False):
    """Explicit source versions/pages; scanned and supplied are separate sets.

    Caller-supplied condition receipts refer to trusted review/execution, not
    query terms. A complete status is bounded by declared_sources ONLY.
    """
    declared = deepcopy(declared_sources)
    expected = set()
    versions = {}
    for d in declared:
        did, digest, numbers = d['document_id'], d['source_sha256'], d['pages']
        if (did in versions or not isinstance(digest, str) or len(digest) != 64
                or not numbers or any(type(n) is not int or n < 1 for n in numbers)
                or len(set(numbers)) != len(numbers)):
            raise ValueError('evidence_scope_declaration_invalid')
        versions[did] = digest
        expected.update((did, n) for n in numbers)
    def bind(rows):
        keys = set()
        for row in rows:
            did, n = row['document_id'], row['page_no']
            if versions.get(did) != row.get('source_sha256') or (did, n) not in expected:
                raise ValueError('evidence_scope_source_or_page_mismatch')
            keys.add((did, n))
        return keys
    scanned, supplied = bind(scanned_pages), bind(supplied_pages)
    if not supplied <= scanned: raise ValueError('evidence_scope_unscanned_supply')
    required = set(required_conditions)
    verified = set(verified_conditions)
    if not verified <= required: raise ValueError('evidence_scope_unknown_condition')
    budget = any('budget' in row.get('reason', '') for row in omitted)
    missing = expected - supplied
    status = ('source_changed' if source_changed else 'budget_exhausted' if budget else
              'ambiguous' if ambiguous else 'incomplete' if missing or required-verified or not expected
              else 'complete_for_declared_scope')
    return {'version': 'bounded-evidence-scope-v1', 'status': status,
            'declared_sources': declared, 'scanned_pages': sorted(scanned),
            'supplied_pages': sorted(supplied), 'unscanned_pages': sorted(expected-scanned),
            'unsupplied_pages': sorted(missing), 'required_conditions': sorted(required),
            'verified_conditions': sorted(verified), 'unverified_conditions': sorted(required-verified),
            'omitted': deepcopy(list(omitted)), 'budget_exhausted': budget,
            'semantic_sufficiency': 'not_proved_by_page_coverage',
            'closure': 'declared_source_versions_and_pages_only_not_global_corpus'}


def row_scope(registry, domain, matching_rows):
    """All actual rows of a selected supplied chain, with precise native anchors."""
    ids = [row['row_id'] for row in domain]
    if len(set(ids)) != len(ids): raise ValueError('native_select_duplicate_row_identity')
    if any(row.get('document_id', registry['document_id']) != registry['document_id']
           or row.get('source_sha256', registry['source_sha256']) != registry['source_sha256']
           for row in domain):
        raise ValueError('native_select_row_source_changed')
    return {'version': 'bounded-row-scope-v1', 'status': 'complete_for_declared_scope',
            'document_id': registry['document_id'], 'source_sha256': registry['source_sha256'],
            'declared_row_ids': ids, 'checked_row_ids': ids,
            'matching_row_ids': [row['row_id'] for row in matching_rows],
            'pages': sorted({row['page_no'] for row in domain}),
            'complete_record_set': 'selected_supplied_chain_only',
            'global_source_closure': False, 'semantic_sufficiency': 'requires_independent_review'}
