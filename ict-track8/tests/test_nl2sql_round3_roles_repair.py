"""New synthetic schema: source roles, opaque handles and bounded repair."""
from copy import deepcopy
import json
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.model_contract import ModelPlanError, ModelPlanValidator
from backend.nl2sql.metric_compiler import MetricCompiler
from backend.nl2sql.planner import SingleTablePlanner
from backend.nl2sql.plan_structure import canonicalize, diagnose
from backend.nl2sql.question_roles import association_scope
from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker
from backend.nl2sql.security import execute_read_only


@pytest.fixture
def database(tmp_path):
    path = tmp_path/'new-schema.sqlite'
    with sqlite3.connect(path) as c:
        c.executescript('''
            CREATE TABLE Vendors(vendor_id INTEGER PRIMARY KEY, country TEXT NOT NULL);
            CREATE TABLE "Inventory Lots"(lot_id INTEGER PRIMARY KEY, vendor_id INTEGER NOT NULL,
                qty_total REAL NOT NULL, unit_price REAL NOT NULL, active TEXT NOT NULL,
                FOREIGN KEY(vendor_id) REFERENCES Vendors(vendor_id));
            CREATE TABLE Returns(return_id INTEGER PRIMARY KEY, unit_price REAL NOT NULL);
            INSERT INTO Vendors VALUES(1,'East'),(2,'West');
            INSERT INTO "Inventory Lots" VALUES(1,1,3,10,'0'),(2,1,7,20,'0'),(3,2,10,100,'1');
            INSERT INTO Returns VALUES(1,999);
        ''')
    return path


def proposal(**changes):
    result = {'version':2,'metrics':[{'id':'stock','table':'Inventory Lots','column':'qty_total',
        'function':'SUM','label':'Stock','unit':'unknown','currency':None,'missing':'null','filters':[]}],
        'derived_metrics':[],'dimensions':[],'filters':[],'analysis_mode':'aggregate','having':None,
        'comparison':None,'top_n':None,'order_desc':True,'order_metric':None,'output_metrics':[],
        'limit':100,'confidence':.95,'rewritten_question':'Original scope'}
    result.update(changes)
    return result


def engine(database, plan=None, **kwargs):
    return Nl2SqlEngine(database, model_plan_provider=(lambda q,t:deepcopy(plan)) if plan else None,
                       metric_catalog_path=database.parent/'absent.json', **kwargs)


def test_fk_description_is_not_a_metric_and_preserves_group_sum(database):
    plan = proposal(dimensions=[{'table':'Vendors','column':'country','transform':'raw','label':'country'}])
    question = '按 Vendors.country 分组，统计 Inventory Lots.qty_total 合计；通过 Inventory Lots.vendor_id 与 Vendors.vendor_id 关联。'
    result = engine(database,plan,model_fallback=False).answer(question)
    assert result.status=='ok'
    assert {(r['country'],r['总qty_total']) for r in result.rows}=={('East',10),('West',10)}
    assert all(x['column']!='vendor_id' for x in result.plan['links'] if x['role']=='metric')
    assert result.plan['planner_source']=='model_validated'


def test_middle_join_verb_accepts_exact_verified_right_hand_field(database):
    plan=proposal(dimensions=[{'table':'Vendors','column':'country','transform':'raw','label':'country'}])
    result=engine(database,plan,model_fallback=False).answer(
        '按 Vendors.country 分组，统计 Inventory Lots.qty_total 合计；通过 Inventory Lots.vendor_id 关联 Vendors.vendor_id。')
    assert result.status=='ok' and result.plan['planner_source']=='model_validated'
    assert {(r['country'],r['总qty_total']) for r in result.rows}=={('East',10),('West',10)}


@pytest.mark.parametrize('suffix', ['Vendors.country', 'Returns.vendor_id', 'Vendors.vendor_id只看East',
                                    'Vendors.vendor_id并统计销售额'])
def test_middle_join_verb_preserves_wrong_field_and_tail_guards(database,suffix):
    result=engine(database,proposal()).answer(
        f'Inventory Lots.qty_total 合计；通过 Inventory Lots.vendor_id 关联 {suffix}。')
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='unverified_join_condition'


@pytest.mark.parametrize('relation', ['Returns 与 Vendors 通过 vendor_id 关联',
                                     '通过 Returns 与 Vendors 的 vendor_id 关联',
                                     'Ghost 与 Vendors 通过 vendor_id 关联',
                                     'Returns 与 Vendors，通过 vendor_id 关联',
                                     'Returns 和 Vendors，经由 vendor_id 连接'])
