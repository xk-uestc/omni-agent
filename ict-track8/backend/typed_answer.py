"""Conservative display projections of independently validated full facts.

Only literal label/value and explicit represent/on-behalf-of relations are
supported. A proof is replayable provenance, not a general entailment proof.
Authoritative citations must already come from the server source snapshot.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .evidence_context import sentence_spans
from .grounded_generation import GroundedGenerator
from .responses_client import GenerationError


VERSION = 'typed-literal-projection-v1'
_SHA = re.compile(r'[0-9a-f]{64}')
_NAME = re.compile(r"[A-Za-z\u3400-\u9fff][A-Za-z\u3400-\u9fff0-9 &'’()_/-]{0,119}")
_NUMBER = re.compile(r'([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|USD|EUR|GBP|CNY|RMB|元|万元|吨|tons?|dollars?|hours?|件|笔)?', re.I)
_SCOPE_LABELS = {'year': 'year', '年份': 'year', 'entity': 'entity', '主体': 'entity',
                 'conditions': 'conditions', 'condition': 'conditions', 'scope': 'conditions',
                 '适用条件': 'conditions', '适用范围': 'conditions', 'units': 'unit', 'unit': 'unit',
                 '单位': 'unit', 'modality': 'modality', '口径': 'modality'}


def _sha(value):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _norm(value):
    # No synonyms, punctuation deletion, word reordering or numeric rounding.
    return ' '.join(value.split()).casefold()


def _entity(value):
    return bool(_NAME.fullmatch(value) and not re.search(
        r'\b(?:not|no|never|if|unless|is|are|was|were|does|do|did|may|might|must|'
        r'could|would|should|will|forecast|predicted|estimated|planned)\b', value, re.I))


def _request(question):
    if not isinstance(question, str) or not 1 <= len(question) <= 500:
        return None
    text = question.strip().rstrip('?？').strip()
    conditions = None
    prefix = re.fullmatch(r'For (.+?),\s*(.+)', text, re.I)
    if prefix:
        conditions, text = prefix.groups()
    scope = {'conditions': conditions}
    year = re.search(r'\s+in (\d{4})$', text, re.I)
    if year:
        scope['year'] = year.group(1)
        text = text[:year.start()]
    match = re.fullmatch(r'Who represents (.+)', text, re.I)
    if match:
        return {'kind': 'relation_subject', 'target': match.group(1), 'scope': scope}
    match = re.fullmatch(r'Which (?:party|parties|entity|entities) (?:does|do) (.+) represent', text, re.I)
    if match:
        return {'kind': 'relation_object', 'target': match.group(1), 'scope': scope}
    match = re.fullmatch(r'(Who is|What is|What number is|What amount is) (?:the )?(.+)', text, re.I)
    if not match:
        return None
    form, label = match.groups()
    entity = re.fullmatch(r'(.+?) (?:for|of) (.+)', label, re.I)
    if entity:
        label, scope['entity'] = entity.groups()
    modality = re.match(r'(forecast|actual) (.+)', label, re.I)
    if modality:
        scope['modality'], label = modality.groups()
        scope['modality'] = scope['modality'].lower()
    if not _NAME.fullmatch(label):
        return None
    kind = {'who is': 'role', 'what is': 'field', 'what number is': 'identifier',
            'what amount is': 'quantity'}[form.lower()]
    return {'kind': kind, 'target': label, 'scope': scope}


def _records(text, quote, quote_start):
    """All recognized scope declarations apply to every projected fact.

    Mixed or unknown extra prose in a supported quote refuses the entire
    quote, rather than silently dropping conditions or competing relations.
    """
    scope, scope_ranges, records = {}, {}, []
    for left, right in sentence_spans(quote):
        raw = quote[left:right]
        part = raw.strip()
        offset = quote_start + left + len(raw) - len(raw.lstrip())
        match = re.fullmatch(r'([^:\n：]{1,120})\s*[:：]\s*(.+)', part, re.S)
        if match:
            label, value = match.groups()
            label, value = label.strip(), value.strip()
            key = _SCOPE_LABELS.get(_norm(label))
            value_start = offset + match.start(2) + len(match.group(2)) - len(match.group(2).lstrip())
            if key:
                if key in scope and _norm(scope[key]) != _norm(value):
                    return []
                if key == 'year' and not re.fullmatch(r'\d{4}', value):
                    return []
                if key == 'modality' and value.lower() not in ('forecast', 'actual'):
                    return []
                scope[key] = value
                scope_ranges[key] = [value_start, value_start + len(value)]
            else:
                if not _NAME.fullmatch(label):
                    return []
                records.append({'kind': 'label_value', 'label': label, 'value': value,
                                'label_range': [offset + match.start(1), offset + match.start(1) + len(label)],
                                'value_range': [value_start, value_start + len(value)],
                                'relation_range': [offset, offset + len(part)]})
            continue
        relation = re.fullmatch(r'(.+?)\s+(represents?|represented|on behalf of)\s+(.+)', part, re.I)
        if relation and all(_entity(relation.group(i).strip()) for i in (1, 3)):
            subject, predicate, obj = relation.groups()
            records.append({'kind': 'representation', 'subject': subject.strip(), 'object': obj.strip(),
                            'predicate': predicate.lower(), 'relation_range': [offset, offset + len(part)],
                            'predicate_range': [offset + relation.start(2), offset + relation.end(2)],
                            'subject_range': [offset + relation.start(1), offset + relation.end(1)],
                            'object_range': [offset + relation.start(3), offset + relation.end(3)]})
            continue
        return []
    # An additional unknown label could be an exclusion, prerequisite or
    # version rather than an independent fact. This first bounded contract
    # authorizes exactly one fact plus explicitly recognized declarations.
    if len(records) != 1:
        return []
    return [{**record, 'scope': deepcopy(scope), 'scope_ranges': deepcopy(scope_ranges)} for record in records]


def _project(request, record):
    for key, value in request['scope'].items():
        if value is not None and (key not in record['scope'] or _norm(record['scope'][key]) != _norm(value)):
            return None
    kind = request['kind']
    if kind in ('relation_subject', 'relation_object'):
        if record['kind'] != 'representation':
            return None
        match_field, value_field = ('object', 'subject') if kind == 'relation_subject' else ('subject', 'object')
        if _norm(record[match_field]) != _norm(request['target']):
            return None
        return record[value_field], 'entity', record[value_field + '_range']
    if record['kind'] != 'label_value' or _norm(record['label']) != _norm(request['target']):
        return None
    value = record['value']
    numeric = _NUMBER.fullmatch(value)
    if numeric:
        if (numeric.group(2) and record['scope'].get('unit')
                and _norm(numeric.group(2)) != _norm(record['scope']['unit'])):
            return None
        unit = numeric.group(2) or record['scope'].get('unit')
        if kind == 'role' or (not unit and kind != 'identifier'):
            return None
        try:
            Decimal(numeric.group(1).replace(',', ''))
        except InvalidOperation:
            return None
        display = value if numeric.group(2) else value + (' ' + unit if unit else '')
        return display, 'quantity' if unit else 'identifier', record['value_range']
    if kind == 'quantity':
        return None
    if kind == 'identifier':
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_/-]{0,79}', value):
            return None
        return value, 'identifier', record['value_range']
    if not _entity(value):
        return None
    return value, 'entity', record['value_range']


def bind_answer_slot(question, claims, citations):
    """Projection only; unsupported results retain all original claims."""
    def unsupported(reason):
        return {'status': 'unsupported', 'reason': reason, 'claims': deepcopy(claims)}

    request = _request(question)
    if request is None:
        return unsupported('question_contract_unsupported')
    if not isinstance(citations, list) or not 1 <= len(citations) <= 8:
        return unsupported('evidence_contract_invalid')
    evidence, sources = {}, {}
    for citation in citations:
        if not isinstance(citation, dict):
            return unsupported('evidence_contract_invalid')
        identifier, generation = citation.get('citation_id'), citation.get('generation_evidence')
        if type(identifier) is not int or identifier in evidence or not isinstance(generation, dict):
            return unsupported('evidence_contract_invalid')
        text, source_sha = generation.get('text'), generation.get('source_sha256')
        if (not isinstance(text, str) or not 1 <= len(text) <= 1800
                or not isinstance(source_sha, str) or not _SHA.fullmatch(source_sha)
                or generation.get('evidence_sha256') != _sha(text)
                or not isinstance(generation.get('source_locator'), str)
                or not 1 <= len(generation['source_locator']) <= 250
                or generation.get('page_no') is not None
                and (type(generation['page_no']) is not int or generation['page_no'] < 1)):
            return unsupported('evidence_pin_invalid')
        metadata = citation.get('metadata', {})
        if (not isinstance(metadata, dict)
                or metadata.get('source_sha256', source_sha) != source_sha):
            return unsupported('source_snapshot_mismatch')
        evidence[identifier], sources[identifier] = text, generation
    try:
        GroundedGenerator.validate({'abstain': False, 'claims': claims}, evidence)
    except (GenerationError, TypeError, KeyError, ValueError):
        return unsupported('complete_claim_validation_failed')
    candidates = []
    for index, claim in enumerate(claims):
        for support in claim['support']:
            identifier, quote = support['citation_id'], support['quote']
            text = evidence[identifier]
            start = text.find(quote)
            if text.find(quote, start + 1) >= 0:
                return unsupported('quote_location_ambiguous')
            # Avoid skipping an unquoted scope declaration elsewhere in the
            # evidence. First implementation requires complete source context.
            if quote.strip().rstrip('.。') != text.strip().rstrip('.。'):
                continue
            for record in _records(text, quote, start):
                projection = _project(request, record)
                if projection is not None:
                    candidates.append((index, identifier, record, projection))
    if not candidates:
        return unsupported('no_verified_relation_slot')
    observed_slots = []
    for identifier, text in evidence.items():
        records = _records(text, text, 0)
        if not records and _norm(request['target']) in _norm(text):
            return unsupported('related_evidence_relation_unverified')
        for record in records:
            if _project(request, record) is not None:
                observed_slots.append((identifier, _sha(record)))
    # Even identical-looking answers from competing records are not assumed
    # to be the same entity/period/version. Uniqueness is evidence-local.
    if len(candidates) != 1 or len(observed_slots) != 1:
        return unsupported('ambiguous_or_conflicting_slot')
    index, identifier, record, (value, value_type, value_range) = candidates[0]
    source = sources[identifier]
    scope = {**record['scope'], 'field': record.get('label'),
             'relation_subject': record.get('subject'), 'relation_object': record.get('object')}
    numeric = _NUMBER.fullmatch(record.get('value', ''))
    unit_range = record['scope_ranges'].get('unit')
    if numeric:
        scope['unit'] = numeric.group(2) or scope.get('unit')
        if numeric.group(2):
            unit_range = [record['value_range'][0] + numeric.start(2),
                          record['value_range'][0] + numeric.end(2)]
    proof = {'version': VERSION, 'question_sha256': _sha(question), 'claims_sha256': _sha(claims),
             'citation_id': identifier, 'claim_index': index, 'source_sha256': source['source_sha256'],
             'evidence_sha256': _sha(evidence[identifier]), 'record_sha256': _sha(record),
             'request_sha256': _sha(request), 'relation_kind': record['kind'],
             'relation_range': record['relation_range'], 'value_range': value_range,
             'label_range': record.get('label_range'), 'predicate_range': record.get('predicate_range'),
             'unit_range': unit_range,
             'scope_ranges': record['scope_ranges'], 'source_locator': source.get('source_locator'),
             'page_no': source.get('page_no'),
             'uniqueness_scope': 'provided_verified_evidence', 'calculator_input_eligible': False}
    return {'status': 'verified', 'answer_value': value, 'answer_type': value_type,
            'answer_scope': scope, 'answer_proof': proof, 'claims': deepcopy(claims)}


def replay_answer_proof(question, result, citations):
    if not isinstance(result, dict) or result.get('status') != 'verified':
        return False
    try:
        return bind_answer_slot(question, result['claims'], citations) == result
    except (KeyError, TypeError, ValueError):
        return False
