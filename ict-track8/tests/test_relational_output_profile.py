"""SQL-derived lineage for presentation, not an oracle-driven label adapter."""
import sqlite3

from backend.nl2sql.schema import SchemaIntrospector


def tables():
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE facts(id INTEGER PRIMARY KEY,owner_id INT,amount REAL,created_at TEXT);')
        return SchemaIntrospector().introspect(db,include_row_count=False)


def profile(sql):
    from backend.nl2sql.relational_output_profile import output_profile
    return output_profile(sql,tables())


def test_actual_output_labels_and_count_star_physical_grain():
    result=profile('SELECT owner_id AS owner,SUM(amount) AS total,COUNT(*) AS count FROM facts GROUP BY owner_id')
    assert result[0]=={'label':'owner','lineage':{'table':'facts','column':'owner_id','operators':[]},'representation':'dimension'}
    assert result[1]['lineage']=={'table':'facts','column':'amount','operators':['SUM']}
    assert result[2]['lineage']['column']=='id'
    assert result[2]['lineage']['count_semantics']=='physical_row_count'


def test_nested_aggregate_is_not_flattened_into_plain_average():
    result=profile('WITH x AS(SELECT owner_id,SUM(amount) total FROM facts GROUP BY owner_id) SELECT owner_id,AVG(total) average FROM x GROUP BY owner_id')
    assert result[1]['representation']=='opaque_relational_expression'
    assert result[1]['lineage']['operators']==['SUM','AVG']


def test_conditional_count_and_weighted_mean_remain_relational_expressions():
    result=profile('SELECT SUM(CASE WHEN amount>0 THEN 1 ELSE 0 END) positive,SUM(amount*id)/NULLIF(SUM(id),0) weighted FROM facts')
    assert all(item['representation']=='opaque_relational_expression' for item in result)


def test_month_is_derived_from_actual_format_string():
    result=profile("SELECT strftime('%Y-%m',created_at) period,COUNT(*) records FROM facts GROUP BY 1")
    assert result[0]['lineage']['column']=='created_at'
    assert result[0]['lineage']['transform']=='month'


def test_distinct_and_nullable_count_are_different_physical_metrics():
    result=profile('SELECT COUNT(DISTINCT owner_id) owners,COUNT(amount) nonnull FROM facts')
    assert result[0]['lineage']['operators']==['COUNT_DISTINCT']
    assert result[1]['lineage']['operators']==['COUNT']
    assert result[1]['lineage']['column']=='amount'
