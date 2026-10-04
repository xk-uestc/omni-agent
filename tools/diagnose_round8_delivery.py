"""Inspect actual runtime SQL contracts; no scorer or oracle access."""
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.models import QueryPlan
from backend.nl2sql.result_scope import configure_complete_scope
from evaluate_round7_complex import SQL_CASES

for identifier,question,_ in SQL_CASES:
    try:
        scope=configure_complete_scope(QueryPlan(rewritten_question=question),question)
        print(json.dumps({'id':identifier,'status':'scope_valid','scope':scope},ensure_ascii=False))
    except ValueError as exc:
        print(json.dumps({'id':identifier,'status':'rejected','code':str(exc)},ensure_ascii=False))
run=Path('D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002/runs/round8-release-development-20261004')
for line in (run/'observed.jsonl').read_text(encoding='utf-8').splitlines():
    row=json.loads(line)
    inner=row.get('response',row.get('result',{}))
    if 'result' in inner:inner=inner['result']
    if row['case_id'] in ('cs-r08','cs-c01'):
        print(json.dumps({'id':row['case_id'],'keys':list(row), 'result':inner},ensure_ascii=False))
