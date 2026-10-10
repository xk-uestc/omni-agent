"""Budgeted Chat Completions bridge to Omni's existing generate protocol.

Only local research harnesses instantiate this; no global service reconfiguration.
Credentials/error bodies are never included in audit. No transport retries.
"""
import json
import os
from pathlib import Path
import stat
import time
import requests
from backend.responses_client import GenerationError


class BudgetedPlanner:
    def __init__(self,config,ledger,*,max_calls,session=None):
        p=Path(config);st=p.lstat()
        if p.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode & 0o077:
            raise ValueError('model config must be private to OS owner')
        c=dict(line.split('=',1) for line in p.read_text().splitlines() if line and not line.startswith('#') and '=' in line)
        self.token=c['DEEPSEEK_API_KEY'].strip();self.model=c['DEEPSEEK_MODEL'].strip()
        base=c['DEEPSEEK_BASE_URL'].rstrip('/')
        if base!='https://api.deepseek.com' or not self.token:raise ValueError('explicit authorized DeepSeek endpoint/key required')
        self.url=base+'/chat/completions';self.ledger=Path(ledger);self.max_calls=max_calls
        if not isinstance(max_calls,int) or not 1<=max_calls<=int(c['M1C_MAX_CALLS']):raise ValueError('call cap exceeds local approved configuration')
        self.session=session or requests.Session();self.audit={};self.calls=[]

    def generate(self,instructions,context,schema,*,name='answer',max_tokens=4000,**kwargs):
        if kwargs.get('image_attachments'):raise GenerationError('text-only research planner')
        # Exclusive advisory lock serializes budget reservation across processes.
        import fcntl
        self.ledger.parent.mkdir(parents=True,exist_ok=True)
        with self.ledger.open('a+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);count=sum(bool(x.strip()) for x in f)
            if count>=self.max_calls:raise GenerationError('approved model call budget exhausted')
            f.write(json.dumps({'call':count+1,'operation':name,'reserved_at':time.time()})+'\n');f.flush();os.fsync(f.fileno())
        started=time.perf_counter();audit={'provider':'deepseek_chat_completions','model':self.model,'operation':name,
            'call_number':count+1,'status':'failed','http_status':None,'temperature':0,'max_output_tokens':min(max_tokens,5000),
            'input_tokens':None,'output_tokens':None,'transport_attempts':1}
        self.audit=audit
        body={'model':self.model,'temperature':0,'max_tokens':min(max_tokens,5000),'stream':False,'response_format':{'type':'json_object'},
            'messages':[{'role':'system','content':instructions+'\nReturn one JSON object matching this schema:\n'+json.dumps(schema,ensure_ascii=False)},
                        {'role':'user','content':json.dumps(context,ensure_ascii=False)}]}
        call={'context':context,'request_schema':schema,'audit':audit}
        try:
            response=self.session.post(self.url,headers={'Authorization':'Bearer '+self.token},json=body,timeout=(10,55),allow_redirects=False)
            audit['http_status']=response.status_code
            if response.status_code!=200:raise GenerationError('model HTTP request rejected',status=response.status_code)
            data=response.json();usage=data.get('usage',{})
            audit.update(input_tokens=usage.get('prompt_tokens'),output_tokens=usage.get('completion_tokens'),usage=usage,
                         response_model=data.get('model'),finish_reason=data['choices'][0].get('finish_reason'))
            output=json.loads(data['choices'][0]['message']['content']);call['output']=output
            if not isinstance(output,dict) or set(output)!=set(schema['required']):raise GenerationError('structured plan keys invalid')
            for key,spec in schema['properties'].items():
                if spec.get('type')=='string' and not isinstance(output[key],str):raise GenerationError('structured plan value type invalid')
                if 'enum' in spec and output[key] not in spec['enum']:raise GenerationError('structured plan enum invalid')
            if audit['finish_reason']!='stop':raise GenerationError('truncated model plan')
            audit.update(status='completed',model_verified=data.get('model')==self.model)
            return output
        except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
            audit['error_type']=type(exc).__name__
            raise GenerationError('model transport or structured output failed') from None
        finally:
            audit['wall_ms']=round((time.perf_counter()-started)*1000,3);self.calls.append(call)
