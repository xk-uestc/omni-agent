"""Multimetric small-sample comparison; public anchors and synthetic CER are separate."""
from pathlib import Path
import json,re,statistics,unicodedata
from html import unescape,escape
from rapidfuzz.distance import Levenshtein
from decimal import Decimal
from score_ocr_candidates import GOLD,strings

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
PROVIDERS=['legacy','rapid','paddle','easyocr','easyocr-english','easyocr-english-fp32','doctr','tesseract','tesseract-osd','mineru-basic','mineru-standard']

def normalize(text):return re.sub(r'\s+','',unicodedata.normalize('NFKC',text).casefold()).replace('−','-')

def field_found(value,norm):
    value=normalize(value)
    if re.fullmatch(r'\$?-?\d[\d,]*(?:\.\d+)?',value):
        return re.search(r'(?<![\d,.$-])'+re.escape(value)+r'(?![\d,]|\.\d)',norm) is not None
    return value in norm

def numeric_cell(value):
    match=re.fullmatch(r'(?:\$|¥|￥)?(-?\d+(?:,\d{3})*(?:\.\d+)?)(?:元)?',normalize(value))
    return Decimal(match[1].replace(',','')) if match else None

def table_pairs(record):
    if not record.get('raw_path'):return []
    raw=json.loads(Path(record['raw_path']).read_text(encoding='utf8'));pairs=[]
    for table in [s for s in strings(raw) if s.startswith('<table')]:
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>',table,re.S):
            cells=[unescape(re.sub('<[^>]+>','',cell)).strip() for cell in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>',row,re.S)]
            if len(cells)==2:pairs.append(cells)
    return pairs

def main():
    cases={c['name']:c for c in json.loads((BASE/'matrix-samples/manifest.json').read_text(encoding='utf8'))['cases']}
    reference_path=BASE/'matrix-samples/budget-reference.json'
    public_reference=json.loads(reference_path.read_text(encoding='utf8')) if reference_path.exists() else None
    records=[];reports=[];summary=[]
    for provider in PROVIDERS:
        paths=sorted((p for p in BASE.glob('matrix-'+provider+'-*/report.json')
            if json.loads(p.read_text(encoding='utf8'))['provider']==provider),key=lambda p:p.stat().st_mtime)
        if not paths:continue
        path=paths[-1];reports.append(str(path));data=json.loads(path.read_text(encoding='utf8'))
        group=[]
        for record in data['records']:
            case=cases[record['name']]
            if record['sha256']!=case['sha256']:raise ValueError('Input identity mismatch')
            norm=normalize(record['text']);checks=case['fields']
            row=dict(provider=provider,name=record['name'],round=record['round'],scenario=case['scenario'],
                sha256=record['sha256'],seconds=record['seconds'],peak_rss_mb=record['peak_rss_mb'],error=record['error'],
                missed_fields=[value for value in checks if not field_found(value,norm)],fields_expected=len(checks),
                text=record['text'],raw_path=record.get('raw_path'))
            row['fields_found']=len(checks)-len(row['missed_fields'])
            eligible=[v for v in checks if not re.search('[\u3400-\u9fff]',v)]
            row['latin_numeric_fields_expected']=len(eligible)
            row['latin_numeric_fields_found']=sum(field_found(v,norm) for v in eligible)
            if record['name'].startswith('budget') and public_reference:
                reference=normalize(public_reference['reference_text'])
                row['public_budget_sequence_CER']=round(Levenshtein.distance(reference,norm)/len(reference),4)
            if case['reference_text']:
                reference=normalize(case['reference_text'])
                row['synthetic_character_error_rate']=round(Levenshtein.distance(reference,norm)/len(reference),4)
                gold_words=case['reference_text'].split();words=record['text'].split()
                row['synthetic_whitespace_word_error_rate']=round(Levenshtein.distance(gold_words,words)/len(gold_words),4)
                # NFKC plus removal of whitespace for amounts; no digit/comma repair.
                observed=set(re.findall(r'-?\d[\d,]*\.\d{2}',norm))
                expected={normalize(r['amount']) for r in case['expected_rows']}
                row['amount_precision']=round(len(expected & observed)/len(observed),4) if observed else 0
                row['amount_recall']=round(len(expected & observed)/len(expected),4)
                matches=[];value_matches=[];observed_row_values=[]
                if provider.startswith('mineru'):
                    pairs=table_pairs(record)
                    for expected_row in case['expected_rows']:
                        matches.append(any(normalize(a)==normalize(expected_row['label']) and normalize(b)==normalize(expected_row['amount']) for a,b in pairs))
                        observed=[numeric_cell(b) for a,b in pairs if normalize(a)==normalize(expected_row['label']) and numeric_cell(b) is not None]
                        value_matches.append(numeric_cell(expected_row['amount']) in observed)
                        observed_row_values.append(observed[0] if len(observed)==1 else None)
                    row['row_pairing_method']='actual_HTML_row_label_and_amount'
                    row['localized_row_pairs_correct']=None
                else:
                    for expected_row in case['expected_rows']:
                        x0,y0,x1,y1=expected_row['bbox'];line=[]
                        for node in record.get('nodes',[]):
                            box=node.get('bbox')
                            if not box or node.get('kind')=='table':continue
                            cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
                            if x0<=cx<=x1 and y0<=cy<=y1 and box[3]-box[1]<=2*(y1-y0):line.append(node['text'])
                        contents=normalize(' '.join(line))
                        matches.append(normalize(expected_row['label']) in contents and field_found(expected_row['amount'],contents))
                        observed=[numeric_cell(v) for v in re.findall(r'(?<![\d,.-])-?\d+(?:,\d{3})*(?:\.\d+)?(?![\d,.])',contents)] if normalize(expected_row['label']) in contents else []
                        value_matches.append(numeric_cell(expected_row['amount']) in observed)
                        observed_row_values.append(observed[0] if len(observed)==1 else None)
                    row['row_pairing_method']='observed_text_inside_known_synthetic_row_band'
                    row['localized_row_pairs_correct']=sum(matches)
                row['row_pairs_correct']=sum(matches);row['row_pairs_expected']=len(matches)
                row['numeric_value_row_pairs_correct']=sum(value_matches)
                row['observed_row_values']=[str(v) if v is not None else None for v in observed_row_values]
                row['arithmetic_consistent']=sum(observed_row_values[:-1])==observed_row_values[-1] if all(v is not None for v in observed_row_values) else None
                # Verify observed row order, not just unordered label presence.
                positions=[norm.find(normalize(r['label'])) for r in case['expected_rows']]
                row['all_labels_in_expected_reading_order']=all(p>=0 for p in positions) and positions==sorted(positions)
            if record['name'].startswith('budget') and provider.startswith('mineru'):
                pairs=table_pairs(record)
                row['budget_HTML_pairs_correct']=sum(any(normalize(a)==normalize(label) and normalize(b)==normalize(amount) for a,b in pairs) for label,amount in GOLD)
                row['budget_HTML_pairs_expected']=11
            group.append(row);records.append(row)
        succeeded=[r for r in group if not r['error']]
        synthetic=[r for r in succeeded if 'synthetic_character_error_rate' in r]
        times=sorted(r['seconds'] for r in succeeded)
        public=[r for r in group if not cases[r['name']]['reference_text']]
        english=[r for r in public if r['name']!='official-chinese']
        summary.append(dict(provider=provider,runs=len(group),planned_runs=len(data.get('requested_names',cases))*data.get('rounds_requested',2),failures=len(group)-len(succeeded),
            failed_call_seconds=[r['seconds'] for r in group if r['error']],
            initialization_seconds=data['initialization_seconds'],
            mean_seconds=round(statistics.mean(times),3) if times else None,
            p50_seconds=round(statistics.median(times),3) if times else None,
            p95_seconds=times[max(0,int(len(times)*.95+.999)-1)] if times else None,
            peak_rss_mb=max((r['peak_rss_mb'] for r in group),default=0),
            public_anchor_found=sum(r['fields_found'] for r in public),public_anchor_expected=sum(r['fields_expected'] for r in public),
            english_public_anchor_found=sum(r['fields_found'] for r in english),english_public_anchor_expected=sum(r['fields_expected'] for r in english),
            latin_numeric_found=sum(r['latin_numeric_fields_found'] for r in group),latin_numeric_expected=sum(r['latin_numeric_fields_expected'] for r in group),
            synthetic_mean_CER=round(statistics.mean(r['synthetic_character_error_rate'] for r in synthetic),4) if synthetic else None,
            budget_mean_sequence_CER=round(statistics.mean(r['public_budget_sequence_CER'] for r in succeeded if 'public_budget_sequence_CER' in r),4) if any('public_budget_sequence_CER' in r for r in succeeded) else None,
            synthetic_row_pairs_correct=sum(r['row_pairs_correct'] for r in synthetic),synthetic_row_pairs_expected=sum(r['row_pairs_expected'] for r in synthetic),
            stable_by_sample={name:len({r['text'] for r in succeeded if r['name']==name})==1 if len([r for r in succeeded if r['name']==name])>=2 else None for name in cases}))
    result=dict(not_official_benchmark=True,public_anchor_coverage_not_accuracy=True,synthetic_CER_not_public_CER=True,
        source_reports=reports,summary=summary,records=records)
    target=BASE/'matrix-comparison.json';target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False));print(target)
    rows=[]
    for s in summary:
        rows.append('<tr>'+''.join('<td>'+escape(str(s[k]))+'</td>' for k in ['provider','runs','failures','public_anchor_found','public_anchor_expected','budget_mean_sequence_CER','synthetic_mean_CER','synthetic_row_pairs_correct','synthetic_row_pairs_expected','mean_seconds','p95_seconds','peak_rss_mb'])+'</tr>')
    details=[]
    for r in records:
        details.append('<details><summary>'+escape(f"{r['provider']} · {r['name']} · 第{r['round']}轮 · 漏项{len(r['missed_fields'])}")+'</summary><p>'+escape(str(r['missed_fields']))+'</p><pre>'+escape(r['error'] or r['text'])+'</pre></details>')
    gallery=''.join('<figure style="display:inline-block;margin:12px"><img style="max-width:150px;height:170px;object-fit:contain" src="matrix-samples/'+escape(name)+'.png"><figcaption>'+escape(name)+'</figcaption></figure>' for name in cases)
    html='''<!doctype html><meta charset="utf-8"><title>OCR多场景实测</title><style>body{font:15px system-ui;margin:32px;color:#202124}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:10px;text-align:left}summary{cursor:pointer;padding:12px;border-bottom:1px solid #ddd}pre{white-space:pre-wrap;line-height:1.6}input{font:inherit;padding:10px;margin:20px 0;width:400px}</style><h1>OCR 多场景实测</h1><p>公开字段覆盖不是准确率；预算CER使用独立PDF文字层、含阅读顺序误差；自建CER来自中文已知原文。不同模型语言范围、线程及启动口径不同；部分质量评测并行共享CPU，耗时不能直接当严格排名。原始错误保留，正式服务未替换。</p>'''+gallery+'<table><tr>'+''.join('<th>'+x+'</th>' for x in ['模型','完成次数','失败','公开字段命中','公开字段总数','预算顺序CER','自建CER','行配对正确','行配对总数','均秒','P95秒','进程峰值MB'])+'</tr>'+''.join(rows)+'</table><input placeholder="筛选模型或场景" oninput="for(const d of document.querySelectorAll(\'details\'))d.hidden=!d.querySelector(\'summary\').textContent.toLowerCase().includes(this.value.toLowerCase())">'+''.join(details)
    (BASE/'matrix-comparison.html').write_text(html,encoding='utf8')

if __name__=='__main__':main()
