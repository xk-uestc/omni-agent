"""Verify a selected comparison operand across successive time clarifications."""
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def main():
    session='comparison-pending-http-'+uuid.uuid4().hex
    reference=Nl2SqlEngine(initialize_database(ROOT/'runtime/comparison-pending-reference.sqlite'))
    records=[]
    report=ROOT/'runtime/comparison-pending-http-20261004.json'
    def call(path,payload,check):
        request=Request('http://127.0.0.1:8030'+path,
            data=json.dumps({**payload,'session_id':session},ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:body=json.load(response)
        row={'question':payload.get('question',payload.get('original_question')),
            'selection':payload.get('selected_value'),'time':payload.get('selected_time'),
            'effective_question':body.get('effective_question'),'status':body.get('status'),
            'route':body.get('route'),'clarification_code':body.get('result',{}).get('clarification_code'),
            'edit_evidence':body.get('result',{}).get('edit_evidence'),
            'rows':body.get('result',{}).get('rows'),'passed':bool(check(body))}
        records.append(row)
        report.write_text(json.dumps({'not_official_benchmark':True,'session_id':session,'turns':records},
            ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
        return body
    def query(question,check):return call('/api/v1/omni/query',{'question':question},check)
    for question in ['2024年销售额趋势 按月','2025年销售额趋势 按月']:
        expected=list(reference.answer(question).rows)
        query(question,lambda b:b['status']=='ok' and b['result']['rows']==expected)
    query('比较最近两次SQL查询',lambda b:b['result'].get('clarification_code')=='comparison_no_pairs')
    query('按月份对齐',lambda b:b['status']=='ok')
    pending=query('把比较值改成完整问题：销售额趋势',lambda b:b['status']=='clarification'
        and b['result']['clarification_code']=='missing_time_range')
    grain=call('/api/v1/omni/clarify',{'original_question':pending['effective_question'],
        'clarification_code':pending['result']['clarification_code'],'selected_value':'year','selected_time':'2025'},
        lambda b:b['status']=='clarification' and b['result']['clarification_code']=='missing_time_grain')
    def final_check(body):
        if body['status']!='ok' or body['route']!='comparison':return False
        evidence=body['result']['comparison_evidence']
        if evidence['sources'][0]['question']!='2024年销售额趋势 按月':return False
        if evidence['sources'][1]['question']!='销售额趋势 2025年 按月':return False
        left={r['月份'][-2:]:r['销售额'] for r in reference.answer('2024年销售额趋势 按月').rows}
        right={r['月份'][-2:]:r['销售额'] for r in reference.answer('2025年销售额趋势 按月').rows}
        if len(body['result']['rows'])!=len(left.keys()|right.keys()):return False
        for row in body['result']['rows']:
            key=json.loads(row['分组'])['月份']
            old,new=left.get(key),right.get(key)
            if (row['基准值'],row['比较值'])!=(old,new):return False
            if row['差值']!=(None if old is None or new is None else new-old):return False
        return True
    call('/api/v1/omni/clarify',{'original_question':grain['effective_question'],
        'clarification_code':grain['result']['clarification_code'],'selected_value':'monthly_trend'},final_check)
    return 0 if all(row['passed'] for row in records) else 1


if __name__=='__main__':raise SystemExit(main())
