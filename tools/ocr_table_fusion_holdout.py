"""Untuned transfer check: two previously unused document sources, two views each."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys

import fitz
from PIL import Image, ImageFilter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ict-track8"))
from backend.ocr_region_fusion import amount_key


def main():
    root=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates\table-fusion-transfer-input-20261005")
    root.mkdir(exist_ok=False)
    (root/"samples").mkdir()
    base=Path(r"D:\ICT8-OfficialDatasets\ohr-bench\pdfs\finance")
    cases=[]
    for name,pdf_name,index in [("options-table","DUDE_073c5e237cfc190effc8ff2a3b0f7fe5.pdf",5),
                               ("motor-balance","DUDE_5eff9d45fd14e5a2c5aaff30d08acd43.pdf",10)]:
        path=base/pdf_name
        with fitz.open(path) as pdf:
            page=pdf[index]
            pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
            image=Image.frombytes("RGB",[pix.width,pix.height],pix.samples)
            cells=[{"text":w[4],"bbox":[v*1.5 for v in w[:4]]} for w in page.get_text("words")
                   if amount_key(w[4]) is not None and any(c in w[4] for c in ",.$()-")]
            for variant in ("clean","degraded"):
                out=image
                scale=1.
                if variant=="degraded":
                    scale=.62
                    out=image.resize((round(image.width*scale),round(image.height*scale))).filter(ImageFilter.GaussianBlur(.45))
                    buffer=BytesIO();out.save(buffer,format="JPEG",quality=55);buffer.seek(0)
                    out=Image.open(buffer).convert("RGB")
                output=root/"samples"/(name+"-"+variant+".png")
                out.save(output)
                cases.append({"name":name+"-"+variant,"path":str(output),"sha256":hashlib.sha256(output.read_bytes()).hexdigest(),
                    "size_px":list(out.size),"numeric_cells":[{**c,"bbox":[v*scale for v in c["bbox"]]} for c in cells],"fields":[],
                    "provenance":{"type":"public_native_pdf_render_not_real_scan","pdf":str(path),"pdf_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                        "page":index+1,"reference":"native_PDF_word_text_and_boxes","transfer_check":"new_document_sources_after_routing_frozen",
                        "degradation":"none" if variant=="clean" else "resize_0.62_blur_0.45_JPEG55"}})
    (root/"manifest.json").write_text(json.dumps({"not_official_benchmark":True,"cases":cases},ensure_ascii=False,indent=2),encoding="utf-8")
    print(root)
    print("numeric cells",sum(len(c["numeric_cells"]) for c in cases))


if __name__=="__main__":main()
