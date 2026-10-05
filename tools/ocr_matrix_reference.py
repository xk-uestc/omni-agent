"""Independent public PDF text-layer reference; OCR always receives raster pixels."""
from pathlib import Path
import hashlib,json
import fitz
BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')
def main():
    output=BASE/'matrix-samples/budget-reference.json'
    if output.exists():print(output);return
    with fitz.open(SOURCE) as doc:text=doc[0].get_text()
    output.write_text(json.dumps(dict(source=str(SOURCE),sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        page=1,reference_text=text,scope='text_layer_reference_for_simple_budget_page_manually_inspected_not_official_OCR_annotation'),
        ensure_ascii=False,indent=2),encoding='utf8')
    print(output)
if __name__=='__main__':main()
