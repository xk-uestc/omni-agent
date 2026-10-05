"""Fresh upstream calls; frozen component ablations; scoring is a separate phase.

One observation pool is shared for paired experiments. Variant costs are summed
from measured stage calls, NOT claimed as separately timed end-to-end latency.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import shutil
import sys
import time
import types

from PIL import Image
from ocr_table_fusion_trial import run_structure
from ocr_region_fusion_live import run_worker, tesseract_observation
from backend.image_geometry import IDENTITY, inverse, rotation_affine
from backend.ocr_region_fusion import map_nodes, rectify_region

VARIANTS = ["rapid", "paddle", "full", "no_structure", "no_review", "no_route"]


def load_policy(path):
    package = "validation_policy_"+hashlib.sha256(str(path).encode()).hexdigest()[:12]
    pkg = types.ModuleType(package)
    pkg.__path__ = [str(path)]
    sys.modules[package] = pkg
    name = package+".ocr_table_fusion"
    spec = importlib.util.spec_from_file_location(name, path/"ocr_table_fusion.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def literal(text):
    return re.sub(r"\s+", "", text).lstrip("$¥￥")


def score(cells, nodes, policy, number_locale=None):
    kw={"number_locale":number_locale} if "number_locale" in inspect.signature(policy.numeric_nodes).parameters else {}
    predictions = policy.numeric_nodes(nodes,**kw)
    edges = {k: [] for k in ("literal", "value", "tight_value")}
    scoped = set()
    for ti, cell in enumerate(cells):
        for pi, pred in enumerate(predictions):
            inter = policy.overlap(cell["bbox"], pred["bbox"])
            coverage = inter/max(1., policy.area(cell["bbox"]))
            iou = inter/max(1.,policy.area(cell["bbox"])+policy.area(pred["bbox"])-inter)
            if coverage < .4:
                continue
            scoped.add(pi)
            if literal(pred["quantity"]["surface"]) == literal(cell["text"]):
                edges["literal"].append((iou, ti, pi))
            if Decimal(pred["quantity"]["value"]) == Decimal(cell["value"]):
                edges["value"].append((iou, ti, pi))
                if iou >= .3:
                    edges["tight_value"].append((iou, ti, pi))
    # Maximum-cardinality matching, with location preference. A repeated number
    # cannot match multiple official words through one predicted region.
    output = {"expected": len(cells), "candidate_count_in_annotated_numeric_scope": len(scoped),
              "ignored_candidates_outside_numeric_scope": len(predictions)-len(scoped)}
    for metric, values in edges.items():
        adj = {}
        for iou, ti, pi in sorted(values, reverse=True):
            adj.setdefault(ti, []).append(pi)
        matches = {}
        def augment(ti, seen):
            for pi in adj.get(ti, []):
                if pi in seen: continue
                seen.add(pi)
                if pi not in matches or augment(matches[pi], seen):
                    matches[pi] = ti
                    return True
            return False
        for ti in range(len(cells)): augment(ti, set())
        correct = sorted(matches.values())
        output[metric] = {"correct": len(correct), "matched_reference_indices": correct,
                          "recall": len(correct)/max(1,len(cells)),
                          "precision_in_annotated_numeric_scope": len(correct)/max(1,len(scoped))}
    return output


def route_map(record, case):
    tr = inverse(rotation_affine(record.get("rotation_ccw_degrees",0),case["size_px"],record.get("output_size_px",case["size_px"])))
    nodes = map_nodes(record.get("nodes",[]),tr,case["sha256"])
    for n in nodes:
        n.update(bbox=n["source_geometry"]["bbox_px"], polygon=n["source_geometry"]["polygon_px"])
    return nodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--split",choices=["dev","holdout"],required=True)
    ap.add_argument("--policy",type=Path)
    ap.add_argument("--reuse-pages",type=Path)
    ap.add_argument("--atomic-reviews",action="store_true")
    ap.add_argument("--explicit-locales",action="store_true")
    ap.add_argument("--expert-recovery",action="store_true")
    ap.add_argument("--snapshot-policy",action="store_true")
    ap.add_argument("--freeze-only",action="store_true")
    args=ap.parse_args()
    if args.expert_recovery and not args.atomic_reviews:
        raise ValueError("expert_recovery_requires_atomic_reviews_for_stable_token_indices")
    root=args.output;root.mkdir(parents=True,exist_ok=False)
    for d in ("crops","working"): (root/d).mkdir()
    policy_path=args.policy or args.input/"frozen/backend"
    if args.snapshot_policy:
        saved=root/"frozen-policy";saved.mkdir()
        for name in ("ocr_table_fusion.py","ocr_region_fusion.py","image_geometry.py"):
            shutil.copy2(policy_path/name,saved/name)
        policy_path=saved
        (root/"policy-freeze.json").write_text(json.dumps({"at_utc":datetime.now(timezone.utc).isoformat(),
            "sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in saved.glob("*.py")},
            "config":{"atomic_reviews":args.atomic_reviews,"explicit_locales":args.explicit_locales,"expert_recovery":args.expert_recovery}},indent=2),encoding="utf-8")
    if args.freeze_only:
        if not args.snapshot_policy:raise ValueError("freeze_only_requires_snapshot")
        print(root/"policy-freeze.json",flush=True)
        return
    policy=load_policy(policy_path)
    manifest=json.loads((args.input/"manifest.json").read_text(encoding="utf-8"))
    cases=[deepcopy(c) for c in manifest["cases"] if c["split"]==args.split]
    requests=[]
    for c in cases:
        c["size_px"]=list(Image.open(c["path"]).size)
        if hashlib.sha256(Path(c["path"]).read_bytes()).hexdigest()!=c["sha256"]:raise ValueError("source_changed")
        requests.append({k:c[k] for k in ("id","path","sha256")})
    start=time.perf_counter()
    if args.reuse_pages:
        reports={p:json.loads((args.reuse_pages/(p+".json")).read_text(encoding="utf-8")) for p in ("rapid-structure","paddle-structure","paddle-page")}
        if any([(r["id"],r["sha256"]) for r in report["records"]] != [(r["id"],r["sha256"]) for r in requests] for report in reports.values()):
            raise ValueError("page_cache_identity_mismatch")
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={p:pool.submit(run_structure,root,p,requests) for p in ("rapid-structure","paddle-structure")}
            reports={p:f.result() for p,f in futures.items()}
        reports["paddle-page"]=run_worker(root,"paddle-page",requests)
    by={p:{r["id"]:r for r in report["records"]} for p,report in reports.items()}
    work={};crops=[]
    for c in cases:
        cid=c["id"];rr=by["rapid-structure"][cid]
        if rr.get("error"):raise RuntimeError(rr["error"])
        if by["paddle-page"][cid].get("error"):raise RuntimeError(by["paddle-page"][cid]["error"])
        first=rr["nodes"];cells=policy.structural_cells(rr,by["paddle-structure"][cid])
        if args.atomic_reviews:
            plans={"full":policy.plan_atomic_reviews(first,cells),"no_structure":policy.plan_atomic_reviews(first)}
        else:
            plans={"full":policy.plan_table_reviews(policy.bind_structures(first,cells)),"no_structure":policy.plan_table_reviews(first)}
        expert=route_map(by["paddle-page"][cid],c)
        recovery=policy.plan_expert_recovery(first,expert,source_sha256=c["sha256"],expert_source_sha256=c["sha256"]) if args.expert_recovery else None
        work[cid]={"first":first,"cells":cells,"plans":plans,"expert":expert,"recovery":recovery}
        needed=sorted({r["node_index"] for p in plans.values() for r in p["requests"]})
        for i in needed:
            review_nodes=plans["full"].get("nodes",first)
            crop,geometry=rectify_region(Image.open(c["path"]).convert("RGB"),review_nodes[i],working_to_source=IDENTITY,source_sha256=c["sha256"])
            p=root/"crops"/f"{cid}-{i}.png";crop.save(p)
            crops.append({"id":f"{cid}/{i}","path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"source_geometry":geometry})
        for i,proposal in enumerate((recovery or {}).get("proposals",[])):
            crop,geometry=rectify_region(Image.open(c["path"]).convert("RGB"),proposal["node"],working_to_source=IDENTITY,source_sha256=c["sha256"])
            p=root/"crops"/f"{cid}-extra-{i}.png";crop.save(p)
            crops.append({"id":f"{cid}/extra-{i}","path":str(p),"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"source_geometry":geometry})
    reqs=[{k:r[k] for k in ("id","path","sha256")} for r in crops]
    if reqs:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures={p:pool.submit(run_worker,root,p,reqs) for p in ("rapid-line","paddle-line")}
            reader_reports={p:f.result() for p,f in futures.items()}
    else:reader_reports={}
    readers={p:{r["id"]:r for r in report["records"]} for p,report in reader_reports.items()}
    crop_index={r["id"]:r for r in crops}
    observations={}
    for crop in crops:
        observations[crop["id"]]=[{"engine":p.split("-")[0],"text":rows[crop["id"]].get("text",""),
               "confidence":rows[crop["id"]].get("confidence",0),"crop_sha256":crop["sha256"],
               "seconds":rows[crop["id"]]["seconds"],"error":rows[crop["id"]].get("error")} for p,rows in readers.items()]
        observations[crop["id"]].append(tesseract_observation(crop["path"],crop["sha256"]))
    results=[]
    for c in cases:
        cid=c["id"];w=work[cid];first=w["first"];expert=w["expert"]
        variants={"rapid":deepcopy(first),"paddle":deepcopy(expert)};decisions={};costs={};routes={}
        rr=by["rapid-structure"][cid];ocr_seconds=rr["ocr_seconds"]
        structure_seconds=rr.get("rapid_table",{}).get("seconds",0)+rr.get("img2table",{}).get("seconds",0)+by["paddle-structure"][cid]["seconds"]
        expert_seconds=by["paddle-page"][cid]["seconds"]
        for v in ("full","no_structure","no_review","no_route"):
            plan=w["plans"]["no_structure" if v=="no_structure" else "full"]
            final=deepcopy(plan.get("nodes",first)) if v!="no_review" else deepcopy(first)
            selected_reviews=[];local_cost=0
            if v!="no_review":
                for r in plan["requests"]:
                    key=f"{cid}/{r['node_index']}";crop=crop_index[key];obs=observations[key]
                    decision=policy.reconcile_table_review(r["original_text"],obs,crop_sha256=crop["sha256"],context=r)
                    decision.update(node_index=r["node_index"],crop_path=crop["path"],source_geometry=crop["source_geometry"])
                    selected_reviews.append(decision)
                    local_cost+=sum(o["seconds"] for o in obs)
                    if decision["status"]=="corrected":
                        final[r["node_index"]]["text"]=decision["selected_text"]
                        final[r["node_index"]].pop("words",None)
            if args.expert_recovery and v!="no_route":
                recovery=w["recovery"];removed=set();added=[]
                expert_requested=recovery["gate"]["eligible"]
                if v!="no_review":
                    for i,proposal in enumerate(recovery["proposals"]):
                        key=f"{cid}/extra-{i}";crop=crop_index[key];obs=observations[key]
                        original=proposal["node"]["text"]
                        decision=policy.reconcile_table_review(original,obs,crop_sha256=crop["sha256"],context={})
                        decision.update(expert_proposal=True,crop_path=crop["path"],source_geometry=crop["source_geometry"],proposal_reason=proposal["reason"])
                        selected_reviews.append(decision);local_cost+=sum(o["seconds"] for o in obs)
                        if decision["status"]=="confirmed":
                            new=deepcopy(proposal["node"]);new["text"]=decision["selected_text"]
                            new["pixel_review"]=decision;added.append(new);removed.update(proposal["replace_token_indices"])
                variants[v]=[n for i,n in enumerate(final) if i not in removed]+added
                routes[v]={"selected":"local_plus_verified_expert_regions" if added else "local_fusion",
                           "expert_requested_by_quality":expert_requested,"gate":recovery["gate"],
                           "proposed":len(recovery["proposals"]),"accepted":len(added),"replaced_fragment_count":len(removed)}
            elif v!="no_route":
                route=policy.select_page_reader(first,final,expert,source_sha256=c["sha256"],expert_source_sha256=c["sha256"])
                variants[v]=route["nodes"]
                routes[v]={k:value for k,value in route.items() if k!="nodes"}
                expert_requested=route["expert_requested_by_quality"]
            else:
                variants[v]=final;expert_requested=False
            decisions[v]=selected_reviews
            costs[v]={"warm_stage_seconds_sum":ocr_seconds+(0 if v=="no_structure" else structure_seconds)+local_cost+(expert_seconds if expert_requested else 0),
                      "page_ocr_calls":1+int(expert_requested),"structure_calls":0 if v=="no_structure" else 3,
                      "crop_reader_calls":len(selected_reviews)*3,"paid_api_calls":0,"paid_api_cost":0,
                      "cost_scope":"measured_hot_stage_sum_shared_observations_not_standalone_latency"}
        costs["rapid"]={"warm_stage_seconds_sum":ocr_seconds,"page_ocr_calls":1,"structure_calls":0,"crop_reader_calls":0,"paid_api_calls":0,"paid_api_cost":0}
        costs["paddle"]={"warm_stage_seconds_sum":expert_seconds,"page_ocr_calls":1,"structure_calls":0,"crop_reader_calls":0,"paid_api_calls":0,"paid_api_cost":0}
        # Locale comes from the published corpus description, not per-word
        # labels. All six systems receive the same explicit parsing setting.
        locale="id-ID" if args.explicit_locales and c["dataset"]=="CORD-v2" else None
        scores={v:score(c["numeric_cells"],ns,policy,locale) for v,ns in variants.items()}
        transitions={}
        for v,s in scores.items():
            before=set(scores["rapid"]["value"]["matched_reference_indices"]);after=set(s["value"]["matched_reference_indices"])
            transitions[v]={"recovered":sorted(after-before),"regressed":sorted(before-after),"reference_regression_rate":len(before-after)/max(1,len(before))}
        results.append(dict(c,scores=scores,costs=costs,transitions=transitions,decisions=decisions,routes=routes,nodes=variants))
        print(json.dumps({"id":cid,"scores":{v:s["value"]["correct"] for v,s in scores.items()},"expected":len(c["numeric_cells"])}),flush=True)
    totals={}
    expected=sum(len(c["numeric_cells"]) for c in cases)
    for v in VARIANTS:
        totals[v]={"expected":expected,"pages":len(cases)}
        for metric in ("literal","value","tight_value"):
            correct=sum(c["scores"][v][metric]["correct"] for c in results)
            candidates=sum(c["scores"][v]["candidate_count_in_annotated_numeric_scope"] for c in results)
            totals[v][metric]={"correct":correct,"recall":correct/max(1,expected),"precision_in_annotated_numeric_scope":correct/max(1,candidates)}
        totals[v].update({k:sum(c["costs"][v][k] for c in results) for k in ("warm_stage_seconds_sum","page_ocr_calls","structure_calls","crop_reader_calls","paid_api_cost")})
        totals[v]["recovered"]=sum(len(c["transitions"][v]["recovered"]) for c in results)
        totals[v]["regressed"]=sum(len(c["transitions"][v]["regressed"]) for c in results)
    result={"summary":totals,"cases":results,"observations":observations,"wall_seconds":time.perf_counter()-start,
            "provenance":{"policy_sha256":hashlib.sha256(Path(policy.__file__).read_bytes()).hexdigest(),
               "implementation_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                    [Path(__file__),Path(__file__).with_name("ocr_table_fusion_worker.py"),Path(__file__).with_name("ocr_region_fusion_worker.py")]},
               "manifest_sha256":hashlib.sha256((args.input/"manifest.json").read_bytes()).hexdigest(),
               "split":args.split,"atomic_reviews":args.atomic_reviews,"explicit_locales":args.explicit_locales,"expert_recovery":args.expert_recovery,
               "locale_shared_by_all_baselines":True,"inference_requests_contain_answers":False,"upstream_pages_reused":bool(args.reuse_pages),
               "ablations_share_observations_not_six_independent_live_runs":True,"precision_scope":"official_numeric_word_locations_only",
               "models_may_have_trained_on_public_data":True,"official_leaderboard_result":False,"production_modified":False},
            "initialization_seconds":{p:r["initialization_seconds"] for p,r in dict(reports,**reader_reports).items()},
            "peak_rss_mb":{p:r.get("peak_rss_mb") for p,r in dict(reports,**reader_reports).items()}}
    (root/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(totals,indent=2),flush=True)


if __name__=="__main__":main()
