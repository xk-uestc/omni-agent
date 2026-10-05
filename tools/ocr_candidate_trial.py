"""Isolated OCR trials: exact same source images, persistent observations."""
import argparse
import hashlib
import json
import time
from pathlib import Path
import fitz
from PIL import Image

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')


def prepare():
    directory=BASE/'samples';directory.mkdir(parents=True,exist_ok=True)
    with fitz.open(SOURCE) as pdf:
        pix=pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
        pix.save(directory/'budget-0.png')
    with Image.open(directory/'budget-0.png') as image:
        for angle in [5,90]:image.rotate(angle,expand=True,fillcolor='white').save(directory/f'budget-{angle}.png')
    for path in sorted(directory.glob('budget-*.png')):
        with Image.open(path) as image,fitz.open() as pdf:
            page=pdf.new_page(width=image.width/1.5,height=image.height/1.5)
            page.insert_image(page.rect,filename=str(path))
            assert not page.get_text().strip()
            pdf.save(path.with_suffix('.pdf'))
    return directory


def main():
    parser=argparse.ArgumentParser();parser.add_argument('provider',choices=['prepare','paddle','paddle-oriented','rapid','legacy'])
    parser.add_argument('--expanded',action='store_true');parser.add_argument('--rounds',type=int,default=1)
    args=parser.parse_args()
    if not 1<=args.rounds<=3:raise ValueError('rounds must be 1..3')
    directory=BASE/'samples'
    if args.provider=='prepare':print(prepare());return
    if not all((directory/f'budget-{angle}.png').is_file() for angle in [0,5,90]):
        raise RuntimeError('Run prepare once before trials; samples must stay identical across engines')
    started=time.perf_counter()
    if args.provider.startswith('paddle'):
        from paddleocr import PaddleOCR
        engine=PaddleOCR(device='cpu',use_doc_orientation_classify=args.provider=='paddle-oriented',use_doc_unwarping=False,
                        use_textline_orientation=args.provider=='paddle-oriented',enable_mkldnn=False)
    elif args.provider=='rapid':
        from rapidocr import RapidOCR
        engine=RapidOCR()
    else:
        from rapidocr_onnxruntime import RapidOCR
        engine=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    initialization=time.perf_counter()-started
    records=[]
    import uuid
    output=BASE/(f'{args.provider}-expanded-{uuid.uuid4().hex}.json' if args.expanded else f'{args.provider}-trial.json')
    samples=sorted(directory.glob('budget-*.png'))
    if args.expanded:samples=[directory/'budget-5.png',directory/'budget-90.png',directory/'official-chinese.png',directory/'public-second.png']
    for round_no,path in [(r,p) for r in range(1,args.rounds+1) for p in samples]:
        started=time.perf_counter()
        if args.provider.startswith('paddle'):
            results=list(engine.predict(str(path)))
            texts=[text for result in results for text in result['rec_texts']]
            for index,result in enumerate(results):result.save_to_json(str(BASE/f'{args.provider}-{path.stem}-round{round_no}-{index}.json'))
        elif args.provider=='rapid':
            result=engine(str(path));texts=list(result.txts) if result.txts is not None else []
        else:
            result,_=engine(str(path));texts=[row[1] for row in result or []]
        text='\n'.join(texts)
        record={'image':path.name,'round':round_no,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'seconds':round(time.perf_counter()-started,3),'text':text,
                'budget_heading':'budget' in text.casefold(),'total_amount':'100,000' in text,
                'amount_8000':'8,000' in text,'amount_2000_count':text.count('2,000'),
                'diapers_label':'diapers' in text.casefold()}
        records.append(record)
        output.write_text(json.dumps({'provider':args.provider,'rounds_requested':args.rounds,'initialization_seconds':initialization,
            'not_official_benchmark':True,'records':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in record.items() if k!='text'}),flush=True)
    print(output)


if __name__=='__main__':main()
