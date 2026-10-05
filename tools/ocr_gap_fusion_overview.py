"""Small offline overview; each batch keeps its own policy and paired baseline."""
from html import escape
import json
from pathlib import Path

BASE=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def main():
    entries=[("新 16 图 · 主动补查","gap-fusion-holdout-20261005-final/fast-isolated",False),
             ("新 8 图 · 自动触发","gap-fusion-auto-holdout-20261005/fast",False),
             ("开发难例 · 自动触发","gap-fusion-auto-development-20261005",True)]
    data=[]
    for label,folder,development in entries:
        path=BASE/folder
        item=json.loads((path/"comparison.json").read_text(encoding="utf-8"))
        data.append(dict(label=label,development=development,summary=item["summary"],
            url=(path/"report.html").as_uri(),metrics=item["metrics"],scenes=item["scenes"]))
    output=BASE/"gap-fusion-overview-20261005.html"
    links="".join(f'<li><a href="{d["url"]}">{escape(d["label"])}</a></li>' for d in data)
    payload=json.dumps(data,ensure_ascii=False).replace("</","<\\/")
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OCR 融合优化 · 实测对照</title>
    <style>body{font:16px/1.7 system-ui;color:#26323e;background:#fafafa;max-width:1160px;margin:40px auto;padding:0 24px}h1{font-size:32px;font-weight:650}h2{font-size:22px;margin-top:30px}p{max-width:1040px}table{border-collapse:collapse;width:100%;margin:18px 0}td,th{padding:12px 8px;border-bottom:1px solid #dbe1e7;text-align:left}th{color:#536170;font-weight:500}.controls{display:flex;gap:12px;align-items:center;margin-top:24px;flex-wrap:wrap}select,button{font:inherit;padding:7px 12px;background:white;border:1px solid #bdc8d3;border-radius:6px}button{cursor:pointer}.good{color:#14785a;font-weight:650}.muted{color:#677582;font-size:14px}svg{width:100%;height:auto}a{color:#155ba1}#verdict{font-size:21px;border-top:1px solid #dbe1e7;border-bottom:1px solid #dbe1e7;padding:16px 0}.scroll{overflow:auto}svg text{font:14px system-ui}</style>
    <h1>OCR 融合优化：把计算用在缺失字段上</h1>
    <p>Rapid 先处理整页；少量疑难数字通过同一块原图的双模型识别复核。当多处金额字段缺失时，才让 Paddle 检测局部区域。目标是保留速度、补回漏项，并给每个结果准确的原图位置。</p>
    <svg viewBox="0 0 1080 132" role="img" aria-label="快速识别后根据金额字段缺失触发局部检测">
    <defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#7a8998"/></marker></defs>
    <g fill="white" stroke="#bac8d4"><rect x="5" y="44" width="140" height="45" rx="4"/><rect x="190" y="44" width="165" height="45" rx="4"/><rect x="425" y="5" width="200" height="45" rx="4"/><rect x="425" y="85" width="200" height="42" rx="4"/><rect x="710" y="44" width="155" height="45" rx="4"/><rect x="915" y="44" width="155" height="45" rx="4"/></g>
    <g text-anchor="middle" fill="#26323e"><text x="75" y="73">Rapid 全页识别</text><text x="272" y="73">多处金额缺失？</text><text x="525" y="34">是：Paddle 局部检测</text><text x="525" y="112">否：跳过检测开销</text><text x="787" y="73">同图识别与核对</text><text x="992" y="73">文字 + 原图定位</text></g>
    <g fill="none" stroke="#7a8998" marker-end="url(#a)"><path d="M145 66H185"/><path d="M355 66L420 27"/><path d="M355 66L420 106"/><path d="M625 27L705 66"/><path d="M625 106L705 66"/><path d="M865 66H910"/></g></svg>
    <div class="controls"><label for="batch">查看批次</label><select id="batch"></select><button id="timing" type="button">计入模型加载</button><a id="evidence">逐图原图与全部证据 ↗</a></div>
    <div id="verdict"></div><div class="scroll"><table><thead><tr><th>方法</th><th>数字匹配</th><th>数字召回</th><th>时间</th><th>相对 Paddle 速度</th></tr></thead><tbody id="rows"></tbody></table></div>
    <p id="cost"></p><p id="ablation"></p><p id="scene"></p>
    <h2>这轮做出的具体改进</h2>
    <ul><li>用原图可见墨迹和快速检测覆盖差异找漏检区域，解决“没有框就没有置信度”的问题。</li><li>用金额字段是否完整决定是否补查；每页最多两个检测窗口、面积最多 40%，正常页面跳过。</li><li>区分表格数字列与旋转编号，避免把三个独立的“0”合并成一串数字。</li><li>输出绑定实际识别裁剪的原图范围；已有字段不被新增候选覆盖，读数或标点不一致时拒绝新增。</li></ul>
    <h2>证据能说明什么</h2><p>这里比较的是公开真实图片的官方数字词标注，不是全文 OCR 或比赛总成绩。不同批次包含不同图像与冻结策略，不能横向比较为同一次实验，也不合并报总分。完整 Paddle 对照独立实跑；开发难例明确单独列出。</p>
    <p>局部检测在开发难例上有恢复作用，但新图若没有新增匹配，应如实记为零贡献。双识别器可能同时出错。Paddle 的行框与 Rapid 字符框口径不同，不以紧框召回或局部精确率宣称检测器全面优劣。</p>
    <p class="muted">时间口径：融合为逐页实际墙钟，含规划、裁剪、I/O 与 IPC；Paddle 为全页推理阶段，未含 IPC，因此速度比较偏保守。“计入模型加载”是在该时间上加初始化时间，不是完整应用冷启动延迟。Rapid 是共享的首阶段，不是另一次完整服务运行。正式 8030 服务尚未切换。工程调度创新不等于已经证明全球首创或 SOTA。</p>
    <h2>完整报告</h2><ul>__LINKS__</ul>
    <script>const batches=__DATA__;let includeInit=false;const select=document.getElementById('batch');batches.forEach((d,i)=>{const o=document.createElement('option');o.value=i;o.textContent=d.label;select.append(o)});function render(){const d=batches[+select.value],s=d.summary,n=s.expected;const fusion=s.wall_seconds+(includeInit?s.fast_initialization_seconds:0);const paddle=s.paddle_inference_seconds+(includeInit?s.paddle_initialization_seconds:0);document.getElementById('evidence').href=d.url;document.getElementById('rows').innerHTML=[['Rapid 首阶段',s.rapid,s.rapid_seconds,null],['融合版',s.fast,fusion,paddle/fusion],['Paddle 全页',s.paddle,paddle,1]].map(([name,count,t,ratio])=>`<tr><td>${name}</td><td>${count}/${n}</td><td>${(count/n*100).toFixed(2)}%</td><td>${t.toFixed(2)}s${includeInit&&name==='Rapid 首阶段'?'（仅推理）':''}</td><td>${ratio?ratio.toFixed(2)+' 倍':'—'}</td></tr>`).join('');document.getElementById('verdict').textContent=`${d.development?'开发诊断 · ':''}相对 Paddle ${ (paddle/fusion).toFixed(2)} 倍速度；数字召回差 ${(100*(s.fast-s.paddle)/n).toFixed(2)} 个百分点。`;document.getElementById('cost').textContent=`相对 Rapid 恢复 ${s.recovered} 项，回退 ${s.regressed} 项。局部检测 ${s.local_detection_calls} 次，平均覆盖页面 ${(s.average_detection_area_fraction*100).toFixed(2)}%。付费 API 0 次；融合路径全页 Paddle OCR 0 次。`;document.getElementById('ablation').textContent=`去掉局部检测后：${s.without_gap}/${n}；完整融合：${s.fast}/${n}。本批局部检测额外匹配 ${s.fast-s.without_gap} 项。前半程共享计时 ${s.without_gap_wall_seconds.toFixed(2)}s，不是独立端到端复测。`;document.getElementById('scene').textContent=d.scenes.map(x=>`${x.dataset}：融合 ${x.fast}/${x.expected}，Paddle ${x.paddle}/${x.expected}`).join('；');}select.onchange=render;document.getElementById('timing').onclick=()=>{includeInit=!includeInit;document.getElementById('timing').textContent=includeInit?'只看热运行':'计入模型加载';render()};render();</script></html>'''
    output.write_text(html.replace("__LINKS__",links).replace("__DATA__",payload),encoding="utf-8")
    print(output)


if __name__=="__main__":main()
