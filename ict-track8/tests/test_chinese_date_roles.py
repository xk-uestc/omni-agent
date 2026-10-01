"""Date-role transfer checks outside the frozen subscription audit schema."""
from contextlib import closing
import sqlite3

import pytest

from backend.nl2sql.date_semantics import is_date_column
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.model_contract import ModelPlanError, ModelPlanValidator
from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker


def ledger(tmp_path, fields):
    path = tmp_path / 'ledger.sqlite'
    definitions = ','.join(f'"{name}" {kind}' for name, kind in fields)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f'CREATE TABLE "资金流水"("流水号" INTEGER PRIMARY KEY,"金额" REAL,{definitions})')
        values = [(1, 120, *(['2024-12-01', '2025-02-01'][:len(fields)])),
                  (2, 70, *(['2025-03-01', '2026-01-01'][:len(fields)]))]
        connection.executemany('INSERT INTO "资金流水" VALUES(' + ','.join('?' for _ in values[0]) + ')', values)
        connection.commit()
        tables = SchemaIntrospector().introspect(connection)
    return Nl2SqlEngine(path, metric_catalog_path=tmp_path/'absent-catalog.json'), tables


@pytest.mark.parametrize('field', ['入账日期', '核验日期', '登记时间', '入帳日期'])
def test_chinese_iso_date_names_filter_the_actual_column(tmp_path, field):
    engine, _ = ledger(tmp_path, [(field, 'TEXT')])
    result = engine.answer('2025年金额合计')
    assert result.status == 'ok'
    assert list(result.rows[0].values()) == [70]
    assert result.plan['filters'][0]['column'] == field
    assert result.parameters[:2] == ('2025-01-01', '2026-01-01')


def test_declared_temporal_type_is_a_date_role_without_english_name(tmp_path):
    engine, _ = ledger(tmp_path, [('生效日', 'DATE')])
    result = engine.answer('2025年金额合计')
    assert result.status == 'ok'
    assert list(result.rows[0].values()) == [70]


@pytest.mark.parametrize('first,second', [('入账日期', '到期日期'), ('BookingDate', 'DueDate')])
def test_equal_distance_date_roles_require_an_explicit_choice(tmp_path, first, second):
    engine, _ = ledger(tmp_path, [(first, 'TEXT'), (second, 'TEXT')])
    result = engine.answer('2025年金额合计')
    assert result.status == 'clarification'
    assert result.clarification_code == 'ambiguous_date_column'
    assert result.sql is None
    assert {item['value'] for item in result.clarification_options} == {f'资金流水.{first}', f'资金流水.{second}'}


@pytest.mark.parametrize('field,expected', [('入账日期', 70), ('到期日期', 120)])
def test_explicit_date_roles_do_not_bind_to_another_column(tmp_path, field, expected):
    engine, _ = ledger(tmp_path, [('入账日期', 'TEXT'), ('到期日期', 'TEXT')])
    result = engine.answer(f'2025年{field}的金额合计')
    assert result.status == 'ok'
    assert list(result.rows[0].values()) == [expected]
    assert next(item for item in result.plan['filters'] if item['operator'] == 'RANGE')['column'] == field


def test_multiple_date_roles_do_not_block_an_untimed_sum(tmp_path):
    engine, _ = ledger(tmp_path, [('入账日期', 'TEXT'), ('到期日期', 'TEXT')])
    result = engine.answer('金额合计')
    assert result.status == 'ok'
    assert list(result.rows[0].values()) == [190]


def test_generic_monthly_grain_does_not_choose_one_of_two_roles(tmp_path):
    engine, _ = ledger(tmp_path, [('入账日期', 'TEXT'), ('到期日期', 'TEXT')])
    result = engine.answer('2025年按月统计金额')
    assert result.status == 'clarification'
    assert result.sql is None


def test_chinese_single_date_supports_monthly_schema_linking(tmp_path):
    engine, tables = ledger(tmp_path, [('入账日期', 'TEXT')])
    links = SchemaLinker().link('2025年按月统计金额', tables)
    assert any(link.column == '入账日期' and link.role == 'dimension' for link in links)
    result = engine.answer('2025年按月统计金额')
    assert result.status == 'ok'
    assert result.plan['dimension_transforms']['入账日期'] == 'month'
    assert result.rows[0]['月份'] == '2025-03'


