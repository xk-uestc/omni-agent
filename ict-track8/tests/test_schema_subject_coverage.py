"""Coverage proof for native selected subjects, not a topic-word whitelist."""
from contextlib import closing
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine


def source(tmp_path, table='农田', metric='产量', extra=False):
    path = tmp_path / 'subjects.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f'CREATE TABLE "{table}"("编号" INTEGER PRIMARY KEY,"{metric}" REAL,"收获日期" TEXT)')
        connection.execute(f'INSERT INTO "{table}" VALUES(1,20,\'2025-03-01\')')
        connection.execute(f'INSERT INTO "{table}" VALUES(2,80,\'2024-12-31\')')
        if extra:
            connection.execute('CREATE TABLE "农业"("编号" INTEGER PRIMARY KEY,"备注" TEXT)')
        connection.commit()
    return Nl2SqlEngine(path, metric_catalog_path=tmp_path/'absent-catalog.json')


@pytest.mark.parametrize('table,metric', [('农田', '产量'), ('账簿', '金额'), ('工厂', '产能')])
def test_real_selected_chinese_subject_is_backed_by_schema(tmp_path, table, metric):
    result = source(tmp_path, table, metric).answer(f'2025年{table}的{metric}合计')
    assert result.status == 'ok'
    assert list(result.rows[0].values()) == [20]
    assert table in result.plan['coverage']['consumed']


@pytest.mark.parametrize('modifier', ['神秘', '未知账本', '错误来源'])
def test_unknown_qualifiers_survive_known_subject_consumption(tmp_path, modifier):
    result = source(tmp_path).answer(f'2025年{modifier}农田的产量合计')
    assert result.status == 'clarification'
    assert result.clarification_code == 'unresolved_terms'
    assert result.sql is None


def test_existing_but_unused_table_is_not_consumed_as_a_subject(tmp_path):
    result = source(tmp_path, extra=True).answer('2025年农业农田的产量合计')
    assert result.status == 'clarification'
    assert '农业' not in result.plan['coverage']['consumed']
    assert result.sql is None


def test_table_substring_inside_native_metric_is_not_separate_subject_evidence(tmp_path):
    result = source(tmp_path, metric='农田产量').answer('2025年农田产量合计')
    assert result.status == 'ok'
    assert '农田产量' in result.plan['coverage']['consumed']
    assert '农田' not in result.plan['coverage']['consumed']
    assert list(result.rows[0].values()) == [20]


def test_subject_prefix_does_not_erase_unknown_suffix(tmp_path):
    result = source(tmp_path).answer('2025年农田秘密区的产量合计')
    assert result.status == 'clarification'
    assert result.sql is None


def test_english_subject_requires_an_identifier_boundary(tmp_path):
    engine = source(tmp_path, table='Ledger', metric='amount')
    actual = engine.answer('2025年Ledger的amount合计')
    assert actual.status == 'ok'
    assert 'ledger' in actual.plan['coverage']['consumed']
    embedded = engine.answer('2025年SuperLedger的amount合计')
    assert 'ledger' not in embedded.plan['coverage']['consumed']


def test_negated_schema_subject_is_not_a_proven_positive_subject(tmp_path):
    result = source(tmp_path).answer('2025年非农田的产量合计')
    assert result.status == 'clarification'
    assert result.sql is None
    assert '农田' not in result.plan['coverage']['consumed']
