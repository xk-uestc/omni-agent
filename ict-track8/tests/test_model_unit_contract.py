"""Provider unit guidance does not weaken the server's independent contract."""
from copy import deepcopy
import sqlite3

import pytest

from backend.nl2sql.model_contract import ModelPlanValidator, ModelPlanError
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider


def payload():
    return {'version':2,'metrics':[{'id':'cost','table':'Claims','column':'CostAmount',
        'function':'SUM','label':'CostAmount','unit':'unknown','currency':None}],
        'dimensions':[],'filters':[],'confidence':.9,'rewritten_question':'Claims CostAmount合计'}


@pytest.fixture
def tables():
    with sqlite3.connect(':memory:') as connection:
        connection.executescript('CREATE TABLE Claims(ClaimId INTEGER PRIMARY KEY, CostAmount REAL);')
        return SchemaIntrospector().introspect(connection)


@pytest.mark.parametrize('key,value', [
    ('unit',''),('unit',None),('unit','x'*41),('currency','x'*11),('currency',123),
])
def test_invalid_units_still_reject_at_server(tables,key,value):
    plan=payload()
    plan['metrics'][0][key]=value
    with pytest.raises(ModelPlanError,match='单位/币种'):
        ModelPlanValidator().validate(plan,tables,question='Claims CostAmount合计')


def test_undeclared_unit_and_currency_remain_unknown(tables):
    plan=ModelPlanValidator().validate(payload(),tables,question='Claims CostAmount合计')
    assert plan.metrics[0].unit=='unknown' and plan.metrics[0].currency is None


def test_actual_provider_request_schema_and_guidance_match_server_bounds(monkeypatch,tables):
    provider=ResponsesModelPlanProvider('https://example.com/v1','test-secret',model='gpt-6-luna',max_retries=0)
    captured={}
    def generate(instructions,context,schema,**kwargs):
        captured.update(instructions=instructions,schema=deepcopy(schema),context=deepcopy(context))
        return {'plan':payload()}
    monkeypatch.setattr(provider.client,'generate',generate)
    provider.propose('Claims CostAmount合计',tables,{'metrics':[]})
    metric=captured['schema']['properties']['plan']['properties']['metrics']['items']['properties']
    assert metric['unit']['minLength']==1 and metric['unit']['maxLength']==40
    assert metric['currency']['maxLength']==10 and 'null' in metric['currency']['type']
    assert 'unknown' in captured['instructions'] and 'currency为null' in captured['instructions']
    assert captured['context']['metric_catalog'] is None
