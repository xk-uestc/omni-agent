"""Run one isolated OCR provider; preserve text, geometry, timings and failures."""
import argparse,csv,hashlib,io,json,os,subprocess,time,uuid,threading,traceback
from pathlib import Path
from html import unescape
import re
import psutil
from PIL import Image

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
PROVIDERS=['legacy','rapid','paddle','easyocr','easyocr-english','easyocr-english-fp32','doctr','tesseract','tesseract-osd','mineru-basic','mineru-standard']

def memory_mb():
    process=psutil.Process();total=process.memory_info().rss
    for child in process.children(recursive=True):
        try:total+=child.memory_info().rss
        except psutil.Error:pass
    return total/1024**2

def bbox(points):
    return [float(min(p[0] for p in points)),float(min(p[1] for p in points)),float(max(p[0] for p in points)),float(max(p[1] for p in points))]

def bounded_command(command,env,timeout,log_path=None):
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
        text=True,encoding='utf8',errors='replace',env=env)
    try:
        stdout,_=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Windows executable launchers may have Python descendants; kill only this test's process tree.
        try:
            children=psutil.Process(process.pid).children(recursive=True)
            for child in reversed(children):
                try:child.kill()
                except psutil.Error:pass
        except psutil.Error:pass
        process.kill();stdout,_=process.communicate()
        if log_path:log_path.write_text(stdout+'\nBENCHMARK_TIMEOUT',encoding='utf8')
        raise TimeoutError(f'Local OCR test exceeded {timeout} seconds; owned subprocess tree stopped')
    if log_path:log_path.write_text(stdout,encoding='utf8')
    return subprocess.CompletedProcess(command,process.returncode,stdout)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('provider',choices=PROVIDERS)
    parser.add_argument('--names',nargs='*');parser.add_argument('--rounds',type=int,default=2)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();provider=args.provider
    root=BASE/('matrix-'+provider+'-'+uuid.uuid4().hex);root.mkdir()
    cases=json.loads((BASE/'matrix-samples/manifest.json').read_text(encoding='utf8'))['cases']
    if args.names:cases=[c for c in cases if c['name'] in args.names]
    records=[];start=time.perf_counter();engine=None
    os.environ['TORCH_HOME']=str(BASE/'torch-cache')
    os.environ['DOCTR_CACHE_DIR']=str(BASE/'doctr-cache')
    if provider.startswith('easyocr'):
        import torch,easyocr
        torch.set_num_threads(4)
        engine=easyocr.Reader(['en'] if provider.startswith('easyocr-english') else ['ch_sim','en'],gpu=False,
            model_storage_directory=str(BASE/'easyocr-models'),verbose=False,quantize=provider!='easyocr-english-fp32')
    elif provider=='doctr':
        import torch
        from doctr.models import ocr_predictor
        torch.set_num_threads(4)
        engine=ocr_predictor(det_arch='db_resnet50',reco_arch='crnn_vgg16_bn',pretrained=True,
            assume_straight_pages=False,detect_orientation=True,straighten_pages=True)
    elif provider=='legacy':
        from rapidocr_onnxruntime import RapidOCR
        engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    elif provider=='rapid':
        from rapidocr import RapidOCR
        engine=RapidOCR()
    elif provider=='paddle':
        from paddleocr import PaddleOCR
        engine=PaddleOCR(device='cpu',use_doc_orientation_classify=True,use_textline_orientation=True,
            use_doc_unwarping=False,enable_mkldnn=False)
    init=time.perf_counter()-start
    if args.prepare_only:
        (root/'initialization.json').write_text(json.dumps(dict(provider=provider,seconds=init)),encoding='utf8')
        print('Prepared '+provider,flush=True);return
    for round_no in range(1,args.rounds+1):
        for case in cases:
            path=Path(case['path']);nodes=[];text='';raw_path=None;error=None;peak=[memory_mb()];stop=threading.Event()
            def monitor():
                while not stop.wait(.15):
                    try:peak[0]=max(peak[0],memory_mb())
                    except psutil.Error:pass
            watcher=threading.Thread(target=monitor,daemon=True);watcher.start();start=time.perf_counter()
            try:
                if hashlib.sha256(path.read_bytes()).hexdigest()!=case['sha256']:raise ValueError('input hash changed')
                if provider.startswith('easyocr'):
                    result=engine.readtext(str(path),detail=1,paragraph=False)
                    nodes=[dict(text=row[1],bbox=bbox(row[0]),confidence=float(row[2])) for row in result]
                    text='\n'.join(n['text'] for n in nodes)
                elif provider=='doctr':
                    from doctr.io import DocumentFile
                    result=engine(DocumentFile.from_images(str(path)))
                    export=result.export();raw_path=root/f'{case["name"]}-round{round_no}.json'
                    raw_path.write_text(json.dumps(export,ensure_ascii=False,default=str),encoding='utf8')
                    w,h=case['width'],case['height']
                    for page in export['pages']:
                        for block in page['blocks']:
                            for line in block['lines']:
                                points=line['geometry'];box=bbox(points)
                                nodes.append(dict(text=' '.join(word['value'] for word in line['words']),
                                    bbox=[box[0]*w,box[1]*h,box[2]*w,box[3]*h]))
                    text='\n'.join(n['text'] for n in nodes)
                elif provider.startswith('tesseract'):
                    executable=BASE/'tesseract/tesseract.exe'
                    result=subprocess.run([str(executable),str(path),'stdout','--tessdata-dir',str(BASE/'tesseract/tessdata'),
                        '-l','chi_sim+eng','--psm','1' if provider=='tesseract-osd' else '3','tsv'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        text=True,encoding='utf8',errors='replace',timeout=180,env=dict(os.environ,OMP_THREAD_LIMIT='4'))
                    if result.returncode:raise RuntimeError(result.stderr[:1500])
                    lines={}
                    for word in csv.DictReader(io.StringIO(result.stdout),delimiter='\t'):
                        if not word['text'].strip():continue
                        key=tuple(word[k] for k in ['page_num','block_num','par_num','line_num'])
                        item=lines.setdefault(key,dict(words=[],boxes=[]))
                        x,y,ww,hh=[float(word[k]) for k in ['left','top','width','height']]
                        item['words'].append(word['text']);item['boxes'] += [(x,y),(x+ww,y+hh)]
                    nodes=[dict(text=' '.join(item['words']),bbox=bbox(item['boxes'])) for item in lines.values()]
                    text='\n'.join(n['text'] for n in nodes)
                elif provider.startswith('mineru'):
                    out=root/f'round{round_no}'/case['name'];tier=provider.split('-')[1]
                    env=dict(os.environ,MINERU_HOME=str(BASE/'mineru-home'),HF_HOME=str(BASE/'hf-cache'))
                    result=bounded_command([str(BASE/'mineru-env/Scripts/mineru-kit.exe'),'parse',str(path),
                        '--tier',tier,'--ocr-mode','ocr','--format','middle_json','--output',str(out)],
                        timeout=240,env=env,log_path=root/f'{case["name"]}-round{round_no}.log')
                    if result.returncode:raise RuntimeError(result.stdout[-1800:])
                    raw_path=next(out.glob('*.json'));data=json.loads(raw_path.read_text(encoding='utf8'))
                    from score_ocr_candidates import strings
                    text='\n'.join(unescape(re.sub('<[^>]+>',' ',s)) for s in strings(data))
                    for page in data.get('pages',[]):
                        for block in page.get('blocks',[]):
                            box=block.get('bbox');s=' '.join(strings(block))
                            if box and s:nodes.append(dict(text=unescape(re.sub('<[^>]+>',' ',s)),kind=block.get('type'),
                                bbox=[box[0]*case['width'],box[1]*case['height'],box[2]*case['width'],box[3]*case['height']]))
                elif provider=='paddle':
                    for i,result in enumerate(engine.predict(str(path))):
                        raw_path=root/f'{case["name"]}-round{round_no}-{i}.json';result.save_to_json(str(raw_path))
                        for s,poly,conf in zip(result['rec_texts'],result['rec_polys'],result['rec_scores']):
                            nodes.append(dict(text=s,bbox=bbox(poly.tolist()),confidence=float(conf)))
                    text='\n'.join(n['text'] for n in nodes)
                elif provider=='rapid':
                    result=engine(str(path))
                    if result.txts is not None:
                        nodes=[dict(text=s,bbox=bbox(poly.tolist()),confidence=float(conf)) for s,poly,conf in zip(result.txts,result.boxes,result.scores)]
                    text='\n'.join(n['text'] for n in nodes)
                else:
                    result,_=engine(str(path))
                    nodes=[dict(text=row[1],bbox=bbox(row[0]),confidence=float(row[2])) for row in result or []]
                    text='\n'.join(n['text'] for n in nodes)
            except Exception:
                error=traceback.format_exc(limit=4)
            finally:
                elapsed=time.perf_counter()-start;stop.set();watcher.join(timeout=1)
            record=dict(provider=provider,name=case['name'],round=round_no,sha256=case['sha256'],
                seconds=round(elapsed,3),peak_rss_mb=round(peak[0],1),text=text,nodes=nodes,
                raw_path=str(raw_path) if raw_path else None,error=error)
            records.append(record)
            (root/'report.json').write_text(json.dumps(dict(provider=provider,initialization_seconds=round(init,3),
                requested_names=[c['name'] for c in cases],rounds_requested=args.rounds,
                not_official_benchmark=True,records=records),ensure_ascii=False,indent=2),encoding='utf8')
            print(json.dumps({k:v for k,v in record.items() if k not in ['text','nodes']},ensure_ascii=False),flush=True)
    print(root)

if __name__=='__main__':main()
