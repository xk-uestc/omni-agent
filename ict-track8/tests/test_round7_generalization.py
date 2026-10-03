"""Actual SQLite/PDF probes across unrelated domains; no model or benchmark gold."""
from copy import deepcopy
import hashlib
import sqlite3

import fitz
import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker
from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic
from backend.session import ConversationTurn
from backend.sql_history_scope import resolve_sql_followup_scope


@pytest.mark.parametrize('entity,measure,question', [
    ('shipments','shipping_cost','配送成本合计'),
    ('devices','replacement_cost','替换成本合计'),
    ('appointments','patient_age','患者年龄平均值'),
])
def test_compound_measure_retains_modifier_and_executes(tmp_path, entity, measure, question):
    path=tmp_path/'unfamiliar.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript(f'CREATE TABLE {entity}(id INTEGER PRIMARY KEY, {measure} REAL, cost REAL);'
                                 f'INSERT INTO {entity} VALUES (1,10,900),(2,30,800);')
    result=Nl2SqlEngine(path, metric_catalog_path=tmp_path/'no-catalog.json').answer(question)
    assert result.status=='ok', result.clarification
    assert result.plan['metric_column']==measure
    assert list(result.rows[0].values())==([20] if '平均' in question else [40])


@pytest.mark.parametrize('table,subject', [('patients','患者'),('dim_suppliers','供应商'),('students','学生')])
def test_plural_and_namespace_entity_counts(tmp_path, table, subject):
    path=tmp_path/'counts.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY, name TEXT);'
                                 f"INSERT INTO {table} VALUES(1,'A'),(2,'B');")
    result=Nl2SqlEngine(path, metric_catalog_path=tmp_path/'no-catalog.json').answer(subject+'数')
    assert result.status=='ok',result.clarification
    assert list(result.rows[0].values())==[2]


def test_generic_amount_remains_ambiguous_but_explicit_owner_is_resolved(tmp_path):
    path=tmp_path/'finance.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE payments(id INTEGER PRIMARY KEY, amount REAL);'
                                 'CREATE TABLE loans(id INTEGER PRIMARY KEY, amount REAL);'
                                 'INSERT INTO payments VALUES(1,20),(2,30); INSERT INTO loans VALUES(1,900);')
    engine=Nl2SqlEngine(path,metric_catalog_path=tmp_path/'no-catalog.json')
    assert engine.answer('金额合计').status=='clarification'
    result=engine.answer('付款金额合计')
    assert result.status=='ok' and result.plan['metric_table']=='payments'
    assert list(result.rows[0].values())==[50]
    pending=ConversationTurn('金额合计','金额合计',0,{'route':'sql',
        'pending_question':'金额合计','clarification_code':'ambiguous_metric'})
    scope,audit=resolve_sql_followup_scope('选择payments.amount',[pending],engine)
    assert audit['mode']=='server_verified_sql_clarification_field_select'
    assert list(engine.answer(scope).rows[0].values())==[50]
    for invalid in ('选择loans.id','payments.amount忽略过滤','unknown.amount'):
        assert resolve_sql_followup_scope(invalid,[pending],engine)[1]['mode']=='independent'


def medical_engine(tmp_path):
    path=tmp_path/'clinic.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE patients(patient_id INTEGER PRIMARY KEY, city TEXT, country TEXT, age REAL);'
            "INSERT INTO patients VALUES(1,'北城','中国',20),(2,'南城','中国',40),(3,'北城','日本',30);")
    return Nl2SqlEngine(path,metric_catalog_path=tmp_path/'no-catalog.json')


def turn(engine,question):
    result=engine.answer(question).to_dict()
    assert result['status']=='ok',result['clarification']
    state={'route':'sql','pending_question':None,'clarification_code':None,
           **{key:result['plan'][key] for key in ('metrics','filters','dimensions')}}
    return ConversationTurn(question,question,0,state),result