def test_model_contract_accepts_chinese_date_filter_and_month_transform(tmp_path):
    _, tables = ledger(tmp_path, [('入账日期', 'TEXT')])
    payload = {
        'version': 1, 'table': '资金流水',
        'metric': {'table': '资金流水', 'column': '金额', 'function': 'SUM'},
        'dimensions': [{'table': '资金流水', 'column': '入账日期', 'transform': 'month'}],
        'filters': [{'table': '资金流水', 'column': '入账日期', 'operator': 'RANGE', 'value': ['2025-01-01', '2026-01-01']}],
        'confidence': 0.9,
    }
    plan = ModelPlanValidator().validate(payload, tables, question='2025年按入账日期月份统计金额')
    assert plan.dimension_transforms['入账日期'] == 'month'
    assert plan.filters[0].column == '入账日期'
    payload['dimensions'][0]['column'] = '流水号'
    with pytest.raises(ModelPlanError, match='日期列'):
        ModelPlanValidator().validate(payload, tables, question='非法日期维度')


@pytest.mark.parametrize('field', ['时间间隔', '日期备注', '日期编号', 'date_format', '金额'])
def test_date_related_text_does_not_establish_a_date_role(field):
    assert not is_date_column(field)


def test_unknown_topic_guard_is_preserved_after_date_recognition(tmp_path):
    engine, _ = ledger(tmp_path, [('入账日期', 'TEXT')])
    result = engine.answer('2025年不存在主题的金额合计')
    assert result.status == 'clarification'
    assert result.sql is None


