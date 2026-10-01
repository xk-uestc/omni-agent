"""Create an immutable pre-model v2 reference correction; never print gold."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import runpy

from cross_schema_round5_common import DEFAULT, EVALUATION_SUBDIR, digest, load


def main():
    root = DEFAULT.resolve()
    parent = root / 'evaluation'
    original = parent / 'private' / 'author_reference.py'
    destination = root / EVALUATION_SUBDIR / 'private' / 'author_reference.py'
    if destination.exists() or (root / EVALUATION_SUBDIR / 'PUBLIC_MANIFEST.json').exists():
        raise ValueError('revision_exists_refusing_overwrite')
    parent_manifest = load(parent / 'PUBLIC_MANIFEST.json')
    if parent_manifest['model_api_calls'] != 0 or (root / 'runs').exists():
        raise ValueError('reference_revision_requires_no_prior_model_runs')
    originals = [path for path in parent.rglob('*') if path.is_file()]
    before = {path.relative_to(parent).as_posix(): digest(path) for path in originals}
    raw = original.read_text(encoding='utf-8')
    tree = ast.parse(raw)
    calls = [node for node in tree.body if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
             and node.value.func.id == 'add' and isinstance(node.value.args[0], ast.Constant)
             and node.value.args[0].value == 'cs-r06']
    if len(calls) != 1:
        raise ValueError('revision_target_not_unique')
    sql_node = calls[0].value.args[4]
    if not isinstance(sql_node, ast.Constant) or not isinstance(sql_node.value, str):
        raise ValueError('revision_target_sql_not_constant')
    revised, count = re.subn(r'(?i)(?<!LEFT )(?<!INNER )(\bJOIN\s+film_actor\b)',
                             r'LEFT \1', sql_node.value)
    if count != 1:
        raise ValueError('revision_expected_one_optional_relation')
    lines = raw.splitlines(keepends=True)
    start = sum(len(line) for line in lines[:sql_node.lineno-1]) + len(lines[sql_node.lineno-1].encode('utf-8')[:sql_node.col_offset].decode('utf-8'))
    end = sum(len(line) for line in lines[:sql_node.end_lineno-1]) + len(lines[sql_node.end_lineno-1].encode('utf-8')[:sql_node.end_col_offset].decode('utf-8'))
    raw = raw[:start] + repr(revised) + raw[end:]
    raw = raw.replace("directory=root/'evaluation'", "directory=root/'evaluation/v2'")
    raw = raw.replace("'system_input_file':'evaluation/system_input.json'", "'system_input_file':'evaluation/v2/system_input.json'")
    revision = {'version': 'v2', 'parent_public_manifest_sha256': digest(parent/'PUBLIC_MANIFEST.json'),
        'parent_oracle_sha256': parent_manifest['oracle_sha256'], 'correction_case_ids': ['cs-r06'],
        'reason_code': 'preserve_fact_rows_without_optional_actor_relation',
        'model_calls_before_revision': 0, 'revision_tool_sha256': digest(Path(__file__))}
    raw = raw.replace("manifest={'created_at':now()", "manifest={'revision':" + repr(revision) + ",'created_at':now()")
    ast.parse(raw)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(raw)
    runpy.run_path(str(destination), run_name='__main__')
    current = load(root / EVALUATION_SUBDIR / 'PUBLIC_MANIFEST.json')
    if current['system_input_sha256'] != parent_manifest['system_input_sha256']:
        raise ValueError('reference_only_revision_changed_system_questions')
    after = {path.relative_to(parent).as_posix(): digest(path) for path in originals}
    if before != after:
        raise ValueError('immutable_v1_bytes_changed')
    print(json.dumps({'revision': 'v2', 'questions_unchanged': True, 'v1_bytes_unchanged': True,
        'correction_case_ids': ['cs-r06'], 'model_api_calls': 0,
        'system_input_sha256': current['system_input_sha256'], 'oracle_sha256': current['oracle_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
