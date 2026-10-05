"""Real-model development cases; expected values never enter model payloads."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from model_runtime import enable_local_model, local_model_headers
from cross_schema_round5_common import implementation_hashes, now

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != ROOT / 'docs' or args.output.exists():
        parser.error('New output in project docs required')
    config = enable_local_model('gpt-6-luna')
    if config['reasoning'] != 'medium':
        raise ValueError('medium reasoning required')
    before = implementation_hashes()
    directory = Path(tempfile.mkdtemp(prefix='record-count-', dir=ROOT / 'runtime'))
    database = directory / 'data.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE events(id INTEGER PRIMARY KEY,event_date TEXT);"
                        "INSERT INTO events VALUES(1,'2025-01-02'),(2,NULL),(3,'2025-01-03'),(4,'2024-12-01');")
    questions = ['统计events记录总数', '统计events中id>=2的记录数',
        '统计events在2025年1月的记录数，时间依据event_date',
        '统计events中event_date非空的记录数', '统计events中event_date为NULL的记录数']
    # Scoring expectations are not given to the engine/provider.
    expected = [4, 3, 2, 3, 1]
    def execute(question):
        provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],
            os.environ['ICT8_OPENAI_API_KEY'], model='gpt-6-luna', reasoning_effort='medium',
            reference_date=date(2026, 1, 1), http_headers=local_model_headers())
        engine = Nl2SqlEngine(database, model_plan_provider=provider,
            reference_date=date(2026, 1, 1), metric_catalog_path=directory / 'absent.json')
        try:
            return engine.answer(question).to_dict()
        except Exception as exc:
            return {'status': 'exception', 'exception_type': type(exc).__name__}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(execute, questions))
    cases = []
    for question, target, response in zip(questions, expected, responses):
        rows = response.get('rows', [])
        model = response.get('plan', {}).get('planner_source') == 'model_validated'
        value_correct = len(rows) == 1 and len(rows[0]) == 1 and next(iter(rows[0].values())) == target
        sql = response.get('sql')
        replay = False
        if sql:
            with sqlite3.connect(f'file:{database.as_posix()}?mode=ro', uri=True) as db:
                replay = db.execute(sql, response.get('parameters', [])).fetchall() == [(target,)]
        passed = response.get('status') == 'ok' and model and value_correct and replay
        cases.append({'question': question, 'expected_scoring_only': target, 'passed': passed,
                      'model_planner': model, 'unchanged_sql_replay': replay, 'response': response})
        print(json.dumps({'question': question, 'passed': passed}, ensure_ascii=False), flush=True)
    after = implementation_hashes()
    report = {'created_at': now(), 'scope': 'exposed_development_cases_not_public_benchmark',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'gold_sent_to_model': False,
        'implementation_stable': before == after, 'implementation_file_sha256': before,
        'implementation_file_sha256_end': after, 'cases': cases,
        'passed': sum(c['passed'] for c in cases), 'total': len(cases)}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if before == after and all(c['passed'] for c in cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
