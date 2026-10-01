"""Prompt aids keep original fact scope; they do not authorize model edits."""
import pytest
import json

from backend.grounded_generation import GroundedGenerator, MAX_CANDIDATE_JSON_CHARS
from backend.responses_client import GenerationError


def evidence(text, citation_id=1):
    return {'text': text, 'citation_id': citation_id}


def test_parallel_ocr_lines_offer_whole_literal_context_not_spliced_response():
    source = '一般工单:首次响应时间为24小时\n紧急工单:首次响应时间为2小时。'
    options = GroundedGenerator.verbatim_candidates('紧急工单首次响应几小时', [evidence(source)])
    assert options
    assert all('一般工单' in item['text'] and '紧急工单' in item['text'] for item in options)
    for item in options:
        assert GroundedGenerator.validate({'abstain': False, 'claims': [item]}, {1: source})


def test_forecast_heading_does_not_offer_actual_claim_from_following_sentence():
    source = 'Forecast:\nProjected revenue 100 USD. Revenue 120 USD.'
    options = GroundedGenerator.verbatim_candidates('revenue', [evidence(source)])
    assert not any(item['text'].strip() == 'Revenue 120 USD' for item in options)
    assert any('Forecast:' in item['text'] for item in options)


def test_candidates_keep_literal_entities_units_negation_and_conditions():
    source = '若审批通过，华东报销限额100元。保修不覆盖人为损坏。'
    options = GroundedGenerator.verbatim_candidates('报销限额', [evidence(source)])
    assert options[0]['text'].startswith('若审批通过，华东')
    assert all(item['support'][0]['quote'] == item['text'] for item in options)
    assert all(item['text'] in source for item in options)
    with pytest.raises(GenerationError):
        GroundedGenerator.validate({'abstain': False, 'claims': [
            {'text': '华东报销限额100元', 'support': options[0]['support']}]}, {1: source})


def test_candidate_size_bound_and_no_cross_source_numeric_composition():
    sources = [evidence(f'区域{i}收入{i}万元。', i) for i in range(1, 60)]
    options = GroundedGenerator.verbatim_candidates('各区域收入', sources)
    assert 0 < len(options) <= 12
    assert sum(len(item['text']) for item in options) <= 2400
    originals = {item['citation_id']: item['text'] for item in sources}
    for item in options:
        assert GroundedGenerator.validate({'abstain': False, 'claims': [item]}, originals)
    wrong = {'text': '区域1收入2万元', 'support': [
        {'citation_id': 1, 'quote': originals[1]}, {'citation_id': 2, 'quote': originals[2]}]}
    with pytest.raises(GenerationError):
        GroundedGenerator.validate({'abstain': False, 'claims': [wrong]}, originals)


def test_long_unbroken_fact_is_omitted_without_truncation():
    source = '若审核完成才允许' + '甲' * 320 + '支付100元'
    assert GroundedGenerator.verbatim_candidates('支付', [evidence(source)]) == []


def claim(text, quote):
    return {'abstain': False, 'claims': [{'text': text, 'support': [{'citation_id': 1, 'quote': quote}]}]}


@pytest.mark.parametrize('source,bare', [
    ('仅限有效订单。\n销售额为100万元。', '销售额为100万元'),
    ('单位：万元。\n华东收入100。', '华东收入100'),
    ('单位：万元。收入100。利润200。', '利润200'),
    ('适用条件：有效订单。销售额100万元。', '销售额100万元'),
    ('仅限有效订单。单位：万元。销售额100。', '销售额100'),
    ('Units: USD. Revenue 100. Profit 200.', 'Profit 200'),
    ('Applicable conditions: approved orders. Revenue 100 USD.', 'Revenue 100 USD'),
])
def test_source_declared_unit_or_adjacent_condition_cannot_be_removed(source, bare):
    with pytest.raises(GenerationError, match='单位或适用条件'):
        GroundedGenerator.validate(claim(bare, bare), {1: source})
    assert GroundedGenerator.validate(claim(source, source), {1: source})


