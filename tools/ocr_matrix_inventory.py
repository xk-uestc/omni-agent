"""Record installed packages and external model/source artifacts without runtime secrets."""
from pathlib import Path
import hashlib,json,subprocess,sys,zipfile
BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
def main():
    inventories=[]
    for name,python in [('legacy',Path(sys.executable)),('rapid',BASE/'rapid-new-env/Scripts/python.exe'),
                        ('paddle',BASE/'paddle-env/Scripts/python.exe'),('mineru',BASE/'mineru-env/Scripts/python.exe'),
                        ('easyocr-doctr',BASE/'torch-ocr-env/Scripts/python.exe')]:
        result=subprocess.run([str(python),'-m','pip','list','--format=json','--disable-pip-version-check'],
            capture_output=True,text=True,encoding='utf8',check=True)
        names={'rapidocr','rapidocr-onnxruntime','paddleocr','paddlepaddle','mineru','easyocr','python-doctr',
            'torch','torchvision','onnxruntime','numpy','opencv-python','opencv-python-headless'}
        inventories.append(dict(environment=name,python=str(python),packages=[r for r in json.loads(result.stdout) if r['name'].lower() in names]))
    archives=[]
    for path in sorted(BASE.glob('*source.zip')):
        if not zipfile.is_zipfile(path):raise ValueError('Not valid source archive '+str(path))
        archives.append(dict(path=str(path),bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    models=[]
    for folder in ['easyocr-models','doctr-cache','mineru-home','paddle-cache','tesseract/tessdata']:
        root=BASE/folder
        files=[p for p in root.rglob('*') if p.is_file()]
        models.append(dict(root=str(root),files=len(files),bytes=sum(p.stat().st_size for p in files)))
    data=dict(repositories={
        'EasyOCR':'https://github.com/JaidedAI/EasyOCR',
        'docTR':'https://github.com/mindee/doctr',
        'Tesseract':'https://github.com/tesseract-ocr/tesseract',
        'Tesseract_Windows_distribution':'https://github.com/UB-Mannheim/tesseract',
        'RapidOCR':'https://github.com/RapidAI/RapidOCR',
        'PaddleOCR':'https://github.com/PaddlePaddle/PaddleOCR',
        'MinerU':'https://github.com/opendatalab/MinerU'},
        environments=inventories,source_archives=archives,model_folders=models,
        tesseract_distribution='5.4.0.20240606; copied from installer default Program Files destination to external D drive test directory',
        tesseract_language='chi_sim+eng, tessdata_fast chi_sim; default psm3 and additional psm1 orientation configuration',
        doctr_configuration='db_resnet50+crnn_vgg16_bn; French vocabulary, Chinese not covered; orientation+straightening enabled',
        easyocr_configuration='Chinese+English baseline, English-only additional configuration; CPU, 4 PyTorch threads')
    output=BASE/'candidate-inventory.json';output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');print(output)
if __name__=='__main__':main()
