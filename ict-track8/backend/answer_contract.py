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
_QUESTION_MARKER = re.compile(r'\b(?:what|which|how|when|where|why|who|whom)\b|'
    r'什么|哪些|多少|为何|为什么|何时|如何|谁|是否', re.I)
_MISSING_FACT = re.compile(r'\b(?:not\s+(?:stated|specified|recorded|provided|reported|available)|'
    r'(?:no|neither)\b[^.\n]{0,65}\b(?:stated|specified|recorded|provided|reported)|'
    r'(?:unknown|unspecified|unreported))\b|'
    r'(?:没有|未|尚未)(?:记载|记录|说明|提供|披露|给出)|未明确|无法确定|不详', re.I)
_NATIVE_MODES = {
    'original-native-table-chain-page-v1': frozenset({'original_native_candidate_table_chain_page'}),
    'original-native-bounded-page-region-v1': frozenset({'original_native_complete_table_region', 'original_native_complete_paragraph_region'}),
    'original-native-bounded-page-region-v2': frozenset({'original_native_complete_table_region', 'original_native_complete_paragraph_region', 'original_native_complete_captioned_table_region'}),
    'original-native-bounded-page-region-v3': frozenset({'original_native_complete_table_region', 'original_native_complete_paragraph_region', 'original_native_complete_captioned_table_region'}),
    'original-native-bounded-page-region-v4': frozenset({'original_native_complete_uncaptioned_table_region'}),
    'original-native-complete-block-context-v3': frozenset({'original_native_complete_block', 'original_native_complete_continuation'}),
    'original-native-complete-block-context-v4': frozenset({'original_native_complete_block', 'original_native_complete_continuation', 'original_native_complete_rotated_row'}),
    'original-native-complete-block-context-v5': frozenset({'original_native_complete_block', 'original_native_complete_continuation', 'original_native_complete_rotated_row'}),
}


