"""Independent scoring adversaries before freezing target evaluation."""
from copy import deepcopy
from pathlib import Path
import json
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from m1c_fixture import setup,seed_cases,BENCH
from evaluate_task_experience_m1c import seed_verifier
from backend.dependency_agent import DependencyAgent


def test_scorer_rejects_wrong_value_source_scope_and_missing_operation(tmp_path):
    engine,k,_=setup(tmp_path);case=seed_cases()[0];gold=json.loads((BENCH/'seed_gold.json').read_text())[0]
    result=DependencyAgent(engine,k).run(case['tasks'],original_question=case['question']);check=seed_verifier(gold,engine,k)
    assert check(case['question'],case['tasks'],result)['task_success']
    for mode in ['value','source','region','operation','time']:
        bad=deepcopy(result)
        if mode=='value':bad['results']['forecast']['value']+=123
        elif mode=='source':bad['source_validation']['documents']['policy']='0'*64
        elif mode in {'region','time'}:
            for f in bad['results']['base']['plan']['filters']:
                if mode=='region' and f['column']=='region':f['value']='华南'
                if mode=='time' and f['column']=='order_date':f['value']=['2024-01-01','2025-01-01']
        else:bad['results'].pop('growth')
        assert not check(case['question'],case['tasks'],bad)['task_success'],mode
