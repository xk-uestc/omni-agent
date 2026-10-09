"""Bounded whole-column storage observations from the execution snapshot.

No sample values or business definitions are sent to the model. Unknown,
mixed, empty and budget-exhausted columns never become ISO assertions.
"""
from datetime import datetime
import re
import sqlite3
import time


_ISO = re.compile(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?\Z')
_ISO_MONTH = re.compile(r'\d{4}-\d{2}\Z')
_TIME_INTENT = re.compile(
    r'\b(?:19|20)\d{2}\b|(?:19|20)\d{2}年|日期|时间|月份|年份|年度|月度|'
    r'最新|最早|天数|用时|间隔|归还|租出|\b(?:date|time|year|month|latest|earliest|duration)\b', re.I)


def date_candidate_fields(tables, question, *, candidate_fields=()):
    """Choose observation targets, never assign a business date/filter role.

    A temporal question mentioning a physical table (including through a
    non-date qualified field) can depend on other dates of that table. Only
    that bounded table scope is expanded; natural language alone does not
    authorize a scan of every date field in an unrelated database.
    """
    from .date_semantics import is_date_column
    catalog = {(t.name, c.name): c for t in tables for c in t.columns}
    dates = {key for key, c in catalog.items() if is_date_column(c.name, c.data_type)}
    selected = []
    def add(key):
        if key in dates and key not in selected:
            selected.append(key)
    for key in candidate_fields:
        if (isinstance(key, (tuple, list)) and len(key) == 2
                and all(isinstance(part, str) for part in key)):
            add(tuple(key))
    owners = set()
    for table, column in catalog:
        if re.search(r'(?<![A-Za-z_0-9])' + re.escape(table) + r'\s*\.\s*'
                     + re.escape(column) + r'(?![A-Za-z_0-9])', question, re.I):
            owners.add(table)
            add((table, column))
    for table, column in catalog:
        if (table, column) not in dates:
            continue
        matches = re.finditer(r'(?<![A-Za-z_0-9.])' + re.escape(column)
                              + r'(?![A-Za-z_0-9])', question, re.I)
        if any(not re.search(r'\.\s*$', question[:m.start()]) for m in matches):
            add((table, column))
            # Ambiguous unqualified names remain observations, but do not
            # establish the owner of other, unmentioned date dependencies.
            if sum(c == column for _, c in catalog) == 1:
                owners.add(table)
    if _TIME_INTENT.search(question):
        owners.update(t.name for t in tables if re.search(
            r'(?<![A-Za-z_0-9])' + re.escape(t.name) + r'(?![A-Za-z_0-9])', question, re.I))
        for key in catalog:
            if key[0] in owners:
                add(key)
    return selected


def sql_date_dependencies(sql, tables):
    """Return physical date references from a bounded, qualified SELECT AST.

    This does not approve or execute candidate SQL. The complex-query safety
    gate remains mandatory. Invalid/ambiguous SQL cannot authorize probes.
    Every physical expression in CTE/subquery scopes is visited, so computed
    output aliases cannot hide their underlying date dependency.
    """
    from sqlglot import exp, parse_one
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.scope import traverse_scope
    from .date_semantics import is_date_column
    from .security import validate_read_only_sql
    if not isinstance(sql, str) or len(sql) > 100_000:
        return []
    schema = {t.name: {c.name: c.data_type for c in t.columns} for t in tables}
    dates = {(t.name, c.name) for t in tables for c in t.columns
             if is_date_column(c.name, c.data_type)}
    try:
        tree = parse_one(validate_read_only_sql(sql).sql, read='sqlite')
        if len(list(tree.walk())) > 3000:
            return []
        tree = qualify(tree, dialect='sqlite', schema=schema, validate_qualify_columns=True)
        result = set()
        for scope in traverse_scope(tree):
            for column in scope.columns:
                owner = scope
                while owner is not None and column.table not in owner.sources:
                    owner = owner.parent
                source = owner.sources.get(column.table) if owner is not None else None
                if isinstance(source, exp.Table):
                    if source.db or source.catalog or source.name not in schema:
                        return []
                    key = (source.name, column.name)
                    if key in dates:
                        result.add(key)
        return sorted(result)
    except Exception:
        return []


def storage_profiles(connection, tables, question, *, candidate_fields=(),
                     max_columns=8, max_seconds=2.0, max_steps=1_000_000):
    candidates = date_candidate_fields(tables, question, candidate_fields=candidate_fields)
    result=[];started=time.monotonic();steps=0
    def progress():
        nonlocal steps
        steps+=1000
        return int(steps>max_steps or time.monotonic()-started>max_seconds)
    def classify(value):
        if value is None:return 'null'
        if isinstance(value,str) and _ISO_MONTH.fullmatch(value):
            try:datetime.strptime(value, '%Y-%m')
            except ValueError:return 'unknown'
            return 'iso_month_text'
        if isinstance(value,str) and _ISO.fullmatch(value):
            try:datetime.fromisoformat(value)
            except ValueError:return 'unknown'
            return 'iso_text'
        if type(value) in (int,float):
            if 1e8<=abs(value)<1e11:return 'numeric_seconds_magnitude'
            if 1e11<=abs(value)<1e14:return 'numeric_milliseconds_magnitude'
        return 'unknown'
    connection.create_function('internal_date_storage_kind',1,classify,deterministic=True)
    connection.set_progress_handler(progress,1000)
    try:
        for table,column in candidates[:max_columns]:
            item={'field':table+'.'+column,'format':'unknown','verification':'whole_column_execution_snapshot'}
            try:
                if steps>max_steps or time.monotonic()-started>=max_seconds:
                    raise sqlite3.OperationalError('date_profile_budget')
                quote=lambda name:'"'+name.replace('"','""')+'"'
                rows=connection.execute(f'SELECT internal_date_storage_kind({quote(column)}),COUNT(*),'
                    f'SUM(CASE WHEN {quote(column)} IS NOT NULL AND ('
                    f'internal_date_storage_kind({quote(column)}) = \'unknown\' '
                    f'OR (internal_date_storage_kind({quote(column)}) = \'iso_text\' '
                    f'AND JULIANDAY({quote(column)}) IS NULL) '
                    f'OR (internal_date_storage_kind({quote(column)}) = \'iso_month_text\' '
                    f'AND strftime(\'%Y-%m\', date({quote(column)} || \'-01\')) != {quote(column)})) '
                    f'THEN 1 ELSE 0 END) '
                    f'FROM {quote(table)} GROUP BY 1').fetchall()
                if steps>max_steps or time.monotonic()-started>=max_seconds:
                    raise sqlite3.OperationalError('date_profile_budget')
                kinds={kind for kind,count,_ in rows if count and kind!='null'}
                item['format'] = ('iso_text' if kinds == {'iso_text'} else
                                  'iso_month_text' if kinds == {'iso_month_text'} else 'unknown')
                # Parseable ISO permits mixed T/space separators and precision.
                # It does not prove chronological text ordering. A separate
                # whole-column SQLite parse observation authorizes only the
                # native SQLite comparator, not epoch guessing or text DESC.
                if item['format']=='iso_text' and not sum(invalid for _,_,invalid in rows):
                    item['time_comparison']={
                        'operator':'JULIANDAY',
                        'verification':'whole_column_sqlite_parse_non_null_values',
                        'text_order_verified':False,
                        'precision_contract':'native_sqlite_time_semantics'}
                elif item['format'] == 'iso_month_text' and not sum(invalid for _,_,invalid in rows):
                    item['time_comparison'] = {
                        'operator': 'text_half_open',
                        'verification': 'whole_column_calendar_month_values',
                        'text_order_verified': True,
                        'precision_contract': 'calendar_month',
                    }
                if len(kinds)==1 and next(iter(kinds)).startswith('numeric_'):
                    item['numeric_encoding_hint']=next(iter(kinds))
                    item['numeric_unit_verification']='not_proven_by_magnitude'
                item['non_null_rows']=sum(count for kind,count,_ in rows if kind!='null')
                item['null_rows']=sum(count for kind,count,_ in rows if kind=='null')
                item['date_time_zone']='not_inferred'
            except sqlite3.OperationalError:
                item['verification']='probe_budget_exhausted_or_unavailable'
                result.append(item)
                break
            result.append(item)
    finally:
        connection.set_progress_handler(None,0)
        connection.create_function('internal_date_storage_kind',1,None)
    return result
