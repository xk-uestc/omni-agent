"""Extract a verified checkpoint into a fresh, disposable project and run its API.

No credential is copied and no remote model is called. Report lives next to ZIP.
"""
import argparse
import hashlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from scripts.verify_package import verify_package


def sample_documents_match(documents, manifest):
    """Fresh startup must expose every shipped sample and its actual version."""
    expected = manifest.get('files', [])
    if len(expected) < 10 or len(documents) != len(expected):
        return False
    expected_by_id = {item['document_id']: (item['sha256'], item['modality']) for item in expected}
    actual_by_id = {item['document_id']: (item['sha256'], item['modality']) for item in documents}
    return (len(expected_by_id) == len(expected) and len(actual_by_id) == len(documents)
            and actual_by_id == expected_by_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('--work-dir', type=Path, help='新目录验收的临时父目录；默认使用系统临时目录，避免占满项目盘')
    args = parser.parse_args()
    package = args.package.resolve()
    verified = verify_package(package)
    if not verified['ok']:
        raise ValueError('压缩包校验失败，不能执行')
    report = {'package_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
              'manifest_verified':True, 'real_model_test':'not_run', 'checks':{}}
    local_config = ROOT/'runtime/model_config.json'
    if local_config.exists():
        # Also check literal credentials that do not match the generic sk- regex.
        # The private configuration is never copied into the disposable project.
        local_secret = json.loads(local_config.read_text(encoding='utf-8'))['api_key'].encode()
        secret_absent = True
        with zipfile.ZipFile(package) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                secret_absent = secret_absent and local_secret not in data
                if name.endswith(('.docx','.pptx')):
                    with zipfile.ZipFile(io.BytesIO(data)) as office:
                        secret_absent = secret_absent and all(local_secret not in office.read(item) for item in office.namelist())
        report['checks']['local_credential_absent'] = secret_absent
        if not secret_absent:
            raise ValueError('包中检测到本机凭据；不执行，不输出凭据')
    (ROOT/'runtime').mkdir(exist_ok=True)
    if args.work_dir:
        args.work_dir.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='package-smoke-', dir=args.work_dir) as directory:
        extracted = Path(directory)
        with zipfile.ZipFile(package) as archive:
            archive.extractall(extracted)
        report['checks']['credential_not_packaged'] = not (extracted/'runtime/model_config.json').exists()
        with socket.socket() as port_probe:
            port_probe.bind(('127.0.0.1',0))
            port = port_probe.getsockname()[1]
        env = os.environ.copy()
        env.update({'ICT8_ENV':'development', 'ICT8_GENERATION_PROVIDER':'', 'ICT8_PLAN_PROVIDER':'',
            'ICT8_PLAN_URL':'', 'ICT8_MANUAL_RETRIEVER_URL':'',
            'ICT8_DB_PATH':str(extracted/'ict-track8/data/demo_sales.sqlite'),
            'ICT8_KNOWLEDGE_ROOT':str(extracted/'runtime/knowledge'),
            'ICT8_SESSION_DB':str(extracted/'runtime/sessions.sqlite'),
            'ICT8_DENSE_MODEL_PATH':str(extracted/'models/bge-small-zh-v1.5'),
            'ICT8_OCR_ENGINE':'rapidocr'})
        for name in ('ICT8_OPENAI_API_KEY','OPENAI_API_KEY','ICT8_OCR_URL'):
            env.pop(name,None)
        with (extracted/'server.log').open('w',encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable,str(extracted/'tools/run_server.py'),'--port',str(port)],
                cwd=extracted, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                base = f'http://127.0.0.1:{port}'
                deadline = time.monotonic()+60
                while time.monotonic()<deadline:
                    if process.poll() is not None:
                        raise RuntimeError('空目录启动失败；未记录任何凭据')
                    try:
                        response = requests.get(base+'/health',timeout=1)
                        if response.ok:
                            break
                    except requests.RequestException:
                        pass
                    time.sleep(.5)
                else:
                    raise RuntimeError('空目录启动超时')
                report['checks']['health'] = response.json()['ok']
                original_text = '緊急工單首欠響應為2小時。'
                quality = requests.post(base+'/api/v1/documents/text-quality',json={'text':original_text},timeout=10).json()
                repair = requests.post(base+'/api/v1/documents/text-repair',json={
                    'text':original_text,'source_sha256':quality['source_sha256'],
                    'accepted_ids':[item['id'] for item in quality['typo_candidates']]},timeout=10).json()
                report['checks']['text_quality_confirmed_preview'] = repair['revised_text']=='紧急工单首次响应为2小时。' and repair['original_preserved']
                stale = requests.post(base+'/api/v1/documents/text-repair',json={
                    'text':original_text+'。','source_sha256':quality['source_sha256'],'accepted_ids':[]},timeout=10)
                report['checks']['text_quality_stale_source_refused'] = stale.status_code==422
                documents = requests.get(base+'/api/v1/knowledge/documents',timeout=5).json()['documents']
                sample_manifest = json.loads((extracted/'samples/manifest.json').read_text(encoding='utf-8'))
                report['checks']['documents'] = sample_documents_match(documents, sample_manifest)
                report['sample_documents'] = {'expected_count': len(sample_manifest['files']),
                                            'actual_count': len(documents),
                                            'verification': 'exact_document_ids_source_sha256_and_modality'}
                answer = requests.post(base+'/api/v1/knowledge/query',json={'question':'标准硬件产品保修期有多久？'},timeout=60).json()
                report['checks']['rag'] = '12个月' in answer['answer'] and bool(answer['citations'])
                report['checks']['dense'] = answer['retrieval']['mode']=='bm25_dense_rrf'
                sql = requests.post(base+'/api/v1/omni/query',json={'question':'2025年华东地区销售额'},timeout=10).json()
                report['checks']['sql'] = sql['result']['rows'][0]['销售额']==29584
                report['checks']['sql_snapshot_provenance'] = (
                    sql['result']['provenance'].get('consistency')=='sqlite_read_transaction'
                    and sql['result']['provenance'].get('source_revision_kind')=='file_generation_and_schema_version_not_content_hash'
                    and len(sql['result']['provenance'].get('source_revision',''))==64)
                threshold_tasks = [
                    {'id':'search','tool':'search','args':{'query':'紧急工单首次响应时间'}},
                    {'id':'fact','tool':'search_fact','args':{'evidence':{'ref':'search','path':[]},'scope':'紧急工单','label':'首次响应','unit':'小时'}},
                    {'id':'standard','tool':'document_cell','args':{'document_id':'service-thresholds','where':{'工单优先级':'紧急','适用版本':'2025'},'column':'首次响应小时'}},
                    {'id':'compare','tool':'compare','args':{'left':{'ref':'fact','path':[]},'right':{'ref':'standard','path':[]},'operator':'le'}},
                ]
                threshold = requests.post(base+'/api/v1/fusion/execute',json={'tasks':threshold_tasks},timeout=60).json()
                compared = threshold.get('results',{}).get('compare',{})
                report['checks']['two_source_threshold_comparison'] = (
                    threshold['status']=='ok' and compared.get('matched') is True and compared.get('operator')=='le'
                    and compared.get('left',{}).get('value')==compared.get('right',{}).get('value')==2
                    and compared['left']['source_uri']!=compared['right']['source_uri'])
                report['checks']['fusion_document_versions_verified'] = (
                    threshold.get('source_validation',{}).get('status')=='verified'
                    and bool(threshold.get('source_validation',{}).get('documents')))
                for script, report_name, check in (
                    ('evaluate_live_sql.py','LIVE_SQL_REPORT.json','live_sql_writer_audit'),
                    ('evaluate_fusion_consistency.py','FUSION_CONSISTENCY_REPORT.json','fusion_mid_plan_mutation_audit'),
                ):
                    audit = subprocess.run([sys.executable,str(extracted/'tools'/script)],
                        cwd=extracted,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
                    audit_report = json.loads((extracted/'docs'/report_name).read_text(encoding='utf-8'))
                    report['checks'][check] = audit.returncode==0 and audit_report['passed']==audit_report['total']==5
                service_audit = subprocess.run([sys.executable,str(extracted/'tools/evaluate_service_consistency.py'),'--base-url',base],
                    cwd=extracted,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=90)
                service_report = json.loads((extracted/'docs/SERVICE_CONSISTENCY_REPORT.json').read_text(encoding='utf-8'))
                report['checks']['actual_http_multisql_snapshot'] = service_audit.returncode==0 and service_report['ok']
                with closing(sqlite3.connect(extracted/'ict-track8/data/demo_sales.sqlite')) as connection:
                    minimum, maximum = connection.execute('SELECT MIN(sales_amount),MAX(sales_amount) FROM sales_orders').fetchone()
                extremes = []
                for cue, expected in (('最小值',minimum),('最大值',maximum)):
                    answer = requests.post(base+'/api/v1/omni/query',json={'question':'销售额'+cue},timeout=10).json()
                    result = answer['result']
                    extremes.append(answer['status']=='ok' and list(result['rows'][0].values())[0]==expected)
                report['checks']['sql_extremes'] = all(extremes)
                session = 'checkpoint-clarification'
                question = '2025年华东地区的情况'
                pending = requests.post(base+'/api/v1/omni/query',json={'question':question,'session_id':session},timeout=10).json()
                options = pending['result']['clarification_options']
                report['checks']['distinct_metric_choices'] = (
                    len({item['value'] for item in options})==len(options)
                    and {'customers.customer_id','sales_orders.customer_id'} <= {item['value'] for item in options})
                price = requests.post(base+'/api/v1/omni/clarify',json={
                    'original_question':pending['effective_question'],'session_id':session,
                    'clarification_code':'missing_metric','selected_value':'unit_price'},timeout=10).json()
                with closing(sqlite3.connect(extracted/'ict-track8/data/demo_sales.sqlite')) as connection:
                    average = connection.execute("SELECT AVG(unit_price) FROM sales_orders WHERE region='华东' AND substr(order_date,1,4)='2025'").fetchone()[0]
                report['checks']['clarification_average_price'] = price['status']=='ok' and abs(price['result']['rows'][0]['平均单价']-average)<1e-8
                followup = requests.post(base+'/api/v1/omni/query',json={'question':'那华南呢','session_id':session},timeout=10).json()
                report['checks']['clarification_followup'] = followup['status']=='ok' and '2025年' in followup['effective_question'] and '华南' in followup['effective_question'] and '平均单价' in followup['effective_question']
                question = '销售额趋势'
                pending = requests.post(base+'/api/v1/omni/query',json={'question':question,'session_id':'checkpoint-trend'},timeout=10).json()
                dated = requests.post(base+'/api/v1/omni/clarify',json={
                    'original_question':pending['effective_question'],'session_id':'checkpoint-trend',
                    'clarification_code':pending['result']['clarification_code'],
                    'selected_value':'year','selected_time':'2025'},timeout=10).json()
                trend = requests.post(base+'/api/v1/omni/clarify',json={
                    'original_question':dated['effective_question'],'session_id':'checkpoint-trend',
                    'clarification_code':'missing_time_grain','selected_value':'monthly_trend'},timeout=10).json()
                periods = [row['月份'] for row in trend['result']['rows']]
                report['checks']['trend_time_and_grain'] = dated['status']=='clarification' and trend['status']=='ok' and trend['result']['plan']['dimension_transforms']['order_date']=='month' and periods==sorted(periods)
                outline = subprocess.run([sys.executable,str(extracted/'tools/evaluate_pdf_outline.py')],
                    cwd=extracted,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
                outline_report = json.loads((extracted/'docs/PDF_OUTLINE_REPORT.json').read_text(encoding='utf-8'))
                report['checks']['actual_pdf_outline_gold'] = outline.returncode==0 and outline_report['passed']==outline_report['total']==8
                import base64
                raw = b'\n\n# Manual\n\n\nReturn within 7 days.\n\n\n## Warranty\n\nWarranty is 12 months.'
                uploaded = requests.post(base+'/api/v1/knowledge/ingest',json={
                    'document_id':'smoke-source-lines','title':'Manual','modality':'md','filename':'manual.md',
                    'file_base64':base64.b64encode(raw).decode()},timeout=10)
                detail = requests.get(base+'/api/v1/knowledge/documents/smoke-source-lines',timeout=5).json()
                original = requests.get(base+'/api/v1/knowledge/documents/smoke-source-lines/original',timeout=5)
                warranty = next(chunk for chunk in detail['chunks'] if 'Warranty is' in chunk['text'])
                report['checks']['source_line_locators'] = uploaded.ok and original.content==raw and warranty['source_locator']=='lines:11-11'
                # Mutate only the disposable extracted corpus. A candidate from
                # cached chunks/vectors must not survive changed original bytes.
                asset = extracted/'runtime/knowledge/assets'/detail['asset']
                asset.write_bytes(b'changed in disposable package verification')
                try:
                    broken = requests.get(base+'/api/v1/knowledge/documents/smoke-source-lines',timeout=5)
                    report['checks']['tampered_original_refused'] = broken.status_code==409 and broken.json()['detail']['code']=='evidence_integrity_failed'
                finally:
                    asset.write_bytes(raw)
                restored = requests.get(base+'/api/v1/knowledge/documents/smoke-source-lines/original',timeout=5)
                report['checks']['same_original_restored'] = restored.ok and restored.content==raw
                report['checks']['frontend'] = requests.get(base+'/knowledge.html',timeout=5).status_code==200
                report['checks']['ocr'] = requests.get(base+'/api/v1/documents/ocr/health',timeout=15).json()['ready']
                scan = extracted/'samples/documents/service-scan-upside-down.png'
                scan_raw = scan.read_bytes()
                scan_payload = {'image_base64':base64.b64encode(scan_raw).decode(),'language':'chi_sim+eng'}
                scan_result = requests.post(base+'/api/v1/documents/ocr',json=scan_payload,timeout=90).json()
                report['checks']['ocr_page_orientation'] = (
                    scan_result.get('metadata',{}).get('orientation',{}).get('rotation_ccw_degrees')==180
                    and 'page_orientation_detected' in scan_result.get('warnings',[])
                    and scan_result['text'].startswith('售后响应通知'))
                preview = requests.post(base+'/api/v1/documents/image-enhance',json={
                    'image_base64':scan_payload['image_base64'],'transforms':['rotate_to_upright'],'rotation_degrees':180},timeout=30)
                preview_result = requests.post(base+'/api/v1/documents/ocr',json={
                    'image_base64':preview.json()['image_base64'],'language':'chi_sim+eng'},timeout=90).json() if preview.ok else {}
                report['checks']['ocr_rotation_preview'] = (
                    preview.ok and preview_result.get('metadata',{}).get('orientation',{}).get('rotation_ccw_degrees')==0
                    and scan.read_bytes()==scan_raw)
                orientation_audit = subprocess.run([sys.executable,str(extracted/'tools/evaluate_orientation.py')],
                    cwd=extracted,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=240)
                orientation_report = json.loads((extracted/'docs/ORIENTATION_REPORT.json').read_text(encoding='utf-8'))
                report['checks']['actual_orientation_audit'] = orientation_audit.returncode==0 and orientation_report['passed']==orientation_report['total']==13
                print(json.dumps(report['checks'],ensure_ascii=False),flush=True)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    report['ok'] = all(report['checks'].values())
    package.with_suffix('.smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
