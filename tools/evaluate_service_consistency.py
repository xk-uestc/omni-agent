"""Read-only HTTP verification of the running SQL/document fusion program."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def evaluate(base):
    def ref(name, path=None):
        return {'ref':name,'path':path or []}
    tasks = [
        {'id':'formula','tool':'document_formula','args':{'document_id':'metric-definitions','label':'客单价'}},
        {'id':'sales','tool':'sql','args':{'question':'2025年华东地区销售额'}},
        {'id':'count','tool':'sql','args':{'question':'2025年华东地区订单数'}},
        {'id':'result','tool':'calculate','args':{'formula':ref('formula'),'parameters':{
            '销售额':ref('sales',['rows',0,'销售额']),'订单数':ref('count',['rows',0,'订单数'])}}},
    ]
    response = requests.post(base+'/api/v1/fusion/execute',json={'tasks':tasks},timeout=60)
    response.raise_for_status()
    result = response.json()
    values = result.get('results',{})
    sql_results = [values.get(name,{}) for name in ('sales','count')]
    revisions = [item.get('provenance',{}).get('source_revision') for item in sql_results]
    checks = {
        'four_step_fusion_ok': result.get('status')=='ok' and len(result.get('trace',[]))==4,
        'actual_average': abs(values.get('result',{}).get('value',0)-29584/3)<1e-8,
        'same_sql_snapshot': bool(revisions[0]) and revisions[0]==revisions[1]
            and all(item.get('provenance',{}).get('consistency')=='sqlite_read_transaction' for item in sql_results),
        'document_version_verified': result.get('source_validation',{}).get('status')=='verified'
            and 'metric-definitions' in result.get('source_validation',{}).get('documents',{}),
    }
    return {'created_at':datetime.now(timezone.utc).isoformat(),
            'scope':'local_http_actual_fusion_not_model_accuracy','model_called':False,
            'checks':checks,'ok':all(checks.values()),
            'actual_value':values.get('result',{}).get('value')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url',default='http://127.0.0.1:8030')
    args = parser.parse_args()
    report = evaluate(args.base_url.rstrip('/'))
    (ROOT/'docs/SERVICE_CONSISTENCY_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
