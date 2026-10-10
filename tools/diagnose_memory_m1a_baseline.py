"""Limited dependency probes for preserved M1-A regression failures."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.knowledge_store import KnowledgeStore
from backend.session import ConversationStore
from backend.omni_agent import OmniAgent
from evaluate_context_semantics_20261009 import score
from datetime import date

original=json.loads((ROOT/'docs/memory_rl/runs/context-no-memory-20261010.json').read_text())
failed={r['id'] for r in original['cases'] if not r['pass']}
payload=json.loads((ROOT/'benchmarks/context_semantics_20261009/cases.json').read_text())
# Failed followups require all earlier turns from the same original session.
needed_sessions={c['session'] for c in payload['cases'] if c['id'] in failed}
selected=[c for c in payload['cases'] if c['session'] in needed_sessions]
out=ROOT/'docs/memory_rl/runs/context-pinned-dependency-probes.json'
assert not out.exists(),'preserve evidence'
runtime=ROOT/'runtime/memory-m1a-pinned-probes';runtime.mkdir(exist_ok=True)
agents={};records=[]
for c in selected:
 f=c['fixture']
 if f not in agents:
  paths=original['fixture_paths'][f]
  e=Nl2SqlEngine(Path(paths['database']),aliases_path=paths['aliases'],reference_date=date(2026,10,9),
    metric_catalog_path=ROOT/'benchmarks/context_semantics_20261009/demo_metric_catalog-frozen.json' if f=='rich' else runtime/'no-catalog.json',result_artifact_dir=runtime/'results'/f)
  agents[f]=OmniAgent(e,KnowledgeStore(runtime/'knowledge'/f),ConversationStore())
 a=agents[f];events=[]
 r=a.query(c['question'],session_id=c['session'],reset_context=c['reset_context'],trace_callback=events.append)
 passed,detail=score(c,r,Path(original['fixture_paths'][f]['database']))
 records.append({'id':c['id'],'original_failed':c['id'] in failed,'pass':passed,'response':r,'score':detail,'trace':events})
d={'scope':'limited dependency diagnosis, not second full 185-turn run','packages':{n:importlib.metadata.version(n) for n in ['sqlglot','fastapi','pydantic','starlette']},
 'total':len(records),'passed':sum(r['pass'] for r in records),'original_failures_still_failed':[r['id'] for r in records if r['original_failed'] and not r['pass']],
 'recovered_failures':[r['id'] for r in records if r['original_failed'] and r['pass']],'records':records}
out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in d.items() if k!='records'},ensure_ascii=False))
