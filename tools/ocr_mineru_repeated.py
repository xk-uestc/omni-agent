"""Sequential warmed-model MinerU basic trials with saved raw structures."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
import argparse
import hashlib

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--tier',choices=['basic','standard'],default='basic')
    parser.add_argument('--rounds',type=int,choices=[1,2],default=2)
    parser.add_argument('--sample',default=None)
    args=parser.parse_args()
    root=BASE/(f'mineru-{args.tier}-expanded-'+uuid.uuid4().hex);root.mkdir()
    env=dict(os.environ,MINERU_HOME=str(BASE/'mineru-home'),HF_HOME=str(BASE/'hf-cache'))
    records=[]
    for round_no in range(1,args.rounds+1):
        for name in ([args.sample] if args.sample else ['budget-5','budget-90','official-chinese','public-second']):
            source=BASE/'samples'/f'{name}.png'
            output=root/f'round{round_no}'/name
            start=time.perf_counter()
            try:
                result=subprocess.run([str(BASE/'mineru-env/Scripts/mineru-kit.exe'),'parse',str(source),
                    '--tier',args.tier,'--ocr-mode','ocr','--format','middle_json','--output',str(output)],
                    env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf8',errors='replace',timeout=240)
                status=result.returncode;stdout=result.stdout
            except subprocess.TimeoutExpired as exc:
                status=-1;stdout=exc.stdout or ''
                if isinstance(stdout,bytes):stdout=stdout.decode('utf8',errors='replace')
                stdout+='\nTIMEOUT: 240 seconds'
            log=root/f'round{round_no}-{name}.log';log.write_text(stdout,encoding='utf8')
            record={'image':source.name,'round':round_no,'seconds':round(time.perf_counter()-start,3),
                'exit_code':status,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                'outputs':[str(path) for path in output.glob('*.json')],'log':str(log)}
            records.append(record)
            (root/'report.json').write_text(json.dumps({'not_official_benchmark':True,'provider':'mineru-'+args.tier,
                'records':records},indent=2),encoding='utf8')
            print(json.dumps(record),flush=True)
    print(root)


if __name__=='__main__':main()
