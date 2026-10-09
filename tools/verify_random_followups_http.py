"""Seeded live multi-turn requests, checked against independent SQLite queries."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import random
import sqlite3
import time
import uuid

import requests

ROOT = Path(__file__).resolve().parents[1]
METRICS = [('销售额', 'sales_amount', 'SUM'), ('订单数', 'order_id', 'COUNT'), ('销量', 'quantity', 'SUM')]


def scenarios(seed):
    rng = random.Random(seed)
    regions = ['华东', '华南', '华北', '西南', '东北', '华中', '东南', '西北']
    patterns = [
        [('region', '{}'), ('metric', '看看{}'), ('year', '换成{}年'), ('metric', '那{}呢')],
        [('region', '换成{}'), ('metric', '再看一下{}'), ('year', '改成{}年'), ('region', '那{}呢')],
        [('year_region', '那{}年{}呢'), ('metric', '改看{}'), ('region', '{}呢'), ('year', '{}年')],
    ]
    output = []
    for edits in patterns:
        year, region, metric = rng.choice([2023, 2024, 2025]), rng.choice(regions), rng.choice(METRICS)
        turns = [(f'{year}年{region}地区的{metric[0]}', year, region, metric)]
        for kind, template in edits:
            if kind in {'year', 'year_region'}:
                year = rng.choice([value for value in [2023, 2024, 2025] if value != year])
            if kind in {'region', 'year_region'}:
                region = rng.choice([value for value in regions if value != region])
            if kind == 'metric':
                metric = rng.choice([value for value in METRICS if value != metric])
            values = (year, region) if kind == 'year_region' else (year if kind == 'year' else region if kind == 'region' else metric[0],)
            turns.append((template.format(*values), year, region, metric))
        output.append(turns)
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='https://raysource.cloud/demo')
    parser.add_argument('--seed', type=int, default=20261007)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()

    def run(case):
        index, turns = case
        session = 'random-followup-' + uuid.uuid4().hex
        records = []
        with sqlite3.connect(f'file:{ROOT / "ict-track8/data/demo_sales.sqlite"}?mode=ro', uri=True) as db:
            for turn, (question, year, region, (label, column, function)) in enumerate(turns):
                expression = 'COUNT(*)' if function == 'COUNT' else f'{function}("{column}")'
                expected = db.execute(f'SELECT {expression} FROM sales_orders WHERE region=? AND order_date>=? AND order_date<?',
                    (region, f'{year}-01-01', f'{year + 1}-01-01')).fetchone()[0]
                started = time.monotonic()
                record = {'case': index + 1, 'round': turn + 1, 'question': question,
                          'expected': {'year': year, 'region': region, 'column': column, 'function': function, 'value': expected}}
                try:
                    response = requests.post(args.base.rstrip('/') + '/api/v1/omni/query', json={
                        'question': question, 'session_id': session, 'reset_context': turn == 0}, timeout=180)
                    response.raise_for_status()
                    data = response.json()
                    result = data.get('result', {})
                    plan = result.get('plan', {})
                    rows = result.get('rows', [])
                    actual = rows[0].get(label) if len(rows) == 1 else None
                    filters = plan.get('filters', [])
                    correct_region = any(f.get('table') == 'sales_orders' and f.get('column') == 'region'
                        and f.get('operator') == '=' and f.get('value') == region for f in filters)
                    correct_year = any(f.get('table') == 'sales_orders' and f.get('column') == 'order_date'
                        and f.get('operator') == 'RANGE' and f.get('value') == [f'{year}-01-01', f'{year + 1}-01-01'] for f in filters)
                    record.update(response=data, actual=actual,
                        passed=data.get('status') == 'ok' and data.get('route') == 'sql'
                        and isinstance(actual, (int, float)) and math.isclose(actual, expected, abs_tol=.005)
                        and correct_region and correct_year and plan.get('metric_column') == column
                        and plan.get('metric_function') == function)
                except Exception as exc:
                    record.update(passed=False, error=f'{type(exc).__name__}: {exc}')
                record['seconds'] = round(time.monotonic() - started, 3)
                records.append(record)
                print(json.dumps({k: record[k] for k in ('case', 'round', 'question', 'passed', 'seconds')}
                    | {'actual': record.get('actual'), 'expected': expected,
                       'scope': record.get('response', {}).get('effective_question'),
                       'clarification': record.get('response', {}).get('result', {}).get('clarification')}, ensure_ascii=False), flush=True)
        return records

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = [record for group in pool.map(run, enumerate(scenarios(args.seed))) for record in group]
    report = {'seed': args.seed, 'base': args.base, 'passed': sum(r['passed'] for r in records),
              'total': len(records), 'records': records}
    destination = ROOT / args.output
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('passed', 'total', 'seed')}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
