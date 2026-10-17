"""Live TCP acceptance only: no app imports, monkeypatches or paid API calls."""
import base64,hashlib,json,platform,subprocess,time
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[2];RUN=Path(__file__).resolve().parent
BASE='http://127.0.0.1:8030'; session=requests.Session();session.trust_env=False
records=[]
def request(name,method,path,payload=None,timeout=60):
 started=time.perf_counter();r=session.request(method,BASE+path,json=payload,timeout=timeout)
 try:body=r.json()
 except ValueError:body=r.text
 item={'id':name,'method':method,'path':path,'http_status':r.status_code,'seconds':time.perf_counter()-started,'response_headers':dict(r.headers),'response':body}
 if payload:item['request']=payload
 records.append(item);(RUN/(name+'.json')).write_text(json.dumps(item,ensure_ascii=False,indent=2))
 print(name,r.status_code,(body.get('status') if isinstance(body,dict) else 'html'),flush=True);return body
for name,path in [('health','/health'),('docs','/docs'),('frontend','/'),('knowledge-page','/knowledge.html'),('schema','/api/v1/nl2sql/schema')]:request(name,'GET',path)
def ingest(name,path,title):
 raw=path.read_bytes();payload={'document_id':name,'title':title,'modality':'pdf','filename':path.name,'file_base64':base64.b64encode(raw).decode()}
 result=request('ingest-'+name,'POST','/api/v1/knowledge/ingest',payload,120)
 # Full file payload remains in this local private runtime only; report has hash.
 result=request('source-'+name,'GET',f'/api/v1/knowledge/documents/{name}')
 r=session.get(BASE+f'/api/v1/knowledge/documents/{name}/original',timeout=30)
 records.append({'id':'original-'+name,'http_status':r.status_code,'sha256':hashlib.sha256(r.content).hexdigest(),'original_sha256':hashlib.sha256(raw).hexdigest(),'matched':r.content==raw})
 return result
warranty=ingest('local-warranty',ROOT/'samples/documents/warranty-policy.pdf','保修规则')
manifest=json.loads((ROOT/'benchmarks/real_document_20261011/odb-manifest.json').read_text())
source=next(d for d in manifest['documents'] if d['split']=='dev' and '61520788' in d['image_name'])
path=RUN/'native-textbook.pdf';assert hashlib.sha256(path.read_bytes()).hexdigest()==source['pdf_sha256']
ingest('local-labor',path,'定额教材')
request('document-question','POST','/api/v1/knowledge/query',{'question':'标准硬件产品的保修期是多久？','document_id':'local-warranty'})
request('omni-document-question','POST','/api/v1/omni/query',{'question':'标准硬件产品的保修期是多久？','reset_context':True})
q='高级熟练工的一般地区值是多少？'
request('native-table-query','POST','/api/v1/omni/query',{'question':q,'reset_context':True})
request('sql-query','POST','/api/v1/nl2sql/query',{'question':'2025年华东地区销售额是多少？'})
request('formula-natural-query','POST','/api/v1/omni/query',{'question':'根据文档《定额教材》公式计算运输保险费；从数据库查询材料采购材料原价合计；从数据库查询保险参数材料运输保险费率合计。','reset_context':True})
tasks=[{'id':'f','tool':'document_formula','args':{'document_id':'local-labor','label':'运输保险费'}},{'id':'price','tool':'sql','args':{'question':'材料采购材料原价合计'}},{'id':'rate','tool':'sql','args':{'question':'保险参数材料运输保险费率合计'}},{'id':'cost','tool':'calculate','args':{'formula':{'ref':'f','path':[]},'parameters':{'材料原价':{'ref':'price','path':['aggregate_cells','材料采购','材料原价','SUM',0]},'材料运输保险费率':{'ref':'rate','path':['aggregate_cells','保险参数','材料运输保险费率','SUM',0]}}}}]
request('formula-explicit-graph','POST','/api/v1/fusion/execute',{'tasks':tasks})
started=time.perf_counter();parts=[]
with session.post(BASE+'/api/v1/omni/query/stream',json={'question':q,'reset_context':True},stream=True,timeout=(10,90)) as r:
 r.encoding='utf-8';http=r.status_code;headers=dict(r.headers)
 for line in r.iter_lines(decode_unicode=True):parts.append(line)
raw='\n'.join(parts)+'\n';(RUN/'table-sse.txt').write_text(raw)
events=[]
for packet in raw.split('\n\n'):
 lines=packet.splitlines();kind=next((l[7:] for l in lines if l.startswith('event: ')),None);data=next((l[6:] for l in lines if l.startswith('data: ')),None)
 if data:events.append({'event':kind,'data':json.loads(data)})
records.append({'id':'table-sse','http_status':http,'seconds':time.perf_counter()-started,'response_headers':headers,'events':events})
print('SSE',http,[e['event'] for e in events],flush=True)
# Check source links returned by real answers, keeping their source identity.
for entry in list(records):
 body=entry.get('response',{})
 if not isinstance(body,dict):continue
 nested=body.get('result',body)
 for cite in nested.get('citations',[]):
  uri=cite.get('source_uri');r=session.get(BASE+uri,timeout=30)
  records.append({'id':'citation-replay','from':entry['id'],'source_uri':uri,'http_status':r.status_code,'sha256':hashlib.sha256(r.content).hexdigest(),'expected_sha256':cite.get('metadata',{}).get('source_sha256')})
# LAN address access from this host confirms the listener, not another-device reachability.
addresses=subprocess.check_output(['hostname','-I'],text=True).split();lan=next((a for a in addresses if '.' in a),None)
if lan:
 r=session.get('http://'+lan+':8030/health',timeout=10);records.append({'id':'lan-address-health','address':lan,'http_status':r.status_code,'response':r.json()})
report={'running_code_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'transport':'real_requests_TCP_to_running_uvicorn','paid_model_calls':0,'model_enabled':False,'formula_classification':'manual_tool_graph_not_autonomous_planning','database_origin':'repository_demo_with_transparent_artificial_material_price_500_CNY_and_rate_0.02','records':records}
(RUN/'http-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
