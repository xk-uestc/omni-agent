import pytest
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import GenerationError
from backend.formula_binding import FormulaBinder, ParameterEvidence
from backend.policy_evidence import select_policy


def test_unsupported_claim_number_and_fabricated_quote_rejected():
    with pytest.raises(GenerationError, match='数字'):
        GroundedGenerator.validate({'abstain': False, 'claims': [{'text': '期限14日', 'support': [{'citation_id': 1, 'quote': '期限7日'}]}]}, {1: '期限7日'})
    with pytest.raises(GenerationError, match='原文'):
        GroundedGenerator.validate({'abstain': False, 'claims': [{'text': '期限7日', 'support': [{'citation_id': 1, 'quote': '期限7日'}]}]}, {1: '期限5日'})


def test_units_convert_percentage_and_reject_currency_mix():
    binder = FormulaBinder()
    p = lambda value, unit: ParameterEvidence(value, 'doc://sample', 'line:1', unit)
    result = binder.calculate('收入*(1+增长)', {'收入': p(2, '万元'), '增长': p(12, '%')}, formula_source='doc://sample', formula_locator='line:2')
    assert result['value'] == pytest.approx(22400)
    assert result['unit_validation'] == 'validated' and result['result_unit'] == 'CNY'
    with pytest.raises(ValueError, match='单位'):
        binder.calculate('a+b', {'a': p(2, 'CNY'), 'b': p(2, 'USD')}, formula_source='doc://sample', formula_locator='line:2')


def test_policy_version_boundary_and_overlap():
    document = {'document_id': 'p', 'sha256': 's', 'chunks': [{'source_locator': 'text:1', 'text': '2024版期限为5日，生效区间为2024-01-01至2024-12-31。\n2025版期限为7日，自2025-01-01起生效。'}]}
    assert select_policy(document, as_of='2024-12-31', label='期限')['value'] == '5日'
    assert select_policy(document, as_of='2025-01-01', label='期限')['value'] == '7日'
    with pytest.raises(ValueError, match='缺失'):
        select_policy(document, as_of='2023-12-31', label='期限')
    document['chunks'][0]['text'] += '\n期限为9日，自2024-06-01起生效。'
    with pytest.raises(ValueError, match='重叠'):
        select_policy(document, as_of='2025-06-01', label='期限')


def test_normalized_ascii_comma_is_not_part_of_policy_value():
    document = {'document_id': 'p', 'sha256': 's', 'chunks': [{'source_locator': 'line:1', 'text': '2025版期限为7日,自2025-01-01起生效。'}]}
    result = select_policy(document, as_of='2025-06-01', label='期限')
    assert result['value'] == '7日'
    assert result['valid_from'] == '2025-01-01'