def requested_question_parts(question):
    """Literal question spans; never inferred subjects, values or answers."""
    # Split only explicit interrogative continuations, not coordinated entities
    # such as 'Formula and Diapers' or conditions such as 'A and B are active'.
    boundaries = list(re.finditer(r'\band\s+(?:(?:particularly|specifically|also|especially)\s+)?'
                                 r'(?=(?:what|which|how|when|where|why|who|whom|by\s+whom|on\s+what)\b)|'
                                 r'(?:以及|并且|同时)[，,\s]*(?=(?:什么|哪些|多少|为何|为什么|何时|如何|谁|是否))', question, re.I))
    # Punctuation only separates independently explicit questions. It must
    # not split lists of entities, a declarative condition, or quoted labels.
    quoted = [match.span() for match in re.finditer(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」', question)]
    punctuation = list(re.finditer(r'[，,；;。？！?!]+\s*|\n+', question))
    for index, match in enumerate(punctuation):
        if any(left <= match.start() < right for left, right in quoted):
            continue
        left = punctuation[index - 1].end() if index else 0
        right = punctuation[index + 1].start() if index + 1 < len(punctuation) else len(question)
        if (_QUESTION_MARKER.search(question[left:match.start()])
                and _QUESTION_MARKER.search(question[match.end():right])):
            boundaries.append(match)
    boundaries.sort(key=lambda match: match.start())
    starts = [0] + [match.end() for match in boundaries]
    ends = [match.start() for match in boundaries] + [len(question)]
    return [question[left:right].strip() for left, right in zip(starts, ends) if question[left:right].strip()]


def question_contract(question):
    purpose = bool(_PURPOSE.search(question))
    explanation = purpose or bool(_EXPLANATION.search(question))
    person = bool(re.match(r'\s*(?:who\b|which\s+(?:person|people|member)\b)', question, re.I))
    parts = requested_question_parts(question)
    compound = len(parts) > 1 or bool(re.search(r'以及|并且|、[^？?\n]{1,100}(?:分别|各自)', question))
    exhaustive = bool(re.search(r'\b(?:which|what)\s+(?:(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\s+)?(?:elements|tests|methods|people|countries|items|'
        r'components|substrates|types|factors|reasons|departments|devices|requirements|provisions|'
        r'roles|duties|metrics|fields|standards)\b|\b(?:all|every)\s+(?:applicable|matching|relevant)\b'
        r'|哪些|所有(?:项目|对象|成分|因素|原因|记录)', question, re.I))
    return {'kind': 'explanation' if explanation else 'literal_fact',
        'purpose': purpose, 'person_or_role': person, 'multiple_requested_fields': compound,
        'exhaustive_selection_required': exhaustive,
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
    if not claims:
        return None
    answer = ' '.join(claim['text'] for claim in claims)
    # An explicit missing record is useful information, but cannot complete
    # a request for the missing fact. Existence/boolean questions may answer
    # negatively; this guard does not assert that the fact exists elsewhere.
    if (_MISSING_FACT.search(answer) and _QUESTION_MARKER.search(question)
            and not re.match(r'\s*(?:does|do|did|is|are|was|were|has|have)\b', question, re.I)
            and not re.search(r'是否|有没有', question)):
        return 'answer_contains_explicit_missing_fact'
    if not question_contract(question)['kind'] == 'explanation':
        return None
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
    if isinstance(native,dict) and native.get('extraction_version')=='original-native-table-chain-page-v1':
        chain=native.get('table_chain',{})
        pages=chain.get('pages',[])
        return (native_context_mode_valid(native) and native.get('calculator_input_eligible') is False
            and native.get('anchor_match_policy')==ANCHOR_POLICY_VERSION
            and len(pages)==2 and all(type(p) is int and p>0 for p in pages)
            and pages[1]==pages[0]+1 and native.get('page_no') in pages
            and chain.get('header_exact_and_lanes_aligned') is True
            and chain.get('semantic_sample_identity_verified') is False
            and chain.get('exhaustive_table_closure_verified') is False
            and isinstance(native.get('members'),list) and bool(native['members']))
    if not isinstance(native, dict) or native.get('extraction_version') not in {
            'original-native-complete-block-context-v4', 'original-native-bounded-page-region-v2',
            'original-native-complete-block-context-v5', 'original-native-bounded-page-region-v3',
            'original-native-bounded-page-region-v4'}:
        return True
    new_anchor = native.get('extraction_version') in {
        'original-native-complete-block-context-v5', 'original-native-bounded-page-region-v3',
        'original-native-bounded-page-region-v4'}
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
    if native.get('mode') == 'original_native_complete_uncaptioned_table_region':
        region = native.get('uncaptioned_native_region')
        members = native.get('members')
        layout = native.get('native_row_layout')
        if (not isinstance(region, dict) or region.get('version') != 'native-uncaptioned-aligned-region-v1'
                or region.get('page_local_only') is not True
                or region.get('semantic_row_column_binding_verified') is not False
                or not isinstance(members, list) or not 4 <= len(members) <= 16
                or not isinstance(layout, list) or not layout
                or not all(isinstance(row, list) and row for row in layout)):
            return False
        member_ids = {member.get('block_id') for member in members if isinstance(member, dict)}
        groups = [region.get(key) for key in ('row_block_ids', 'scope_block_ids', 'annotation_block_ids')]
        if (len(member_ids) != len(members) or region.get('header_block_id') not in member_ids
                or any(not isinstance(group, list)
                       or any(type(item) is not int or item not in member_ids for item in group)
                       or len(group) != len(set(group)) for group in groups)
                or len(groups[0]) < 2
                or set().union(*map(set, groups), {region['header_block_id']}) != member_ids):
            return False
        boundary = region.get('bottom_boundary')
        return (isinstance(boundary, dict) and boundary.get('kind') == 'native_prose_block'
                and type(boundary.get('block_id')) is int
                and (boundary['block_id'] not in member_ids or boundary['block_id'] in groups[1])
                and isinstance(boundary.get('bbox_fitz_unrotated_pt'), list)
                and len(boundary['bbox_fitz_unrotated_pt']) == 4)
    return True


def native_context_mode_valid(native):
    return isinstance(native, dict) and native.get('mode') in _NATIVE_MODES.get(native.get('extraction_version'), ())
