"""Question-shape navigation aids, never source relations or semantic proof.

These generic rules neither know corpus answers nor authorize a fact. They
help retrieve substantive prose and keep literal projections focused; the
existing source validation and independent review remain authoritative.
"""
import re

from .native_anchor import ANCHOR_POLICY_VERSION


_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9'’-]*|[\u3400-\u9fff]{2,}")
_PURPOSE = re.compile(r'\b(?:purpose|purposes|objective|objectives|aim|aims)\b|目的|宗旨', re.I)
_EXPLANATION = re.compile(r'\b(?:why|impact|effect|reason|reasons|cause|causes|roles?)\b|原因|影响|职责|作用', re.I)
_PURPOSE_BODY = re.compile(r'\b(?:prescribes?|specifies?|intended|aims?|objectives?|purpose|purposes)\b|'
    r'\b(?:scope|introduction)\b|\bto\s+(?:ensure|establish|estimate|evaluate|stabilize|balance|provide|help)\b|'
    r'旨在|目的是|规定|适用范围', re.I)
_EXPLANATORY_MARKER = re.compile(r'^\s*to\b|\b(?:because|therefore|so|prescribes?|specifies?|intended|'
    r'enables?|ensures?|helps?|allows?|requires?|caused?|resulted?|continued?|stopped?|prevented?)\b|'
    r'旨在|为了|由于|因此|规定|导致|帮助|允许|继续|停止', re.I)
_NAVIGATION_STOP = set('a an the what which who is are was were be for to of from in on at by '
    'with and or as purpose purposes objective objectives aim aims'.split())
_NATIVE_MODES = {
    'original-native-bounded-page-region-v1': frozenset({'original_native_complete_table_region', 'original_native_complete_paragraph_region'}),
    'original-native-bounded-page-region-v2': frozenset({'original_native_complete_table_region', 'original_native_complete_paragraph_region', 'original_native_complete_captioned_table_region'}),
    'original-native-bounded-page-region-v3': frozenset({'original_native_complete_table_region', 'original_native_complete_paragraph_region', 'original_native_complete_captioned_table_region'}),
    'original-native-complete-block-context-v3': frozenset({'original_native_complete_block', 'original_native_complete_continuation'}),
    'original-native-complete-block-context-v4': frozenset({'original_native_complete_block', 'original_native_complete_continuation', 'original_native_complete_rotated_row'}),
    'original-native-complete-block-context-v5': frozenset({'original_native_complete_block', 'original_native_complete_continuation', 'original_native_complete_rotated_row'}),
}


def requested_question_parts(question):
    """Literal question spans; never inferred subjects, values or answers."""
    # Split only explicit interrogative continuations, not coordinated entities
    # such as 'Formula and Diapers' or conditions such as 'A and B are active'.
    boundaries = list(re.finditer(r'\band\s+(?=(?:what|which|how|when|where|why|who|on\s+what)\b)|'
                                 r'(?:以及|并且|同时)[，,\s]*(?=(?:什么|哪些|多少|为何|为什么|何时|如何|谁|是否))', question, re.I))
    starts = [0] + [match.end() for match in boundaries]
    ends = [match.start() for match in boundaries] + [len(question)]
    return [question[left:right].strip() for left, right in zip(starts, ends) if question[left:right].strip()]


def question_contract(question):
    purpose = bool(_PURPOSE.search(question))
    explanation = purpose or bool(_EXPLANATION.search(question))
    person = bool(re.match(r'\s*(?:who\b|which\s+(?:person|people|member)\b)', question, re.I))
    parts = requested_question_parts(question)
    compound = len(parts) > 1 or bool(re.search(r'以及|并且', question))
    return {'kind': 'explanation' if explanation else 'literal_fact',
        'purpose': purpose, 'person_or_role': person, 'multiple_requested_fields': compound,
        'requested_parts': parts,
        'instruction': ('Answer every requested field with substantive source facts. A document title '
            'only identifies the subject; it does not explain purpose, impact, reasons or roles. '
            'Keep all requested people and their separate roles together. For a literal short answer, '
            'select the smallest complete source phrase: a name, value with units/signs, date or event. '
            'Put supporting years, role labels and conditions in context/scope, not extra answer prose. '
            'Never remove a qualifier needed by the answer itself or replace missing evidence with inference.')}


