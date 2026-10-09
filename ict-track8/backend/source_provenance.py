"""Physical table/field navigation derived from the executed SQL AST."""
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope
from sqlglot.errors import SqlglotError
import re
import unicodedata


def locate_quote_chunks(quote, chunks):
    """Locate original chunks using the ingestion punctuation normalization."""
    def normalize(value):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value))
    normalized = normalize(quote)
    if not normalized:
        return []
    for raw in (False, True):
        parts = [(chunk, normalize(
                 chunk.get('metadata', {}).get('raw_text', chunk['text']) if raw else chunk['text']))
                 for chunk in chunks]
        combined = ''.join(text for _, text in parts)
        start = combined.find(normalized)
        if start < 0:
            continue
        end, offset, selected = start + len(normalized), 0, []
        for chunk, text in parts:
            if offset < end and offset + len(text) > start:
                selected.append(chunk)
            offset += len(text)
        return selected
    return []


def sql_sources(result, engine, *, executed_record=None):
    sql = result.get('sql')
    reused = result.get('provenance', {}).get('execution_status') == 'reused_verified_result'
    if not sql and reused and executed_record:
        sql = executed_record.get('payload', {}).get('sql')
    if not sql or result.get('status') != 'ok':
        return None
    schema = {table['name']: {column['name'] for column in table['columns']}
              for table in engine.schema(include_row_count=False)['tables']}
    used = {}
    try:
        for scope in traverse_scope(parse_one(sql, read='sqlite')):
            aliases = {alias: source.name for alias, (_, source) in scope.selected_sources.items()
                       if isinstance(source, exp.Table) and source.name in schema}
            for table in aliases.values():
                used.setdefault(table, set())
            for column in scope.columns:
                if column.table:
                    candidates = [aliases[column.table]] if column.table in aliases else []
                else:
                    candidates = list({table for table in aliases.values() if column.name in schema[table]})
                if len(candidates) == 1 and column.name in schema[candidates[0]]:
                    used[candidates[0]].add(column.name)
            for select in scope.expression.selects:
                if select.is_star:
                    targets = ([aliases[select.table]] if isinstance(select, exp.Column)
                               and select.table in aliases else aliases.values())
                    for table in targets:
                        used[table].update(schema[table])
    except (SqlglotError, ValueError, KeyError, AttributeError):
        return None  # No invented table fallback for unsupported ASTs.
    provenance = result.get('provenance', {})
    return {'database':engine.database_path.name,
            'source_revision':provenance.get('source_revision'),
            'query_hash':provenance.get('query_hash'), 'source_question':provenance.get('source_question'),
            'sql':sql, 'parameters':(executed_record['payload'].get('parameters', []) if reused
                                    else result.get('parameters', [])), 'reused_result':reused,
            'tables':[{'name':table, 'used_columns':sorted(columns)} for table, columns in used.items()],
            'basis':'executed_sql_ast_physical_sources'} if used else None


def attach_sql_sources(response, engine):
    result = response.get('result', {})
    if response.get('route') == 'sql':
        manifest = sql_sources(result, engine,
            executed_record=response.get('state', {}).get('executed_sql_context'))
        if manifest:
            result['source_tables'] = manifest
    elif response.get('route') == 'fusion':
        for step in result.get('results', {}).values():
            manifest = sql_sources(step, engine)
            if manifest:
                step['source_tables'] = manifest