@pytest.mark.parametrize('source,bare', [
    ('仅限有效订单。\n销售额为100万元。', '销售额为100万元'),
    ('单位：万元。收入100。利润200。', '利润200'),
])
def test_aids_offer_literal_scoped_block_not_bare_fact(source, bare):
    options = GroundedGenerator.verbatim_candidates(bare, [evidence(source)])
    assert options
    assert not any(option['text'].strip() == bare for option in options)
    assert any(source.split('。')[0] in option['text'] and bare in option['text'] for option in options)
    assert all(option['text'] in source and option['support'][0]['quote'] == option['text'] for option in options)


@pytest.mark.parametrize('source,bare', [
    ('仅限有效订单。销售额100万元。\n\n硬件保修12个月。', '硬件保修12个月'),
    ('单位：万元。收入100。\n\n硬件保修12个月。', '硬件保修12个月'),
    ('单位：万元。收入100。\n# 硬件保修\n期限12个月。', '\n# 硬件保修\n期限12个月'),
    ('单位：万元。收入100。\n硬件保修：\n期限12个月。', '\n硬件保修：\n期限12个月'),
])
def test_explicit_scope_does_not_pollute_separate_fact_or_section(source, bare):
    assert GroundedGenerator.validate(claim(bare, bare), {1: source})


def test_new_unit_heading_does_not_inherit_previous_unit():
    source = '单位：万元。收入100。单位：小时。首次响应2。'
    assert GroundedGenerator.validate(claim('单位：小时。首次响应2', '单位：小时。首次响应2'), {1: source})
    with pytest.raises(GenerationError):
        GroundedGenerator.validate(claim('首次响应2', '首次响应2'), {1: source})


def test_equal_relevance_aids_diversify_sources_without_reordering_higher_match():
    sources = [evidence(''.join(f'区域{i}收入{j}万元。' for j in range(20)), i) for i in range(1, 9)]
    options = GroundedGenerator.verbatim_candidates('区域收入', sources)
    assert len({item['support'][0]['citation_id'] for item in options}) == 8
    assert len(json.dumps(options, ensure_ascii=False)) <= MAX_CANDIDATE_JSON_CHARS
    assert GroundedGenerator.verbatim_candidates('保修', [evidence('收入100万元。')]) == []


def test_full_serialized_json_budget_counts_repeated_quotes_and_overhead():
    sources = [evidence(''.join(f'收入{j}为100万元，' + '适用于已批准订单并保留单位，' * 15 + '结束。' for j in range(8)), i)
               for i in range(1, 9)]
    options = GroundedGenerator.verbatim_candidates('收入已批准订单', sources)
    assert options and len(json.dumps(options, ensure_ascii=False)) <= MAX_CANDIDATE_JSON_CHARS
    assert sum(len(item['text']) for item in options) <= 2400 and len(options) <= 12


def test_cached_source_scope_proof_is_immutable_and_content_scoped():
    from backend.grounded_generation import _adjacent_declared_scopes
    scopes = _adjacent_declared_scopes('单位：万元。收入100。')
    with pytest.raises(TypeError):
        scopes[0]['headings'] = ()
    source = '单位：元。收入100。'
    with pytest.raises(GenerationError):
        GroundedGenerator.validate(claim('收入100', '收入100'), {1: source})
    assert GroundedGenerator.validate(claim(source, source), {1: source})


def test_four_heading_chain_never_authorizes_a_shorter_suffix():
    source = '仅限有效订单。仅在完成支付。若审批通过。除非系统故障。销售额100元。'
    suffix = '仅在完成支付。若审批通过。除非系统故障。销售额100元'
    with pytest.raises(GenerationError, match='单位或适用条件'):
        GroundedGenerator.validate(claim(suffix, suffix), {1: source})
    assert GroundedGenerator.validate(claim(source, source), {1: source})
    options = GroundedGenerator.verbatim_candidates('销售额', [evidence(source)])
    assert options
    assert all('仅限有效订单' in option['text'] for option in options if '销售额' in option['text'])