def body_answer_affinity(question, body):
    """A bounded ranking bonus for generic purpose prose, not truth scoring."""
    topic = {word.casefold() for word in _WORDS.findall(question)} - _NAVIGATION_STOP
    body_words = {word.casefold() for word in _WORDS.findall(body)}
    return 2.5 if (_PURPOSE.search(question) and _PURPOSE_BODY.search(body)
        and len(topic.intersection(body_words)) >= 2) else 1.0


def substantive_numbered_heading(text):
    """Recover numbered normative prose misclassified as a section heading.

    A leading clause number does not make its following assertion a title.
    True headings and arbitrary keyword windows are still excluded.
    """
    if not re.match(r'\s*\d+(?:\.\d+)+(?:[.)])?\s+', text):
        return False
    return bool(re.search(r'\b(?:is|are|was|were|has|have|had|shall|must|will|may|can|should|'
        r'prescribes?|specifies?|includes?|applies?|means?)\b|应当|必须|规定|适用于|是指', text, re.I))


def whole_answer_shape_error(question, claims):
    """Catch an answer that only repeats the asked-about title/subject.

    This deliberately does not attempt general semantic completeness. A
    failure asks for a fresh evidence-based answer before any abstention.
    """
    if not claims or not question_contract(question)['kind'] == 'explanation':
        return None
    answer = ' '.join(claim['text'] for claim in claims)
    if _EXPLANATORY_MARKER.search(answer):
        return None
    answer_words = {word.casefold() for word in _WORDS.findall(answer)}
    question_words = {word.casefold() for word in _WORDS.findall(question)}
    if len(answer_words) >= 3 and answer_words <= question_words:
        return 'answer_only_repeats_question_subject'
    return None


def literal_answer_shape_error(question, literal):
    """Recognize obvious table-row and framing leakage, without trimming it."""
    span, kind = literal['answer_span'], literal['answer_type']
    contract = question_contract(question)
    if contract['person_or_role'] and kind == 'entity' and re.search(r'\b(?:18|19|20|21)\d{2}\b', span):
        return 'entity_answer_contains_table_year'
    if kind == 'quantity' and re.match(r'\s*(?:subtotal\b|total\s+(?!of\b)|'
            r'.{1,100}?\b(?:is|are|was|were)\s+(?=[+−\-$€£¥]?\d))', span, re.I):
        return 'quantity_answer_contains_framing'
    return whole_answer_shape_error(question, [{'text': span}])


def native_source_only_contract_valid(native):
    """New geometry modes are provenance only, never verified numeric rows."""
    if not isinstance(native, dict) or native.get('extraction_version') not in {
            'original-native-complete-block-context-v4', 'original-native-bounded-page-region-v2',
            'original-native-complete-block-context-v5', 'original-native-bounded-page-region-v3'}:
        return True
    new_anchor = native.get('extraction_version') in {
        'original-native-complete-block-context-v5', 'original-native-bounded-page-region-v3'}
    policy = (ANCHOR_POLICY_VERSION if new_anchor
              else 'whitespace_and_printed_alphabetic_line_wrap_hyphen_only')
    if (not native_context_mode_valid(native) or native.get('calculator_input_eligible') is not False
            or native.get('anchor_match_policy') != policy):
        return False
    if new_anchor:
        match = native.get('anchor_match')
        if (not isinstance(match, dict) or match.get('policy_version') != policy
                or not isinstance(match.get('members'), list) or not match['members']):
            return False
    if native.get('mode') == 'original_native_complete_rotated_row':
        rotated = native.get('rotated_native_row')
        return isinstance(rotated, dict) and rotated.get('semantic_row_column_binding_verified') is False
    if native.get('mode') == 'original_native_complete_captioned_table_region':
        layout = native.get('native_row_layout')
        return isinstance(layout, list) and bool(layout) and all(isinstance(row, list) and row for row in layout)
    return True


def native_context_mode_valid(native):
    return isinstance(native, dict) and native.get('mode') in _NATIVE_MODES.get(native.get('extraction_version'), ())
