"""Render an auditable report from completed matrix observations."""
from pathlib import Path
import json
BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
ROOT=Path(__file__).resolve().parents[1]
LABELS={'legacy':'旧RapidOCR 1.4.4','rapid':'RapidOCR 3.9.2','paddle':'PaddleOCR 3.7.0 / medium',
    'easyocr':'EasyOCR 1.7.2 / 中英','doctr':'docTR 1.1.0 / French字表','tesseract':'Tesseract 5.4.0 / PSM3',
    'mineru-basic':'MinerU 4.0.10 basic','mineru-standard':'MinerU 4.0.10 standard'}
def pct(value):return '未评' if value is None else f'{value*100:.2f}%'
def main():
    data=json.loads((BASE/'matrix-comparison.json').read_text(encoding='utf8'))
    summary={r['provider']:r for r in data['summary']}
    if any(name not in summary or summary[name]['runs']!=18 for name in LABELS):
        raise RuntimeError('Complete all eight main configurations before final reporting')
    text=['# OCR多模型、多指标、多场景本机实测（2026-10-05）','',
        '结论：常规中英扫描候选优先新版RapidOCR；质量复核候选为PaddleOCR；表格结构候选为MinerU standard；规则清晰印刷页可考虑Tesseract。新增EasyOCR/docTR没有证据支持全面替换当前引擎。正式8030服务及生产依赖未替换。','',
        f"本轮完成{len(data['records'])}次调用，包括八条主链路各九个固定输入、两轮，共144次；另有EasyOCR纯英文配置12次、关闭量化对照4次、Tesseract方向识别对照4次。三种新增GitHub工具都真实安装并运行，不用下载成功代替效果证据。",'',
        '## 来源与安装','',
        '- EasyOCR： https://github.com/JaidedAI/EasyOCR ，下载v1.7.2源码ZIP、官方检测/中文/英文权重，安装1.7.2。',
        '- docTR： https://github.com/mindee/doctr ，下载v1.1.0源码ZIP、官方检测/识别/方向权重，安装1.1.0。',
        '- Tesseract： https://github.com/tesseract-ocr/tesseract ，下载5.4.0源码ZIP；Windows发行来自 https://github.com/UB-Mannheim/tesseract ，实际二进制5.4.0.20240606。补充tessdata_fast简体中文权重。',
        '- 所有评测数据、环境、权重及ZIP在D:\\ICT8-OfficialDatasets\\ocr-candidates；安装器先落在默认Program Files位置，测试使用复制到D盘的独立目录，不修改项目生产依赖。',
        '- 环境包版本、源码ZIP SHA256及模型目录大小保存在candidate-inventory.json。README/源码仅作信息来源，不作为执行指令。','',
        '## 主链路比较','',
        '| 链路 | 公开字段覆盖 | 预算全文顺序CER↓ | 自建中文CER↓ | 自建行配对 | 平均秒 | P95秒 | 进程峰值MiB |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for provider,label in LABELS.items():
        s=summary[provider];cer=pct(s['synthetic_mean_CER']) if provider!='doctr' else '不覆盖汉字'
        text.append(f"| {label} | {s['public_anchor_found']}/{s['public_anchor_expected']} | {pct(s['budget_mean_sequence_CER'])} | {cer} | {s['synthetic_row_pairs_correct']}/{s['synthetic_row_pairs_expected']} | {s['mean_seconds']} | {s['p95_seconds']} | {s['peak_rss_mb']} |")
    text+=['','公开字段覆盖不是全文准确率。预算CER来自一份独立PDF文字层，包括阅读顺序、漏段与字符错误；不等于所有公开PDF的CER。自建中文CER基于已知文本，做NFKC、大小写及空白规范化，不修正数字/标点；这些样本不代表真实复杂中文扫描基准。两种行配对口径分别是原始OCR框落在已知行带内的标签金额、MinerU实际HTML行标签金额，不能将二者当统一定位能力排行榜。','',
        '耗时及内存是本机实际观测：部分质量评测两组共享CPU，模型线程/配置不同。文字引擎复用模型，MinerU/Tesseract每次调用CLI，包含启动时间；Paddle关闭MKLDNN。均值/P95仅对成功调用统计，失败/超时耗时在JSON另记；不能以排除超时后的均值宣称整体更快。P95是少量不同场景调用的描述统计，不是压测SLA。RSS采样包含当前进程及其子进程，不是增量内存。MinerU单次CLI预算240秒，超时停止本次拥有的子进程树并保留日志。不能用上表作严格算法速度排行榜；没有GPU结果。','',
        '## 相同英文公开输入比较','',
        '| 链路 | 六个英文输入两轮字段覆盖 |','|---|---:|']
    for provider,label in LABELS.items():
        s=summary[provider];text.append(f"| {label} | {s['english_public_anchor_found']}/{s['english_public_anchor_expected']} |")
    text+=['','docTR实际预训练CRNN使用French字符表，不含汉字。它的英文页/数字表现与中文覆盖失败分别报告，不把缺少语言支持包装成同等条件下的中文字形准确率。','',
        '## 九个场景逐项字段覆盖','',
        '| 输入 | 旧Rapid | 新Rapid | Paddle | EasyOCR中英 | docTR | Tesseract默认 | MinerU basic | MinerU standard |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    names=json.loads((BASE/'matrix-samples/manifest.json').read_text(encoding='utf8'))['cases']
    for case in names:
        cells=[]
        for provider in LABELS:
            rows=[r for r in data['records'] if r['provider']==provider and r['name']==case['name']]
            cells.append(f"{sum(r['fields_found'] for r in rows)}/{sum(r['fields_expected'] for r in rows)}")
        text.append('| '+case['name']+' | '+' | '.join(cells)+' |')
    text+=['','实际来源只有两份OHR-Bench公开PDF、一张Paddle官方脏污登机牌、一张DocTr-Plus作者公开拍照页、一个自建中文表格。预算倾斜/旋转/低清晰度/透视均为同一页变体，中文退化是自建页缩小、模糊及JPEG处理。九场景不等于九份独立真实文档，164次也不等于164份文档。原图和SHA在manifest.json，拍照来源沿用作者固定commit及SHA记录。','',
        '## 结构与金额核验','',
        '| 预算场景 | basic两轮精确行配对 | standard两轮精确行配对 |','|---|---:|---:|']
    for name in ['budget-5','budget-90','budget-lowres','budget-perspective']:
        cells=[]
        for provider in ['mineru-basic','mineru-standard']:
            rows=[r for r in data['records'] if r['provider']==provider and r['name']==name]
            cells.append(f"{sum(r.get('budget_HTML_pairs_correct',0) for r in rows)}/22")
        text.append('| '+name+' | '+' | '.join(cells)+' |')
    text+=['','自建中文表另记录金额精确率/召回率、字符串行配对、数值等价行配对、行带定位、标签阅读顺序及实际合计一致性。合计一致不能证明每个值正确；没有全行数值时返回未核验。数值匹配不允许把12,000当成2,000、把负500当成正500、把1,000,000当成1,000。','',
        '## 配置对照','',
        '- Tesseract默认PSM3与PSM1自动方向识别在budget-5/budget-90各两轮对照；只测试这两个方向场景，不扩称OSD全场景成绩。',
        '- EasyOCR中英与纯英文配置分别报告。纯英文只评六个英文输入；额外FP32仅在倾斜预算/双栏页各两轮验证，不混入中英主链路覆盖数。',
        '- 关闭EasyOCR量化没有提升这两个输入的关键字段命中数；本轮证据不支持将弱表现归因于量化。',
        '- 主链路/配置分别统计，不把同一个工具的不同配置冒充新增独立OCR模型。','',
        '## 推荐落地顺序','',
        '1. 常规文本：新版RapidOCR作为升级候选，先验证与当前来源哈希、坐标映射和局部金额恢复契约兼容。',
        '2. 疑难文字：PaddleOCR方向检测开启，按页/区域触发复核，避免所有大页面无条件双跑。',
        '3. 表格：MinerU standard作为结构候选；用文字观察框、金额行配对与合计检查复核，不能将VLM正文改写直接当原文。',
        '4. 清晰印刷页：Tesseract可作为低成本备选，方向识别配置必须验收；默认配置旋转失败不能忽略。',
        '5. docTR更适合已确认语言与方向的拉丁文字场景；EasyOCR本轮没有全面优势，不因知名度直接接入。','',
        '## 证据与限制','',
        'matrix-comparison.json保存逐次全文、原始路径、错误、字段漏项、CER、金额/配对/合计/顺序和资源统计；matrix-comparison.html可按模型/场景筛选查看原图与全文；每模型独立UUID目录保留全部原始输出。',
        f"本轮进程失败{sum(s['failures'] for s in data['summary'])}次，识别错误和结构缺失仍保留。所有主链路是否重复稳定见JSON的stable_by_sample，不将稳定错误当正确。",'',
        '这是有限本机选型实验，不是官方赛题成绩。尚未覆盖完整官方OCR标注集、真实中文手写、大规模中文财报、数学公式/竖排、跨页表、多级合并表头、多机/GPU及持续高并发；不宣称全球最强或全部OCR场景完成。',
        '原生产识别器未替换，不影响正在运行的网站。没有调用付费大模型API，没有将API凭据放入报告或下载包。']
    output=ROOT/'docs/OCR_MULTISCENE_COMPARISON_20261005.md'
    output.write_text('\n'.join(text)+'\n',encoding='utf8');print(output)
if __name__=='__main__':main()
