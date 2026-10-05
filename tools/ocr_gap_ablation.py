"""Frozen, paired OCR gap ablation; independent serial runs and evidence report."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from html import escape
import hashlib
import json
from pathlib import Path
import random
import statistics
import subprocess
import sys

PROJECT=Path(__file__).resolve().parents[1]
BASE=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")
SOURCES=[PROJECT/"ict-track8/backend"/f for f in (
    "ocr_gap_fusion.py","ocr_fast_fusion.py","ocr_table_fusion.py","ocr_region_fusion.py","image_geometry.py")]
SOURCES += [PROJECT/"tools"/f for f in ("ocr_fast_fusion_trial.py","ocr_fast_fusion_worker.py","ocr_fusion_validation.py")]
MODES=("off","auto","always")


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(root):
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    samples={c["sha256"] for c in manifest["cases"]}
    prior=[]
    # Known OCR experiment manifests, not arbitrary unrelated model folders.
    for folder in BASE.iterdir():
        p=folder/"manifest.json"
        if folder==root or not p.exists() or not any(x in folder.name for x in ("fusion","ablation")):continue
        d=json.loads(p.read_text(encoding="utf-8"))
        previous={c.get("sha256") for c in d.get("cases",[])}
        if samples&previous:prior.append(str(p))
    if prior:raise RuntimeError("input_overlaps_previous_experiments: "+str(prior))
    for c in manifest["cases"]:
        if digest(Path(c["path"]))!=c["sha256"]:raise RuntimeError("input_changed")
    if len(samples)!=len(manifest["cases"]):raise RuntimeError("duplicate_input")
    frozen=dict(at_utc=datetime.now(timezone.utc).isoformat(),selection=manifest["freeze"]["sample_selection"],
        manifest_sha256=digest(root/"manifest.json"),implementation_sha256={str(p):digest(p) for p in SOURCES},
        modes=list(MODES),run_order=list(MODES),paid_api_calls=0,policy_updates_allowed=False,
        previous_overlap=[],baseline="off: same fusion with local detection disabled",
        scope="original public images, numeric-word location and value; not full-text OCR or official contest score",
        selection_policy="consecutive unseen test indices, no selection using gold answers or OCR performance")
    (root/"ablation-freeze.json").write_text(json.dumps(frozen,indent=2),encoding="utf-8")
    (root/"ablation-source").mkdir(exist_ok=False)
    for p in SOURCES:(root/"ablation-source"/p.name).write_bytes(p.read_bytes())
    return frozen


def verify_freeze(root):
    f=json.loads((root/"ablation-freeze.json").read_text(encoding="utf-8"))
    if digest(root/"manifest.json")!=f["manifest_sha256"]:raise RuntimeError("manifest_changed")
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    for sample in manifest["cases"]:
        if digest(Path(sample["path"]))!=sample["sha256"]:raise RuntimeError("input_changed")
    for p,h in f["implementation_sha256"].items():
        if digest(Path(p))!=h:raise RuntimeError("policy_changed_after_freeze: "+p)
        if digest(root/"ablation-source"/Path(p).name)!=h:raise RuntimeError("snapshot_changed")
    return f


def index_result(data,manifest):
    expected={(c["id"],c["sha256"]) for c in manifest["cases"]}
    records=data["cases"]
    if len(records)!=len(expected) or {(c["id"],c["sha256"]) for c in records}!=expected:
        raise RuntimeError("incomplete_or_wrong_inputs")
    return {c["id"]:c for c in records}


def comparison(before,after,metric="value"):
    b=set(before[metric]["matched_reference_indices"]);a=set(after[metric]["matched_reference_indices"])
    return dict(recovered=sorted(a-b),regressed=sorted(b-a),net=len(a)-len(b))


def bootstrap_page_difference(rows,seed=20261005):
    if not rows:return None
    rng=random.Random(seed);values=[]
    for _ in range(4000):
        selected=rng.choices(rows,k=len(rows))
        values.append(100*sum(r["paired_local_effect"]["net"] for r in selected)/max(1,sum(r["expected"] for r in selected)))
    values.sort()
    return [values[int(len(values)*.025)],values[int(len(values)*.975)]]


def metrics(cases,method):
    n=sum(len(c["numeric_cells"]) for c in cases)
    scope=sum(c["scores"][method]["candidate_count_in_annotated_numeric_scope"] for c in cases)
    out=dict(expected=n,annotated_scope_candidates=scope,
        outside_scope_candidates=sum(c["scores"][method]["ignored_candidates_outside_numeric_scope"] for c in cases))
    for metric in ("value","literal","tight_value"):
        correct=sum(c["scores"][method][metric]["correct"] for c in cases)
        out[metric]=dict(correct=correct,recall=correct/max(1,n),precision_in_annotated_numeric_scope=correct/max(1,scope))
    return out


def analyze(root):
    f=verify_freeze(root);manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    results={m:json.loads((root/m/"result.json").read_text(encoding="utf-8")) for m in MODES}
    indices={m:index_result(d,manifest) for m,d in results.items()}
    for m,d in results.items():
        if d["implementation_sha256"]!=f["implementation_sha256"]:raise RuntimeError("unmatched_implementation_freeze")
        if bool(d.get("gap_detection"))!=(m!="off") or (m!="off" and d.get("gap_policy")!=m):
            raise RuntimeError("wrong_ablation_mode: "+m)
        if any(v[e].get("error") for c in d["cases"] for v in c["reviews"] for e in ("rapid","paddle")):
            raise RuntimeError("reader_error_in_result")
    rows=[]
    for sample in manifest["cases"]:
        cid=sample["id"];off=indices["off"][cid]
        row=dict(id=cid,dataset=sample["dataset"],expected=len(sample["numeric_cells"]),modes={})
        for mode in MODES:
            c=indices[mode][cid]
            local=comparison(c["scores"]["without_gap"],c["scores"]["fast"])
            independent=comparison(off["scores"]["fast"],c["scores"]["fast"])
            stable=c["scores"]["without_gap"]==off["scores"]["fast"]
            row["modes"][mode]=dict(correct=c["scores"]["fast"]["value"]["correct"],
                literal=c["scores"]["fast"]["literal"]["correct"],
                warm_seconds=c["timing"]["warm_page_wall_seconds"],
                shared_prefix_seconds=c["timing"]["without_gap_wall_seconds"],
                additional_seconds=max(0,c["timing"]["warm_page_wall_seconds"]-c["timing"]["without_gap_wall_seconds"]),
                paired_local_effect=local,independent_effect=independent,shared_prefix_equals_off=stable,
                triggered=c.get("gap_plan",{}).get("route",{}).get("triggered",False),
                detected_windows=len(c.get("gap_plan",{}).get("regions",[])),
                area_fraction=c.get("gap_plan",{}).get("area_fraction",0),
                gap_reviews=c.get("gap_review_count",0),
                compared_to_rapid=comparison(c["scores"]["rapid"],c["scores"]["fast"]),
                reasons=dict(Counter(v["decision"]["reason"] for v in c["reviews"] if v["request"].get("proposal_source")=="budgeted_paddle_local_detection")))
        rows.append(row)
    totals={}
    for mode in MODES:
        selected=[dict(r["modes"][mode],expected=r["expected"]) for r in rows];d=results[mode]
        totals[mode]=dict(metrics=metrics(d["cases"],"fast"),warm_seconds=sum(r["warm_seconds"] for r in selected),
            initialization_seconds=d["initialization_wall_seconds"],median_page_seconds=statistics.median(r["warm_seconds"] for r in selected),
            recovered_by_local=sum(len(r["paired_local_effect"]["recovered"]) for r in selected),
            regressed_by_local=sum(len(r["paired_local_effect"]["regressed"]) for r in selected),
            recovered_vs_rapid=sum(len(r["compared_to_rapid"]["recovered"]) for r in selected),
            regressed_vs_rapid=sum(len(r["compared_to_rapid"]["regressed"]) for r in selected),
            pages_improved=sum(r["paired_local_effect"]["net"]>0 for r in selected),
            pages_regressed=sum(r["paired_local_effect"]["net"]<0 for r in selected),
            baseline_prefix_mismatch_pages=sum(not r["shared_prefix_equals_off"] for r in selected),
            actual_detection_pages=sum(r["detected_windows"]>0 for r in selected),
            triggered_pages=sum(r["triggered"] for r in selected),
            local_detection_windows=sum(r["detected_windows"] for r in selected),
            mean_area_fraction=statistics.mean(r["area_fraction"] for r in selected),
            incremental_shared_wall_seconds=sum(r["additional_seconds"] for r in selected),
            paired_page_bootstrap_95ci_pp=bootstrap_page_difference(selected))
    scenes=[]
    for dataset in sorted({r["dataset"] for r in rows}):
        subset=[r for r in rows if r["dataset"]==dataset]
        scenes.append(dict(dataset=dataset,pages=len(subset),expected=sum(r["expected"] for r in subset),
            modes={m:dict(correct=sum(r["modes"][m]["correct"] for r in subset),
                recovered=sum(len(r["modes"][m]["paired_local_effect"]["recovered"]) for r in subset),
                regressed=sum(len(r["modes"][m]["paired_local_effect"]["regressed"]) for r in subset)) for m in MODES}))
    result=dict(freeze=f,totals=totals,rows=rows,scenes=scenes,
        confidence_interval="4000 paired page bootstrap draws, seed 20261005; small sample exploratory interval",
        limitations=["numeric-word score only", "public samples may overlap upstream pretraining", "single run per mode, fixed order",
                    "annotated-scope precision is not full-page false-positive rate", "no full Paddle rerun in this ablation",
                    "consecutive real scans/receipts; not claimed a Chinese or complex-table benchmark"],production_enabled=False)
    (root/"ablation-summary.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    render(root,result,indices)
    print(json.dumps(totals,ensure_ascii=False,indent=2),flush=True)


def render(root,result,indices):
    labels=dict(off="关闭局部检测",auto="自动触发局部检测",always="始终检查覆盖缺口")
    table=""
    for mode,s in result["totals"].items():
        metric=s["metrics"];n=metric["expected"]
        ci=s["paired_page_bootstrap_95ci_pp"]
        table+=f'<tr><td>{labels[mode]}</td><td>{metric["value"]["correct"]}/{n}（{metric["value"]["recall"]:.2%}）</td><td>{metric["literal"]["recall"]:.2%}</td><td>{metric["value"]["precision_in_annotated_numeric_scope"]:.2%}</td><td>+{s["recovered_by_local"]} / −{s["regressed_by_local"]}</td><td>{s["actual_detection_pages"]} 页 / {s["local_detection_windows"]} 窗口</td><td>{s["warm_seconds"]:.2f}s</td><td>{s["initialization_seconds"]:.2f}s</td><td>[{ci[0]:+.2f}, {ci[1]:+.2f}]</td></tr>'
    detail=""
    for row in result["rows"]:
        c=indices["always"][row["id"]];w,h=c["size_px"]
        parts=[]
        for mode in ("auto","always"):
            v=row["modes"][mode]
            recovered_texts=[c["numeric_cells"][i]["text"] for i in v["paired_local_effect"]["recovered"]]
            parts.append(f'{labels[mode]}：{v["correct"]} 项，新增 {len(v["paired_local_effect"]["recovered"])}（{escape(str(recovered_texts))}），回退 {len(v["paired_local_effect"]["regressed"])}，{v["warm_seconds"]:.2f}s')
        boxes=""
        gained=set(row["modes"]["always"]["paired_local_effect"]["recovered"])
        for i,q in enumerate(c["numeric_cells"]):
            b=q["bbox"];color="#059669" if i in gained else "#94a3b8"
            boxes+=f'<rect x="{b[0]}" y="{b[1]}" width="{b[2]-b[0]}" height="{b[3]-b[1]}" fill="none" stroke="{color}" stroke-width="2"><title>{escape(q["text"])}</title></rect>'
        for r in c["gap_plan"]["regions"]:
            b=r["bbox"];boxes+=f'<rect x="{b[0]}" y="{b[1]}" width="{b[2]-b[0]}" height="{b[3]-b[1]}" fill="#7c3aed" fill-opacity=".06" stroke="#7c3aed" stroke-dasharray="8 5"/>'
        review="".join(f'<tr><td>{escape(v["rapid"].get("text",""))}</td><td>{escape(v["paddle"].get("text",""))}</td><td>{escape(v["decision"]["reason"])}</td></tr>' for v in c["reviews"] if v["request"].get("proposal_source")=="budgeted_paddle_local_detection")
        off=row["modes"]["off"]
        baseline=indices["off"][row["id"]]
        failures=""
        for i in off["compared_to_rapid"]["regressed"]:
            ref=baseline["numeric_cells"][i]
            failures+=f'<p>原有裁剪复核回退：官方参考数字 {escape(ref["text"])}，原图位置 {ref["bbox"]}。</p>'
        if failures:
            failures+='<table><tr><th>Rapid 裁剪读数</th><th>Paddle 裁剪读数</th><th>原有复核决定（全部接受项）</th></tr>'
            for v in baseline["reviews"]:
                if v["decision"].get("accepted"):
                    failures+=f'<tr><td>{escape(v["rapid"].get("text",""))}</td><td>{escape(v["paddle"].get("text",""))}</td><td>{escape(v["decision"]["reason"])}</td></tr>'
            failures+='</table>'
        detail+=f'<details><summary>{escape(row["id"])} · 基线 {off["correct"]}/{row["expected"]} · 自动 {row["modes"]["auto"]["correct"]} · 始终 {row["modes"]["always"]["correct"]}</summary><p>{"；".join(parts)}</p>{failures}<svg viewBox="0 0 {w} {h}"><image href="{Path(c["path"]).as_uri()}" width="{w}" height="{h}"/>{boxes}</svg><table><tr><th>Rapid 读数</th><th>Paddle 读数</th><th>局部检测候选决定</th></tr>{review}</table></details>'
    auto=result["totals"]["auto"];always=result["totals"]["always"];n=auto["metrics"]["expected"]
    verdict=f'自动模式独立恢复 {auto["recovered_by_local"]} 项、回退 {auto["regressed_by_local"]} 项；始终补查恢复 {always["recovered_by_local"]} 项、回退 {always["regressed_by_local"]} 项。'
    if auto["recovered_by_local"]==0:verdict+=" 本批未证明自动局部检测能增加数字匹配。"
    mismatch=sum(s["baseline_prefix_mismatch_pages"] for s in result["totals"].values())
    scenes="".join(f'<tr><td>{s["dataset"]}</td><td>{s["pages"]} 页 / {s["expected"]} 项</td><td>{s["modes"]["off"]["correct"]}</td><td>{s["modes"]["auto"]["correct"]}</td><td>{s["modes"]["always"]["correct"]}</td></tr>' for s in result["scenes"])
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>局部检测创新 · 冻结新样本消融</title><style>body{{font:16px/1.7 system-ui;color:#26323e;max-width:1250px;margin:35px auto;padding:0 25px;background:#fafafa}}h1{{font-size:30px}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #d6dee6;padding:10px 8px;text-align:left}}th{{font-weight:500;color:#586675}}.scroll{{overflow:auto}}.verdict{{font-size:21px;border-block:1px solid #ccd5df;padding:15px 0}}details{{border-top:1px solid #d6dee6;padding:16px 0}}summary{{cursor:pointer}}svg{{width:100%;max-height:600px}}a{{color:#155ba1}}</style>
    <h1>局部检测：独立贡献与计算成本</h1><p>固定规则、固定新样本，三种模式分别完整真实推理，按关闭→自动→始终顺序串行运行。{len(result["rows"])} 张首次使用的真实图像，{n} 个官方数字词标注。选择：{escape(result["freeze"]["selection"])}。</p><p class="verdict">{verdict}</p>
    <div class="scroll"><table><tr><th>方法</th><th>同值位置匹配</th><th>字面召回</th><th>标注范围内精确率</th><th>局部机制新增 / 回退</th><th>实际检测</th><th>热墙钟</th><th>模型加载</th><th>页重采样95%区间（百分点）</th></tr>{table}</table></div>
    <p>关闭局部检测仍包含原有双读裁剪复核；本报告衡量新增检测分支，而不是与单独 Rapid 比较。额外新增/回退采用同一次运行的共享前半程逐词配对，跨独立关闭模式的前半程评分不一致累计 {mismatch} 页。</p>
    <p>原有裁剪融合相对初始 Rapid 恢复 {result["totals"]["off"]["recovered_vs_rapid"]} 项、回退 {result["totals"]["off"]["regressed_vs_rapid"]} 项。新增分支回退为零不代表整个融合系统回退为零；两个识别器在同一裁剪上也可能一致地读错。</p>
    <p>自动机制实际额外墙钟 {auto["incremental_shared_wall_seconds"]:.2f}s，始终补查 {always["incremental_shared_wall_seconds"]:.2f}s；两者平均检测面积分别 {auto["mean_area_fraction"]:.2%}、{always["mean_area_fraction"]:.2%}。独立总时间包含 I/O、IPC、裁剪和所有推理，不含评分；加载时间另计。固定顺序各运行一次，不能作为稳定 P95 延迟结论。</p>
    <h2>真实扫描表单与拍摄收据</h2><table><tr><th>语料</th><th>样本</th><th>关闭</th><th>自动</th><th>始终</th></tr>{scenes}</table>
    <p>没有根据参考答案或 OCR 成绩筛选困难图片，没有人造退化图。FUNSD 为真实噪声扫描，CORD 为真实收据拍摄；没有中文复杂表格样本，不能称已经覆盖中文/复杂表格。公开数据可能进入上游预训练。只评数字词，范围内精确率不是全页误报率。页级 bootstrap 区间为小样本探索性估计，区间含零时不足以认定稳定增益。</p>
    <h2>逐图原图、局部窗口及失败读数</h2><p>紫色为始终补查的原图检测窗口；绿色为该分支新增匹配的官方数字词，灰色为其他数字词。所有无增益页保留。</p>{detail}
    <p><a href="ablation-summary.json">完整指标</a> · <a href="ablation-freeze.json">冻结记录</a> · <a href="off/result.json">关闭原始结果</a> · <a href="auto/result.json">自动原始结果</a> · <a href="always/result.json">始终原始结果</a></p><p>付费 API 0 次，未修改算法阈值，未切换正式生产服务。本消融不重新运行完整 Paddle，避免将此前不同图像的速度比混入本报告。</p></html>'''
    (root/"ablation-report.html").write_text(html,encoding="utf-8")


def main():
    ap=argparse.ArgumentParser();ap.add_argument("root",type=Path);ap.add_argument("--run",action="store_true")
    args=ap.parse_args();root=args.root.resolve()
    if args.run:
        if (root/"ablation-freeze.json").exists():verify_freeze(root)
        else:freeze(root)
        for mode in MODES:
            verify_freeze(root)
            output=root/mode
            if (output/"result.json").exists():
                index_result(json.loads((output/"result.json").read_text(encoding="utf-8")),json.loads((root/"manifest.json").read_text(encoding="utf-8")))
                continue
            command=[sys.executable,str(PROJECT/"tools/ocr_fast_fusion_trial.py"),"--source",str(root),"--output",str(output)]
            if mode!="off":command += ["--gap-detection","--gap-policy",mode]
            subprocess.run(command,cwd=PROJECT,check=True)
    analyze(root)


if __name__=="__main__":main()