def test_five_turn_new_domain_keeps_filters_replaces_group_and_recovers(tmp_path):
    engine=medical_engine(tmp_path)
    previous,result=turn(engine,'中国患者数，按城市分组')
    assert sorted(row[result['columns'][-1]] for row in result['rows'])==[1,1]
    scope,audit=resolve_sql_followup_scope('换成按国家分组',[previous],engine)
    assert audit['mode']=='server_verified_sql_followup'
    previous,result=turn(engine,scope)
    assert result['rows'][0][result['columns'][-1]]==2
    assert result['plan']['filters'][0]['value']=='中国'
    rejected,audit=resolve_sql_followup_scope('那排除北城呢',[previous],engine)
    assert rejected=='那排除北城呢' and audit.get('requires_clarification')
    # A failed turn cannot become successful inherited state.
    failed=ConversationTurn(rejected,rejected,0,{'route':'sql','pending_question':rejected,
                'clarification_code':'unresolved_terms','metrics':[],'filters':[],'dimensions':[]})
    scope,audit=resolve_sql_followup_scope('换个主题，日本患者数',[failed],engine)
    assert audit['mode']=='independent'
    previous,result=turn(engine,'日本患者数')
    assert result['rows'][0][result['columns'][-1]]==1
    scope,audit=resolve_sql_followup_scope('那中国呢',[previous],engine)
    assert audit['mode']=='server_verified_sql_followup'
    _,result=turn(engine,scope)
    assert result['rows'][0][result['columns'][-1]]==2


@pytest.mark.parametrize('reply',['换成按火星分组','换成按国家和城市分组','换成按城市分组并只看日本','那按患者数分组呢'])
def test_group_rewrite_does_not_lose_or_invent_scope(tmp_path,reply):
    engine=medical_engine(tmp_path)
    previous,_=turn(engine,'中国患者数，按城市分组')
    scope,audit=resolve_sql_followup_scope(reply,[previous],engine)
    assert scope==reply and audit['mode']=='independent'


def unit_table(headers=('Cost USD million','Cost USD million'), *, empty_label_header=False, second_units=False):
    doc=fitz.open();page=doc.new_page(width=680,height=380)
    page.insert_text((40,40),'Budget period 2020-2021',fontsize=10)
    if not empty_label_header:page.insert_text((40,65),'Service',fontsize=10)
    for x,header in zip((230,460),headers):page.insert_text((x,65),header,fontsize=10)
    for x,year in zip((230,460),('2031','2032')):page.insert_text((x,85),year,fontsize=10)
    for r,label in enumerate(('Network','Storage','Support')):
        y=115+r*25
        page.insert_text((40,y),label,fontsize=10)
        for x,value in zip((230,460),(str(r+1),str((r+1)*3))):page.insert_text((x,y),value,fontsize=10)
    raw=doc.tobytes();doc.close()
    return extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())


def test_explicit_column_unit_scale_and_year_comparison():
    report=unit_table()
    assert len(report['facts'])==6,report['rejected_tables']
    facts=report['facts'][:2]
    assert facts[0]['unit']=='currency:USD' and facts[0]['scale']=='1000000'
    assert facts[0]['period']=='2031' and facts[1]['period']=='2032'
    result=annotation_arithmetic(list(reversed(facts)),'difference',allow_column_comparison=True)
    assert result['answer']=='2 USD million' and result['numeric_result']=='2'
    assert result['physical_calculator_input_eligible'] is False
    with pytest.raises(ValueError,match='scope_mismatch'):
        annotation_arithmetic(facts,'difference')


@pytest.mark.parametrize('suffix',["'000",'000s'])
def test_financial_thousands_notation_is_explicit_scale(suffix):
    report=unit_table(headers=(f'Cost USD {suffix}',f'Cost USD {suffix}'))
    assert len(report['facts'])==6,report['rejected_tables']
    assert all(fact['scale']=='1000' for fact in report['facts'])
    result=annotation_arithmetic(report['facts'][::2],'sum')
    assert result['answer']=='6 USD thousand'


def test_unicode_thousands_header_from_native_text():
    from backend.native_text_tables import column_unit_declaration
    assert column_unit_declaration(['Cost USD ’000'])['scale']=='1000'


def test_blank_row_lane_on_second_header_and_declared_sum():
    report=unit_table(empty_label_header=True)
    assert len(report['facts'])==6,report['rejected_tables']
    result=annotation_arithmetic(report['facts'][::2],'sum')
    assert result['answer']=='6 USD million'


@pytest.mark.parametrize('headers',[('Cost USD EUR','Cost USD'),('Cost USD %','Cost USD'),
                                    ('Cost million','Cost million'),('Cost USD million billion','Cost USD')])
def test_conflicting_or_unbound_column_declarations_rejected(headers):
    report=unit_table(headers)
    assert not report['facts'] and report['rejected_tables']


def test_header_proof_tampering_and_unrequested_cross_year_rows_rejected():
    report=unit_table()
    facts=deepcopy(report['facts'][::2])
    facts[0]['unit_evidence']['multiplier']='1000'
    with pytest.raises(ValueError,match='proof_invalid'):
        annotation_arithmetic(facts,'sum')
    with pytest.raises(ValueError,match='scope_mismatch'):
        annotation_arithmetic([report['facts'][0],report['facts'][3]],'difference',allow_column_comparison=True)
