"""Unknown reimbursement schema preserves local functions, nulls and entities."""
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine


@pytest.fixture
def engine(tmp_path):
    database=tmp_path/'reimbursements.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE Reimbursements(
                ClaimId INTEGER PRIMARY KEY, CostAmount REAL NOT NULL,
                ClaimDate DATE, Region TEXT, ReferenceNo INTEGER, BatchCode INTEGER NOT NULL);
            INSERT INTO Reimbursements VALUES
                (10,100,'2025-01-01','北区',NULL,7),
                (30,200,'2025-02-01','南区',99,7),
                (70,50,'2025-03-01','北区',99,8);
        ''')
    return Nl2SqlEngine(database,metric_catalog_path=tmp_path/'no-catalog.json')


@pytest.mark.parametrize('cue,function,expected', [
    ('记录计数','COUNT',3), ('记录数','COUNT',3), ('计数','COUNT',3),
    ('去重计数','COUNT_DISTINCT',2),
])
def test_named_nonnull_field_count_has_actual_selected_semantics(engine,cue,function,expected):
    result=engine.answer(f'2025年Reimbursements的BatchCode{cue}').to_dict()
    assert result['status']=='ok'
    assert result['plan']['metric_function']==function
    assert list(result['rows'][0].values())==[expected]
    if function=='COUNT':
        assert 'COUNT(*)' in result['sql']
    else:
        assert 'COUNT(DISTINCT' in result['sql']


@pytest.mark.parametrize('question', [
    '2025年Reimbursements的CostAmount合计、ClaimId记录计数',
    '2025年Reimbursements的ClaimId记录计数、CostAmount合计',
])
def test_neighboring_sum_and_count_do_not_share_functions(engine,question):
    result=engine.answer(question).to_dict()
    assert result['status']=='ok',result.get('clarification')
    metrics={metric['column']:metric for metric in result['plan']['metrics']}
    assert metrics['CostAmount']['function']=='SUM' and metrics['ClaimId']['function']=='COUNT'
    assert result['rows'][0][metrics['CostAmount']['label']]==350
    assert result['rows'][0][metrics['ClaimId']['label']]==3


def test_nullable_named_count_never_silently_promises_count_column(engine):
    result=engine.answer('2025年Reimbursements的ReferenceNo计数').to_dict()
    assert result['status']=='clarification'
    assert result['clarification_code']=='ambiguous_count_semantics'
    assert not result['rows'] and result['sql'] is None


def test_nullable_distinct_count_preserves_null_and_duplicate_behavior(engine):
    result=engine.answer('2025年Reimbursements的ReferenceNo去重计数').to_dict()
    assert result['status']=='ok' and list(result['rows'][0].values())==[1]
    assert result['plan']['metric_function']=='COUNT_DISTINCT'


def test_find_presentation_is_consumed_only_when_requested_rank_executes(engine):
    result=engine.answer('2025年Reimbursements按Region统计CostAmount合计并找出最高的Region').to_dict()
    assert result['status']=='ok'
    assert result['plan']['top_n']==1 and result['rows'][0]['Region']=='南区'
    assert 'DENSE_RANK' in result['sql'] and result['plan']['order_desc'] is True


@pytest.mark.parametrize('question', [
    '2025年Reimbursements北区和火星区的CostAmount合计',
    '2025年Reimbursements的CostAmount合计并找出火星区',
    '2025年Reimbursements的CostAmount最大值和最小值',
    '2025年Reimbursements的CostAmount合计和计数',
    '2025年Reimbursements的CostAmount对应火星区的合计',
])
def test_unknown_entities_and_unimplemented_function_combinations_still_stop(engine,question):
    result=engine.answer(question).to_dict()
    assert result['status']=='clarification' and result['sql'] is None and not result['rows']


def test_negation_and_fresh_topic_are_not_previous_filter_noise(engine):
    result=engine.answer('换个主题，2025年Reimbursements不包括北区的CostAmount合计').to_dict()
    assert result['status']=='ok' and list(result['rows'][0].values())==[200]
    assert any(f['operator']=='!=' and f['value']=='北区' for f in result['plan']['filters'])


def test_date_correspondence_and_tie_words_require_actual_slots(engine):
    result=engine.answer('按Region统计2025年Reimbursements的ClaimDate对应CostAmount合计，取最高前三名含并列').to_dict()
    assert result['status']=='ok'
    assert result['plan']['top_n']==3 and 'DENSE_RANK' in result['sql']
    assert any(f['column']=='ClaimDate' and f['operator']=='RANGE' for f in result['plan']['filters'])
