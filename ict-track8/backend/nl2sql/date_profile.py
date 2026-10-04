"""Bounded whole-column storage observations from the execution snapshot.

No sample values or business definitions are sent to the model. Unknown,
mixed, empty and budget-exhausted columns never become ISO assertions.
"""
from datetime import datetime
import re
import sqlite3
import time


_ISO = re.compile(r'\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?\Z')


def storage_profiles(connection, tables, question, *, max_columns=8, max_seconds=2.0, max_steps=1_000_000):
    from .date_semantics import is_date_column
    candidates=[(table.name,column.name) for table in tables for column in table.columns
        if is_date_column(column.name,column.data_type)
        and re.search(r'(?<![A-Za-z_0-9])'+re.escape(column.name)+r'(?![A-Za-z_0-9])',question)]
    result=[];started=time.monotonic();steps=0
    def progress():
        nonlocal steps
        steps+=1000
        return int(steps>max_steps or time.monotonic()-started>max_seconds)
    def classify(value):
        if value is None:return 'null'
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
                    f'SUM(CASE WHEN {quote(column)} IS NOT NULL AND JULIANDAY({quote(column)}) IS NULL '
                    f'THEN 1 ELSE 0 END) '
                    f'FROM {quote(table)} GROUP BY 1').fetchall()
                if steps>max_steps or time.monotonic()-started>=max_seconds:
                    raise sqlite3.OperationalError('date_profile_budget')
                kinds={kind for kind,count,_ in rows if count and kind!='null'}
                item['format']='iso_text' if kinds=={'iso_text'} else 'unknown'
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
