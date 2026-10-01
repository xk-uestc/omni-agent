"""Contract tests for the official CSV port, not benchmark successes."""
import importlib.util
from pathlib import Path
import pytest


@pytest.fixture
def fetcher(monkeypatch):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'tools'))
    spec=importlib.util.spec_from_file_location('fetch_adventureworks_contract',root/'tools/fetch_adventureworks.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def evaluator(monkeypatch):
    root=Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root/'tools'))
    spec=importlib.util.spec_from_file_location('evaluate_adventureworks_contract',root/'tools/evaluate_adventureworks.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_money_precision_and_nullable_date_are_checked(fetcher):
    assert fetcher.convert_cell('20565.6206',{'name':'SubTotal','sqlite_type':'NUMERIC','nullable':False})=='20565.6206'
    with pytest.raises(ValueError,match='precision'):
        fetcher.convert_cell('1.12345',{'name':'SubTotal','sqlite_type':'NUMERIC','nullable':False})
    with pytest.raises(ValueError,match='NULL'):
        fetcher.convert_cell('',{'name':'CustomerID','sqlite_type':'INTEGER','nullable':False})
    assert fetcher.convert_cell('',{'name':'ShipDate','sqlite_type':'DATETIME','nullable':True}) is None
    with pytest.raises(ValueError):
        fetcher.convert_cell('2023-99-04',{'name':'OrderDate','sqlite_type':'DATETIME','nullable':False})


def test_contract_uses_official_field_order_computed_column_pk_and_fk(fetcher):
    sql="""CREATE TABLE [Sales].[Customer](
 [CustomerID] [int] NOT NULL,
 [TerritoryID] [int] NULL,
 [AccountNumber] AS ISNULL('AW' + CustomerID,''),
 [ModifiedDate] [datetime] NOT NULL
) ON [PRIMARY];
CONSTRAINT [PK_Customer_CustomerID] PRIMARY KEY CLUSTERED ([CustomerID])
CONSTRAINT [FK_Customer_SalesTerritory_TerritoryID] FOREIGN KEY ([TerritoryID]) REFERENCES [Sales].[SalesTerritory] ([TerritoryID])
BULK INSERT [Sales].[Customer] FROM 'Customer.csv' WITH (CODEPAGE = '65001', FIELDTERMINATOR = '\\t');"""
    contract=fetcher.table_contract(sql,'Customer')
    assert [f['name'] for f in contract['columns']]==['CustomerID','TerritoryID','AccountNumber','ModifiedDate']
    assert contract['columns'][2]['source_type']=='computed_original_csv_value'
    assert contract['primary_key']==['CustomerID']
    assert contract['internal_foreign_keys'][0]['table']=='SalesTerritory'


def test_multiset_comparison_rejects_missing_duplicate_or_truncated_rows(evaluator):
    assert evaluator.same_rows([('US',1.00000001),('UK',2)],[('UK',2),('US',1)])
    assert not evaluator.same_rows([('US',1)],[('US',1),('US',1)])
    assert not evaluator.same_rows([('US',1)],[('US',2)])
    assert evaluator.compact_rows([(i,i) for i in range(25)])['count']==25
    assert len(evaluator.compact_rows([(i,i) for i in range(25)])['sample_first_20'])==20


def test_projection_rejects_identical_column_on_wrong_table(evaluator):
    case={'metric':{'table':'SalesOrderHeader','column':'SubTotal','function':'SUM'},
        'dimensions':[{'table':'Customer','column':'AccountNumber'}]}
    result={'plan':{'table':'SalesOrderHeader','metric_table':'SalesOrderHeader','metric_column':'SubTotal',
        'metric_function':'SUM','metric_label':'合计','dimensions':['AccountNumber'],
        'dimension_tables':{'AccountNumber':'SalesOrderHeader'}},'columns':['AccountNumber','合计'],
        'rows':[{'AccountNumber':'AW01','合计':2}]}
    assert evaluator.projection(case,result) is None
    result['plan']['dimension_tables']['AccountNumber']='Customer'
    assert evaluator.projection(case,result)==[('AW01',2)]