def test_long_heading_chain_rejects_even_validated_suffix_or_full_fact():
    from backend.grounded_generation import MAX_LOCAL_SCOPE_HEADINGS
    headings = [f'仅限条件{chr(0x7532 + index)}' for index in range(MAX_LOCAL_SCOPE_HEADINGS + 1)]
    source = '。'.join([*headings, '销售额100元']) + '。'
    suffix = '。'.join([*headings[1:], '销售额100元'])
    for text in (source, suffix, '销售额100元'):
        with pytest.raises(GenerationError, match='标题链超出核验预算'):
            GroundedGenerator.validate(claim(text, text), {1: source})
    options = GroundedGenerator.verbatim_candidates('销售额', [evidence(source)])
    assert not any('销售额' in option['text'] for option in options)


def test_eight_heading_chain_is_complete_at_the_explicit_budget():
    from backend.grounded_generation import MAX_LOCAL_SCOPE_HEADINGS
    headings = [f'仅限条件{chr(0x7532 + index)}' for index in range(MAX_LOCAL_SCOPE_HEADINGS)]
    source = '。'.join([*headings, '销售额100元']) + '。'
    assert GroundedGenerator.validate(claim(source, source), {1: source})
    for index in (0, 3, MAX_LOCAL_SCOPE_HEADINGS - 1):
        text = '。'.join([heading for number, heading in enumerate(headings) if number != index] + ['销售额100元'])
        # Use the whole literal source as support: quote validity alone must
        # not permit dropping a middle heading from the claim.
        with pytest.raises(GenerationError):
            GroundedGenerator.validate(claim(text, source), {1: source})


@pytest.mark.parametrize('source,bare', [
    ('仅限有效订单。销售额100元。订单数2。', '订单数2'),
    ('仅限有效订单。销售额100元。若审批通过。订单数2。', '若审批通过。订单数2'),
    ('仅限有效订单。单位：元。销售额100。单位：笔。订单数2。', '单位：笔。订单数2'),
    ('Applicable conditions: approved orders. Revenue 100 USD. Orders 2.', 'Orders 2'),
])
def test_declared_conditions_persist_across_all_paragraph_facts(source, bare):
    with pytest.raises(GenerationError, match='单位或适用条件'):
        GroundedGenerator.validate(claim(bare, bare), {1: source})
    assert GroundedGenerator.validate(claim(source, source), {1: source})
    options = GroundedGenerator.verbatim_candidates(bare, [evidence(source)])
    assert options
    final_fact = bare.split('。')[-1]
    assert any(final_fact in option['text'] for option in options)
    assert all(source.split('。')[0].split('. ')[0] in option['text']
               for option in options if final_fact in option['text'])


@pytest.mark.parametrize('boundary', ['\n\n', '\n# 独立章节\n', '\n[独立章节]\n', '\n独立章节：\n'])
def test_new_paragraph_or_explicit_section_resets_conditions_and_unit(boundary):
    source = '仅限有效订单。单位：元。销售额100。' + boundary + '订单数2。'
    quote = boundary + '订单数2'
    assert GroundedGenerator.validate(claim(quote, quote), {1: source})


def test_declaration_budget_cannot_be_bypassed_by_later_fact_or_heading():
    from backend.grounded_generation import MAX_LOCAL_SCOPE_HEADINGS
    headings = [f'仅限条件{chr(0x7532 + index)}' for index in range(MAX_LOCAL_SCOPE_HEADINGS + 1)]
    source = '。'.join([*headings, '销售额100元', '订单数2', '单位：笔', '退货数3']) + '。'
    for text in (source, '订单数2', '单位：笔。退货数3'):
        with pytest.raises(GenerationError, match='标题链超出核验预算'):
            GroundedGenerator.validate(claim(text, text), {1: source})
    assert not GroundedGenerator.verbatim_candidates('订单数退货数', [evidence(source)])
    new_paragraph = source + '\n\n保修12个月。'
    assert GroundedGenerator.validate(claim('保修12个月', '保修12个月'), {1: new_paragraph})


def test_declaration_budget_accumulates_across_interleaved_facts():
    from backend.grounded_generation import MAX_LOCAL_SCOPE_HEADINGS
    source = ''.join(f'仅限条件{chr(0x7532 + index)}。收入{index}元。'
                     for index in range(MAX_LOCAL_SCOPE_HEADINGS + 1))
    text = source.rstrip('。')
    with pytest.raises(GenerationError, match='标题链超出核验预算'):
        GroundedGenerator.validate(claim(text, text), {1: source})
