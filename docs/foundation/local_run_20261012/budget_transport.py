"""Deployment-only request budget for the authorized interactive model session."""
import fcntl,hashlib,json,os,time,uuid
from pathlib import Path
from copy import deepcopy
import requests
from backend.responses_client import GenerationError
LEDGER=Path(__file__).resolve().parent/'interactive-model-ledger.json'
MAX_CALLS=40;MAX_CNY=10.0
# Conservative budget ceilings, not provider billing prices.
INPUT_CNY_PER_M=10.0;OUTPUT_CNY_PER_M=40.0

def update(callback):
 LEDGER.parent.mkdir(parents=True,exist_ok=True)
 with LEDGER.with_suffix('.lock').open('a+') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  state=json.loads(LEDGER.read_text()) if LEDGER.exists() else {'authorized_calls':MAX_CALLS,'authorized_cny':MAX_CNY,'calls':[]}
  value=callback(state)
  tmp=LEDGER.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2));os.replace(tmp,LEDGER)
  return value

class BudgetSession(requests.Session):
 def post(self,url,**kwargs):
  if url!='https://api.deepseek.com/responses':raise GenerationError('体验模型地址未获授权')
  body=deepcopy(kwargs.get('json',{}));body['max_output_tokens']=min(body.get('max_output_tokens',5000),5000)
  body['temperature']=0
  raw=json.dumps(body,ensure_ascii=False).encode();size=len(raw)
  if size>100000:raise GenerationError('输入超出本轮模型体验预算')
  output=body['max_output_tokens'];reserve=((size+2000)*INPUT_CNY_PER_M+output*OUTPUT_CNY_PER_M)/1e6
  operation=body.get('text',{}).get('format',{}).get('name','unknown');call_id=uuid.uuid4().hex
  def book(state):
   if len(state['calls'])>=MAX_CALLS or sum(c['reserved_or_upper_cny'] for c in state['calls'])+reserve>MAX_CNY:
    raise GenerationError('本轮模型体验调用次数或费用预算已用完')
   state['calls'].append({'id':call_id,'operation':operation,'started_at':time.time(),'state':'reserved','input_sha256':hashlib.sha256(raw).hexdigest(),'input_bytes':size,'reserved_or_upper_cny':reserve})
  update(book);start=time.perf_counter();receipt={'state':'response_unknown'}
  try:
   kwargs['json']=body;response=super().post(url,**kwargs)
   receipt['http_status']=response.status_code
   if response.status_code==200:
    payload=response.json();usage=payload.get('usage',{});i=usage.get('input_tokens');o=usage.get('output_tokens')
    receipt.update(state=payload.get('status'),response_model=payload.get('model'),usage=usage)
    if type(i) is int and type(o) is int and i>=0 and o>=0:
     receipt['reserved_or_upper_cny']=(i*INPUT_CNY_PER_M+o*OUTPUT_CNY_PER_M)/1e6
   return response
  finally:
   receipt['seconds']=time.perf_counter()-start
   def finish(state):
    c=next(c for c in state['calls'] if c['id']==call_id);c.update(receipt)
   update(finish)
