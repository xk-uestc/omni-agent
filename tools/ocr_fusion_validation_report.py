"""Auditable transfer report, component contributions and original-image viewer."""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys

from ocr_fusion_validation import load_policy

LABELS={"rapid":"Rapid 单模型","paddle":"Paddle 单模型","full":"完整融合","no_structure":"移除结构融合",
        "no_review":"移除局部复核","no_route":"移除专家路由"}


def aggregate(cases):
    results={}
    for variant in LABELS:
        count=sum(c["scores"][variant]["expected"] for c in cases)
        candidates=sum(c["scores"][variant]["candidate_count_in_annotated_numeric_scope"] for c in cases)
        result={"expected":count,"candidates":candidates,"pages":len(cases)}
        for metric in ("literal","value","tight_value"):
            n=sum(c["scores"][variant][metric]["correct"] for c in cases)
            result[metric]={"correct":n,"recall":n/max(1,count),"precision":n/max(1,candidates)}
            recovered=regressed=before_correct=0
            for c in cases:
                before=set(c["scores"]["rapid"][metric]["matched_reference_indices"])
                after=set(c["scores"][variant][metric]["matched_reference_indices"])
                recovered+=len(after-before);regressed+=len(before-after);before_correct+=len(before)
            result[metric].update(recovered=recovered,regressed=regressed,
                                 reference_regression_rate=regressed/max(1,before_correct))
        durations=[c["costs"][variant]["warm_stage_seconds_sum"] for c in cases]
        result["cost"]={"stage_seconds_sum":sum(durations),"mean_stage_seconds":statistics.mean(durations),
                        "median_stage_seconds":statistics.median(durations),"max_stage_seconds":max(durations),
                        "not_independently_timed_latency":True}
        for k in ("page_ocr_calls","structure_calls","crop_reader_calls","paid_api_cost"):
            result["cost"][k]=sum(c["costs"][variant][k] for c in cases)
        results[variant]=result
    return results


def report(root):
    names={"frozen_dev":"frozen-dev","frozen_holdout":"frozen-holdout",
           "optimized_dev":"optimized-dev-final","optimized_holdout":"optimized-holdout"}
    runs={k:json.loads((root/v/"result.json").read_text(encoding="utf-8")) for k,v in names.items()}
    freeze=json.loads((root/"freeze.json").read_text())
    optimized_freeze=json.loads((root/"optimized-freeze/policy-freeze.json").read_text())
    assert all(runs[k]["provenance"]["policy_sha256"]==freeze["sha256"]["ocr_table_fusion.py"] for k in ("frozen_dev","frozen_holdout"))
    assert all(runs[k]["provenance"]["policy_sha256"]==optimized_freeze["sha256"]["ocr_table_fusion.py"] for k in ("optimized_dev","optimized_holdout"))
    groups={k:aggregate(v["cases"]) for k,v in runs.items()}
    groups["optimized_all"]=aggregate(runs["optimized_dev"]["cases"]+runs["optimized_holdout"]["cases"])
    groups["frozen_all"]=aggregate(runs["frozen_dev"]["cases"]+runs["frozen_holdout"]["cases"])
    by_scene={}
    for scene in ("FUNSD","CORD-v2"):
        by_scene[scene]=aggregate([c for k in ("optimized_dev","optimized_holdout") for c in runs[k]["cases"] if c["dataset"]==scene])
    ablation={}
    for group in ("optimized_all","optimized_holdout","frozen_all"):
        g=groups[group]
        ablation[group]={v:{"additional_reference_matches_with_component":g["full"]["value"]["correct"]-g[v]["value"]["correct"],
                             "additional_stage_seconds_with_component":g["full"]["cost"]["stage_seconds_sum"]-g[v]["cost"]["stage_seconds_sum"]}
                         for v in ("no_structure","no_review","no_route")}
    viewer=[]
    policy=load_policy(root/"optimized-freeze/frozen-policy")
    for k in ("optimized_dev","optimized_holdout"):
        for c in runs[k]["cases"]:
            locale="id-ID" if c["dataset"]=="CORD-v2" else None
            predictions={v:policy.numeric_nodes(ns,number_locale=locale) for v,ns in c["nodes"].items()}
            viewer.append({key:c[key] for key in ("id","dataset","split","size_px","sha256","numeric_cells","scores","costs","decisions","routes","source")}
                          | {"predictions":predictions,"image":"images/"+Path(c["path"]).name})
    artifact={"groups":groups,"by_scene":by_scene,"ablations":ablation,"cases":viewer,
              "freeze":freeze,"optimized_freeze":optimized_freeze,
              "cold_start_seconds":{k:v["initialization_seconds"] for k,v in runs.items()},
              "worker_peak_rss_mb":{k:v["peak_rss_mb"] for k,v in runs.items()},
              "limitations":["16页小样本迁移验证，不是赛题官方评测，不证明SOTA或独一无二",
                             "公开数据可能进入上游模型训练，无法证明与预训练无重合",
                             "金额区域精确率仅针对官方数字标注位置，不代表全页精确率",
                             "Paddle基线未启用字符对齐；紧框指标反映当前输出形式，不能证明检测器优劣",
                             "每种策略复放同一组真实模型观察；成本是实际热阶段加总，不是六次独立端到端计时",
                             "id-ID是显式文档设置，六种优化版系统共用；相关增益不能全部归功于融合",
                             "尚未接入正式8030生产OCR链路"]}
    (root/"validation-summary.json").write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding="utf-8")
    payload=json.dumps(artifact,ensure_ascii=False).replace("<","\\u003c")
    html=HTML.replace("__DATA__",payload)
    (root/"validation-report.html").write_text(html,encoding="utf-8")
    print(json.dumps({"holdout":groups["optimized_holdout"]["full"],"ablations":ablation},ensure_ascii=False,indent=2),flush=True)
    print(root/"validation-report.html",flush=True)


