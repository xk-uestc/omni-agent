"""Actual isolated model calls, numeric localization scoring and evidence viewer."""
from __future__ import annotations

import argparse
import csv
from html import escape
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ict-track8"))
from backend.image_geometry import IDENTITY, inverse, rotation_affine
from backend.ocr_region_fusion import (amount_key, apply_decisions, map_nodes, observed_column_totals,
                                       numeric_like, plan_regions, reconcile, rectify_region, spatial_consensus)
from ocr_fusion_trial import field_present

BASE = Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")
WORKER = Path(__file__).with_name("ocr_region_fusion_worker.py")


def run_worker(root, provider, requests, *, reuse=False):
    output = root / (provider + ".json")
    if reuse and output.exists():
        cached = json.loads(output.read_text(encoding="utf-8"))
        expected = {(r["id"], r["sha256"]) for r in requests}
        actual = {(r["id"], r["sha256"]) for r in cached["records"]}
        if expected == actual:
            return cached
    request_path = root / (provider + "-requests.json")
    request_path.write_text(json.dumps(requests, ensure_ascii=False, indent=2), encoding="utf-8")
    runtime = BASE / ("rapid-new-env" if provider.startswith("rapid") else "paddle-env") / "Scripts/python.exe"
    log = root / (provider + ".log")
    started = time.perf_counter()
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen([str(runtime), str(WORKER), provider, str(request_path), str(output)],
                                   stdout=stream, stderr=subprocess.STDOUT)
        try:
            process.wait(timeout=600)
        except subprocess.TimeoutExpired:
            try:
                for child in reversed(psutil.Process(process.pid).children(recursive=True)):
                    child.kill()
            except psutil.Error:
                pass
            process.kill()
            process.wait()
            raise TimeoutError(f"Owned worker timed out: {provider}; log={log}")
    if process.returncode:
        raise RuntimeError(f"Worker failed: {provider}; log={log}")
    data = json.loads(output.read_text(encoding="utf-8"))
    data["worker_wall_seconds"] = round(time.perf_counter() - started, 4)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"stage": provider, "requests": len(requests), "seconds": data["worker_wall_seconds"]}), flush=True)
    return data


