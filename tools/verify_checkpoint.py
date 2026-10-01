"""Extract a verified checkpoint into a fresh, disposable project and run its API.

No credential is copied and no remote model is called. Report lives next to ZIP.
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from scripts.verify_package import verify_package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    args = parser.parse_args()
    package = args.package.resolve()
    verified = verify_package(package)
    if not verified['ok']:
        raise ValueError('压缩包校验失败，不能执行')
    report = {'package_sha256':hashlib.sha256(package.read_bytes()).hexdigest(),
              'manifest_verified':True, 'real_model_test':'not_run', 'checks':{}}
    (ROOT/'runtime').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='package-smoke-', dir=ROOT/'runtime') as directory:
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
                documents = requests.get(base+'/api/v1/knowledge/documents',timeout=5).json()['documents']
                report['checks']['documents'] = len(documents)==15
                answer = requests.post(base+'/api/v1/knowledge/query',json={'question':'标准硬件产品保修期有多久？'},timeout=60).json()
                report['checks']['rag'] = '12个月' in answer['answer'] and bool(answer['citations'])
                report['checks']['dense'] = answer['retrieval']['mode']=='bm25_dense_rrf'
                sql = requests.post(base+'/api/v1/omni/query',json={'question':'2025年华东地区销售额'},timeout=10).json()
                report['checks']['sql'] = sql['result']['rows'][0]['销售额']==29584
                report['checks']['frontend'] = requests.get(base+'/knowledge.html',timeout=5).status_code==200
                report['checks']['ocr'] = requests.get(base+'/api/v1/documents/ocr/health',timeout=15).json()['ready']
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