def test_local_join_table_pair_cannot_borrow_a_source_from_the_metric_clause(database,relation):
    result=engine(database,proposal()).answer(f'Inventory Lots.qty_total 合计；{relation}。')
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='unverified_join_condition'


def test_homonymous_group_column_cannot_change_the_explicit_source(database):
    with sqlite3.connect(database) as c:
        c.executescript('''ALTER TABLE "Inventory Lots" ADD country TEXT;
                          UPDATE "Inventory Lots" SET country='Warehouse';''')
    plan=proposal(dimensions=[{'table':'Inventory Lots','column':'country','label':'country'}])
    result=engine(database,plan).answer('按 Vendors.country 分组，统计 Inventory Lots.qty_total 合计')
    assert result.status=='ok' and result.plan['planner_source']=='rules_fallback'
    assert result.plan['planner_audit']['reason_code']=='missing_group_dimension'
    assert {(r['country'],r['总qty_total']) for r in result.rows}=={('East',10),('West',10)}
    assert result.plan['dimension_tables']=={'country':'Vendors'}


@pytest.mark.parametrize('requested,actual', [('月','year'), ('月','raw'), ('年','month')])
def test_model_cannot_change_explicit_date_group_grain(database,requested,actual):
    with sqlite3.connect(database) as c:
        c.executescript('''ALTER TABLE "Inventory Lots" ADD booked_at DATE;
            UPDATE "Inventory Lots" SET booked_at=CASE lot_id WHEN 1 THEN '2020-02-01' WHEN 2 THEN '2020-03-01' ELSE '2021-01-01' END;''')
    plan=proposal(dimensions=[{'table':'Inventory Lots','column':'booked_at','transform':actual,'label':'period'}])
    provider=RepairProvider(plan,plan)
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer(
        f'按{requested}统计 Inventory Lots.qty_total 合计，日期使用 Inventory Lots.booked_at')
    assert result.plan['planner_source']=='rules_fallback' and provider.repairs==0
    assert result.plan['planner_audit']['reason_code']=='time_grain_mismatch'
    assert result.plan['dimension_transforms']=={'booked_at':'month' if requested=='月' else 'year'}


@pytest.mark.parametrize('noun,grain', [('月份','month'), ('月度','month'), ('年份','year'), ('年度','year')])
def test_explicit_date_field_grain_noun_is_accepted_without_global_trend_cues(database,noun,grain):
    with sqlite3.connect(database) as c:
        c.executescript('''ALTER TABLE "Inventory Lots" ADD booked_at DATE;
            UPDATE "Inventory Lots" SET booked_at=CASE lot_id WHEN 1 THEN '2020-02-01' WHEN 2 THEN '2020-03-01' ELSE '2021-01-01' END;''')
    plan=proposal(dimensions=[{'table':'Inventory Lots','column':'booked_at','transform':grain,'label':'period'}])
    question=f'按 Inventory Lots.booked_at 的{noun}分组，统计 Inventory Lots.qty_total 合计'
    result=engine(database,plan,model_fallback=False).answer(question)
    assert result.status=='ok' and result.plan['planner_source']=='model_validated'
    assert result.plan['dimension_tables']=={'booked_at':'Inventory Lots'}
    assert result.plan['dimension_transforms']=={'booked_at':grain}
    expected={('2020-02',3),('2020-03',7),('2021-01',10)} if grain=='month' else {('2020',10),('2021',10)}
    assert {(r['period'],r['总qty_total']) for r in result.rows}==expected
    for wrong in ({'raw','month','year'}-{grain}):
        plan['dimensions'][0]['transform']=wrong
        rejected=engine(database,plan).answer(question)
        assert rejected.plan['planner_source']=='rules_fallback'
        assert rejected.plan['planner_audit']['reason_code']=='time_grain_mismatch'
        assert rejected.plan['dimension_transforms']=={'booked_at':grain}


def test_conflicting_explicit_grains_on_the_same_date_field_are_not_guessed(database):
    with sqlite3.connect(database) as c:
        c.execute('ALTER TABLE "Inventory Lots" ADD booked_at DATE')
    plan=proposal(dimensions=[{'table':'Inventory Lots','column':'booked_at','transform':'month','label':'period'}])
    result=engine(database,plan).answer(
        '按 Inventory Lots.booked_at 的月份和 Inventory Lots.booked_at 的年份分组，统计 Inventory Lots.qty_total 合计')
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='ambiguous_time_grain'


