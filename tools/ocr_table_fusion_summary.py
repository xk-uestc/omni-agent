"""Aggregate identically scored runs, including the failed transfer experiment."""
from html import escape
import json
from pathlib import Path

BASE=Path(r"D:\ICT8-OfficialDatasets\ocr-candidates")


def main():
    runs=[("开发样本",BASE/"table-fusion-20261005-final-live"),("新文档迁移检查",BASE/"table-fusion-transfer-20261005")]
    data=[(label,root,json.loads((root/"table-fusion-result.json").read_text(encoding="utf-8"))) for label,root in runs]
    expected=sum(d["summary"]["expected"] for _,_,d in data)
    metrics=[]
    for i in (0,1,2,4,5):
        strict=sum(c["scores"][i]["strict"]["correct"] for _,_,d in data for c in d["cases"])
        normalized=sum(c["scores"][i]["normalized"]["correct"] for _,_,d in data for c in d["cases"])
        metrics.append({"label":data[0][2]["cases"][0]["scores"][i]["label"],"strict_correct":strict,
                        "shared_parser_correct":normalized,"expected":expected,"shared_parser_match_rate":round(normalized/expected*100,2)})
    report={"metrics":metrics,"runs":[{"label":l,"root":str(r),"summary":d["summary"],"provenance":d["provenance"]} for l,r,d in data],
            "not_official_benchmark":True,"adaptive_policy_added_after_transfer_failure":True,
            "metric_is_reference_cell_matching_not_candidate_precision_or_whole_OCR_accuracy":True,
            "no_verified_structure_accuracy_benchmark":True}
    output=BASE/"table-fusion-summary-20261005.json"
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    rows="".join(f'<tr><td>{escape(m["label"])}</td><td>{m["strict_correct"]}/{expected}</td><td>{m["shared_parser_correct"]}/{expected}</td><td>{m["shared_parser_match_rate"]:.2f}%</td></tr>' for m in metrics)
    links="".join(f'<a href="{r.name}/table-fusion-report.html">{escape(l)} · 查看原图、单元格候选和识别证据 →</a>' for l,r,_ in data)
    detail="".join(f'<tr><td>{escape(l)}</td><td>{d["summary"]["previous_normalized"]}/{d["summary"]["expected"]}</td><td>{d["summary"]["final_normalized"]}/{d["summary"]["expected"]}</td><td>{d["summary"]["adaptive_normalized"]}/{d["summary"]["expected"]}</td></tr>' for l,_,d in data)
    html=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Omni · 开源融合实测总览</title>
<style>body{{margin:0;background:#fafafa;color:#20242a;font:16px system-ui;line-height:1.8}}main{{max-width:1040px;margin:auto;padding:40px 28px}}h1{{font-size:30px}}h2{{font-size:21px;margin-top:32px}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{text-align:left;padding:12px;border-bottom:1px solid #ddd}}a{{display:block;color:#2769c4;padding:14px 0;border-bottom:1px solid #ddd}}.note{{color:#626d79;font-size:14px}}strong{{color:#137753}}</style><main>
<h1>Omni · 开源融合实测总览</h1><p><strong>复用 RapidOCR、RapidTable、PaddleOCR、img2table，融合原图定位、金额单位和质量路由。</strong></p>
<p>两批共 {expected} 个参考数值单元格。统一使用“数值匹配＋原图位置重叠”的口径，先比较相同金额解析方式，再讨论模型改进。</p>
<table><tr><th>方案</th><th>原严格解析</th><th>共享金额解析</th><th>共享解析匹配率</th></tr>{rows}</table>
<h2>保留失败结果，解释为什么需要路由</h2><table><tr><th>样本组</th><th>上轮局部融合</th><th>本轮局部融合</th><th>质量路由候选</th></tr>{detail}</table>
<p>新文档上的密集低清表格，局部融合仍明显落后于整页 Paddle。质量路由以低置信数字占比、有效数值数量等可观测信号选择识别链路，不读取答案或样本名称。这条策略是在发现迁移失败后加入的，当前结果属于开发验证，不能作为盲测结论。</p>
<h2>查看原图与证据</h2>{links}
<h2>我们补上的部分</h2><p>① 表格结构仅作为候选，SLANet 的 Paddle 与 ONNX 版本不当成独立的两票。② 单字位置帮助拆开标签与金额，同时保留 CTC 对齐估计的来源。③ 金额、币种、单位及倍数分开保存，无法确定的 m 保留待确认。④ 高置信数字改动需要局部图读数和行列格式支持，原文和冲突均保留。⑤ 质量路由允许在密集低清页切换整页专家，并保留选择理由。</p>
<h2>实测边界</h2><p class="note">输入包括公开 PDF 原生转图、人工退化及自建中文表；不代表真实拍照或官方赛题测试集。该指标是参考单元格匹配率，未统计全部候选精确率，未验证完整表结构准确率或百万单位换算准确率。整页专家使用此前同输入的真实调用结果复放，路由耗时不包含这些调用；不能据此宣称比整页专家更快。当前为独立离线候选，未替换正式 8030 服务、未调用付费 API。创新属于我们的工程融合方案，尚未完成论文查新或公开基准 SOTA 验证。</p>
</main></html>'''
    (BASE/"table-fusion-summary-20261005.html").write_text(html,encoding="utf-8")
    print(json.dumps(metrics,ensure_ascii=False,indent=2))
    print(BASE/"table-fusion-summary-20261005.html")


if __name__=="__main__":main()
