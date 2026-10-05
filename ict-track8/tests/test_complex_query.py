"""Actual SQLite relational cases and adversarial proposal validation."""
import sqlite3
from types import SimpleNamespace

import pytest

from backend.nl2sql.complex_query import validate_proposal, propose_complex, CHECKS
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError, execute_read_only
from backend.responses_client import GenerationError


@pytest.fixture
def source(tmp_path):
    path = tmp_path/'source.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
            amount REAL, rental_id INTEGER, created_at TEXT);
        INSERT INTO customers VALUES(1,'alpha'),(2,'beta'),(3,'empty');
        INSERT INTO payments VALUES
        (1,1,10,101,'2026-01-01'),(2,1,0,101,'2026-01-02'),
        (3,1,30,102,'2026-01-02'),(4,2,5,NULL,'2026-01-03');
        ''')
    return path


def run(path, sql):
    with sqlite3.connect(path) as db:
        tables = SchemaIntrospector().introspect(db, include_row_count=False)
        compiled, params, _, _, _ = validate_proposal(sql, tables)
        return execute_read_only(db, compiled, params)[1]


@pytest.mark.parametrize('sql,expected', [
    ('SELECT COUNT(*) n,COUNT(rental_id) nonnull,SUM(CASE WHEN rental_id IS NULL THEN 1 ELSE 0 END) missing FROM payments',
     [{'n':4,'nonnull':3,'missing':1}]),
    ('SELECT customer_id,SUM(CASE WHEN amount=0 THEN 1 ELSE 0 END) zero_count,SUM(CASE WHEN amount>0 THEN 1 ELSE 0 END) positive_count FROM payments GROUP BY customer_id ORDER BY customer_id',
     [{'customer_id':1,'zero_count':1,'positive_count':2},{'customer_id':2,'zero_count':0,'positive_count':1}]),
    ('WITH per_rental AS (SELECT customer_id,rental_id,SUM(amount) total FROM payments GROUP BY customer_id,rental_id) SELECT customer_id,AVG(total) avg_total FROM per_rental GROUP BY customer_id ORDER BY customer_id',
     [{'customer_id':1,'avg_total':20},{'customer_id':2,'avg_total':5}]),
    ('WITH totals AS (SELECT customer_id,SUM(amount) total FROM payments GROUP BY customer_id) SELECT customer_id,total FROM totals WHERE total>(SELECT AVG(total) FROM totals)',
     [{'customer_id':1,'total':40}]),
    ('SELECT c.id,c.name FROM customers c WHERE NOT EXISTS(SELECT 1 FROM payments p WHERE p.customer_id=c.id)',
     [{'id':3,'name':'empty'}]),
    ('WITH ranked AS (SELECT id,customer_id,amount,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY created_at DESC,id DESC) rn FROM payments) SELECT id,customer_id,amount FROM ranked WHERE rn=1 ORDER BY customer_id',
     [{'id':3,'customer_id':1,'amount':30},{'id':4,'customer_id':2,'amount':5}]),
    ('WITH totals AS (SELECT customer_id,SUM(amount) amount FROM payments GROUP BY customer_id) SELECT c.id,t.amount FROM customers c JOIN totals t ON c.id=t.customer_id ORDER BY c.id',
     [{'id':1,'amount':40},{'id':2,'amount':5}]),
    ('SELECT customer_id,SUM(amount) total FROM payments GROUP BY 1 ORDER BY 2 DESC',
     [{'customer_id':1,'total':40},{'customer_id':2,'total':5}]),
    ('SELECT SUM(amount*id)/NULLIF(SUM(id),0) weighted FROM payments', [{'weighted':12}]),
    ('SELECT id FROM payments WHERE amount>0 AND customer_id=1 ORDER BY id', [{'id':1},{'id':3}]),
    ('SELECT id FROM payments WHERE amount=0 OR rental_id IS NULL ORDER BY id', [{'id':2},{'id':4}]),
])
def test_relational_answers(source, sql, expected):
    assert run(source, sql) == tuple(expected)


@pytest.mark.parametrize('sql', [
    'DELETE FROM payments', 'SELECT * FROM payments; SELECT * FROM customers',
    'SELECT secret FROM payments', 'SELECT * FROM missing',
    'SELECT random() FROM payments', 'SELECT readfile(name) FROM customers',
    'SELECT p.id FROM payments p JOIN customers c ON p.amount=c.id',
    'SELECT p.id FROM payments p CROSS JOIN customers c',
    'SELECT p.id FROM payments p JOIN customers c ON p.customer_id=c.id OR 1=1',
    'SELECT p.id FROM payments p JOIN customers c ON p.customer_id=p.customer_id',
    'SELECT p.id FROM payments p JOIN customers c USING(id)',
    'SELECT p.id FROM payments p JOIN customers c ON p.customer_id=c.id AND p.amount=c.id',
    'WITH wrong AS(SELECT SUM(amount) id FROM payments) SELECT c.id FROM customers c JOIN wrong w ON c.id=w.id',
    'SELECT id,id FROM payments', 'SELECT id FROM payments WHERE amount>?',
    'SELECT c.id FROM customers c WHERE NOT EXISTS(SELECT 1 FROM payments p WHERE p.amount=c.id)',
    'SELECT c.id FROM customers c WHERE NOT EXISTS(SELECT 1 FROM payments p WHERE p.customer_id=c.id OR 1=1)',
    'WITH RECURSIVE x(n) AS(SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT n FROM x',
])
def test_rejects_unsafe_or_unbound_queries(source, sql):
    with pytest.raises(SqlSafetyError):
        run(source, sql)


def test_parameter_print_order_and_quoted_identifier(tmp_path):
    path = tmp_path/'special.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE "a:p0"(id INT, value TEXT); INSERT INTO "a:p0" VALUES(1,\'literal:p0\');')
    assert run(path, 'WITH x AS(SELECT id FROM "a:p0" WHERE value=\'literal:p0\') SELECT id+8 result FROM x WHERE id>0') == ({'result':9},)


class Client:
    def __init__(self, sql, approved=True, error=None):
        self.sql,self.approved,self.error,self.calls = sql,approved,error,[]
    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(kwargs['name'])
        if self.error:
            raise self.error
        if kwargs['name'] == 'complex_sql_proposal':
            return {'sql':self.sql,'clarification':None}
        return {'approved':self.approved, 'checks':{k:self.approved for k in CHECKS},'clarification':None}


def provider(client):
    from datetime import date
    return SimpleNamespace(client=client,reference_date=date(2026,1,1),catalog=None,audit={},supports_complex_queries=True)


def test_engine_executes_only_reviewed_proposal(source):
    client = Client('SELECT COUNT(*) n,COUNT(rental_id) nonnull FROM payments')
    result = Nl2SqlEngine(source,model_plan_provider=provider(client)).answer('查询记录总数和rental_id非空记录数')
    assert result.status == 'ok' and result.rows == ({'n':4,'nonnull':3},)
    assert result.plan['planner_source'] == 'model_validated'
    assert result.plan['planner_audit']['validation_channel']=='relational_ast_plus_independent_model_review'
    assert result.plan['planner_audit']['typed_v2_intent_verified'] is False
    assert client.calls == ['complex_sql_proposal','complex_sql_independent_review']


@pytest.mark.parametrize('status,http_status', [(None, None), (429, 429), (503, 503)])
def test_complex_channel_retries_only_transient_provider_failures(source, status, http_status):
    class RetryClient(Client):
        def __init__(self):
            super().__init__('SELECT COUNT(*) n FROM payments')
            self._audit = {}

        @property
        def audit(self):
            return self._audit

        def generate(self, instructions, context, schema, **kwargs):
            self.calls.append(kwargs['name'])
            if len(self.calls) == 1:
                self._audit = {'http_status': http_status}
                raise GenerationError('temporary provider failure', status=status)
            if kwargs['name'] == 'complex_sql_proposal':
                self._audit = {'http_status': 200}
                return {'sql': self.sql, 'clarification': None}
            self._audit = {'http_status': 200}
            return {'approved': True, 'checks': {key: True for key in CHECKS}, 'clarification': None}

    client = RetryClient()
    model_provider = SimpleNamespace(client=client, reference_date=provider(client).reference_date,
        catalog=None, audit={}, supports_complex_queries=True, max_retries=1)
    result = Nl2SqlEngine(source, model_plan_provider=model_provider).answer('查询记录总数')
    assert result.status == 'ok' and result.rows == ({'n': 4},)
    assert client.calls == ['complex_sql_proposal', 'complex_sql_proposal', 'complex_sql_independent_review']


def test_completed_invalid_model_output_is_not_retried(source):
    class InvalidOutputClient(Client):
        def __init__(self):
            super().__init__('')
            self._audit = {'http_status': 200}

        @property
        def audit(self):
            return self._audit

        def generate(self, instructions, context, schema, **kwargs):
            self.calls.append(kwargs['name'])
            raise GenerationError('invalid structured output')

    client = InvalidOutputClient()
    base = provider(client)
    model_provider = SimpleNamespace(**vars(base), max_retries=1)
    result = Nl2SqlEngine(source, model_plan_provider=model_provider).answer('查询记录总数')
    assert result.status == 'clarification'
    assert client.calls == ['complex_sql_proposal']


@pytest.mark.parametrize('status', [400, 401, 403])
def test_nonretryable_http_errors_are_not_retried_even_without_audit_status(source, status):
    client = Client('', error=GenerationError('non-retryable provider response', status=status))
    model_provider = SimpleNamespace(**vars(provider(client)), max_retries=1)
    engine = Nl2SqlEngine(source, model_plan_provider=model_provider)
    if status in (401, 403):
        with pytest.raises(GenerationError):
            engine.answer('查询记录总数')
    else:
        assert engine.answer('查询记录总数').status == 'clarification'
    assert client.calls == ['complex_sql_proposal']


def test_semantic_rejection_never_executes(source):
    client = Client('SELECT COUNT(*) n,COUNT(rental_id) nonnull FROM payments',approved=False)
    result = Nl2SqlEngine(source,model_plan_provider=provider(client)).answer('查询记录总数和rental_id非空记录数')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'complex_query_semantic_review_rejected'
    assert result.plan['semantic_audit']['status'] == 'rejected'
    assert client.calls == ['complex_sql_proposal', 'complex_sql_independent_review']


@pytest.mark.parametrize('status',[401,403])
def test_auth_failure_no_retry(source,status):
    client=Client('',error=GenerationError('authorization',status=status))
    with pytest.raises(GenerationError):
        Nl2SqlEngine(source,model_plan_provider=provider(client)).answer('查询记录总数')
    assert len(client.calls)==1


def test_complete_result_uses_existing_artifact(source,tmp_path):
    client=Client('SELECT id,amount FROM payments ORDER BY id')
    result=Nl2SqlEngine(source,max_rows=2,model_plan_provider=provider(client),
        result_artifact_dir=tmp_path/'artifacts').answer('查询全部付款明细',complete_results=True)
    assert result.status=='ok' and len(result.rows)==2
    assert result.provenance['complete_result']['row_count']==4
    assert result.provenance['complete_result']['preview_truncated'] is True


def test_structural_repair_is_once_and_semantic_review_still_required(source):
    class RepairClient(Client):
        def generate(self,instructions,context,schema,**kwargs):
            self.calls.append(kwargs['name'])
            if kwargs['name']=='complex_sql_proposal':
                return {'sql':'SELECT p.id FROM payments p CROSS JOIN customers c','clarification':None}
            if kwargs['name']=='complex_sql_structural_repair':
                assert context['structural_failure']=='complex_query_unverified_join'
                assert 'preserve original semantics' in context['repair_constraint'].lower()
                return {'sql':'SELECT COUNT(*) n FROM payments','clarification':None}
            return {'approved':False,'checks':{k:False for k in CHECKS},'clarification':'Missing a requested field'}
    client=RepairClient('')
    result=Nl2SqlEngine(source,model_plan_provider=provider(client)).answer('查询付款记录总数和非空租赁数')
    assert result.status=='clarification' and result.sql is None
    assert client.calls==['complex_sql_proposal','complex_sql_structural_repair','complex_sql_independent_review']


def test_cross_source_typed_scope_never_enters_relational_channel(source):
    engine=Nl2SqlEngine(source)
    required=engine.extract_required_intent('付款金额合计')
    client=Client('SELECT COUNT(*) n FROM payments')
    engine.model_plan_provider=provider(client)
    engine.answer('查询付款记录总数',required_intent=required)
    assert client.calls==[]


def test_actual_composite_fk_requires_every_component(tmp_path):
    path=tmp_path/'composite.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''CREATE TABLE parent(a INT,b INT,PRIMARY KEY(a,b));
        CREATE TABLE child(id INT PRIMARY KEY,a INT,b INT,FOREIGN KEY(a,b) REFERENCES parent(a,b));
        INSERT INTO parent VALUES(1,2); INSERT INTO child VALUES(1,1,2);''')
    assert run(path,'SELECT c.id FROM child c JOIN parent p ON c.a=p.a AND c.b=p.b')==({'id':1},)
    with pytest.raises(SqlSafetyError):
        run(path,'SELECT c.id FROM child c JOIN parent p ON c.a=p.a')