def test_date_filter_directive_cannot_authorize_an_unrequested_name_group(database):
    with sqlite3.connect(database) as c:
        c.executescript('''CREATE TABLE Invoices(invoice_id INTEGER PRIMARY KEY, sent_on DATE, invoice_name TEXT);
            INSERT INTO Invoices VALUES(1,'2020-02-01','A'),(2,'2020-05-01','B'),(3,'2021-03-01','C');''')
    plan=proposal(dimensions=[{'table':'Invoices','column':'invoice_name','label':'发票'}],
                  filters=[{'table':'Invoices','column':'sent_on','operator':'RANGE','value':['2020-01-01','2021-01-01']}])
    plan['metrics'][0].update(table='Invoices',column='invoice_id',function='COUNT',unit='count',label='发票数')
    provider=RepairProvider(plan,plan)
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer(
        '按 Invoices.sent_on 筛选 2020 年，统计 Invoices 发票记录数，包含 2020-01-01，不包含 2021-01-01。')
    assert result.plan['planner_source']=='rules_fallback'
    assert result.plan['planner_audit']['reason_code']=='extra_dimension'
    assert result.plan['dimensions']==[] and provider.repairs==0
    assert result.sql is None or (result.status=='ok' and len(result.rows)==1 and list(result.rows[0].values())==[2])
    plan['dimensions']=[]
    correct=engine(database,plan,model_fallback=False).answer(
        '按 Invoices.sent_on 筛选 2020 年，统计 Invoices 发票记录数，包含 2020-01-01，不包含 2021-01-01。')
    assert correct.status=='ok' and correct.plan['planner_source']=='model_validated'
    assert correct.plan['dimensions']==[] and list(correct.rows[0].values())==[2]


def test_local_table_owns_unqualified_measure_even_when_other_table_has_same_column(database):
    plan = proposal()
    plan['metrics'][0].update(column='unit_price',function='AVG',label='Average')
    plan['filters']=[{'table':'Inventory Lots','column':'active','operator':'=','value':'0'}]
    result = engine(database,plan,model_fallback=False).answer('Inventory Lots 中 active 为字符串 0 的记录，其 unit_price 平均值是多少？')
    assert result.status=='ok' and list(result.rows[0].values())==[15.0]
    assert 'active' not in [x['column'] for x in result.plan['links'] if x['role']=='metric']


@pytest.mark.parametrize('column',['vendor_id','country'])
def test_wrong_or_incomplete_relationship_is_never_masked_into_success(database,column):
    question=f'Inventory Lots.qty_total 合计；通过 Inventory Lots.vendor_id 与 Vendors.{column} 关联。'
    if column=='vendor_id':
        # The requested edge is retained even when there is no other dimension.
        question='Inventory Lots.qty_total 合计；通过 Inventory Lots.lot_id 与 Vendors.vendor_id 关联。'
    result=engine(database,proposal()).answer(question)
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='unverified_join_condition'


def test_join_key_description_cannot_hide_a_requested_measure(database):
    with sqlite3.connect(database) as c:
        tables=SchemaIntrospector().introspect(c)
    masked, bindings, rejected=association_scope('Inventory Lots 与 Vendors 通过 vendor_id 关联后统计 qty_total 合计',tables)
    assert rejected and not bindings and 'qty_total' in masked


@pytest.mark.parametrize('owner,column', [('Returns','vendor_id'), ('Returns','return_id'),
                                         ('Imaginary','vendor_id'), ('Inventory Lots','missing_id'),
                                         ('"Returns"','"vendor_id"'), ('未知表','vendor_id')])
def test_wrong_explicit_join_owner_never_borrows_a_key_from_another_table(database,owner,column):
    question=f'Inventory Lots.qty_total 合计；通过 {owner}.{column} 与 Vendors.vendor_id 关联。'
    result=engine(database,proposal()).answer(question)
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='unverified_join_condition'


def test_unknown_natural_join_owner_is_not_masked(database):
    result=engine(database,proposal()).answer('Inventory Lots.qty_total 合计；通过 Ghost的vendor_id 与 Vendors.vendor_id 关联。')
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='unverified_join_condition'