HTML=r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Omni · OCR 独立验证</title>
<style>
:root{color-scheme:light;font-family:Inter,'Segoe UI','Microsoft Yahei',sans-serif;color:#243330;background:#f7f8f5}*{box-sizing:border-box}body{margin:0}main{max-width:1340px;margin:auto;padding:34px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #d7ded8;padding-bottom:18px}small,.muted{color:#65736d;font-size:13px;line-height:1.65}h1{font-size:34px;letter-spacing:-1px;margin:30px 0 10px}h2{font-size:20px;margin:28px 0 16px}p{line-height:1.75}button,select{font:inherit;cursor:pointer;padding:10px 13px;border:1px solid #cdd6cf;background:white;border-radius:6px;color:inherit}.controls{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.statline{display:flex;gap:42px;padding:22px 0;border-top:1px solid #d7ded8;border-bottom:1px solid #d7ded8}.statline strong{font-size:28px;display:block}.table-wrap{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:13px 10px;border-bottom:1px solid #dce2dc;white-space:nowrap}th{font-size:12px;color:#64716a}tr.selected{background:#e8f1e9}td strong{color:#137453}.viewer{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:28px;border-top:1px solid #d7ded8;padding-top:20px}.canvas{background:#e9eee8;padding:12px;max-height:740px;overflow:auto}.canvas svg{width:100%;height:auto;display:block}pre{white-space:pre-wrap;font:12px/1.65 Consolas,monospace;background:#eff1ed;padding:15px;max-height:400px;overflow:auto}.notes{border-top:1px solid #d7ded8;margin-top:30px;padding-top:20px}label{font-size:13px;display:flex;align-items:center;gap:7px}.legend{display:flex;gap:20px;margin:12px 0;font-size:13px}.dot{width:10px;height:10px;border-radius:50%;display:inline-block;margin-right:5px}.detail-list{max-height:310px;overflow:auto}a{color:#137453}li{margin:9px 0;line-height:1.5}@media(max-width:900px){main{padding:20px}.viewer{grid-template-columns:1fr}.statline{gap:22px;flex-wrap:wrap}h1{font-size:27px}}
</style><main><header><strong>Omni / OCR 实验记录</strong><small>冻结规则 · 真实图像 · 组件消融</small></header>
<h1>融合有多少收益，让新样本回答</h1><p id="conclusion"></p><div class="statline"><div><strong>16</strong><small>独立原始图像</small></div><div><strong>222</strong><small>官方标注数字</small></div><div><strong>8 + 8</strong><small>开发验证 / 留出验证</small></div><div><strong>0</strong><small>付费 API 调用</small></div></div>
<h2>统一比较</h2><div class="controls"><select id="group"><option value="optimized_holdout">优化后 · 留出验证</option><option value="optimized_dev">优化后 · 开发验证</option><option value="optimized_all">优化后 · 全部图像</option><option value="frozen_all">原冻结版 · 全部图像</option><option value="frozen_dev">原冻结版 · 开发验证</option><option value="frozen_holdout">原冻结版 · 留出验证</option></select><select id="metric"><option value="value">数值 + 原图位置</option><option value="literal">字面量 + 原图位置</option><option value="tight_value">数值 + 较紧定位（IoU ≥ 0.3）</option></select></div>
<div class="table-wrap"><table><thead><tr><th>方案</th><th>匹配数 / 标注数</th><th>召回率</th><th>区域精确率</th><th>恢复 / 回退</th><th>阶段耗时加总</th><th>全页 / 裁剪调用</th></tr></thead><tbody id="scores"></tbody></table></div><p class="muted">数值定位要求覆盖标注框至少 40%，一对一匹配；“较紧定位”另要求 IoU ≥ 0.3。区域精确率只计算覆盖官方数字标注区域的候选。阶段耗时共用实际推理观察，排除冷启动，不等于独立端到端延迟。id-ID 显式解析设置由优化版所有方案共享。</p>
<h2>拆开组件，检验贡献</h2><div id="ablation" class="table-wrap"></div><p class="muted">“移除局部复核”同时禁止采用未经原图裁剪确认的专家候选；组件贡献可能相互依赖，不可简单相加。</p>
<h2>查看原图、定位与复核证据</h2><div class="controls"><select id="case"></select><select id="variant"></select><label><input id="truth" type="checkbox" checked>官方数字框</label><label><input id="prediction" type="checkbox" checked>输出位置</label></div>
<div class="legend"><span><i class="dot" style="background:#27a56d"></i>官方标注</span><span><i class="dot" style="background:#2589cf"></i>当前输出位置</span></div><div class="viewer"><div class="canvas" id="canvas"></div><div><h3 id="caseTitle"></h3><p class="muted" id="caseInfo"></p><div id="caseScore"></div><h3>修复与复核记录</h3><div class="detail-list" id="reviews"></div><h3>路由与来源</h3><pre id="route"></pre></div></div>
<div class="notes"><h2>证据范围与限制</h2><ul id="limits"></ul><p class="muted">数据：<a href="https://guillaumejaume.github.io/FUNSD/">FUNSD 作者发布的真实扫描表单</a>；<a href="https://github.com/clovaai/cord">CORD 作者发布的真实收据图像</a>。CORD CC BY 4.0；部分图像可见拍摄倾斜、折痕和照明差异，逐张采集设备未公开。不是人工退化 PDF 伪装的扫描件。</p><details><summary>冻结 SHA、冷启动与内存证据</summary><pre id="provenance"></pre></details></div></main>
<script>
const DATA=__DATA__,L={rapid:'Rapid 单模型',paddle:'Paddle 单模型',full:'完整融合',no_structure:'移除结构融合',no_review:'移除局部复核',no_route:'移除专家路由'};
const $=id=>document.getElementById(id),esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),pct=n=>(100*n).toFixed(2)+'%';
const H=DATA.groups.optimized_holdout,delta=H.full.value.correct-H.rapid.value.correct,pd=H.full.value.correct-H.paddle.value.correct;
$('conclusion').textContent=`留出样本：完整融合匹配 ${H.full.value.correct}/${H.full.expected} 个数值，比共享解析设置的 Rapid ${delta>=0?'多':'少'} ${Math.abs(delta)} 个，比 Paddle ${pd>=0?'多':'少'} ${Math.abs(pd)} 个。相对 Rapid 的已正确参考项回退 ${H.full.value.regressed} 个。这是小样本工程验证，不能据此宣布 SOTA。`;
function table(){const g=$('group').value,m=$('metric').value,S=DATA.groups[g];$('scores').innerHTML=Object.entries(L).map(([v,name])=>{const s=S[v],q=s[m];return `<tr class="${v==='full'?'selected':''}"><td>${name}</td><td><strong>${q.correct} / ${s.expected}</strong></td><td>${pct(q.recall)}</td><td>${pct(q.precision)}</td><td>+${q.recovered} / −${q.regressed}</td><td>${s.cost.stage_seconds_sum.toFixed(2)} s</td><td>${s.cost.page_ocr_calls} / ${s.cost.crop_reader_calls}</td></tr>`}).join('');$('ablation').innerHTML='<table><thead><tr><th>移除组件</th><th>完整融合额外匹配数</th><th>完整融合额外阶段耗时</th></tr></thead><tbody>'+['no_structure','no_review','no_route'].map(v=>`<tr><td>${L[v]}</td><td>${S.full[m].correct-S[v][m].correct}</td><td>${(S.full.cost.stage_seconds_sum-S[v].cost.stage_seconds_sum).toFixed(2)} s</td></tr>`).join('')+'</tbody></table>'}
DATA.cases.forEach((c,i)=>$('case').add(new Option(`${c.split==='holdout'?'留出':'开发'} · ${c.dataset} · ${c.id}`,i)));Object.entries(L).forEach(([k,v])=>$('variant').add(new Option(v,k)));$('variant').value='full';
function viewer(){const c=DATA.cases[+$('case').value],v=$('variant').value,w=c.size_px[0],h=c.size_px[1];let svg=`<svg viewBox="0 0 ${w} ${h}" xmlns="http://www.w3.org/2000/svg"><image href="${esc(c.image)}" width="${w}" height="${h}"/>`;const box=(b,color,t)=>`<rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"><title>${esc(t)}</title></rect>`;
if($('truth').checked)c.numeric_cells.forEach(n=>svg+=box(n.bbox,'#27a56d',`官方 ${n.text} → ${n.value}`));if($('prediction').checked)c.predictions[v].forEach(n=>svg+=box(n.bbox,'#2589cf',`输出 ${n.quantity.surface} → ${n.quantity.value}`));$('canvas').innerHTML=svg+'</svg>';
$('caseTitle').textContent=c.id;const s=c.scores[v];$('caseInfo').textContent=`${c.dataset} · ${c.size_px.join(' × ')} · ${c.split} · SHA-256 ${c.sha256}`;$('caseScore').textContent=`数值匹配 ${s.value.correct}/${s.expected}；字面量 ${s.literal.correct}/${s.expected}；较紧定位 ${s.tight_value.correct}/${s.expected}`;
const rs=c.decisions[v]||[];$('reviews').innerHTML=rs.length?rs.map(r=>`<p><strong>${esc(r.original_text)} → ${esc(r.selected_text)}</strong><br><small>${esc(r.status)} · ${esc(r.reason)}</small><br><a href="${esc(r.crop_path.replace(/\\/g,'/').replace(/^([A-Z]):/,'file:///$1:'))}" target="_blank">查看原图裁剪</a><br><small>${r.observations.map(o=>`${esc(o.engine)}：${esc(o.text)} (${Number(o.confidence).toFixed(3)})`).join(' / ')}</small></p>`).join(''):'没有裁剪复核记录';$('route').textContent=JSON.stringify({route:c.routes[v]||'单模型 / 无路由',source:c.source},null,2)}
$('limits').innerHTML=DATA.limitations.map(v=>`<li>${esc(v)}</li>`).join('');$('provenance').textContent=JSON.stringify({original_freeze:DATA.freeze,optimized_freeze:DATA.optimized_freeze,cold_start_seconds:DATA.cold_start_seconds,peak_rss_mb:DATA.worker_peak_rss_mb},null,2);
['group','metric'].forEach(id=>$(id).addEventListener('change',table));['case','variant','truth','prediction'].forEach(id=>$(id).addEventListener('change',viewer));table();viewer();
</script></html>'''


if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("root",type=Path);args=ap.parse_args();report(args.root)
