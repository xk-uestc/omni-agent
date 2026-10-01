"""Aggregate cue coverage must preserve SQL semantics, not discard unknown words."""
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / 'claims.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript('CREATE TABLE Claims(ClaimId INTEGER PRIMARY KEY, ApprovedAmount REAL);'
                                'INSERT INTO Claims VALUES(1,150.5),(2,800),(3,100);')
    return Nl2SqlEngine(db, metric_catalog_path=tmp_path/'no-domain-catalog.json')


@pytest.mark.parametrize('cue,function,expected', [
    ('最小值','MIN',100), ('最低值','MIN',100), ('单笔最低','MIN',100),
    ('最大值','MAX',800), ('最高值','MAX',800), ('单次最高','MAX',800),
    ('总和','SUM',1050.5), ('总计','SUM',1050.5), ('均值','AVG',1050.5/3),
])
def test_selected_aggregate_consumes_its_own_cue(engine, cue, function, expected):
    answer = engine.answer(f'Claims ApprovedAmount{cue}').to_dict()
    assert answer['status'] == 'ok'
    assert answer['plan']['metric_function'] == function
    assert list(answer['rows'][0].values())[0] == pytest.approx(expected)


@pytest.mark.parametrize('suffix', ['最大值和最小值','合计和最大值','平均值和最大值','最小值的波动幅度'])
def test_conflicting_aggregates_and_unknown_modifiers_still_clarify(engine, suffix):
    answer = engine.answer(f'Claims ApprovedAmount{suffix}').to_dict()
    assert answer['status'] == 'clarification'
    assert not answer['rows']