@pytest.mark.parametrize('suffix', ['只看East', '并统计销售额', '后统计库存总量', '后统计 qty_total 合计'])
def test_join_description_cannot_silently_remove_natural_filter_or_measure(database,suffix):
    question=f'Inventory Lots.qty_total 合计；通过 Inventory Lots.vendor_id 与 Vendors.vendor_id 关联{suffix}'
    with sqlite3.connect(database) as c:
        tables=SchemaIntrospector().introspect(c)
    masked,bindings,rejected=association_scope(question,tables)
    assert rejected and not bindings and suffix.replace(' ','').lower() in masked
    result=engine(database,proposal()).answer(question)
    assert result.status=='clarification' and result.sql is None
    assert result.clarification_code=='unverified_join_condition'


def test_explicit_numeric_physical_group_field_has_a_dimension_role(database):
    result=engine(database).answer('按 Inventory Lots.unit_price 分组，统计 Inventory Lots.qty_total 合计')
    assert result.status=='ok'
    assert {(r['unit_price'],r['总qty_total']) for r in result.rows}=={(10,3),(20,7),(100,10)}
    assert next(x for x in result.plan['links'] if x['column']=='unit_price')['role']=='dimension'


def test_explicit_table_row_count_has_a_schema_grounded_representative_key(database):
    plan=proposal(dimensions=[{'table':'Vendors','column':'country','label':'country'}])
    plan['metrics'][0].update(column='lot_id',function='COUNT',label='Records',unit='count')
    result=engine(database,plan,model_fallback=False).answer(
        '按 Vendors.country 分组，统计 Inventory Lots 产品记录数；通过 Inventory Lots.vendor_id 与 Vendors.vendor_id 关联。')
    assert result.status=='ok'
    assert {(r['country'],next(v for k,v in r.items() if k!='country')) for r in result.rows}=={('East',2),('West',1)}


def test_true_unowned_measure_ambiguity_cannot_be_overridden_by_model(database):
    plan=proposal();plan['metrics'][0].update(column='unit_price')
    result=engine(database,plan).answer('unit_price 合计')
    assert result.status=='clarification' and result.sql is None and result.clarification_code=='ambiguous_metric'


def test_canonical_unique_opaque_id_keeps_exact_same_metric_and_all_references(database):
    plan=proposal(order_metric='stock total',output_metrics=['stock total'])
    plan['metrics'][0]['id']='stock total'
    result=engine(database,plan,model_fallback=False).answer('Inventory Lots 的 qty_total 合计')
    assert result.status=='ok' and list(result.rows[0].values())==[20.0]
    diagnostics=result.plan['model_plan_diagnostics']
    assert diagnostics['attempts'][0]['structural_normalizations'][0]['kind']=='logical_id'
    assert diagnostics['attempts'][0]['original_structure']['metrics'][0]['id']['identifier_valid'] is False
    assert result.plan['metrics'][0]['id']==result.plan['output_metrics'][0]==result.plan['order_metric']


def test_canonicalization_rewrites_derived_graph_and_executes_unchanged_expression(database):
    plan=proposal(output_metrics=['doubled total'],order_metric='doubled total')
    plan['metrics'][0]['id']='stock total'
    plan['derived_metrics']=[{'id':'doubled total','label':'Double',
        'expression':{'op':'multiply','left':{'ref':'stock total'},'right':{'constant':2}}}]
    checked,changes=canonicalize(plan)
    assert plan['metrics'][0]['id']=='stock total'  # Original diagnostics retain their input.
    with sqlite3.connect(database) as c:
        tables=SchemaIntrospector().introspect(c)
        validated=ModelPlanValidator().validate(checked,tables,question='Explicit synthetic expression')
        sql,params=MetricCompiler(SingleTablePlanner()).compile(validated,tables)
        _,rows=execute_read_only(c,sql,params)
    assert rows==({'Double':40.0},) and len(changes)==2


def test_duplicate_handles_remain_rejected_not_silently_deduplicated():
    plan=proposal();plan['metrics'].append(deepcopy(plan['metrics'][0]))
    with pytest.raises(ModelPlanError,match='ID 重复'):
        canonicalize(plan)


def test_colliding_display_labels_do_not_change_source_or_aggregation(database):
    plan=proposal(dimensions=[{'table':'Vendors','column':'country','label':'Stock'}])
    checked,changes=canonicalize(plan)
    assert checked['metrics'][0]['table']=='Inventory Lots' and checked['metrics'][0]['function']=='SUM'
    assert checked['metrics'][0]['label']!=checked['dimensions'][0]['label']
    assert changes[0]['kind']=='display_label'
    result=engine(database,plan,model_fallback=False).answer('按 Vendors.country 统计 Inventory Lots.qty_total 合计')
    assert result.status=='ok' and len(result.rows)==2


