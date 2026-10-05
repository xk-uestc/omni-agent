"""Publish developer fixture results, sample workbook archive and readable matrix."""
from html import escape
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=Path('D:/ICT8-OfficialDatasets/excel-irregular-20261005')


def main():
    baseline=json.loads((OUTPUT/'baseline-26/parser-report.json').read_text(encoding='utf-8'))
    final=json.loads((OUTPUT/'final/parser-report.json').read_text(encoding='utf-8'))
    by_name={r['name']:r for r in baseline['results']}
    rows=[]
    for result in final['results']:
        old=by_name[result['name']]
        rows.append('<tr><td>'+escape(result['name'])+'</td><td>'+escape(result['description'])+'</td>'
                    +'<td class="'+('ok' if old['passed'] else 'bad')+'">'+('通过' if old['passed'] else '失败')+'</td>'
                    +'<td class="'+('ok' if result['passed'] else 'bad')+'">'+('通过' if result['passed'] else '失败')+'</td>'
                    +f'<td>{result["seconds"]*1000:.1f} ms</td></tr>')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>不规则Excel读取验收</title><style>body{font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif;color:#222;margin:40px auto;max-width:1120px;padding:0 24px;background:#fafafa}h1{font-size:28px}table{border-collapse:collapse;width:100%;background:white}th,td{padding:12px;text-align:left;border-bottom:1px solid #ddd}th{background:#f0f2f4}.ok{color:#147548}.bad{color:#b5443e}a{color:#285bbb}.notice{border-left:3px solid #999;padding:8px 16px}.links{display:flex;gap:20px}</style>
<h1>不规则 Excel：26种已知答案样本</h1><p>同一批期望记录核对表头、字段关系、原始行号、值与额外记录。原读取器 '''+str(baseline['passed'])+'/26，新读取器 '+str(final['passed'])+'''/26。</p>
<p class="notice">这是自建开发样本验收，不是公开基准准确率，也不证明任意文件都能完美识别。公式仅保留表达式与未核验缓存，不运行宏；未知表头需确认区域和层数；支持.xlsx，旧.xls或加密文件需转换。</p>
<p>测试还包含63个平移变体、5000行数值对照、合并金额不复制、原始可见单元格逐个核对，以及真实HTTP保存与重启恢复。小样本耗时是当前机器开发观察值，不是大文件速度保证。</p>
<div class="links"><a href="final/samples/expected.json">预期答案</a><a href="final/parser-report.json">最终原始报告</a><a href="baseline-26/parser-report.json">基线与失败记录</a><a href="excel-irregular-samples-26.zip">下载26份样本</a></div>
<table><thead><tr><th>样本</th><th>场景</th><th>原读取器</th><th>新读取器</th><th>本次读取耗时</th></tr></thead><tbody>'''+''.join(rows)+'''</tbody></table>
<p>使用方式：打开项目“文档与跨源”页面，找到“Excel · 表格识别与单元格核对”，上传样本。未知表头可调整工作表、区域和表头层数再识别；0表示无表头。确认后加入资料库，原文件保持不变。</p></html>'''
    (OUTPUT/'comparison.html').write_text(html,encoding='utf-8')
    archive=OUTPUT/'excel-irregular-samples-26.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED) as zipfile:
        for path in sorted((OUTPUT/'final/samples').iterdir()):zipfile.write(path,'samples/'+path.name)
        zipfile.writestr('README.txt','26种不规则Excel已知答案测试样本。自建开发集，不是公开基准。\nexpected.json含每份文件的字段、行号、值与SHA-256。\n公式不在本包中重算，故意错误缓存文件用于验证系统不会把缓存当已核验结果。\n隐藏行列和工作表默认不进入可检索内容。\n使用项目“文档与跨源”的Excel识别预览入口上传。\n')
    (OUTPUT/'sample-package-sha256.txt').write_text(hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n',encoding='utf-8')
    print(json.dumps({'baseline_passed':baseline['passed'],'final_passed':final['passed'],'total':final['total'],
                      'zip':str(archive),'bytes':archive.stat().st_size,'report':str(OUTPUT/'comparison.html')},ensure_ascii=False))


if __name__=='__main__':main()
