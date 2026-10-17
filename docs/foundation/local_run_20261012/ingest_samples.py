"""Upload shipped demo sources over the live API; no source regeneration."""
import argparse,base64,hashlib,json,time
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[2];RUN=Path(__file__).resolve().parent
p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8030');a=p.parse_args();s=requests.Session();s.trust_env=False
value=s.get(a.base+'/api/v1/knowledge/documents',timeout=20).json();existing=value['documents'] if isinstance(value,dict) else value;seen={d['sha256'] for d in existing};records=[]
for d in json.loads((ROOT/'samples/manifest.json').read_text())['files']:
 raw=(ROOT/'samples/documents'/d['filename']).read_bytes();digest=hashlib.sha256(raw).hexdigest();matches=digest==d['sha256'];line_endings=False
 if not matches:
  line_endings=hashlib.sha256(raw.replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')).hexdigest()==d['sha256']
  if not line_endings:raise ValueError('sample original differs beyond platform line endings: '+d['filename'])
 record={'document_id':d['document_id'],'actual_source_sha256':digest,'manifest_sha256':d['sha256'],'manifest_bytes_equal':matches,'only_LF_CRLF_difference':line_endings}
 if digest in seen:record['status']='already_present_same_bytes'
 else:
  payload={'document_id':d['document_id'],'title':d['title'],'modality':d['modality'],'filename':d['filename'],'file_base64':base64.b64encode(raw).decode(),'language':'chi_sim+eng'}
  start=time.perf_counter();x=s.post(a.base+'/api/v1/knowledge/ingest',json=payload,timeout=180);record.update(http_status=x.status_code,seconds=time.perf_counter()-start,response=x.json());x.raise_for_status();seen.add(digest);print(d['document_id'],x.status_code,flush=True)
 records.append(record);(RUN/'sample-corpus-ingest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
