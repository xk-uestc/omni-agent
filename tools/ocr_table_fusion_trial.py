"""Live upstream structure inference plus bounded, source-bound numeric review."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from PIL import Image
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"ict-track8"))
from backend.image_geometry import IDENTITY, inverse, rotation_affine
from backend.ocr_region_fusion import map_nodes, rectify_region
from backend.ocr_table_fusion import bind_structures, numeric_nodes, plan_table_reviews, reconcile_table_review, structural_cells, table_context, select_page_reader
from ocr_region_fusion_live import localized_score, run_worker as read_worker, tesseract_observation

BASE=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")
DEFAULT=BASE/"region-fusion-b6d33fe47fa0409bba4c3479079c2494"


def run_structure(root, provider, requests, reuse=False):
    output=root/(provider+".json")
    if reuse and output.exists():
        cached=json.loads(output.read_text(encoding="utf-8"))
        if {(r["id"],r["sha256"]) for r in cached["records"]}=={(r["id"],r["sha256"]) for r in requests}:
            return cached
        raise ValueError("structure_cache_identity_mismatch")
    req=root/(provider+"-requests.json")
    req.write_text(json.dumps(requests,ensure_ascii=False,indent=2),encoding="utf-8")
    runtime=BASE/("table-fusion-env" if provider.startswith("rapid") else "paddle-env")/"Scripts/python.exe"
    started=time.perf_counter()
    with (root/(provider+".log")).open("w",encoding="utf-8") as log:
        process=subprocess.Popen([str(runtime),str(Path(__file__).with_name("ocr_table_fusion_worker.py")),provider,str(req),str(output)],stdout=log,stderr=subprocess.STDOUT)
        try:process.wait(timeout=360)
        except subprocess.TimeoutExpired:
            try:
                for child in reversed(psutil.Process(process.pid).children(recursive=True)):child.kill()
            except psutil.Error:pass
            process.kill();process.wait()
            raise TimeoutError(f"{provider}: owned worker timed out; log preserved")
    if process.returncode:raise RuntimeError(f"{provider}: inspect {root/(provider+'.log')}")
    report=json.loads(output.read_text(encoding="utf-8"))
    report["worker_wall_seconds"]=round(time.perf_counter()-started,3)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report


def render(root, result):
    payload=json.dumps(result,ensure_ascii=False).replace("<","\\u003c")
    html=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Omni · 表格证据融合</title>
<style>*{box-sizing:border-box}body{margin:0;background:#fafafa;color:#20242a;font:15px system-ui}header{padding:26px 34px;background:white;border-bottom:1px solid #ddd}h1{font-size:27px;margin:0 0 12px}h2{font-size:19px}p{line-height:1.7}.note{color:#67707c;font-size:13px}.summary{display:flex;flex-wrap:wrap;gap:32px;margin:20px 0}.summary strong{font-size:26px;display:block}.controls{display:flex;gap:20px;align-items:center;flex-wrap:wrap}select{padding:8px;font:inherit;border:1px solid #ccc}.layout{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(400px,1fr);gap:30px;padding:28px 34px}.image{position:relative;align-self:start}.image img{display:block;width:100%}.bbox{position:absolute;border:1px solid #9babbc;background:#8098ae08;pointer-events:none}.evidence{position:absolute;border:2px solid #1a976b;background:#17ad7818;cursor:pointer;min-width:6px;min-height:6px}.evidence.pending{border-color:#dc9d25;background:#ffce4825}.evidence.active{outline:3px solid #397ae4}.panel{min-width:0}table{width:100%;border-collapse:collapse}td,th{padding:9px 6px;text-align:left;border-bottom:1px solid #ddd;font-size:13px}article{border-top:1px solid #ddd;padding:16px 0;cursor:pointer}pre{font:13px ui-monospace,monospace;white-space:pre-wrap;word-break:break-word}.crop{max-width:100%;background:white}details{border-top:1px solid #ddd;padding:14px 0}button{font:inherit}a{color:#2869c5}@media(max-width:900px){.layout{grid-template-columns:1fr;padding:18px}.summary{gap:20px}header{padding:20px}}</style>
<header><h1>Omni · 表格证据融合</h1><p>复用开源表格结构，让每个金额都有位置、单位和复核记录。</p><div class="summary" id="summary"></div><p class="note">本页为开发实测，包含公开 PDF 转图和自建退化样本。不是官方比赛成绩或 SOTA 证明。旧结果复用与本次真实调用分别记录；所有方案同时报告相同金额解析口径。</p><div class="controls"><select id="case"></select><label><input id="structures" type="checkbox">显示候选单元格结构</label><span class="note">绿色：采用/确认 · 黄色：待复核 · 灰色：上游结构候选</span></div></header>
<main class="layout"><div class="image" id="image"></div><section class="panel"><div id="metrics"></div><div id="reviews"></div><details><summary>结构与单位提取详情</summary><pre id="units"></pre></details><details><summary>开源复用与实测边界</summary><pre id="provenance"></pre></details></section></main>
<script>const data=PAYLOAD;const select=document.querySelector('#case');const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const s=data.summary;document.querySelector('#summary').innerHTML=[['上轮融合·原口径',s.previous_strict+'/'+s.expected],['局部融合·原口径',s.final_strict+'/'+s.expected],['共享解析·质量路由',s.adaptive_normalized+'/'+s.expected],[data.provenance.structures_reused||data.provenance.crop_readers_reused?'本次复放耗时':'新增调用耗时',s.incremental_wall_seconds+'s']].map(x=>'<div><strong>'+esc(x[1])+'</strong><span class="note">'+esc(x[0])+'</span></div>').join('');for(const c of data.cases){const o=document.createElement('option');o.value=c.name;o.textContent=c.name;select.append(o)}select.value=data.cases.find(c=>c.scores[0].strict.expected>0)?.name||data.cases[0].name;
function show(){const c=data.cases.find(x=>x.name===select.value),image=document.querySelector('#image');image.innerHTML='<img src="samples/'+encodeURIComponent(c.name)+'.png">';const addBox=(b,cls)=>{const e=document.createElement('button');e.className=cls;e.style.left=100*b[0]/c.size_px[0]+'%';e.style.top=100*b[1]/c.size_px[1]+'%';e.style.width=100*(b[2]-b[0])/c.size_px[0]+'%';e.style.height=100*(b[3]-b[1])/c.size_px[1]+'%';image.append(e);return e};
if(document.querySelector('#structures').checked)for(const cell of c.cells)addBox(cell.source_bbox,'bbox');
document.querySelector('#metrics').innerHTML='<h2>'+esc(c.name)+'</h2>'+(c.scores[0].strict.expected?'<table><tr><th>方案</th><th>原严格解析</th><th>共享金额解析</th></tr>'+c.scores.map(r=>'<tr><td>'+esc(r.label)+'</td><td>'+r.strict.correct+'/'+r.strict.expected+'</td><td>'+r.normalized.correct+'/'+r.normalized.expected+'</td></tr>').join('')+'</table>':'<p>回归字段：'+c.regression_fields.correct+'/'+c.regression_fields.expected+'；本页不计入数值单元格分母。</p>')+'<p class="note">同一原图位置＋数值匹配。共享解析提取单位和文字内的金额；其提升不能直接算 OCR 模型提升。CTC 字符位置属于模型对齐估计，不是人工真值。读数一致也可能一致地读错，未解决项保留。</p>';
const reviews=document.querySelector('#reviews');reviews.innerHTML='';for(const r of c.reviews){const a=document.createElement('article');a.innerHTML='<strong>'+esc(r.original_text)+' → '+esc(r.selected_text)+'</strong><p class="note">'+esc(r.status)+' · '+esc(r.reason)+'</p><img class="crop" src="crops/'+encodeURIComponent(r.crop_name)+'"><pre>'+esc(r.observations.map(o=>o.engine+': '+o.text+' · '+Number(o.confidence).toFixed(3)).join('\n'))+'</pre>';reviews.append(a);const b=addBox(r.source_geometry.source_polygon_px.reduce((v,p)=>[Math.min(v[0],p[0]),Math.min(v[1],p[1]),Math.max(v[2],p[0]),Math.max(v[3],p[1])],[Infinity,Infinity,-Infinity,-Infinity]),'evidence'+(r.status==='needs_review'?' pending':''));const focus=()=>{image.querySelectorAll('.active').forEach(x=>x.classList.remove('active'));b.classList.add('active');a.scrollIntoView({behavior:'smooth',block:'nearest'})};b.onclick=focus;a.onclick=focus}if(!c.reviews.length)reviews.textContent='本页没有触发新增复核。';document.querySelector('#units').textContent=JSON.stringify(c.quantities.map(n=>({text:n.quantity.surface,value:n.quantity.value,unit:n.quantity.unit_raw,scale:n.quantity.scale,unit_reason:n.quantity.unit_reason,row_labels:n.row_label_candidates?.map(l=>l.text),cell:n.table_cell_candidate?.logic,location:n.location_kind,structure:n.structure_status})),null,2);document.querySelector('#provenance').textContent=JSON.stringify(data.provenance,null,2)}select.onchange=show;document.querySelector('#structures').onchange=show;show();</script></html>'''.replace("PAYLOAD",payload)
    (root/"table-fusion-report.html").write_text(html,encoding="utf-8")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=DEFAULT)
    parser.add_argument("--output",type=Path)
    parser.add_argument("--reuse-structures",action="store_true")
    parser.add_argument("--reuse-readers",action="store_true")
    args=parser.parse_args()
    root=args.output or BASE/("table-fusion-"+uuid.uuid4().hex)
    root.mkdir(exist_ok=True)
    for d in ("samples","working","crops"): (root/d).mkdir(exist_ok=True)
    manifest=json.loads((args.source/"manifest.json").read_text(encoding="utf-8"))
    previous=json.loads((args.source/"region-fusion-result.json").read_text(encoding="utf-8"))
    prior={c["name"]:c for c in previous["cases"]}
    requests=[]; transforms={}
    for case in manifest["cases"]:
        source=Path(case["path"])
        if hashlib.sha256(source.read_bytes()).hexdigest()!=case["sha256"]:raise ValueError("source_changed")
        shutil.copy2(source,root/"samples"/(case["name"]+".png"))
        image=Image.open(source).convert("RGB")
        angle=prior[case["name"]]["rotation_ccw_degrees"]
        before=list(image.size)
        image=image.rotate(angle,expand=True,fillcolor="white") if angle else image
        transform=inverse(rotation_affine(angle,before,list(image.size))) if angle else IDENTITY
        path=root/"working"/(case["name"]+".png")
        image.save(path)
        requests.append({"id":case["name"],"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
        transforms[case["name"]]=transform
    started=time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={p:pool.submit(run_structure,root,p,requests,args.reuse_structures) for p in ("rapid-structure","paddle-structure")}
        reports={p:f.result() for p,f in futures.items()}
    rapid={r["id"]:r for r in reports["rapid-structure"]["records"]}
    paddle={r["id"]:r for r in reports["paddle-structure"]["records"]}
    work={};crops=[]
    for case in manifest["cases"]:
        name=case["name"];record=rapid[name]
        if record.get("error"):raise RuntimeError(f"Rapid structure worker failed: {name}: {record['error']}")
        cells=structural_cells(record,paddle[name])
        nodes=bind_structures(record["nodes"],cells)
        plan=plan_table_reviews(nodes)
        image=Image.open(root/"working"/(name+".png")).convert("RGB")
        work[name]={"cells":cells,"nodes":nodes,"plan":plan}
        for review in plan["requests"]:
            index=review["node_index"]
            crop,geometry=rectify_region(image,nodes[index],working_to_source=transforms[name],source_sha256=case["sha256"])
            # Enlarge actual observed pixels before recognition, never synthesize digits.
            path=root/"crops"/(name+"-"+str(index)+".png")
            crop.save(path)
            crops.append({"id":name+"/"+str(index),"case":name,"path":str(path),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"geometry":geometry,"request":review})
    crop_requests=[{k:c[k] for k in ("id","path","sha256")} for c in crops]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures={p:pool.submit(read_worker,root,p,crop_requests,reuse=args.reuse_readers) for p in ("rapid-line","paddle-line")}
        reader_reports={p:f.result() for p,f in futures.items()}
    readers={p:{r["id"]:r for r in report["records"]} for p,report in reader_reports.items()}
    reviews={}
    for crop in crops:
        observations=[]
        for p,rows in readers.items():
            r=rows[crop["id"]]
            observations.append({"engine":p.split("-")[0],"text":r.get("text",""),"confidence":r.get("confidence",0),"crop_sha256":r["sha256"],"error":r.get("error")})
        observations.append(tesseract_observation(crop["path"],crop["sha256"]))
        decision=reconcile_table_review(crop["request"]["original_text"],observations,crop_sha256=crop["sha256"],context=crop["request"])
        decision.update(crop_name=Path(crop["path"]).name,source_geometry=crop["geometry"],trigger=crop["request"]["reasons"],node_index=crop["request"]["node_index"])
        reviews.setdefault(crop["case"],[]).append(decision)
    wall=time.perf_counter()-started
    results=[]
    original_initial={r["id"]:r for r in previous["stage_reports"]["initial"]["records"]}
    previous_paddle={r["id"]:r for r in previous["stage_reports"]["paddle_full"]["records"]}
    for case in manifest["cases"]:
        name=case["name"];item=work[name];source_sha=case["sha256"]
        final=deepcopy(item["nodes"])
        # Preserve prior accepted source-bound decisions when the fresh node overlaps.
        for node in final:
            from backend.ocr_table_fusion import overlap, area
            for old_review in prior[name]["reviews"]:
                if old_review["status"] not in {"corrected","split_corrected"}:continue
                if node["text"]!=old_review["original_text"]:continue
                # Cached repair is tied to the original region, not just its text.
                geometry=old_review.get("source_geometry",{})
                if geometry.get("source_sha256")!=source_sha:continue
                crop_polygon=geometry.get("source_polygon_px",[])
                from backend.image_geometry import point
                old_work=[point(inverse(transforms[name]),p) for p in crop_polygon]
                if not old_work:continue
                old_box=[min(p[0] for p in old_work),min(p[1] for p in old_work),max(p[0] for p in old_work),max(p[1] for p in old_work)]
                if overlap(node["bbox"],old_box)/max(1.,area(node["bbox"]))<.6:continue
                if old_review["status"]=="corrected":node["text"]=old_review["selected_text"]
                elif old_review.get("nodes"):
                    chosen=old_review["nodes"]
                    if len(chosen)==1 and overlap(node["bbox"],chosen[0]["bbox"])/max(1.,area(chosen[0]["bbox"]))>=.6:
                        node.update(text=chosen[0]["text"],bbox=chosen[0]["bbox"],polygon=chosen[0]["polygon"])
                break
        for decision in reviews.get(name,[]):
            if decision["status"]=="corrected":
                final[decision["node_index"]]["text"]=decision["selected_text"]
                final[decision["node_index"]].pop("words",None)
        # No stale CTC alignment may survive a changed literal.
        for before,after in zip(item["nodes"],final):
            if before["text"]!=after["text"]:after.pop("words",None)
        initial=map_nodes(original_initial[name]["nodes"],IDENTITY,source_sha)
        pf=previous_paddle[name]
        ptransform=inverse(rotation_affine(pf.get("rotation_ccw_degrees",0),case["size_px"],pf.get("output_size_px",case["size_px"])))
        pnodes=map_nodes(pf.get("nodes",[]),ptransform,source_sha)
        baselines=[("原始 Rapid（上轮缓存）",initial,IDENTITY),("全页 Paddle（上轮缓存）",pnodes,IDENTITY),
                   ("上轮局部融合（缓存）",prior[name]["nodes"],IDENTITY),
                   ("本轮 Rapid＋字符定位",item["nodes"],transforms[name]),("本轮表格融合",final,transforms[name])]
        final_source=map_nodes(final,transforms[name],source_sha)
        first_source=map_nodes(item["nodes"],transforms[name],source_sha)
        route=select_page_reader(first_source,final_source,pnodes,source_sha256=source_sha,expert_source_sha256=pf["sha256"])
        chosen=deepcopy(route["nodes"])
        for n in chosen:
            n["polygon"]=n["source_geometry"]["polygon_px"]
            n["bbox"]=n["source_geometry"]["bbox_px"]
        baselines.append(("质量路由候选（开发策略）",chosen,IDENTITY))
        scores=[]
        for label,nodes,transform in baselines:
            if nodes and nodes[0].get("source_geometry") and transform==IDENTITY:
                # The cached fusion keeps working polygons and separate original
                # polygons. Do not relabel a rotated working box as source pixels.
                nodes=deepcopy(nodes)
                for node in nodes:
                    sg=node.get("source_geometry")
                    if sg:
                        node["bbox"]=sg["bbox_px"]
                        node["polygon"]=sg["polygon_px"]
            strict=localized_score(case,map_nodes(nodes,transform,source_sha))
            normalized=localized_score(case,map_nodes(numeric_nodes(nodes),transform,source_sha))
            scores.append({"label":label,"strict":strict,"normalized":normalized})
        qnodes=map_nodes(table_context(bind_structures(numeric_nodes(final),item["cells"]),final),transforms[name],source_sha)
        for cell in item["cells"]:
            mapped=map_nodes([{"bbox":cell["bbox"],"text":"","confidence":0}],transforms[name],source_sha)[0]
            cell["source_bbox"]=mapped["source_geometry"]["bbox_px"]
        from ocr_fusion_trial import field_present
        field_text="\n".join(n["text"] for n in final)
        results.append({"name":name,"size_px":case["size_px"],"scores":scores,"reviews":reviews.get(name,[]),"cells":item["cells"],
                        "quantities":qnodes,"nodes":map_nodes(final,transforms[name],source_sha),"deferred":item["plan"]["deferred"],"provenance":case["provenance"],
                        "regression_fields":{"correct":sum(field_present(v,field_text) for v in case["fields"]),"expected":len(case["fields"])},
                        "page_route":{k:v for k,v in route.items() if k!="nodes"},"adaptive_nodes":chosen,
                        "paddle_structure_error":paddle[name].get("error"),"img2table_error":rapid[name].get("img2table",{}).get("error")})
    totals=lambda i,k:sum(c["scores"][i][k]["correct"] for c in results)
    transitions=[]
    for c in results:
        key=lambda cell:json.dumps(cell,sort_keys=True,ensure_ascii=False)
        old={key(v):v for v in c["scores"][2]["normalized"]["missed"]}
        new={key(v):v for v in c["scores"][4]["normalized"]["missed"]}
        transitions.append({"case":c["name"],"recovered":[old[k] for k in old.keys()-new.keys()],"regressed":[new[k] for k in new.keys()-old.keys()]})
    summary={"expected":sum(c["scores"][0]["strict"]["expected"] for c in results),"previous_strict":totals(2,"strict"),
             "previous_normalized":totals(2,"normalized"),"fresh_rapid_strict":totals(3,"strict"),"fresh_rapid_normalized":totals(3,"normalized"),
             "final_strict":totals(4,"strict"),"final_normalized":totals(4,"normalized"),"incremental_wall_seconds":round(wall,3),
             "adaptive_strict":totals(5,"strict"),"adaptive_normalized":totals(5,"normalized"),
             "adaptive_full_page_selections":sum(c["page_route"]["selected"]=="paddle_full" for c in results),
             "reviewed_regions":len(crops),"adopted_reviews":sum(r["status"]=="corrected" for rs in reviews.values() for r in rs),
             "unresolved_reviews":sum(r["status"]=="needs_review" for rs in reviews.values() for r in rs),
             "upstream_cells":sum(len(c["cells"]) for c in results),
             "recovered_normalized_cells":sum(len(t["recovered"]) for t in transitions),
             "regressed_normalized_cells":sum(len(t["regressed"]) for t in transitions),
             "character_aligned_quantities":sum(q["location_kind"]=="ctc_character_alignment_not_independent_detector" for c in results for q in c["quantities"]),
             "cross_family_structured_quantities":sum(q.get("structure_status")=="cross_family_cell_support" for c in results for q in c["quantities"])}
    result={"method":"upstream_structure_candidates_with_source_bound_numeric_review","summary":summary,"cases":results,"transitions":transitions,
            "provenance":{"source_run":str(args.source),"prior_ocr_and_decisions_reused":True,"structures_reused":args.reuse_structures,"crop_readers_reused":args.reuse_readers,
                "orientation_from_prior_observed_model_output":True,"routing_reads_reference_labels":False,"not_official_benchmark":True,
                "timing_scope":"incremental_new_calls_plus_coordinator_not_fresh_end_to_end","structure_dependencies":{p:r["versions"] for p,r in reports.items()},
                "upstream_inference_wall_seconds":{p:r["worker_wall_seconds"] for p,r in reports.items()},
                "source_result_sha256":hashlib.sha256((args.source/"region-fusion-result.json").read_bytes()).hexdigest(),
                "source_manifest_sha256":hashlib.sha256((args.source/"manifest.json").read_bytes()).hexdigest(),
                "adaptive_policy_added_after_transfer_failure_not_blind_evaluation":True,
                "adaptive_expert_outputs_reused_from_previous_actual_full_page_calls":True,
                "adaptive_timing_excludes_prior_full_page_expert_cost":True,
                "paddle_and_rapid_table_same_model_family_not_two_independent_votes":True,"production_pipeline_modified":False}}
    implementation=[Path(__file__),Path(__file__).with_name("ocr_table_fusion_worker.py"),
                    Path(__file__).resolve().parents[1]/"ict-track8/backend/ocr_table_fusion.py"]
    result["provenance"]["implementation_sha256"]={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation}
    if (root/"table-fusion-result.json").exists():
        old=json.loads((root/"table-fusion-result.json").read_text(encoding="utf-8"))
        with (root/"run-history.jsonl").open("a",encoding="utf-8") as history:
            history.write(json.dumps({"summary":old["summary"],"provenance":old["provenance"]},ensure_ascii=False)+"\n")
    (root/"table-fusion-result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    (root/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    render(root,result)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    print(root/"table-fusion-report.html",flush=True)


if __name__=="__main__":main()
