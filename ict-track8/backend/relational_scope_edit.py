"""Explicit cross-table edits change bound WHERE values, never SQL structure."""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re
import sqlite3
from sqlglot import exp, parse_one, tokenize
from sqlglot.errors import ParseError
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.tokens import TokenType
from .nl2sql.models import QueryPlan, QueryResult
from .nl2sql.security import SqlSafetyError, execute_read_only, validate_read_only_sql
from .sql_history_scope import _confirmed_replacement_context_valid


class RelationalScopeEditAgent:
    CLAUSE = re.compile(r'(?:把)?(?P<field>[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*|时间)(?:改成|改为|换成|换为)(?P<value>[^，,；;]{1,80})')

    def __init__(self, engine):
        self.engine = engine

    def run(self, question, history, *, complete_results=False):
        if not history:
            return None
        turn = history[-1]
        state = turn.state or {}
        payload = (state.get('executed_sql_context') or {}).get('payload') or {}
        sql = payload.get('sql','')
        text = question.strip().rstrip('。！？!?')
        # Only explicit physical fields opt into this structural channel.
        # Natural-language edits continue through their original rule verifier.
        if (not re.match(r'^(?:把)?(?:[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*|时间)(?:改成|改为|换成|换为)',text)
                or not sql):
            return None
        try:
            raw = parse_one(sql,read='sqlite')
        except ParseError:
            return None
        # CTE names are logical query scopes, not additional physical tables.
        # A derived metric can aggregate one fact table in several CTEs; its
        # natural-language edits belong to the complete rule-scope verifier.
        physical_tables = {source.name for scope in traverse_scope(raw)
                           for source in scope.sources.values() if isinstance(source, exp.Table)}
        if len(physical_tables) < 2:
            return None
        def rejected(reason):
            message='这次跨表修改未执行，原查询条件仍保留。请使用明确的表名.字段名和一个实际取值；不支持的条件请重新给出完整问题。'
            return {'verified':False,'reason':reason,'message':message,'scope':turn.effective_question,'replacements':[]}
        if (state.get('route')!='sql' or state.get('pending_question') is not None
                or 'pending_question' not in state or state.get('clarification_code')
                or not _confirmed_replacement_context_valid(question,turn.effective_question,state,self.engine)):
            return rejected('relational_edit_source_unverified')
        if complete_results:
            return rejected('relational_edit_complete_delivery_requires_full_query')
        clauses=re.split(r'[，,；;]',text)
        if len(text)>320 or not 1<=len(clauses)<=4:
            return rejected('relational_edit_too_complex')
        try:
            with self.engine.consistent_reads(),self.engine._connect() as connection:
                if not _confirmed_replacement_context_valid(question,turn.effective_question,state,self.engine):
                    return rejected('relational_edit_source_changed')
                tables,_,revision=self.engine._snapshot_for(connection)
                schema={table.name:{column.name for column in table.columns} for table in tables}
                params=deepcopy(payload['parameters'])
                # Name placeholders by textual position; AST traversal order is
                # not SQL parameter order (especially with nested CTEs).
                positions=[token for token in tokenize(sql,read='sqlite') if token.token_type==TokenType.PLACEHOLDER]
                if len(positions)!=len(params) or any(sql[t.start:t.end+1]!='?' for t in positions):
                    return rejected('relational_edit_parameter_binding_unverified')
                named=sql
                for i,token in reversed(list(enumerate(positions))):
                    named=named[:token.start]+f':dialogue_{i}'+named[token.end+1:]
                tree=parse_one(named,read='sqlite')
                predicates={}
                for scope in traverse_scope(tree):
                    for column in scope.columns:
                        source=scope.sources.get(column.table)
                        if not isinstance(source,exp.Table) or source.name not in schema or column.name not in schema[source.name]:
                            continue
                        operand=column
                        if (isinstance(column.parent,exp.Anonymous) and column.parent.name.upper()=='JULIANDAY'
                                and len(column.parent.expressions)==1 and column.parent.expressions[0] is column):
                            operand=column.parent
                        parent=operand.parent
                        if not isinstance(parent,(exp.EQ,exp.GTE,exp.LT)) or parent.this is not operand:
                            continue
                        value=parent.expression
                        if operand is not column:
                            if (not isinstance(parent,(exp.GTE,exp.LT)) or not isinstance(value,exp.Anonymous)
                                    or value.name.upper()!='JULIANDAY' or len(value.expressions)!=1):
                                continue
                            value=value.expressions[0]
                        if not isinstance(value,exp.Placeholder) or not re.fullmatch(r'dialogue_\d+',value.name):
                            continue
                        ancestor=parent.parent
                        while isinstance(ancestor,(exp.And,exp.Paren)):
                            ancestor=ancestor.parent
                        if not isinstance(ancestor,exp.Where):
                            continue  # Never change JOIN keys, OR branches, HAVING or projections.
                        index=int(value.name.split('_')[-1])
                        predicates.setdefault((source.name,column.name),[]).append((type(parent),index))
                scope_question=turn.effective_question
                replacements=[];changed=set()
                for clause in clauses:
                    match=self.CLAUSE.fullmatch(clause.strip())
                    if match is None or match['field'] in changed:
                        return rejected('relational_edit_clause_unverified')
                    field,value=match['field'],match['value'].strip()
                    changed.add(field)
                    if field=='时间':
                        years=re.findall(r'(?<!\d)(\d{4})年',scope_question)
                        year=re.fullmatch(r'(\d{4})年',value)
                        if len(years)!=1 or year is None:
                            return rejected('relational_edit_time_ambiguous')
                        old,new=int(years[0]),int(year[1])
                        date(new,1,1);date(new+1,1,1)
                        pairs=[]
                        for key,items in predicates.items():
                            lower=[i for operator,i in items if operator==exp.GTE and params[i]==f'{old:04}-01-01']
                            upper=[i for operator,i in items if operator==exp.LT and params[i]==f'{old+1:04}-01-01']
                            if len(items)==2 and len(lower)==len(upper)==1:
                                pairs.append((key,lower[0],upper[0]))
                        if len(pairs)!=1 or '.'.join(pairs[0][0]) in changed:
                            return rejected('relational_edit_time_binding_unverified')
                        key,lower,upper=pairs[0]
                        changed.add('.'.join(key))
                        params[lower],params[upper]=f'{new:04}-01-01',f'{new+1:04}-01-01'
                        old_span,new_span=f'{old:04}年',f'{new:04}年'
                    else:
                        table,column=field.split('.')
                        items=predicates.get((table,column),[])
                        if len(items)!=1 or items[0][0]!=exp.EQ:
                            return rejected('relational_edit_filter_not_unique')
                        index=items[0][1];old_value=params[index]
                        if not isinstance(old_value,str) or not old_value or scope_question.count(old_value)!=1:
                            return rejected('relational_edit_original_span_ambiguous')
                        explicit=re.search(r'(?<![A-Za-z_0-9])'+re.escape(field)+r'\s*(?:=|为|是)\s*'+re.escape(old_value),scope_question)
                        matches=[v for v in self.engine.analyze_slots(scope_question)['values'] if v.span==old_value]
                        if not explicit and (len(matches)!=1 or (matches[0].table,matches[0].column)!=(table,column)
                                or matches[0].via!='exact' or matches[0].negated):
                            return rejected('relational_edit_original_text_binding_unverified')
                        quote=lambda name:'"'+name.replace('"','""')+'"'
                        if not connection.execute(f'SELECT 1 FROM {quote(table)} WHERE {quote(column)}=? LIMIT 1',(value,)).fetchone():
                            return rejected('relational_edit_value_not_in_source')
                        params[index]=value
                        old_span,new_span=old_value,value
                    scope_question=scope_question.replace(old_span,new_span,1)
                    replacements.append({'slot':field,'from':old_span,'to':new_span})
                # SQL bytes, joins, CTEs, grouping, NULL expressions, result
                # columns, ordering and all unrelated parameters are unchanged.
                validate_read_only_sql(sql,self.engine.max_rows)
                columns,rows=execute_read_only(connection,sql,params,max_rows=self.engine.max_rows,
                    max_steps=self.engine.max_steps,max_seconds=self.engine.max_seconds)
                plan=QueryPlan(rewritten_question=scope_question).to_dict()
                plan.update({'metrics':deepcopy(state.get('metrics',[])),
                    'dimensions':deepcopy(state.get('dimensions',[])),
                    'planner_source':'server_verified_relational_parameter_edit'})
                digest=hashlib.sha256(repr(revision).encode()).hexdigest()
                result=QueryResult(status='ok',question=scope_question,rewritten_question=scope_question,
                    sql=sql,parameters=tuple(params),columns=columns,rows=rows,plan=plan,
                    explanation=('沿用原查询结构，仅修改明确指定的筛选参数；本轮重新读取数据库。',),
                    provenance={'source_type':'structured_database','database':self.engine.database_path.name,
                        'source_revision':digest,'consistency':'sqlite_read_transaction',
                        'source_revision_kind':'file_generation_and_schema_version_not_content_hash',
                        'result_completeness':'within_return_limit','structure_preserved':True},
                    result_state='partial_rows' if len(rows)>=self.engine.max_rows else 'rows' if rows else 'empty').to_dict()
                return {'verified':True,'scope':scope_question,'result':result,'replacements':replacements,
                    'reason':'relational_where_parameters_only','complete_results_requested':complete_results}
        except (ValueError,KeyError,TypeError,SqlSafetyError,ParseError,sqlite3.Error):
            return rejected('relational_edit_unverified')
