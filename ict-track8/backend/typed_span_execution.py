"""Bounded executions on literal question/source spans, never model numbers.

Semantic alignment belongs to an independent review. This module only handles
one unambiguous percent observation against one explicit percent inequality.
It cannot infer a threshold, convert units, choose a convention or answer a
negated/compound question.
"""
from decimal import Decimal
import re

VERSION = 'source-percent-threshold-v1'
_NUM = r'[+−-]?\d+(?:\.\d+)?'
_VALUE = re.compile(r'(?<![A-Za-z0-9_.,+−-])('+_NUM+r')\s*%')
_THRESHOLD = re.compile(
    r'(?P<operator>≤|>=|≥|<=|<|>|at\s+most|at\s+least|less\s+than\s+or\s+equal\s+to|'
    r'greater\s+than\s+or\s+equal\s+to|less\s+than|greater\s+than|'
    r'not\s+exceed|no\s+more\s+than|不超过|不高于|不低于|至多|至少|小于|大于)'
    r'\s*(?P<value>'+_NUM+r')\s*%', re.I)


def boolean_question(question):
    if not isinstance(question, str):
        return False
    return bool(re.match(r'\s*(?:does|do|did|is|are|was|were|can|could|will|would|should|has|have)\b', question, re.I)
        or re.match(r'\s*if\b', question, re.I) and re.search(r'\b(?:does|do|is|are|can|will|would)\b', question, re.I)
        or re.search(r'是否|能否|是不是|可否', question))


def entity_question(question):
    return bool(re.match(r'\s*who\b|\s*which\s+(?:person|people|member|body|organization|entity|entities|party|parties|company|team)\b', question, re.I)
                or re.search(r'谁|哪个(?:人|机构|公司|团队)', question))


def question_observation(question):
    if not boolean_question(question) or not re.search(r'\b(?:meet|satisfy|comply|within|acceptable|pass)\b|符合|满足|达标|合格', question, re.I):
        raise ValueError('threshold_question_unsupported')
    if re.search(r"\b(?:not|unless|except|either|both|neither)\b|n['’]t\b|不满足|不符合|不合格|除外|或者", question, re.I):
        raise ValueError('threshold_question_polarity_or_compound')
    values = list(_VALUE.finditer(question))
    # Do not silently ignore a second observation, a year, or an arithmetic
    # expression. Unsupported numeric forms must not become suffix values.
    numerals = list(re.finditer(r'\d+(?:\.\d+)?', question))
    if len(values) != 1 or len(numerals) != 1:
        raise ValueError('threshold_question_observation_ambiguous')
    match = values[0]
    if any(char.isnumeric() and not match.start()<=index<match.end() for index,char in enumerate(question)):
        raise ValueError('threshold_question_observation_unsupported')
    if numerals[0].start() < match.start() or numerals[0].end() > match.end():
        raise ValueError('threshold_question_observation_ambiguous')
    if re.search(r'[/=*:^±≈~～]', question):
        raise ValueError('threshold_question_observation_unsupported')
    return {'quote': match.group(), 'offsets': [match.start(), match.end()],
            'value': str(Decimal(match.group(1).replace('−','-'))), 'unit': 'percent'}


def execute_threshold(question, threshold_quote):
    observation = question_observation(question)
    if re.search(r'\bnot\s+(?!exceed\b)|\bno\s+(?!more\s+than\b)|unless|except|不(?:小于|大于|满足|符合)|除外',threshold_quote,re.I):
        raise ValueError('threshold_source_negation_or_exception_unsupported')
    values = list(_VALUE.finditer(threshold_quote))
    matches = list(_THRESHOLD.finditer(threshold_quote))
    if len(values) != 1 or len(matches) != 1:
        raise ValueError('threshold_source_inequality_ambiguous')
    match = matches[0]
    if any(char.isnumeric() and not match.start()<=index<match.end() for index,char in enumerate(threshold_quote)):
        raise ValueError('threshold_source_numeric_scope_ambiguous')
    if values[0].start() < match.start() or values[0].end() > match.end():
        raise ValueError('threshold_source_inequality_ambiguous')
    # Other numeric content can encode a range, exception or a competing year.
    if len(list(re.finditer(r'\d+(?:\.\d+)?', threshold_quote))) != 1:
        raise ValueError('threshold_source_numeric_scope_ambiguous')
    if re.search(r'[/*^±≈~～]|(?<![<>])=(?![<>])', threshold_quote):
        raise ValueError('threshold_source_expression_unsupported')
    operator = re.sub(r'\s+', ' ', match.group('operator').casefold())
    mapping = {'≤':'le','<=':'le','at most':'le','less than or equal to':'le',
        'not exceed':'le','no more than':'le','不超过':'le','不高于':'le','至多':'le',
        '≥':'ge','>=':'ge','at least':'ge','greater than or equal to':'ge',
        '不低于':'ge','至少':'ge','<':'lt','less than':'lt','小于':'lt',
        '>':'gt','greater than':'gt','大于':'gt'}
    operation = mapping[operator]
    left = Decimal(observation['value'])
    right = Decimal(match.group('value').replace('−','-'))
    outcome = {'le':left <= right,'ge':left >= right,'lt':left < right,'gt':left > right}[operation]
    return {'version':VERSION, 'operation':operation, 'question_observation':observation,
        'threshold_quote':threshold_quote, 'threshold_operator_quote':match.group('operator'),
        'threshold_value':str(right), 'unit':'percent', 'outcome':outcome,
        'answer_value':'Yes' if outcome else 'No', 'answer_type':'boolean',
        'calculation': f'{left} {operation} {right}'}
