"""Persistent isolated reader; JSON-lines responses, diagnostics on stderr."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sys
import time

BASE=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def box(poly):
    return [min(p[0] for p in poly),min(p[1] for p in poly),max(p[0] for p in poly),max(p[1] for p in poly)]


def main():
    provider=sys.argv[1]
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"]="True"
    os.environ["PADDLE_PDX_CACHE_HOME"]=str(BASE/"paddle-cache")
    start=time.perf_counter()
    with contextlib.redirect_stdout(sys.stderr):
        if provider=="rapid":
            from rapidocr import RapidOCR
            engine=RapidOCR()
        else:
            from paddleocr import TextRecognition
            engine=TextRecognition(model_name="PP-OCRv6_medium_rec",model_dir=str(BASE/"paddle-cache/official_models/PP-OCRv6_medium_rec"),
                                   device="cpu",enable_mkldnn=False,cpu_threads=4)
            if provider=="paddle-gap":
                from paddleocr import TextDetection
                detector=TextDetection(model_name="PP-OCRv6_medium_det",model_dir=str(BASE/"paddle-cache/official_models/PP-OCRv6_medium_det"),
                                       device="cpu",enable_mkldnn=False,cpu_threads=4)
    print(json.dumps({"kind":"ready","initialization_seconds":time.perf_counter()-start}),flush=True)
    for line in sys.stdin:
        if not line.strip():continue
        req=json.loads(line);start=time.perf_counter();records=[]
        if req["mode"]=="line":
            try:
                items=req["requests"]
                for item in items:
                    if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()!=item["sha256"]:raise ValueError("source_changed")
                with contextlib.redirect_stdout(sys.stderr):
                    if provider=="rapid":
                        import cv2
                        from rapidocr.ch_ppocr_rec import TextRecInput
                        images=[cv2.imread(item["path"]) for item in items]
                        if any(im is None for im in images):raise ValueError("crop_load_failed")
                        result=engine.text_rec(TextRecInput(img=images,return_word_box=False))
                        values=list(zip(result.txts,result.scores))
                    else:
                        result=engine.predict([item["path"] for item in items],batch_size=8)
                        by_path={str(Path(r["input_path"])):r for r in result}
                        values=[(by_path[str(Path(item["path"]))]["rec_text"],by_path[str(Path(item["path"]))]["rec_score"]) for item in items]
                if len(values)!=len(items):raise ValueError("batch_result_count_mismatch")
                records=[{"id":item["id"],"sha256":item["sha256"],"error":None,"text":str(v[0]),"confidence":float(v[1]),
                          "timing_scope":"shared_batch_only"} for item,v in zip(items,values)]
            except Exception as e:
                records=[{"id":item["id"],"sha256":item["sha256"],"error":str(e)} for item in req["requests"]]
            print(json.dumps({"kind":"result","records":records,"seconds":time.perf_counter()-start,"batched":True}),flush=True)
            continue
        for item in req["requests"]:
            t=time.perf_counter();record={"id":item["id"],"sha256":item["sha256"],"error":None}
            try:
                if hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest()!=item["sha256"]:raise ValueError("source_changed")
                with contextlib.redirect_stdout(sys.stderr):
                    if req["mode"]=="detect":
                        if provider!="paddle-gap":raise ValueError("detector_not_loaded")
                        r=detector.predict(item["path"],limit_side_len=640,limit_type="max")[0]
                        record["nodes"]=[dict(polygon=p.tolist(),bbox=box(p.tolist()),confidence=float(s)) for p,s in zip(r["dt_polys"],r["dt_scores"])]
                    elif provider=="rapid":
                        page=req["mode"]=="page"
                        r=engine(item["path"],use_det=page,use_cls=True,use_rec=True,return_word_box=page,return_single_char_box=page)
                        texts=list(r.txts) if r.txts is not None else []
                        scores=list(r.scores) if r.scores is not None else []
                        record.update(text=" ".join(texts),confidence=float(min(scores)) if scores else 0.)
                        if page:
                            nodes=[];word_lines=r.word_results or []
                            for i,(text,score,poly) in enumerate(zip(texts,scores,r.boxes if r.boxes is not None else [])):
                                poly=poly.tolist() if hasattr(poly,"tolist") else poly;words=[]
                                for wt,ws,wp in (word_lines[i] if i<len(word_lines) else []) or []:
                                    words.append({"text":wt,"confidence":float(ws),"bbox":box(wp.tolist() if hasattr(wp,"tolist") else wp)})
                                nodes.append({"text":text,"confidence":float(score),"polygon":poly,"bbox":box(poly),"words":words})
                            record["nodes"]=nodes
                    else:
                        r=engine.predict(item["path"])[0]
                        record.update(text=str(r["rec_text"]),confidence=float(r["rec_score"]))
            except Exception as e:record["error"]=str(e)
            record["seconds"]=time.perf_counter()-t;records.append(record)
        print(json.dumps({"kind":"result","records":records,"seconds":time.perf_counter()-start}),flush=True)


if __name__=="__main__":main()
