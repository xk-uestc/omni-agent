"""Mutate saved synthetic results to verify independent scoring contracts."""
import argparse,sys,json,copy
from pathlib import Path
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'tools'))
from score_memory_sensitive_m1a import score
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--label',default='m1a-memory-sensitive-no-memory-20261010')
parser.add_argument('--output-name',default='scorer-negative-checks.json')
args=parser.parse_args()
out=root/'docs/memory_rl/runs'/args.label
summary=json.loads((out/'summary.json').read_text());prep=json.loads((out/'preparation.json').read_text())
db=root/'runtime'/args.label/'mixed.sqlite'
docs=summary['document_versions']['original']
gold={'kind':'fusion','baseline_sql':"SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>='2024-01-01' AND order_date<'2025-01-01' AND region='华东'",'rate':.12,'documents':['policy','targets'],'region':'华东','year':2026}
original={'status':prep['raw_result']['status'],'result':prep['raw_result']}
assert not (out/args.output_name).exists(), 'preserve evidence'
checks=[]
def check(name,r,expected):
 passed,detail=score(gold,r,db,docs);checks.append({'probe':name,'score_pass':passed,'expected':expected,'contract_met':passed==expected,'detail':detail})
check('actual independently verified method',original,True)
r=copy.deepcopy(original);r['result']['results']['forecast']['value']+=1;check('corrupt final value',r,False)
r=copy.deepcopy(original);r['result']['source_validation']['documents']['targets']='0'*64;check('corrupt document version',r,False)
r=copy.deepcopy(original);r['result']['results']['growth']['matched_conditions']['地区']='华南';check('wrong source row',r,False)
r=copy.deepcopy(original);r['result']['results']['base']['parameters'][0]='2023-01-01';check('wrong baseline input population',r,False)
r=copy.deepcopy(original);r['result']['trace']=[e for e in r['result']['trace'] if e['tool']!='document_formula'];check('missing necessary formula evidence',r,False)
d={'method':'scorer mutation probes; inputs and scorer unchanged','checks':checks,'passed':sum(c['contract_met'] for c in checks),'total':len(checks)}
(out/args.output_name).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'scorer_contract_probes_passed':d['passed'],'total':d['total']}))
assert all(c['contract_met'] for c in checks)
