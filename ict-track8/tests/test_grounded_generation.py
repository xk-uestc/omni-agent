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


def _claim(text, quote):
    return {'abstain': False, 'claims': [{'text': text, 'support': [{'citation_id': 1, 'quote': quote}]}]}


@pytest.mark.parametrize('text,quote', [
    ('华东收入200万元，华西收入100万元。', '华东收入100万元，华西收入200万元。'),
    ('华东收入100万元。', '华西收入100万元。'),
    ('收入100万元。', '华东收入100万元。'),
    ('亏损100万元。', '亏损-100万元。'),
    ('亏损-100万元。', '亏损100万元。'),
    ('保修期12年。', '保修期12个月。'),
    ('保修覆盖人为损坏。', '保修不覆盖人为损坏。'),
    ('没有成本数据可以计算毛利率。', '没有成本数据不能计算毛利率。'),
    ('2026年实际收入100万元。', '2026年预测收入100万元。'),
    ('2026年收入100万元。', '2026年预测收入100万元。'),
    ('报销限额100万元。', '若审批通过，报销限额100万元。'),
    ('华公司收入100万元。', '华为公司收入100万元。'),
    ('账户余额123456789012345678901234567890元。', '账户余额123456789012345678901234567891元。'),
])
def test_unverified_fact_relation_degrades_instead_of_reusing_number_set(text, quote):
    with pytest.raises(GenerationError):
        GroundedGenerator.validate(_claim(text, quote), {1: quote})


@pytest.mark.parametrize('text,quote', [
    ('华东收入是100.0万元。', '华东收入为100万元。'),
    ('华东收入达到100万元，华西收入是200万元。', '华东收入100万元，华西收入200万元。'),
    ('保修期限是12个月。', '保修期为12个月。'),
    ('华为公司收入是100万元。', '华为公司收入100万元。'),
    ('2026年预计收入100万元。', '2026年预计收入为100万元。'),
    ('没有成本数据无法计算毛利率。', '没有成本数据不能计算毛利率。'),
    ('资料显示保修不覆盖人为损坏。', '保修不覆盖人为损坏。'),
    ('若审批通过，报销限额100万元。', '若审批通过，报销限额为100万元。'),
])
def test_conservative_correct_paraphrase_preserves_entity_units_and_scope(text, quote):
    assert GroundedGenerator.validate(_claim(text, quote), {1: quote})[0]['text'] == text


def test_arbitrary_qualitative_paraphrase_is_unverified_not_a_semantic_proof():
    quote = '团队采用重点客户分层和每周回访。'
    with pytest.raises(GenerationError, match='未核验'):
        GroundedGenerator.validate(_claim('团队业绩好得益于客户管理创新。', quote), {1: quote})


@pytest.mark.parametrize('text,quote,original', [
    ('覆盖人为损坏', '覆盖人为损坏', '不覆盖人为损坏。'),
    ('华东收入100万元', '华东收入100万元', '预计华东收入100万元。'),
    ('亏损100万元', '亏损100万元', '并非亏损100万元。'),
    ('报销限额100万元', '报销限额100万元', '若审批通过，报销限额100万元。'),
    ('华东收入100万元', '华东收入100万元', '本节为预测数据，华东收入100万元。'),
    ('覆盖人为损坏', '覆盖人为损坏', '不\n覆盖人为损坏。'),
    ('华东收入100万元', '华东收入100万元', '预计\n华东收入100万元。'),
])
def test_truncated_literal_quote_cannot_strip_original_sentence_scope(text, quote, original):
    # Each quote is literally present, but that alone cannot authorize
    # deletion of the original fact's qualifier.
    assert quote in original
    with pytest.raises(GenerationError, match='未核验'):
        GroundedGenerator.validate(_claim(text, quote), {1: original})


@pytest.mark.parametrize('text,quote,original', [
    ('保修期是12个月', '保修期为12个月', '预计华东收入100万元。保修期为12个月。'),
    ('华西实际收入200万元', '华西实际收入200万元', '预计华东收入100万元，华西实际收入200万元。'),
    ('不覆盖人为损坏', '不覆盖人为损坏', '保修期为12个月。不覆盖人为损坏。'),
    ('不\n覆盖人为损坏', '不\n覆盖人为损坏', '不\n覆盖人为损坏。'),
    ('若审批通过，报销限额100万元', '若审批通过，报销限额100万元', '预计收入100万元。若审批通过，报销限额100万元。'),
])
def test_scope_validation_keeps_independent_sentences_and_local_parallel_facts(text, quote, original):
    assert GroundedGenerator.validate(_claim(text, quote), {1: original})[0]['text'] == text


@pytest.mark.parametrize('text,quote,original', [
    ('服务不覆盖人工损坏和私自拆机', '不覆盖人工损坏和私自拆机', '服务覆盖正常使用的故障,不覆盖人工损坏和私自拆机。'),
    ('服务不提供临时住宿', '不提供临时住宿', '服务提供交通接送,不提供临时住宿。'),
    ('合同允许公开资料访问', '允许公开资料访问', '合同允许内部资料访问,允许公开资料访问。'),
    ('服务覆盖制造缺陷', '覆盖制造缺陷', '服务不覆盖人工损坏,覆盖制造缺陷。'),
])
def test_repeated_parallel_subject_is_verified_in_original_clause(text, quote, original):
    assert GroundedGenerator.validate(_claim(text, quote), {1: original})[0]['text'] == text


@pytest.mark.parametrize('text', [
    '其他服务不覆盖人工损坏和私自拆机',
    '服务覆盖人工损坏和私自拆机',
    '服务不覆盖人工损坏',
])
def test_parallel_subject_cannot_change_entity_polarity_or_complete_object(text):
    original = '服务覆盖正常使用的故障,不覆盖人工损坏和私自拆机。'
    with pytest.raises(GenerationError, match='未核验'):
        GroundedGenerator.validate(_claim(text, '不覆盖人工损坏和私自拆机'), {1: original})


def test_preceding_negative_predicate_cannot_be_inherited_as_subject():
    with pytest.raises(GenerationError, match='未核验'):
        GroundedGenerator.validate(_claim('服务不覆盖制造缺陷', '覆盖制造缺陷'),
                                   {1: '服务不覆盖人工损坏,覆盖制造缺陷。'})


def test_invalid_generated_relation_uses_real_knowledge_store_extract_fallback(tmp_path):
    from backend.knowledge_store import KnowledgeStore

    class Client:
        model = 'gpt-6-luna'
        audit = {}

        def generate(self, instructions, context, schema, **kwargs):
            evidence = context['evidence'][0]
            return {'abstain': False, 'claims': [{'text': '华东收入200万元，华西收入100万元。',
                                                'support': [{'citation_id': evidence['citation_id'],
                                                             'quote': evidence['text']}]}]}

    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(Client()))
    store.ingest('华东收入100万元，华西收入200万元。'.encode(), document_id='revenue',
                 title='地区收入', modality='txt', filename='revenue.txt')
    result = store.answer('华东收入')
    assert result['answer_mode'] == 'extractive_fallback'
    assert '华东收入100万元' in result['answer']
    assert '华东收入200万元' not in result['answer']
    assert result['trace'][-1]['fallback'] == 'attributed_extracts'


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
