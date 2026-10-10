"""Transport contract tests only; mocks cannot count as model task success."""
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from m1c_model_client import BudgetedPlanner
from backend.responses_client import GenerationError

SCHEMA={'type':'object','required':['route'],'properties':{'route':{'type':'string','enum':['fusion']}}}
class Reply:
    status_code=200
    def json(self):return {'model':'fixture-model','usage':{'prompt_tokens':10,'completion_tokens':2},'choices':[{'finish_reason':'stop','message':{'content':'{"route":"fusion"}'}}]}
class Transport:
    def __init__(self):self.sent=[]
    def post(self,url,**kwargs):self.sent.append(kwargs);return Reply()

def test_persisted_budget_and_secret_free_audit(tmp_path):
    config=tmp_path/'private';config.write_text('DEEPSEEK_API_KEY=secret-fixture\nDEEPSEEK_MODEL=fixture-model\nDEEPSEEK_BASE_URL=https://api.deepseek.com\nM1C_MAX_CALLS=1\n');config.chmod(0o600)
    transport=Transport();ledger=tmp_path/'ledger'
    client=BudgetedPlanner(config,ledger,max_calls=1,session=transport)
    assert client.generate('instruction',{},SCHEMA)=={'route':'fusion'}
    with pytest.raises(GenerationError,match='budget'):BudgetedPlanner(config,ledger,max_calls=1,session=transport).generate('instruction',{},SCHEMA)
    assert len(transport.sent)==1 and 'secret-fixture' not in json.dumps(client.calls)
    assert client.audit['input_tokens']==10
    assert transport.sent[0]['allow_redirects'] is False
    with pytest.raises(ValueError):BudgetedPlanner(config,ledger,max_calls=2)
