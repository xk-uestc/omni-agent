"""One preselected retained-original acceptance after production freeze.

No Gold is read. The first retained manifest source is fixed before parsing;
failure cannot trigger source replacement or production tuning in this run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--ledger', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', 'ict-track8/backend'], cwd=ROOT, check=True)
    freeze = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    sys.path.insert(0, str(ROOT / 'ict-track8'))
    from backend.knowledge_store import KnowledgeStore
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    from m1c_model_client import BudgetedPlanner
    manifest = json.loads((ROOT / 'benchmarks/real_document_20261011/odb-manifest.json').read_text())
    source = next(d for d in manifest['documents'] if d['split'] == 'retained')
    raw = (a.data_root / source['pdf_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == source['pdf_sha256']
    question = '先从数据库按变量统计监测次数合计，取最高的1个，再从文档《独立工程文献》中查询该变量的Description。'
    rows = [(1, '1', 8), (2, '2', 23), (3, '3', 11)]
    catalog = {'version': 'transparent_retained_acceptance_mapping_v1', 'metrics': [
        {'id': 'count', 'table': '工况监测', 'column': '监测次数', 'function': 'SUM',
         'label': '总监测次数', 'query_alias': '监测次数', 'aliases': ['监测次数'], 'unit': 'count'}]}
    model = BudgetedPlanner(a.config, a.ledger, max_calls=20); model.supports_visual_source_review = False
    with tempfile.TemporaryDirectory(prefix='omni-frozen-acceptance-') as tmp:
        root = Path(tmp); db = root / 'monitor.sqlite'
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE 工况监测(记录ID INTEGER PRIMARY KEY, 变量 TEXT, 监测次数 REAL)')
            connection.executemany('INSERT INTO 工况监测 VALUES(?,?,?)', rows)
        cat = root / 'catalog.json'; cat.write_text(json.dumps(catalog, ensure_ascii=False))
        engine = Nl2SqlEngine(db, metric_catalog_path=cat); knowledge = KnowledgeStore(root / 'knowledge')
        knowledge.ingest(raw, document_id='acceptance-source', title='独立工程文献', modality='pdf', filename=Path(source['pdf_path']).name)
        events = []; started = time.perf_counter()
        result = OmniAgent(engine, knowledge, ConversationStore(), model).query(question, reset_context=True, trace_callback=events.append)
        # This manual literal transcription is scoring-only and is assigned
        # after the model run; it never enters corpus, question or planning.
        expected = 'Flow of fresh feed to reactor riser'
        cells = [v for v in result.get('result', {}).get('results', {}).values()
                 if v.get('validation') == 'native_table_literal_cell_replayed']
        report = {'production_freeze_sha': freeze, 'selection': 'first_retained_manifest_original_no_reselection',
            'classification': 'real_text_model_one_source_acceptance_not_aggregate_benchmark',
            'source_exposure': 'not_used_for_this_round_development; prior_project_parser_experiments_may_have_seen_it',
            'source': source, 'question': question, 'database_rows': rows, 'metric_catalog': catalog,
            'database_classification': 'artificial_mapping_not_public_paired_business_data',
            'expected_scoring_only_manual_original_transcription': expected,
            'seconds': time.perf_counter()-started, 'result': result, 'events': events, 'model_calls': model.calls,
            'complete_task_passed': result['status'] == 'ok' and result.get('planner_source') == 'model_validated'
                                  and len(cells) == 1 and cells[0]['value'] == expected}
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'status': result['status'], 'planner': result.get('planner_source'),
                         'passed': report['complete_task_passed'], 'model_calls': len(model.calls)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
