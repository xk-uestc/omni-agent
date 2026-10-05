"""Local real-model query pairs followed by deterministic saved-result comparison."""
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def main():
    engine=Nl2SqlEngine(initialize_database(ROOT/'runtime/comparison-reference.sqlite'))
    session='comparison-http-'+uuid.uuid4().hex
    records=[]
    def ask(question,check):
        request=Request('http://127.0.0.1:8030/api/v1/omni/query',
            data=json.dumps({'question':question,'session_id':session},ensure_ascii=False).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:result=json.load(response)
        records.append({'question':question,'status':result.get('status'),'route':result.get('route'),
            'rows':result.get('result',{}).get('rows'),
            'clarification_code':result.get('result',{}).get('clarification_code'),
            'planner_source':result.get('planner_source',result.get('planning_source')),
            'edit_evidence':result.get('result',{}).get('edit_evidence'),
            'passed':bool(check(result))})
        print(json.dumps(records[-1],ensure_ascii=False),flush=True)
        return result
    for question in ['2025年华东销售额','2025年华南销售额']:
        expected=list(engine.answer(question).rows)
        ask(question,lambda r:r['status']=='ok' and r['result']['rows']==expected)
    ask('比较刚才两次查询',lambda r:r['route']=='comparison' and r['status']=='ok'
        and r['result']['rows'][0]['差值']==-6592)
    ask('把基准换成较晚结果',lambda r:r['status']=='ok' and r['result']['rows'][0]['差值']==6592)
    operand_edit='--operand-edit' in sys.argv
    if operand_edit:
        ask('那华北呢',lambda r:r['status']=='clarification'
            and r['result']['clarification_code']=='comparison_edit_role_required'
            and len(r['result']['comparison_actions'])==2)
        north=engine.answer('2025年华北销售额').rows[0]['销售额']
        ask('比较值',lambda r:r['status']=='ok' and r['result']['rows'][0]['基准值']==22992
            and r['result']['rows'][0]['比较值']==north
            and r['result']['edit_evidence']['replacement_question']=='2025年华北销售额')
        south2024=engine.answer('2024年华南销售额').rows[0]['销售额']
        ask('把基准值改成2024年',lambda r:r['status']=='ok'
            and r['result']['rows'][0]['基准值']==south2024
            and r['result']['rows'][0]['比较值']==north)
    for question in ['2024年销售额趋势 按月','2025年销售额趋势 按月']:
        expected=list(engine.answer(question).rows)
        ask(question,lambda r:r['status']=='ok' and r['result']['rows']==expected)
    ask('比较最近两次SQL查询',lambda r:r['status']=='clarification'
        and r['result']['clarification_code']=='comparison_no_pairs')
    def aligned(result):
        if result['status']!='ok':return False
        left={r['月份'][-2:]:r['销售额'] for r in engine.answer('2024年销售额趋势 按月').rows}
        right={r['月份'][-2:]:r['销售额'] for r in engine.answer('2025年销售额趋势 按月').rows}
        for row in result['result']['rows']:
            month=json.loads(row['分组'])['月份']
            baseline,current=left.get(month),right.get(month)
            if (row['基准值'],row['比较值'])!=(baseline,current):return False
            delta=None if baseline is None or current is None else current-baseline
            if row['差值']!=delta:return False
        return len(result['result']['rows'])==len(left.keys()|right.keys())
    ask('按月份对齐',aligned)
    report=ROOT/('runtime/conversation-comparison-edit-http-20261004.json' if operand_edit
        else 'runtime/conversation-comparison-http-20261004.json')
    report.write_text(json.dumps({'not_official_benchmark':True,'turns':records},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
