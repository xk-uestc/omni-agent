# Deployment configuration only; core backend/frontend remain at the pinned SHA.
import os,stat
from pathlib import Path
from fastapi.staticfiles import StaticFiles
ROOT=Path(__file__).resolve().parents[2]
if os.getenv('OMNI_MODEL_ENABLED','0')=='1':
 config=Path(os.environ['OMNI_MODEL_CONFIG']);st=config.lstat()
 if config.is_symlink() or not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:
  raise ValueError('Model config must be private to its OS owner')
 values=dict(l.split('=',1) for l in config.read_text().splitlines() if l and not l.startswith('#') and '=' in l)
 if values['DEEPSEEK_BASE_URL'].rstrip('/')!='https://api.deepseek.com':raise ValueError('Unapproved model endpoint')
 os.environ.update(ICT8_GENERATION_PROVIDER='responses',ICT8_PLAN_PROVIDER='responses',ICT8_OPENAI_BASE_URL='https://api.deepseek.com',ICT8_OPENAI_API_KEY=values['DEEPSEEK_API_KEY'].strip(),ICT8_OPENAI_MODEL=values['DEEPSEEK_MODEL'].strip(),ICT8_OPENAI_REASONING='low',ICT8_PLAN_RETRIES='0',ICT8_STREAM_TIMEOUT='180')
from backend import app as api
app=api.app
if os.getenv('OMNI_MODEL_ENABLED','0')=='1':
 from budget_transport import BudgetSession
 clients=[api.generation_client,api.model_plan_provider.client]
 for client in clients:
  client.session=BudgetSession();client.transport_attempt_limit=1
app.mount('/',StaticFiles(directory=ROOT/'ict-track8/frontend',html=True),name='frontend')
