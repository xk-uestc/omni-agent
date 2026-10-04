"""Inspect exposed question shapes and saved failures, never private answers."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from cross_schema_round5_common import DEFAULT,verify_public


def main():
    _,database,_,bundle=verify_public(DEFAULT)
    engine=Nl2SqlEngine(database,metric_catalog_path=ROOT/'runtime/absent-catalog.json')
    for case in bundle['cases']:
        if case['kind']!='session':continue
        slots=engine.analyze_slots(case['question'])
        print(json.dumps({'id':case['case_id'],'question':case['question'],
            'time_spans':slots['time_spans'],
            'metrics':[(m.table,m.column,m.matched_alias) for m in slots['metrics']],
            'dimensions':[(m.table,m.column,m.matched_alias) for m in slots['dimensions']],
            'values':[(m.table,m.column,m.span) for m in slots['values']]},ensure_ascii=False))


if __name__=='__main__':main()
