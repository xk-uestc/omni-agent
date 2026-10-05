"""Run a fresh sequential Paddle baseline, then render paired numeric evidence."""
import argparse
from html import escape
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ict-track8"))
from backend import ocr_table_fusion as policy
from ocr_fusion_validation import score, route_map
from ocr_region_fusion_live import run_worker


def main():
    ap=argparse.ArgumentParser();ap.add_argument("root",type=Path);ap.add_argument("--run-paddle",action="store_true")
    ap.add_argument("--experiment",type=Path)
    ap.add_argument("--development",action="store_true")
    args=ap.parse_args();root=args.root
    experiment=args.experiment or root/"fast";out=experiment if args.experiment else root
    fast=json.loads((experiment/"result.json").read_text(encoding="utf-8"))
    cases=fast["cases"]
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    if len(cases)!=len(manifest["cases"]):
        raise RuntimeError("incomplete_fast_run")
    requests=[{k:c[k] for k in ("id","path","sha256")} for c in cases]
    if args.run_paddle:baseline=run_worker(root,"paddle-page",requests,reuse=False)
    else:baseline=json.loads((root/"paddle-page.json").read_text(encoding="utf-8"))
    records=baseline["records"]
    if len(records)!=len(cases):raise RuntimeError("incomplete_baseline")
    by_id={r["id"]:r for r in records}
    rows=[]
    for c in cases:
        r=by_id[c["id"]]
        if r.get("error") or r["sha256"]!=c["sha256"]:raise RuntimeError("invalid_baseline")
        nodes=route_map(r,c)
        ps=score(c["numeric_cells"],nodes,policy,"id-ID" if c["dataset"]=="CORD-v2" else None)
        rows.append(dict(id=c["id"],expected=len(c["numeric_cells"]),rapid=c["scores"]["rapid"],fast=c["scores"]["fast"],
                         paddle=ps,fast_wall=c["timing"]["warm_page_wall_seconds"],paddle_inference=r["seconds"],
                         recovered=c["recovered"],regressed=c["regressed"],crops=c["crop_count"],
                         without_gap=c["scores"].get("without_gap",c["scores"]["fast"]),
                         without_gap_wall=c["timing"].get("without_gap_wall_seconds",c["timing"]["warm_page_wall_seconds"])))
    summary=dict(fast["summary"])
    summary.update(paddle=sum(r["paddle"]["value"]["correct"] for r in rows),
                   paddle_inference_seconds=sum(r["paddle_inference"] for r in rows),
                   paddle_worker_wall_seconds=baseline["worker_wall_seconds"],
                   fast_initialization_seconds=fast["initialization_wall_seconds"],
                   paddle_initialization_seconds=baseline["initialization_seconds"])
    summary["conservative_speed_ratio"]=summary["paddle_inference_seconds"]/summary["wall_seconds"]
    summary["accuracy_gap_to_paddle_pp"]=100*(summary["fast"]-summary["paddle"])/summary["expected"]
    summary["without_gap"]=sum(r["without_gap"]["value"]["correct"] for r in rows)
    summary["without_gap_wall_seconds"]=sum(r["without_gap_wall"] for r in rows)
    summary["local_detection_calls"]=fast.get("local_paddle_detection_calls",0)
    summary["average_detection_area_fraction"]=sum(c.get("gap_plan",{}).get("area_fraction",0) for c in cases)/len(cases)
    aggregates={}
    for method in ("rapid","fast","paddle"):
        scoped=sum(r[method]["candidate_count_in_annotated_numeric_scope"] for r in rows)
        aggregates[method]={"numeric_candidates_in_annotated_scope":scoped,
            "ignored_candidates_outside_numeric_scope":sum(r[method]["ignored_candidates_outside_numeric_scope"] for r in rows)}
        for metric in ("literal","value","tight_value"):
            correct=sum(r[method][metric]["correct"] for r in rows)
            aggregates[method][metric]={"correct":correct,"recall":correct/summary["expected"],
                "precision_in_annotated_numeric_scope":correct/max(1,scoped)}
    scenes=[]
    for dataset in sorted({c["dataset"] for c in cases}):
        subset=[r for c,r in zip(cases,rows) if c["dataset"]==dataset]
        total=sum(r["expected"] for r in subset)
        scenes.append(dict(dataset=dataset,expected=total,pages=len(subset),
            rapid=sum(r["rapid"]["value"]["correct"] for r in subset),
            fast=sum(r["fast"]["value"]["correct"] for r in subset),
            paddle=sum(r["paddle"]["value"]["correct"] for r in subset),
            without_gap=sum(r["without_gap"]["value"]["correct"] for r in subset),
            fast_wall_seconds=sum(r["fast_wall"] for r in subset),paddle_seconds=sum(r["paddle_inference"] for r in subset)))
    result=dict(summary=summary,rows=rows,scope="official annotated numeric words only; not full-text OCR or contest score",
                source_selection=manifest["freeze"]["sample_selection"],
                locale="id-ID shared by all CORD methods",upstream_training_overlap_unknown=True,
                timing="Fast warm wall includes crop I/O and IPC; Paddle warm inference excludes IPC; conservative comparison, one run",
                production_enabled=False,metrics=aggregates,development=args.development,scenes=scenes,gap_policy=fast.get("gap_policy","always"))
    (out/"comparison.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    n=summary["expected"]
    top="".join(f'<tr><td>{label}</td><td>{summary[key]}/{n}</td><td>{summary[key]/n:.1%}</td><td>{seconds:.2f}s</td></tr>'
        for label,key,seconds in [("Rapid 全页","rapid",summary["rapid_seconds"]),("Rapid + 裁剪双读","fast",summary["wall_seconds"]),("Paddle 全页","paddle",summary["paddle_inference_seconds"])])
    details=""
    metric_table="".join(f'<tr><td>{method}</td><td>{a["literal"]["recall"]:.1%}</td><td>{a["value"]["precision_in_annotated_numeric_scope"]:.1%}</td><td>{a["tight_value"]["recall"]:.1%}</td><td>{a["ignored_candidates_outside_numeric_scope"]}</td></tr>' for method,a in aggregates.items())
    scene_table="".join(f'<tr><td>{s["dataset"]}</td><td>{s["rapid"]}/{s["expected"]}</td><td>{s["without_gap"]}/{s["expected"]}</td><td>{s["fast"]}/{s["expected"]}</td><td>{s["paddle"]}/{s["expected"]}</td><td>{s["fast_wall_seconds"]:.2f}s / {s["paddle_seconds"]:.2f}s</td></tr>' for s in scenes)
    for c,r in zip(cases,rows):
        img=Path(c["path"]).as_uri()
        boxes="".join(f'<rect x="{q["bbox"][0]}" y="{q["bbox"][1]}" width="{q["bbox"][2]-q["bbox"][0]}" height="{q["bbox"][3]-q["bbox"][1]}" fill="none" stroke="{ "#dc2626" if i not in c["scores"]["fast"]["value"]["matched_reference_indices"] else "#059669"}" stroke-width="2"><title>{escape(q["text"])}</title></rect>' for i,q in enumerate(c["numeric_cells"]))
        boxes+="".join(f'<rect x="{v["bbox"][0]}" y="{v["bbox"][1]}" width="{v["bbox"][2]-v["bbox"][0]}" height="{v["bbox"][3]-v["bbox"][1]}" fill="#7c3aed" fill-opacity=".07" stroke="#7c3aed" stroke-dasharray="8 5" stroke-width="2"><title>局部检测预算区域</title></rect>' for v in c.get("gap_plan",{}).get("regions",[]))
        reviews="".join(f'<tr><td>{escape(v["request"]["kind"])}</td><td>{escape(v["request"]["text"])}</td><td>{escape(v["rapid"].get("text",""))}</td><td>{escape(v["paddle"].get("text",""))}</td><td>{escape(v["decision"]["reason"])}</td></tr>' for v in c["reviews"])
        w,h=c["size_px"]
        details+=f'<details><summary>{escape(c["id"])} · Rapid {r["rapid"]["value"]["correct"]} / 融合 {r["fast"]["value"]["correct"]} / Paddle {r["paddle"]["value"]["correct"]} · 共 {r["expected"]} 项 · {r["crops"]} 次裁剪</summary><svg viewBox="0 0 {w} {h}"><image href="{img}" width="{w}" height="{h}"/>{boxes}</svg><table><tr><th>裁剪类型</th><th>原文</th><th>Rapid</th><th>Paddle</th><th>决定</th></tr>{reviews}</table></details>'
    innovation=(f'<h2>覆盖缺口与局部检测消融</h2><p>移除局部检测：{summary["without_gap"]}/{n}，共享前半程墙钟 {summary["without_gap_wall_seconds"]:.2f}s；启用局部检测：{summary["fast"]}/{n}，实际完整墙钟 {summary["wall_seconds"]:.2f}s。共 {summary["local_detection_calls"]} 个局部检测窗口，平均面积 {summary["average_detection_area_fraction"]:.1%}，每页最多 2 窗口、累计面积最多 40%。紫色虚线为实际补查区域。</p>' if fast.get("gap_detection") else "")
    evidence_link="result.json" if args.experiment else "fast/result.json"
    freeze_link="freeze.json" if args.experiment else "fast/freeze.json"
    sample_label="参与开发诊断的" if args.development else "冻结规则后首次验证的"
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>快速 OCR 融合 · 新样本实测</title>
    <style>body{{font:16px/1.7 system-ui;margin:35px auto;max-width:1100px;padding:0 24px;color:#18212d;background:#f7f9fb}}h1{{font-size:30px}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{border-bottom:1px solid #ddd;text-align:left;padding:10px}}details{{border-top:1px solid #ccd5df;padding:16px 0}}summary{{cursor:pointer}}svg{{max-height:600px;width:100%;margin:20px 0}}.metric{{font-size:22px;color:#126351}}</style>
    <h1>Rapid 检测 + Paddle 局部识别</h1><p>{sample_label} {len(cases)} 张真实图片，{n} 个官方数字词标注。没有全页 Paddle 专家调用，没有付费 API。此报告评估数字识别，不代表全文 OCR 或赛题总成绩。</p>
    <table><tr><th>方法</th><th>数字匹配</th><th>召回</th><th>热运行时间</th></tr>{top}</table>
    <p class="metric">相对全页 Paddle：{summary["conservative_speed_ratio"]:.2f} 倍速度；精度差 {summary["accuracy_gap_to_paddle_pp"]:+.2f} 个百分点。</p>
    <p>恢复 {summary["recovered"]} 项，回退 {summary["regressed"]} 项。融合冷启动 {summary["fast_initialization_seconds"]:.2f}s；Paddle 冷启动 {summary["paddle_initialization_seconds"]:.2f}s。Rapid 时间是融合中共享的全页推理阶段；融合是实际逐页墙钟，Paddle 是全页推理阶段，未含 IPC，速度对比偏保守。单轮结果，未证明统计显著性。</p>
    <p>匹配要求同值、原图位置覆盖 ≥40%、一对一。CORD 所有方案共享印尼数字格式。绿色框为融合正确的官方数字位置，红色框为未匹配；未标注区域不计入精确率。Paddle 行框没有字符对齐，紧框指标不能直接代表检测器优劣。上游预训练是否含这些公开图像未知。实验尚未切换生产服务。</p>
    <table><tr><th>方法</th><th>字面数字召回</th><th>标注范围内数值精确率</th><th>紧框数字召回</th><th>标注范围外候选</th></tr>{metric_table}</table>
    {innovation}<h2>分场景对照</h2><table><tr><th>数据</th><th>Rapid</th><th>无局部检测</th><th>完整融合</th><th>Paddle</th><th>融合 / Paddle 时间</th></tr>{scene_table}</table>
    <p>局部检测策略：{escape(fast.get("gap_policy","always"))}。无局部检测的耗时来自同一次运行的共享前半程，并非另一次独立端到端实测。Paddle 检测器仅支持位置，不作为第三个文字识别投票；两个识别模型同属 PP-OCR 系列，错误可能相关。这是局部调度与同源证据融合的工程方案，尚无证据证明全球首创或 SOTA。Paddle 行框跨度较大，会把邻近数字预测计入精确率分母；不应仅凭这一列认定其全文误报率更高。</p>
    <h2>逐图原图与裁剪证据</h2>{details}<p><a href="comparison.json">完整机器可读指标</a> · <a href="{freeze_link}">实现冻结哈希</a> · <a href="{evidence_link}">全部推理证据</a></p></html>'''
    (out/"report.html").write_text(html,encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)


if __name__=="__main__":main()
