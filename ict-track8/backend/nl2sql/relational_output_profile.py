"""Derive output lineage from qualified SQL, never from expected answers.

Only one representable physical aggregate or direct dimension is exposed in
legacy slots. Nested/conditional/derived expressions retain explicit AST
lineage; they must not masquerade as a single AVG/SUM metric.
"""
from sqlglot import exp, parse_one
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope


def output_profile(sql, tables):
    schema = {t.name: {c.name:c.data_type for c in t.columns} for t in tables}
    tree = qualify(parse_one(sql,read='sqlite'),dialect='sqlite',schema=schema)
    scopes=list(traverse_scope(tree));root=scopes[-1]
    definitions={t.name:t for t in tables}

    def resolve(expression,scope,seen=frozenset()):
        if isinstance(expression,exp.Alias):return resolve(expression.this,scope,seen)
        if isinstance(expression,exp.Column):
            key=(id(scope),expression.table,expression.name)
            if key in seen:return None
            owner=scope
            while owner and expression.table not in owner.sources:owner=owner.parent
            if owner is None:return None
            source=owner.sources[expression.table]
            if isinstance(source,exp.Table):
                return {'table':source.name,'column':expression.name,'operators':[]}
            if isinstance(source,Scope):
                candidates=[s for s in source.expression.selects if s.alias_or_name==expression.name]
                if len(candidates)==1:return resolve(candidates[0],source,seen|{key})
            return None
        if isinstance(expression,exp.Count) and isinstance(expression.this,exp.Star):
            sources=list(scope.selected_sources.values())
            if len(sources)==1 and isinstance(sources[0][1],exp.Table):
                table=sources[0][1].name
                primary=[c.name for c in definitions[table].columns if c.primary_key]
                return {'table':table,'column':primary[0] if len(primary)==1 else '*',
                        'operators':['COUNT'],'count_semantics':'physical_row_count'}
            # COUNT(*) on a joined rowset counts fanout, not distinct entities.
            # An explicit non-null key on the preserved FROM side is a valid
            # lineage anchor only for INNER/LEFT joins. Nullable SQLite text
            # primary keys and RIGHT/FULL null extension cannot prove this.
            joins=scope.expression.args.get('joins',[])
            from_clause=scope.expression.args.get('from_')
            if (sources and joins and from_clause and isinstance(from_clause.this,exp.Table)
                    and all(j.args.get('side','') in ('','LEFT') and
                            j.args.get('kind','') in ('','INNER','OUTER') for j in joins)):
                source=scope.sources.get(from_clause.this.alias_or_name)
                if isinstance(source,exp.Table):
                    primary=[c for c in definitions[source.name].columns if c.primary_key]
                    if len(primary)==1 and not primary[0].nullable:
                        return {'table':source.name,'column':primary[0].name,'operators':['COUNT'],
                            'count_semantics':'joined_row_count_including_fanout',
                            'non_null_anchor_verification':'declared_not_null_preserved_from_side'}
            return None
        if isinstance(expression,exp.Count) and isinstance(expression.this,exp.Distinct):
            expressions=expression.this.expressions
            if len(expressions)!=1:return None
            origin=resolve(expressions[0],scope,seen)
            return {**origin,'operators':[*origin['operators'],'COUNT_DISTINCT']} if origin else None
        if isinstance(expression,(exp.Sum,exp.Avg,exp.Count,exp.Min,exp.Max)):
            origin=resolve(expression.this,scope,seen)
            return {**origin,'operators':[*origin['operators'],expression.sql_name()]} if origin else None
        if isinstance(expression,exp.Coalesce):
            origin=resolve(expression.this,scope,seen)
            extra=expression.expressions
            if origin and len(extra)==1 and isinstance(extra[0],exp.Literal) and extra[0].this=='0':
                return {**origin,'missing':'zero'}
        if isinstance(expression,exp.TimeToStr):
            origin=resolve(expression.this,scope,seen)
            format_string=expression.args.get('format')
            if origin and isinstance(format_string,exp.Literal) and format_string.this in ('%Y','%Y-%m'):
                return {**origin,'transform':'year' if format_string.this=='%Y' else 'month'}
        if isinstance(expression,(exp.TsOrDsToTimestamp,exp.TsOrDsToDate)):
            return resolve(expression.this,scope,seen)
        return None

    result=[]
    for selection in root.expression.selects:
        lineage=resolve(selection,root)
        item={'label':selection.alias_or_name,'lineage':lineage,
              'representation':'opaque_relational_expression'}
        if lineage:
            if not lineage['operators']:item['representation']='dimension'
            elif len(lineage['operators'])==1:item['representation']='metric'
        result.append(item)
    return result
