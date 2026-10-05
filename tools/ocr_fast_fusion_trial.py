"""Fresh persistent Rapid + crop-only Paddle, including real page wall timing."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ict-track8"))
from backend.ocr_fast_fusion import plan_fast_reviews, decide_fast_review, apply_fast_reviews, refine_vertical_regions, retry_atomic_regions, crop_node
from backend.ocr_region_fusion import rectify_region
from backend.image_geometry import IDENTITY
from backend import ocr_table_fusion as policy
from backend.ocr_gap_fusion import plan_detection_gaps, expert_gap_requests, merge_confirmed_gap_nodes, decide_detected_number
from ocr_fusion_validation import score, route_map

BASE=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def checked_records(response, requests):
    records=response.get("records",[])
    if len(records)!=len(requests):raise RuntimeError("reader_result_count_mismatch")
    for record,request in zip(records,requests):
        if record.get("id")!=request["id"] or record.get("sha256")!=request["sha256"]:
            raise RuntimeError("reader_result_identity_mismatch")
        if record.get("error"):raise RuntimeError("reader_failed: "+str(record["error"]))
    return records


class Reader:
    def __init__(self,name,root):
        self.log=(root/(name+"-persistent.log")).open("w",encoding="utf-8")
        env=BASE/("rapid-new-env" if name=="rapid" else "paddle-env")/"Scripts/python.exe"
        self.proc=subprocess.Popen([str(env),str(Path(__file__).with_name("ocr_fast_fusion_worker.py")),name],
                                   stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,encoding="utf-8")
        self.queue=queue.Queue()
        def consume():
            for line in self.proc.stdout:self.queue.put(line)
            self.queue.put(None)
        threading.Thread(target=consume,daemon=True).start()
        self.ready=self.receive("ready")
    def receive(self,kind):
        while True:
            try:line=self.queue.get(timeout=180)
            except queue.Empty:
                self.close();raise TimeoutError("reader_timeout")
            if line is None:raise RuntimeError("reader_exited; inspect worker log")
            try:data=json.loads(line)
            except ValueError:
                self.log.write(line);continue
            if data.get("kind")==kind:return data
    def call(self,mode,requests):
        if not requests:return {"records":[],"seconds":0.}
        self.proc.stdin.write(json.dumps({"mode":mode,"requests":requests})+"\n");self.proc.stdin.flush()
        result=self.receive("result")
        checked_records(result,requests)
        if mode in {"page","detect"} and any(not isinstance(r.get("nodes"),list) for r in result["records"]):
            raise RuntimeError("reader_missing_detected_nodes")
        return result
    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.log.close()


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--source",type=Path,required=True);ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--split",default="all",choices=["dev","holdout","all"])
    ap.add_argument("--gap-detection",action="store_true")
    ap.add_argument("--gap-policy",choices=["auto","always"],default="auto")
    args=ap.parse_args();root=args.output;root.mkdir(parents=True,exist_ok=False);(root/"crops").mkdir()
    cases=json.loads((args.source/"manifest.json").read_text(encoding="utf-8"))["cases"]
    cases=[c for c in cases if args.split=="all" or c["split"]==args.split]
    source_files=[Path(__file__),Path(__file__).with_name("ocr_fast_fusion_worker.py"),Path(__file__).parents[1]/"ict-track8/backend/ocr_fast_fusion.py",
                  Path(policy.__file__),Path(__file__).parents[1]/"ict-track8/backend/ocr_region_fusion.py",
                  Path(__file__).parents[1]/"ict-track8/backend/image_geometry.py",Path(__file__).with_name("ocr_fusion_validation.py"),
                  Path(__file__).parents[1]/"ict-track8/backend/ocr_gap_fusion.py"]
    hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    (root/"source-snapshot").mkdir()
    for p in source_files:(root/"source-snapshot"/p.name).write_bytes(p.read_bytes())
    (root/"freeze.json").write_text(json.dumps({"at_unix":time.time(),"implementation_sha256":hashes,
        "manifest_sha256":hashlib.sha256((args.source/"manifest.json").read_bytes()).hexdigest(),"gap_detection":args.gap_detection,"gap_policy":args.gap_policy,
        "models":{"rapid":"RapidOCR 3.9.2 / PP-OCRv6 small ONNX","paddle":"PP-OCRv6_medium_rec / CPU 4 threads / MKLDNN off",
                  "local_detector":"PP-OCRv6_medium_det / max side 640 / CPU 4 threads / MKLDNN off" if args.gap_detection else None}},indent=2),encoding="utf-8")
    rapid=paddle=None;results=[];startup=time.perf_counter()
    try:
        rapid=Reader("rapid",root);paddle=Reader("paddle-gap" if args.gap_detection else "paddle",root)
        initialization_wall=time.perf_counter()-startup
        with ThreadPoolExecutor(max_workers=2) as pool:
            for c in cases:
                started=time.perf_counter();cid=c["id"];locale="id-ID" if c["dataset"]=="CORD-v2" else None
                req={k:c[k] for k in ("id","path","sha256")}
                initial=rapid.call("page",[req])["records"][0]
                if initial.get("error"):raise RuntimeError(initial["error"])
                image=Image.open(c["path"]).convert("RGB")
                first=initial["nodes"];plan=refine_vertical_regions(image,first,plan_fast_reviews(first,page_size=image.size));crop_requests=[];prepared=[]
                for i,r in enumerate(plan["requests"]):
                    crop,geometry=rectify_region(image,crop_node(r),working_to_source=IDENTITY,source_sha256=c["sha256"])
                    path=root/"crops"/f"{cid}-{i}.png";crop.save(path)
                    cr={"id":str(i),"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                    crop_requests.append(cr);prepared.append({"request":r,"crop":cr,"source_geometry":geometry})
                rf=pool.submit(rapid.call,"line",crop_requests);pf=pool.submit(paddle.call,"line",crop_requests)
                rr=rf.result();pr=pf.result();reviews=[]
                for prep,r,p in zip(prepared,rr["records"],pr["records"]):
                    d=decide_fast_review(prep["request"],r,p,crop_sha256=prep["crop"]["sha256"],number_locale=locale)
                    reviews.append(dict(prep,rapid=r,paddle=p,decision=d))
                retries=retry_atomic_regions(first,reviews)
                retry_prepared=[];retry_requests=[]
                for i,r in enumerate(retries):
                    crop,geometry=rectify_region(image,crop_node(r),working_to_source=IDENTITY,source_sha256=c["sha256"])
                    path=root/"crops"/f"{cid}-retry-{i}.png";crop.save(path)
                    cr={"id":f"retry-{i}","path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
                    retry_requests.append(cr);retry_prepared.append({"request":r,"crop":cr,"source_geometry":geometry})
                if retries:
                    rf=pool.submit(rapid.call,"line",retry_requests);pf=pool.submit(paddle.call,"line",retry_requests)
                    rr2=rf.result();pr2=pf.result()
                    rr["seconds"]+=rr2["seconds"];pr["seconds"]+=pr2["seconds"]
                    for prep,r,p in zip(retry_prepared,rr2["records"],pr2["records"]):
                        d=decide_fast_review(prep["request"],r,p,crop_sha256=prep["crop"]["sha256"],number_locale=locale)
                        reviews.append(dict(prep,rapid=r,paddle=p,decision=d))
                final=apply_fast_reviews(first,reviews)
                without_gap=final;without_gap_wall=time.perf_counter()-started
                gap_plan={"regions":[],"area_fraction":0};gap_detections=[];gap_reviews=[];det_seconds=0.
                if args.gap_detection:
                    gap_plan=plan_detection_gaps(image,first,automatic=args.gap_policy=="auto")
                    windows=[]
                    for i,region in enumerate(gap_plan["regions"]):
                        path=root/"crops"/f"{cid}-detection-{i}.png";image.crop(region["bbox"]).save(path)
                        windows.append(dict(id=f"detect-{i}",path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
                    detected=paddle.call("detect",windows);det_seconds=detected["seconds"]
                    for region,record in zip(gap_plan["regions"],detected["records"]):
                        x,y=region["bbox"][:2]
                        for node in record["nodes"]:
                            poly=[[p[0]+x,p[1]+y] for p in node["polygon"]]
                            gap_detections.append(dict(node,polygon=poly,bbox=[min(p[0] for p in poly),min(p[1] for p in poly),max(p[0] for p in poly),max(p[1] for p in poly)],
                                source_sha256=c["sha256"],detection_crop_sha256=record["sha256"]))
                    gap_requests=expert_gap_requests(gap_detections,final)
                    prepared_gap=[];to_read=[]
                    for i,r in enumerate(gap_requests):
                        crop,geometry=rectify_region(image,crop_node(r),working_to_source=IDENTITY,source_sha256=c["sha256"])
                        path=root/"crops"/f"{cid}-gap-{i}.png";crop.save(path)
                        cr=dict(id=f"gap-{i}",path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                        to_read.append(cr);prepared_gap.append(dict(request=r,crop=cr,source_geometry=geometry))
                    rf=pool.submit(rapid.call,"line",to_read);pf=pool.submit(paddle.call,"line",to_read)
                    rg=rf.result();pg=pf.result();rr["seconds"]+=rg["seconds"];pr["seconds"]+=pg["seconds"]
                    for prep,r,p in zip(prepared_gap,rg["records"],pg["records"]):
                        d=decide_detected_number(prep["request"],r,p,crop_sha256=prep["crop"]["sha256"],number_locale=locale)
                        gap_reviews.append(dict(prep,rapid=r,paddle=p,decision=d))
                    final=merge_confirmed_gap_nodes(final,apply_fast_reviews([],gap_reviews))
                # Timer includes first OCR, planning, actual crop I/O, both
                # readers, IPC and fusion. Reference scoring happens afterward.
                wall=time.perf_counter()-started
                scores={"rapid":score(c["numeric_cells"],first,policy,locale),"fast":score(c["numeric_cells"],final,policy,locale),
                        "without_gap":score(c["numeric_cells"],without_gap,policy,locale)}
                before=set(scores["rapid"]["value"]["matched_reference_indices"]);after=set(scores["fast"]["value"]["matched_reference_indices"])
                results.append(dict(c,size_px=list(image.size),nodes=final,initial=first,reviews=reviews+gap_reviews,scores=scores,
                    gap_plan=gap_plan,gap_detections=gap_detections,gap_review_count=len(gap_reviews),
                    timing={"warm_page_wall_seconds":wall,"rapid_inference_seconds":initial["seconds"],"rapid_crop_seconds":rr["seconds"],"paddle_crop_seconds":pr["seconds"],
                            "without_gap_wall_seconds":without_gap_wall,"local_detection_seconds":det_seconds},
                    recovered=sorted(after-before),regressed=sorted(before-after),crop_count=len(crop_requests)+len(retry_requests)+len(gap_reviews),deferred=plan["deferred"]))
                summary={"expected":sum(len(x["numeric_cells"]) for x in results),"rapid":sum(x["scores"]["rapid"]["value"]["correct"] for x in results),
                         "fast":sum(x["scores"]["fast"]["value"]["correct"] for x in results),"wall_seconds":sum(x["timing"]["warm_page_wall_seconds"] for x in results),
                         "rapid_seconds":sum(x["timing"]["rapid_inference_seconds"] for x in results),"recovered":sum(len(x["recovered"]) for x in results),
                         "regressed":sum(len(x["regressed"]) for x in results)}
                result={"summary":summary,"cases":results,"initialization_wall_seconds":initialization_wall,"initialization":{"rapid":rapid.ready,"paddle":paddle.ready},
                        "implementation_sha256":hashes,"full_page_paddle_calls":0,"paid_api_calls":0,"all_page_inference_fresh":True,
                        "gap_detection":args.gap_detection,"gap_policy":args.gap_policy,"local_paddle_detection_calls":sum(len(x["gap_plan"]["regions"]) for x in results)}
                (root/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
                print(json.dumps({"id":cid,"expected":len(c["numeric_cells"]),"rapid":scores["rapid"]["value"]["correct"],"fast":scores["fast"]["value"]["correct"],"seconds":round(wall,2),"crops":len(crop_requests)}),flush=True)
        print(json.dumps(summary,indent=2),flush=True)
    finally:
        if rapid:rapid.close()
        if paddle:paddle.close()


if __name__=="__main__":main()
