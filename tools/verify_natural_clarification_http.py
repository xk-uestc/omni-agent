"""Local dialogue acceptance; not an official benchmark."""
import json
import sys
import uuid
import argparse
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--recovery',action='store_true')
    parser.add_argument('--help-and-grains',action='store_true')
    parser.add_argument('--acknowledgements',action='store_true')
    args=parser.parse_args()
    engine = Nl2SqlEngine(initialize_database(ROOT / 'runtime/natural-clarification-gold.sqlite'))
    session = 'natural-clarification-' + uuid.uuid4().hex
    records = []
    turns = [
        ('销售额趋势', 'missing_time_range', False),
        ('看2025年吧', 'missing_time_grain', False),
        ('选择按月趋势', '2025年按月销售额趋势', False),
        ('2024年华南订单数', '2024年华南订单数', True),
    ]
    if args.recovery:
        turns[1:1]=[('2025-13','missing_time_range',False),('看2025-13吧','missing_time_range',False),
                    ('0000年','missing_time_range',False),('不知道','missing_time_range',False)]
        grain_selection=next(index for index,turn in enumerate(turns) if turn[0]=='选择按月趋势')
        turns.insert(grain_selection,('按月或者按年','missing_time_grain',False))
    if args.help_and_grains:
        turns=[('销售额趋势','missing_time_range',False),
               ('什么意思','missing_time_range',False),
               ('看2025年吧','missing_time_grain',False),
               ('按季度','missing_time_grain',False),
               ('选项有什么区别','missing_time_grain',False),
               ('按年和按月','missing_time_grain',False),
               ('按月统计','2025年按月销售额趋势',False),
               ('2024年华南订单数','2024年华南订单数',True)]
    if args.acknowledgements:
        turns=[('销售额趋势','missing_time_range',False),
               ('好的','missing_time_range',False),
               ('看2025年吧','missing_time_grain',False),
               ('继续吧','missing_time_grain',False),
               ('这些选项是什么意思','missing_time_grain',False),
               ('确认','missing_time_grain',False),
               ('按月统计','2025年按月销售额趋势',False),
               ('2024年华南订单数','2024年华南订单数',True)]
    for question, expected, reset in turns:
        request = Request('http://127.0.0.1:8030/api/v1/omni/query',
            data=json.dumps({'question':question, 'session_id':session, 'reset_context':reset}, ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request, timeout=300) as response:
            data = json.load(response)
        result = data.get('result', {})
        if expected.startswith('missing_'):
            passed = data.get('status') == 'clarification' and result.get('clarification_code') == expected
            gold = None
        else:
            gold = engine.answer(expected).to_dict()['rows']
            passed = data.get('status') == 'ok' and result.get('rows') == gold
        record = {'question':question, 'status':data.get('status'), 'effective_question':data.get('effective_question'),
            'clarification_code':result.get('clarification_code'), 'context_resolution':data.get('context_resolution'),
            'rows':result.get('rows'), 'expected_rows':gold, 'planner_source':data.get('planner_source'), 'passed':passed}
        if question in {'2025-13','看2025-13吧','0000年','不知道','按月或者按年'}:
            record['passed']=passed and data.get('context_resolution',{}).get('mode')=='pending_sql_clarification_retained' and result.get('sql') is None
        if args.help_and_grains and question in {'什么意思','按季度','选项有什么区别','按年和按月'}:
            reason={'什么意思':'clarification_help_requested','按季度':'unsupported_time_grain',
                    '选项有什么区别':'clarification_help_requested','按年和按月':'multiple_grains_not_confirmed'}[question]
            record['passed']=passed and data.get('context_resolution',{}).get('reason')==reason and result.get('sql') is None
            if question=='选项有什么区别':
                record['passed']=record['passed'] and '逐月列出' in result.get('clarification','')
        if args.acknowledgements and question in {'好的','继续吧','确认','这些选项是什么意思'}:
            reason='clarification_help_requested' if question=='这些选项是什么意思' else 'condition_not_supplied'
            record['passed']=passed and result.get('sql') is None and data.get('context_resolution',{}).get('reason')==reason
            if reason=='clarification_help_requested':
                options=result.get('clarification_options',[])
                record['passed']=record['passed'] and bool(options) and all(
                    item['label'] in result.get('clarification','') for item in options)
        records.append(record)
        print(json.dumps(record, ensure_ascii=False), flush=True)
    report = ROOT / f'runtime/natural-clarification-http-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps({'not_official_benchmark':True,'session_id':session,'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(report), flush=True)
    return 0 if all(record['passed'] for record in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