class RepairProvider:
    def __init__(self,first,second):
        self.first,self.second,self.repairs,self.feedback=first,second,0,None
    def propose(self,q,t,context):
        return deepcopy(self.first)
    def repair(self,q,t,context,feedback):
        self.repairs+=1;self.feedback=feedback
        return deepcopy(self.second)


def test_one_structural_feedback_repair_preserves_explicit_filter(database):
    second=proposal(filters=[{'table':'Vendors','column':'country','operator':'=','value':'East'}])
    first=deepcopy(second);first['metrics'].append(deepcopy(first['metrics'][0]))
    provider=RepairProvider(first,second)
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer(
        'Inventory Lots.qty_total 合计，只看 Vendors.country 为 East；通过 Inventory Lots.vendor_id 与 Vendors.vendor_id 关联。')
    assert result.status=='ok' and list(result.rows[0].values())==[10.0]
    assert provider.repairs==1
    assert [a['status'] for a in result.plan['model_plan_diagnostics']['attempts']]==['rejected','accepted']
    assert provider.feedback['reason_code']=='model_contract_or_grounding_rejected'


def test_second_invalid_plan_stops_without_a_third_model_request(database):
    bad=proposal();bad['metrics'].append(deepcopy(bad['metrics'][0]))
    provider=RepairProvider(bad,bad)
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer(
        'Inventory Lots.qty_total 合计，只看 Vendors.country 为 East；通过 Inventory Lots.vendor_id 与 Vendors.vendor_id 关联。')
    assert provider.repairs==1 and result.plan['planner_source']=='rules_fallback'
    assert [a['status'] for a in result.plan['model_plan_diagnostics']['attempts']]==['rejected','rejected']
    assert result.sql is None or all(next(iter(row.values()))==10 for row in result.rows)


def test_semantic_filter_failure_never_starts_a_structure_repair(database):
    provider=RepairProvider(proposal(),proposal(filters=[{'table':'Vendors','column':'country','operator':'=','value':'East'}]))
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer(
        'Inventory Lots.qty_total 合计，只看 Vendors.country 为 East')
    assert provider.repairs==0 and not result.plan['model_plan_diagnostics']['repair_attempted']


def test_ambiguous_source_does_not_request_a_repair_to_guess_owner(database):
    plan=proposal();plan['metrics'][0].update(column='unit_price')
    provider=RepairProvider(plan,plan)
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer('unit_price 合计')
    assert result.status=='clarification' and provider.repairs==0 and result.sql is None


def test_provider_failure_does_not_trigger_validation_repair(database):
    provider=RepairProvider(proposal(),proposal())
    def fail(*args):
        raise ModelPlanError('真实模型规划服务暂不可用')
    provider.propose=fail
    result=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=database.parent/'absent.json').answer('Inventory Lots.qty_total 合计')
    assert provider.repairs==0 and result.plan['model_plan_diagnostics']['attempts']==[]


def test_structural_diagnostics_hash_values_and_unrecognized_arbitrary_metadata(database):
    secret='sk-' + 'example-private-' + 'never-log-this'
    plan=proposal(filters=[{'table':'Vendors','column':'country','operator':'=','value':secret,'source_text':secret}])
    plan['metrics'][0].update(id=secret,label=secret)
    plan['token']=secret
    with sqlite3.connect(database) as c:
        tables=SchemaIntrospector().introspect(c)
    diagnostic=diagnose(plan,tables)
    assert secret not in json.dumps(diagnostic)
    assert diagnostic['metrics'][0]['table']=='Inventory Lots'
    assert diagnostic['filters'][0]['value']['type']=='str'


@pytest.mark.parametrize('kind',['omitted_filter','wrong_aggregation','wrong_owner'])
def test_structural_rebinding_never_authorizes_a_semantically_wrong_plan(database,kind):
    plan=proposal();plan['metrics'][0]['id']='opaque id'
    question='Inventory Lots.qty_total 合计，只看 Vendors.country 为 East'
    if kind=='wrong_aggregation':
        plan['metrics'][0]['function']='AVG'
    elif kind=='wrong_owner':
        plan['metrics'][0].update(table='Returns',column='unit_price')
    result=engine(database,plan).answer(question)
    assert result.plan['planner_source']=='rules_fallback'
    assert result.sql is None or all(next(iter(row.values()))==10 for row in result.rows)
