"""Run exposed synthetic memory-sensitive development tasks in No Memory mode.

No production memory implementation, paid API, final-answer injection or B/C
score. Frozen hashes are checked before execution. Fixtures and traces are
separate from inputs and production data.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import importlib.metadata
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / 'benchmarks/memory_sensitive_m1a_20261010'
sys.path.insert(0, str(ROOT / 'ict-track8'))
from score_memory_sensitive_m1a import score
from evaluate_context_semantics_20261009 import build_fixtures


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--label', required=True)
    p.add_argument('--mode', choices=['A', 'B', 'C'], default='A')
    args = p.parse_args()
    if args.mode != 'A':
        p.error('B/C not_run: Memory Adapter not implemented; no scores may be produced')
    frozen = json.loads((BENCH / 'manifest.json').read_text())
    for name, expected in frozen['files'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('frozen input/scorer changed: ' + name)
    output = ROOT / 'docs/memory_rl/runs' / args.label
    if output.exists():
        p.error('preserve evidence: output label already exists')
    output.mkdir(parents=True)
    runtime = ROOT / 'runtime' / args.label
    if runtime.exists():
        p.error('runtime label already exists')
    runtime.mkdir(parents=True)
    # Lock evaluated rules configuration; no inherited external model wiring.
    os.environ['ICT8_FAST_SQL'] = '0'
    os.environ['ICT8_DISABLE_DENSE'] = '1'
    from backend.knowledge_store import KnowledgeStore
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.nl2sql.seed import initialize_rich_demo_data
    from backend.session import ConversationStore
    from backend.omni_agent import OmniAgent
    from backend.dependency_agent import DependencyAgent
    from openpyxl import Workbook
    payload = json.loads((BENCH / 'tasks.json').read_text())
    sources = json.loads((BENCH / 'sources.json').read_text())
    pool = json.loads((BENCH / 'candidate_pool.json').read_text())
    golds = json.loads((BENCH / 'gold.json').read_text())['tasks']
    paths = build_fixtures(runtime, initialize_rich_demo_data)
    database, aliases = paths['mixed']
    code_before = {str(f.relative_to(ROOT)): digest(f) for f in (ROOT / 'ict-track8/backend').rglob('*.py')}
    db_before = digest(database)
    agents, document_versions = {}, {}
    for variant in ['original', 'terms_changed', 'targets_changed']:
        store = KnowledgeStore(runtime / 'knowledge' / variant)
        store.ingest(sources['terms_changed' if variant == 'terms_changed' else 'terms'].encode(),
            document_id='terms', title='业务术语确认卡', modality='txt', filename='terms.txt')
        store.ingest(sources['policy'].encode(), document_id='policy', title='指标与计划', modality='txt', filename='policy.txt')
        workbook = Workbook()
        for row in sources['target_rows_changed' if variant == 'targets_changed' else 'target_rows']:
            workbook.active.append(row)
        # Avoid ZIP timestamps making document versions vary between replays.
        from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
        raw = BytesIO(); workbook.save(raw)
        stable = BytesIO()
        with ZipFile(raw) as source_zip, ZipFile(stable, 'w', ZIP_DEFLATED) as dest:
            for name in source_zip.namelist():
                item = ZipInfo(name, (2026, 10, 10, 0, 0, 0)); item.compress_type = ZIP_DEFLATED
                dest.writestr(item, source_zip.read(name))
        store.ingest(stable.getvalue(), document_id='targets', title='区域计划', modality='xlsx', filename='targets.xlsx')
        engine = Nl2SqlEngine(database, aliases_path=aliases, reference_date=date.fromisoformat(payload['reference_date']),
            metric_catalog_path=runtime / 'no-catalog.json', result_artifact_dir=runtime / 'results' / variant)
        agents[variant] = OmniAgent(engine, store, ConversationStore())
        document_versions[variant] = {r['document_id']: r['sha256'] for r in store.list_documents()}
    # Verify legal method candidate via actual tools; values remain out of memory
    # candidates and out of the target planner context. This is preparation,
    # separate from target-task scoring.
    prep_tasks = [
        {'id': 'formula', 'tool': 'document_formula', 'args': {'document_id': 'policy', 'label': '目标销售额'}},
        {'id': 'base', 'tool': 'sql', 'args': {'question': '2024年华东销售额'}},
        {'id': 'growth', 'tool': 'document_cell', 'args': {'document_id': 'targets', 'where': {'地区': '华东', '年份': 2026}, 'column': '目标增长率'}},
        {'id': 'forecast', 'tool': 'calculate', 'args': {'formula': {'ref': 'formula', 'path': []}, 'parameters': {
            '基准销售额': {'ref': 'base', 'path': ['rows', 0, '销售额']}, '目标增长率': {'ref': 'growth', 'path': []}}}}]
    a = agents['original']
    preparatory = DependencyAgent(a.engine, a.knowledge).run(prep_tasks)
    prep_gold = {'kind': 'fusion', 'baseline_sql': "SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>='2024-01-01' AND order_date<'2025-01-01' AND region='华东'", 'rate': .12, 'documents': ['policy', 'targets']}
    prep_pass, prep_score = score(prep_gold, {'status': preparatory['status'], 'result': preparatory}, database, document_versions['original'])
    dump(output / 'preparation.json', {'status': 'executed', 'pass': prep_pass, 'score': prep_score,
         'tasks': prep_tasks, 'raw_result': preparatory, 'injected_to_target': False})
    candidate_versions = {c['id']: {'source_documents': document_versions['original'],
        'database_sha256': db_before, 'aliases_sha256': digest(aliases), 'schema_sha256': hashlib.sha256(json.dumps(a.engine.schema(include_row_count=False), sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        'method_verified': prep_pass if c['id'] == 'method' else None} for c in pool['candidates']}
    dump(output / 'candidate-evidence.json', candidate_versions)
    records = []
    for task in payload['tasks']:
        agent = agents[task['source_variant']]
        pre_responses = []
        for q in task['preparation_questions']:
            pre_responses.append(agent.query(q, session_id=task['preparation_session']))
        prior = len(agent.conversations.context(task['target_session']))
        if not task['same_session_control'] and prior:
            raise ValueError('cross-session target polluted')
        events = []
        started = time.perf_counter()
        response = None
        try:
            response = agent.query(task['question'], session_id=task['target_session'], trace_callback=events.append)
            wall_ms = round((time.perf_counter() - started) * 1000, 3)
            try:
                passed, detail = score(golds[task['id']], response, database, document_versions[task['source_variant']])
                score_error = None
            except Exception as exc:
                passed, detail, score_error = False, {}, type(exc).__name__ + ': ' + str(exc)
            record = {'id': task['id'], 'category': task['category'], 'execution_status': 'executed', 'pass': passed,
                'score': detail, 'score_error': score_error, 'response': response, 'wall_ms': wall_ms}
        except Exception as exc:
            record = {'id': task['id'], 'category': task['category'], 'execution_status': 'error', 'pass': False,
                'error': type(exc).__name__ + ': ' + str(exc), 'response': response, 'wall_ms': round((time.perf_counter()-started)*1000,3)}
        record.update(target_session=task['target_session'], preparation_session=task['preparation_session'],
            target_history_before=prior, preparation_responses=pre_responses, trace=events, memory_injected=False,
            model_calls=0, input_tokens=0, output_tokens=0, tokens_basis='no model instantiated',
            reported_tool_calls=sum(e.get('status') == 'running' for e in events),
            retrieval_misuse='not_measurable_without_adapter', source_versions=document_versions[task['source_variant']])
        records.append(record)
        dump(output / (task['id'] + '.json'), record)
    code_after = {str(f.relative_to(ROOT)): digest(f) for f in (ROOT / 'ict-track8/backend').rglob('*.py')}
    categories = {}
    for category in sorted({r['category'] for r in records}):
        selected = [r for r in records if r['category'] == category]
        categories[category] = {'passed': sum(r['pass'] for r in selected), 'total': len(selected)}
    report = {'mode': 'A', 'label': args.label, 'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'created_at_utc': datetime.now(timezone.utc).isoformat(), 'user_date': '2026-10-10 Asia/Shanghai',
        'input_manifest_sha256': digest(BENCH / 'manifest.json'), 'frozen_hashes': frozen['files'],
        'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
        'packages': dict(sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())),
        'configuration': {'model': None, 'rules': 'original rules_basic', 'ICT8_FAST_SQL': '0', 'reference_date': payload['reference_date'],
            'memory_budget': 0, 'fixed_or_oracle_future_budget': {'max_items': 3, 'max_characters': 1800},
            'seed': 'deterministic fixture generator, no stochastic model', 'paid_api_calls': 0,
            'engine_limits': {k:getattr(a.engine,k) for k in ['max_rows','max_steps','max_seconds']}},
        'total': len(records), 'passed': sum(r['pass'] for r in records), 'by_category': categories,
        'benefit': {'passed': sum(r['pass'] for r in records if r['category'].startswith('benefit_')), 'total': sum(r['category'].startswith('benefit_') for r in records)},
        'same_session_and_fresh_session_controls_separate': True, 'method_preparation_verified': prep_pass,
        'source_before': code_before, 'source_after': code_after, 'implementation_stable': code_before == code_after,
        'database_sha256_before': db_before, 'database_sha256_after': digest(database), 'database_read_only_verified': db_before == digest(database),
        'aliases_sha256': digest(aliases), 'document_versions': document_versions,
        'records': records, 'comparison_groups': {'B': {'status': 'not_run', 'reason': 'Memory Adapter not implemented'}, 'C': {'status': 'not_run', 'reason': 'Memory Adapter not implemented'}},
        'real_model_baseline': {'status': 'not_run', 'reason': 'No approved model endpoint/configuration or paid-call budget used; this run is rules-only'},
        'limitations': ['Synthetic exposed development only, not official benchmark.', 'No retrieval instrumentation; guards only validate response behavior.',
            'Preparation method verified by explicit DependencyAgent graph, not learned by OmniAgent.', 'Database generated from frozen helper; candidates never injected in A.']}
    dump(output / 'summary.json', report)
    print(json.dumps({k: report[k] for k in ['mode','label','total','passed','benefit','by_category','method_preparation_verified','implementation_stable','database_read_only_verified']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