def tesseract_observation(path, crop_sha):
    started = time.perf_counter()
    try:
        process = subprocess.run([str(BASE / "tesseract/tesseract.exe"), str(path), "stdout", "--tessdata-dir",
            str(BASE / "tesseract/tessdata"), "-l", "eng", "--psm", "7", "tsv"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            env=dict(os.environ, OMP_THREAD_LIMIT="2"))
        if process.returncode:
            raise RuntimeError(process.stderr[:500])
        words = [word for word in csv.DictReader(io.StringIO(process.stdout), delimiter="\t") if word["text"].strip()]
        confidences = [float(word["conf"]) / 100 for word in words if float(word["conf"]) >= 0]
        return {"engine": "tesseract", "text": " ".join(word["text"] for word in words),
                "confidence": min(confidences) if confidences else 0., "crop_sha256": crop_sha,
                "seconds": round(time.perf_counter() - started, 4), "error": None}
    except Exception as exc:
        return {"engine": "tesseract", "text": "", "confidence": 0., "crop_sha256": crop_sha,
                "seconds": round(time.perf_counter() - started, 4), "error": str(exc)}


def localized_score(case, nodes):
    truth = case["numeric_cells"]
    correct = 0
    missed = []
    claimed = set()
    for cell in truth:
        expected = amount_key(cell["text"])
        x0, y0, x1, y1 = cell["bbox"]
        candidates = []
        for index, node in enumerate(nodes):
            actual = amount_key(node.get("text", ""))
            if index in claimed or actual is None or actual[1] != expected[1]:
                continue
            if case["provenance"]["type"].startswith("synthetic") and actual[0] != expected[0]:
                continue
            box = node.get("source_geometry", {}).get("bbox_px", node["bbox"])
            overlap_x = max(0, min(x1, box[2]) - max(x0, box[0]))
            overlap_y = max(0, min(y1, box[3]) - max(y0, box[1]))
            overlap = overlap_x * overlap_y
            area = max(1., (x1 - x0) * (y1 - y0))
            if overlap / area >= .4:
                candidates.append((overlap / area, index))
        if candidates:
            claimed.add(max(candidates)[1])
            correct += 1
        else:
            missed.append(cell)
    return {"correct": correct, "expected": len(truth), "missed": missed,
            "method": "numeric_value_plus_same_native_source_cell_overlap_0.4"}


def render_viewer(root, result):
    data = json.dumps(result, ensure_ascii=False).replace("<", "\\u003c")
    html = r'''<!doctype html><html lang="zh"><meta charset="utf-8"><title>OCR 局部复核实测</title>
<style>body{margin:0;background:#f5f6f8;color:#20242c;font:15px system-ui}header{padding:24px 32px;background:white;border-bottom:1px solid #ddd}h1{margin:0 0 10px}select{font:inherit;padding:8px}.layout{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(360px,1fr);gap:24px;padding:24px}.page{position:relative;align-self:start}.page img{display:block;width:100%}.box{position:absolute;border:2px solid #d39500;cursor:pointer;box-sizing:border-box;background:#ffc80018}.box.corrected,.box.split_corrected{border-color:#139768;background:#14b88022}.box.needs_review{border-color:#d04b40;background:#e74c3c22}.box.active{outline:3px solid #2f6bff}.panel{background:white;padding:22px;min-width:0}article{border-top:1px solid #ddd;padding:18px 0;cursor:pointer}pre{white-space:pre-wrap;font:14px ui-monospace,monospace}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}.muted{color:#626b78;line-height:1.65}.crop{max-width:100%;image-rendering:auto;background:#fafafa;border:1px solid #ddd}@media(max-width:850px){.layout{grid-template-columns:1fr}}</style>
<header><h1>OCR 局部复核：真实模型实测</h1><p class="muted">点原图框查看该区域的原始识别、复核读数与最终处理。绿色=有两条链路支持的改动；红色=冲突保留。新公开样本是 PDF 原生页面转图，中文表是自建压力样本，不是官方比赛成绩。</p><select id="select"></select></header><main class="layout"><div class="page" id="page"></div><div class="panel"><div id="metrics"></div><div id="details"></div></div></main><script>const data=DATA;
const select=document.querySelector('#select');for(const c of data.cases){const o=document.createElement('option');o.value=c.name;o.textContent=c.name;select.append(o)}
const safe=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function show(){const c=data.cases.find(x=>x.name===select.value);const page=document.querySelector('#page');page.innerHTML='<img src="samples/'+encodeURIComponent(c.name)+'.png">';const cells=c.initial_score.expected>0;const scores=cells?['initial_score','orientation_score','fusion_score'].map(k=>'<td>'+c[k].correct+'/'+c[k].expected+'</td>').join(''):'<td>'+c.initial_fields+'/'+c.fields_expected+'</td><td>—</td><td>'+c.fusion_fields+'/'+c.fields_expected+'</td>';document.querySelector('#metrics').innerHTML='<h2>'+safe(c.name)+'</h2><table><tr><th>原始 Rapid</th><th>方向修复</th><th>局部复核后</th></tr><tr>'+scores+'</tr></table><p class="muted">'+(cells?'按数值和原图位置检查；不是只看全文里有没有这个数字。':'该回归页检查指定字段覆盖，不计入新样本单元格定位分数。')+'方向修复：'+safe(c.rotation_ccw_degrees)+'°；复核区域：'+c.reviews.length+'。</p>';
const details=document.querySelector('#details');details.innerHTML='';for(const [i,r] of c.reviews.entries()){const p=r.source_geometry.source_polygon_px;const xs=p.map(v=>v[0]),ys=p.map(v=>v[1]);const b=document.createElement('button');b.className='box '+r.status;b.style.left=100*Math.min(...xs)/c.size_px[0]+'%';b.style.top=100*Math.min(...ys)/c.size_px[1]+'%';b.style.width=100*(Math.max(...xs)-Math.min(...xs))/c.size_px[0]+'%';b.style.height=100*(Math.max(...ys)-Math.min(...ys))/c.size_px[1]+'%';b.title=r.original_text+' → '+r.selected_text;page.append(b);const a=document.createElement('article');a.innerHTML='<strong>'+safe(r.original_text)+' → '+safe(r.selected_text)+'</strong><p>'+safe(r.status)+' · '+safe(r.reason)+'</p><img class="crop" src="crops/'+encodeURIComponent(r.crop_name)+'"><pre>'+r.observations.map(o=>safe(o.engine)+': '+safe(o.text)+' (score '+Number(o.confidence).toFixed(3)+')'+(o.error?' ERROR':'')).join('\n')+'</pre>';details.append(a);const focus=()=>{document.querySelectorAll('.box').forEach(x=>x.classList.remove('active'));b.classList.add('active');a.scrollIntoView({behavior:'smooth',block:'nearest'})};b.onclick=focus;a.onclick=focus}if(!c.reviews.length)details.textContent='本页没有触发局部金额复核。';}select.onchange=show;show();</script></html>'''.replace("DATA", data)
    (root / "region-fusion-report.html").write_text(html, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--reuse-initial", action="store_true")
    parser.add_argument("--reuse-stages", action="store_true")
    parser.add_argument("--full-baseline", action="store_true")
    parser.add_argument("--reuse-full-baseline", action="store_true")
    args = parser.parse_args()
    root = args.root
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    cases = manifest["cases"]
    started = time.perf_counter()
    requests = [{"id": case["name"], "path": case["path"], "sha256": case["sha256"]} for case in cases]
    if args.reuse_initial:
        initial = json.loads((root / "rapid-initial.json").read_text(encoding="utf-8"))
    else:
        initial = run_worker(root, "rapid-page", requests)
    initial_by_name = {record["id"]: record for record in initial["records"]}
    (root / "working").mkdir(exist_ok=True)
    (root / "crops").mkdir(exist_ok=True)
    corrected_requests = []
    work = {}
    for case in cases:
        record = initial_by_name[case["name"]]
        if record["error"]:
            raise RuntimeError(f"Initial OCR failed: {case['name']}")
        orientation = record.get("orientation") or {}
        angle = orientation.get("correction_ccw_degrees", 0.) if orientation.get("status") == "estimated" else 0.
        # Fix substantial page rotation. Small deskew is separate, not blindly applied.
        angle = round(angle / 90) * 90 if angle is not None and abs(angle) >= 45 else 0
        image = Image.open(case["path"]).convert("RGB")
        reverse = IDENTITY
        path = Path(case["path"])
        if angle:
            before = list(image.size)
            image = image.rotate(angle, expand=True, fillcolor="white")
            reverse = inverse(rotation_affine(angle, before, list(image.size)))
            path = root / "working" / (case["name"] + ".png")
            image.save(path)
            corrected_requests.append({"id": case["name"], "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        work[case["name"]] = {"path": path, "reverse": reverse, "angle": angle, "image": image}
    corrected = run_worker(root, "rapid-page", corrected_requests, reuse=args.reuse_stages) if corrected_requests else {"records": []}
    corrected_by_name = {record["id"]: record for record in corrected["records"]}
    crops = []
    for case in cases:
        item = work[case["name"]]
        record = corrected_by_name.get(case["name"], initial_by_name[case["name"]])
        if record["error"]:
            raise RuntimeError(f"Rotated OCR failed: {case['name']}")
        nodes = map_nodes(record["nodes"], item["reverse"], case["sha256"])
        item.update(nodes=nodes, record=record)
        plan = plan_regions(nodes)
        item["plan"] = plan
        for request in plan["requests"]:
            index = request["node_index"]
            crop, geometry = rectify_region(item["image"], nodes[index], working_to_source=item["reverse"], source_sha256=case["sha256"])
            crop_name = case["name"] + "-" + str(index) + ".png"
            path = root / "crops" / crop_name
            crop.save(path)
            crops.append({"id": case["name"] + "/" + str(index), "case": case["name"], "node_index": index,
                          "path": str(path), "crop_name": crop_name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "geometry": geometry, "request": request})
    crop_requests = [{key: crop[key] for key in ("id", "path", "sha256")} for crop in crops]
    crop_reports = {}
    if crops:
        for provider in ("rapid-line", "paddle-line"):
            crop_reports[provider] = run_worker(root, provider, crop_requests, reuse=args.reuse_stages)
    readers = {provider: {record["id"]: record for record in report["records"]} for provider, report in crop_reports.items()}
    reviews = {}
    for crop in crops:
        observations = []
        for provider in crop_reports:
            record = readers[provider][crop["id"]]
            observations.append({"engine": provider.split("-")[0], "text": record.get("text", ""),
                "confidence": record.get("confidence", 0.), "crop_sha256": record["sha256"],
                "seconds": record["seconds"], "error": record["error"]})
        observations.append(tesseract_observation(crop["path"], crop["sha256"]))
        decision = reconcile(crop["request"]["original_text"], observations, crop_sha256=crop["sha256"])
        decision.update(node_index=crop["node_index"], crop_name=crop["crop_name"], source_geometry=crop["geometry"],
                        trigger=crop["request"]["reasons"])
        reviews[crop["id"]] = decision
    # A malformed merged cell needs detection at higher resolution, not another
    # recognition-only vote over the same tightly normalized line image.
    (root / "contexts").mkdir(exist_ok=True)
    contexts = []
    for crop in crops:
        decision = reviews[crop["id"]]
        if decision["status"] != "needs_review" or amount_key(decision["original_text"]) is not None:
            continue
        image = Image.open(crop["path"]).convert("RGB")
        zoom = 3
        image = image.resize((image.width * zoom, image.height * zoom), Image.Resampling.LANCZOS)
        path = root / "contexts" / crop["crop_name"]
        image.save(path)
        from backend.image_geometry import compose
        geometry = dict(crop["geometry"])
        scaling_inverse = [1/zoom, 0., 0., 0., 1/zoom, 0.]
        geometry["crop_to_source"] = compose(geometry["crop_to_source"], scaling_inverse)
        geometry["crop_to_working"] = compose(geometry["crop_to_working"], scaling_inverse)
        contexts.append({"id": crop["id"], "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "geometry": geometry})
    context_reports = {}
    fragments = []
    if contexts:
        context_requests = [{key: context[key] for key in ("id", "path", "sha256")} for context in contexts]
        for provider in ("rapid-region", "paddle-region"):
            context_reports[provider] = run_worker(root, provider, context_requests, reuse=args.reuse_stages)
        by_id = {provider: {record["id"]: record for record in report["records"]} for provider, report in context_reports.items()}
        for context in contexts:
            review = reviews[context["id"]]
            decision = spatial_consensus(review["original_text"], by_id["rapid-region"][context["id"]],
                by_id["paddle-region"][context["id"]], crop_sha256=context["sha256"], geometry=context["geometry"])
            review["context_detection"] = {"decision": decision,
                "readers": {provider: by_id[provider][context["id"]] for provider in by_id}}
            if decision["status"] == "split_corrected":
                review.update(status="split_corrected", reason=decision["reason"], nodes=decision["nodes"],
                              selected_text=" | ".join(node["text"] for node in decision["nodes"]))
            else:
                proposals = [node for node in by_id["paddle-region"][context["id"]].get("nodes", [])
                             if numeric_like(node["text"]) and amount_key(node["text"]) is not None and node["confidence"] >= .9]
                context_image = Image.open(context["path"]).convert("RGB")
                for index, proposal in enumerate(proposals[:3]):
                    fragment, geometry = rectify_region(context_image, proposal,
                        working_to_source=context["geometry"]["crop_to_source"], source_sha256=context["geometry"]["source_sha256"])
                    path = root / "crops" / ("fragment-" + context["id"].replace("/", "-") + "-" + str(index) + ".png")
                    fragment.save(path)
                    fragments.append({"id": context["id"] + "/fragment" + str(index), "parent_id": context["id"],
                                      "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "geometry": geometry, "proposal": proposal, "context_geometry": context["geometry"]})
    fragment_reports = {}
    if fragments:
        fragment_requests = [{key: fragment[key] for key in ("id", "path", "sha256")} for fragment in fragments]
        for provider in ("rapid-fragment", "paddle-fragment"):
            fragment_reports[provider] = run_worker(root, provider, fragment_requests, reuse=args.reuse_stages)
        records = {provider: {record["id"]: record for record in report["records"]} for provider, report in fragment_reports.items()}
        accepted_fragments = {}
        for fragment in fragments:
            observations = [{"engine": provider.split("-")[0], "text": records[provider][fragment["id"]].get("text", ""),
                "confidence": records[provider][fragment["id"]].get("confidence", 0), "crop_sha256": fragment["sha256"],
                "seconds": records[provider][fragment["id"]]["seconds"], "error": records[provider][fragment["id"]]["error"]}
                for provider in records]
            observations.append(tesseract_observation(fragment["path"], fragment["sha256"]))
            review = reviews[fragment["parent_id"]]
            decision = reconcile(review["original_text"], observations, crop_sha256=fragment["sha256"])
            review.setdefault("proposal_verifications", []).append({"proposal": fragment["proposal"], "decision": decision,
                "crop_name": Path(fragment["path"]).name, "geometry": fragment["geometry"]})
            if decision["status"] != "corrected" or amount_key(decision["selected_text"]) != amount_key(fragment["proposal"]["text"]):
                continue
            mapped = map_nodes([fragment["proposal"]], fragment["context_geometry"]["crop_to_source"], fragment["geometry"]["source_sha256"])[0]
            source_quad = mapped["source_geometry"]["polygon_px"]
            case_name = fragment["parent_id"].rsplit("/", 1)[0]
            from backend.image_geometry import point
            working_quad = [point(inverse(work[case_name]["reverse"]), xy) for xy in source_quad]
            mapped.update(text=decision["selected_text"], polygon=working_quad,
                          bbox=[min(p[0] for p in working_quad), min(p[1] for p in working_quad),
                                max(p[0] for p in working_quad), max(p[1] for p in working_quad)])
            accepted_fragments.setdefault(fragment["parent_id"], []).append(mapped)
        for parent_id, nodes in accepted_fragments.items():
            review = reviews[parent_id]
            review.update(status="split_corrected", reason="detector_proposal_verified_on_its_own_pixels_by_two_readers",
                          nodes=nodes, selected_text=" | ".join(node["text"] for node in nodes),
                          geometry_support="one_detector_source_mapping_with_crop_reader_consensus")
    fusion_wall_seconds = time.perf_counter() - started
    full_report = run_worker(root, "paddle-page", requests, reuse=args.reuse_stages or args.reuse_full_baseline) if args.full_baseline else None
    full_by_name = {record["id"]: record for record in full_report["records"]} if full_report else {}
    results = []
    for case in cases:
        item = work[case["name"]]
        decisions = {review["node_index"]: review for key, review in reviews.items() if key.startswith(case["name"] + "/")}
        final_nodes = apply_decisions(item["nodes"], decisions)
        baseline = map_nodes(initial_by_name[case["name"]]["nodes"], IDENTITY, case["sha256"])
        texts = {"initial": initial_by_name[case["name"]]["text"], "fusion": "\n".join(node["text"] for node in final_nodes)}
        row = {"name": case["name"], "size_px": case["size_px"], "source_sha256": case["sha256"],
                        "provenance": case["provenance"], "rotation_ccw_degrees": item["angle"],
                        "initial_score": localized_score(case, baseline), "orientation_score": localized_score(case, item["nodes"]),
                        "fusion_score": localized_score(case, final_nodes), "reviews": list(decisions.values()),
                        "initial_fields": sum(field_present(value, texts["initial"]) for value in case["fields"]),
                        "fusion_fields": sum(field_present(value, texts["fusion"]) for value in case["fields"]),
                        "fields_expected": len(case["fields"]), "deferred_regions": item["plan"]["deferred"],
                        "column_total_checks": observed_column_totals(final_nodes), "nodes": final_nodes}
        if full_report:
            full = full_by_name[case["name"]]
            transform = inverse(rotation_affine(full.get("rotation_ccw_degrees", 0), case["size_px"], full.get("output_size_px", case["size_px"])))
            full_nodes = map_nodes(full.get("nodes", []), transform, case["sha256"])
            row["paddle_full_score"] = localized_score(case, full_nodes)
            row["paddle_full_fields"] = sum(field_present(value, full.get("text", "")) for value in case["fields"])
        results.append(row)
    summary = {"pages": len(results), "reviewed_regions": len(crops),
               "corrected_regions": sum(review["status"] in {"corrected", "split_corrected"} for review in reviews.values()),
               "unresolved_regions": sum(review["status"] == "needs_review" for review in reviews.values()),
               "expected_numeric_cells": sum(case["initial_score"]["expected"] for case in results),
               "initial_correct_cells": sum(case["initial_score"]["correct"] for case in results),
               "orientation_correct_cells": sum(case["orientation_score"]["correct"] for case in results),
               "fusion_correct_cells": sum(case["fusion_score"]["correct"] for case in results),
               "initial_page_inference_seconds": round(sum(record["seconds"] for record in initial["records"]), 3),
               "rotation_page_inference_seconds": round(sum(record["seconds"] for record in corrected["records"]), 3),
               "crop_inference_seconds": round(sum(obs["seconds"] for review in reviews.values() for obs in review["observations"]), 3),
               "context_inference_seconds": round(sum(record["seconds"] for report in context_reports.values() for record in report["records"]), 3),
               "fragment_inference_seconds": round(sum(record["seconds"] for report in fragment_reports.values() for record in report["records"]), 3),
               "fragment_tesseract_inference_seconds": round(sum(obs["seconds"] for review in reviews.values()
                    for proposal in review.get("proposal_verifications", []) for obs in proposal["decision"]["observations"] if obs["engine"] == "tesseract"), 3),
               "fusion_wall_seconds": round(fusion_wall_seconds, 3),
               "coordinator_wall_seconds_excluding_reused_initial": round(time.perf_counter() - started, 3)}
    if full_report:
        summary["paddle_full_correct_cells"] = sum(case["paddle_full_score"]["correct"] for case in results)
        summary["paddle_full_inference_seconds"] = round(sum(record["seconds"] for record in full_report["records"]), 3)
    result = {"method": "live_model_inference_with_source_bound_region_consensus", "not_official_benchmark": True,
              "initial_stage_reused_from_same_live_run": args.reuse_initial, "routing_reads_reference_labels": False,
              "fusion_stages_reused": args.reuse_stages, "full_baseline_reused": args.reuse_full_baseline,
              "score_scope": "numeric_value_and_native_source_cell_localization_not_whole_OCR_accuracy",
              "stage_reports": {"initial": initial, "rotation": corrected, **crop_reports, **context_reports, **fragment_reports, "paddle_full": full_report}, "summary": summary, "cases": results}
    (root / "region-fusion-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    render_viewer(root, result)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(root / "region-fusion-report.html", flush=True)


if __name__ == "__main__":
    main()