def test_explicit_date_on_one_to_many_side_is_never_replaced_by_parent_date(tmp_path):
    path = tmp_path / 'payment-roles.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript('''
            CREATE TABLE "账务"("编号" INTEGER PRIMARY KEY,"金额" REAL,"入账日期" TEXT);
            CREATE TABLE "支付记录"("支付编号" INTEGER PRIMARY KEY,"账务编号" INTEGER REFERENCES "账务"("编号"),"付款时间" TEXT);
            INSERT INTO "账务" VALUES(1,100,'2024-01-01');
            INSERT INTO "支付记录" VALUES(1,1,'2025-02-01'),(2,1,'2025-03-01');
        ''')
        connection.commit()
    engine = Nl2SqlEngine(path, metric_catalog_path=tmp_path/'absent-catalog.json')
    result = engine.answer('2025年按付款时间每月统计金额')
    assert result.plan['dimension_tables'].get('付款时间') == '支付记录'
    assert '入账日期' not in result.plan['dimensions']
    assert any(item['column'] == '付款时间' for item in result.plan['filters'])
    assert result.status == 'clarification'  # allocation across child payments is not inferred
    assert result.sql is None


@pytest.mark.parametrize('scale', [1, 1000])
def test_numeric_chinese_timestamp_filters_work_but_calendar_grouping_refuses(tmp_path, scale):
    from datetime import datetime, timezone
    path = tmp_path / 'epoch-calendar.sqlite'
    epochs = [int(datetime(year, 3, 1, tzinfo=timezone.utc).timestamp()) * scale for year in (2024, 2025)]
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('CREATE TABLE "资金流水"("编号" INTEGER PRIMARY KEY,"金额" REAL,"登记时间" INTEGER)')
        connection.executemany('INSERT INTO "资金流水" VALUES(?,?,?)', [(1,120,epochs[0]),(2,70,epochs[1])])
        connection.commit()
    engine = Nl2SqlEngine(path, metric_catalog_path=tmp_path/'absent-catalog.json')
    filtered = engine.answer('2025年金额合计')
    assert filtered.status == 'ok'
    assert list(filtered.rows[0].values()) == [70]
    for question in ('2025年按月统计金额', '按年统计金额'):
        grouped = engine.answer(question)
        assert grouped.status == 'clarification'
        assert grouped.clarification_code == 'unsupported_time_storage'
        assert grouped.sql is None


@pytest.mark.parametrize('field,expected', [('生效日', 70), ('截止日', 120)])
def test_two_declared_date_roles_without_suffix_honor_explicit_field(tmp_path, field, expected):
    engine, _ = ledger(tmp_path, [('生效日', 'DATE'), ('截止日', 'DATE')])
    result = engine.answer(f'2025年{field}的金额合计')
    assert result.status == 'ok'
    assert list(result.rows[0].values()) == [expected]
    assert result.plan['dimensions'] == []
    assert next(item for item in result.plan['filters'] if item['operator'] == 'RANGE')['column'] == field
    ambiguous = engine.answer('2025年金额合计')
    assert ambiguous.clarification_code == 'ambiguous_date_column'
    assert ambiguous.sql is None


def test_declared_date_explicit_month_role_has_calendar_dimensions(tmp_path):
    engine, _ = ledger(tmp_path, [('生效日', 'DATE'), ('截止日', 'DATE')])
    result = engine.answer('2025年按生效日每月统计金额')
    assert result.status == 'ok'
    assert result.plan['dimension_transforms'] == {'生效日': 'month'}
    assert result.rows[0]['月份'] == '2025-03'
    assert list(result.rows[0].values()) == ['2025-03', 70]


def test_declared_date_explicit_role_cannot_bypass_unknown_topic_guard(tmp_path):
    engine, _ = ledger(tmp_path, [('生效日', 'DATE'), ('截止日', 'DATE')])
    result = engine.answer('2025年神秘来源生效日的金额合计')
    assert result.status == 'clarification'
    assert result.clarification_code == 'unresolved_terms'
    assert result.sql is None


def test_declared_date_role_on_child_does_not_rebind_to_parent(tmp_path):
    path = tmp_path / 'typed-child-role.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript('''
            CREATE TABLE "账务"("编号" INTEGER PRIMARY KEY,"金额" REAL,"生效日" DATE);
            CREATE TABLE "核验记录"("核验编号" INTEGER PRIMARY KEY,"账务编号" INTEGER REFERENCES "账务"("编号"),"核验日" DATE);
            INSERT INTO "账务" VALUES(1,100,'2024-01-01');
            INSERT INTO "核验记录" VALUES(1,1,'2025-02-01'),(2,1,'2025-03-01');
        ''')
        connection.commit()
    engine = Nl2SqlEngine(path, metric_catalog_path=tmp_path/'absent-catalog.json')
    result = engine.answer('2025年按核验日每月统计金额')
    assert result.plan['dimension_tables'].get('核验日') == '核验记录'
    assert '生效日' not in result.plan['dimensions']
    assert any(item['column'] == '核验日' for item in result.plan['filters'])
    assert result.status == 'clarification'
    assert result.clarification_code == 'fan_out_risk'
    assert result.sql is None


@pytest.mark.parametrize('scale', [1, 1000])
def test_declared_date_numeric_storage_preserves_filter_but_rejects_grouping(tmp_path, scale):
    from datetime import datetime, timezone
    path = tmp_path / 'typed-date-epoch.sqlite'
    epochs = [int(datetime(year, 3, 1, tzinfo=timezone.utc).timestamp()) * scale for year in (2024, 2025)]
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('CREATE TABLE "资金流水"("编号" INTEGER PRIMARY KEY,"金额" REAL,"生效日" DATE,"截止日" DATE)')
        connection.executemany('INSERT INTO "资金流水" VALUES(?,?,?,?)', [(1,120,epochs[0],epochs[1]),(2,70,epochs[1],epochs[0])])
        connection.commit()
    engine = Nl2SqlEngine(path, metric_catalog_path=tmp_path/'absent-catalog.json')
    filtered = engine.answer('2025年生效日的金额合计')
    assert filtered.status == 'ok'
    assert list(filtered.rows[0].values()) == [70]
    grouped = engine.answer('2025年按生效日每月统计金额')
    assert grouped.clarification_code == 'unsupported_time_storage'
    assert grouped.sql is None
