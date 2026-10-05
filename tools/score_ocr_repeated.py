"""Auditable sampled key-field coverage and budget row pairing, not accuracy."""
import json
import re
from pathlib import Path
from statistics import mean
from html import unescape
from score_ocr_candidates import GOLD, strings

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
CHECKS={
    'budget-5.png':[value for pair in GOLD for value in pair],
    'budget-90.png':[value for pair in GOLD for value in pair],
    'official-chinese.png':['登机牌','航班','日期','舱位','序号','姓名','MU2379','03DEC','035','FUZHOU','TAIYUAN','ZHANGQIWEI','G11'],
    'public-second.png':['UKRAINE','$154.70','$25.06','$168.72','$82.16','$72.54','Chornobyl Shelter Implementation Plan','$20.55',
                         'Democratic Reform','$16.62','Economic Restructuring','$3.93','Private Sector Initiatives','$6.33',
                         'Social Sector Reform','$7.93','Special/Cross-Cutting Initiatives','$1.72']}


def compact(text):return re.sub(r'\s+','',text).casefold()


def evaluate(provider,item,text,raw=None):
    checks=list(dict.fromkeys(CHECKS[item['image']]));norm=compact(text)
    missed=[value for value in checks if compact(value) not in norm]
    result={'provider':provider,'image':item['image'],'round':item['round'],'seconds':item['seconds'],
        'sampled_fields_found':len(checks)-len(missed),'sampled_fields_expected':len(checks),'missed_fields':missed,
        'text':text,'sha256':item.get('sha256')}
    if item['image'].startswith('budget'):
        # Separately inspect original PDF phrases; table correctness does not establish prose fidelity.
        probes=['November 2015-June 2016','ODJFS','received a grant','ultrasounds to clients']
        result['additional_original_phrase_probes']={value:compact(value) in norm for value in probes}
    if raw and item['image'].startswith('budget'):
        pairs=[]
        for table in [s for s in strings(raw) if s.startswith('<table')]:
            for row in re.findall(r'<tr[^>]*>(.*?)</tr>',table,re.S):
                cells=[unescape(re.sub('<[^>]+>','',cell)).strip() for cell in re.findall(r'<td[^>]*>(.*?)</td>',row,re.S)]
                if len(cells)==2:pairs.append(cells)
        result['correct_budget_pairs']=sum(any(compact(label)==compact(a) and amount==b for a,b in pairs) for label,amount in GOLD)
        result['budget_pairs_expected']=11
    return result


def main():
    records=[];sources=[]
    for provider in ['legacy','rapid','paddle-oriented']:
        paths=sorted(BASE.glob(f'{provider}-expanded-*.json'),key=lambda p:p.stat().st_mtime)
        if not paths:continue
        path=paths[-1];sources.append(str(path))
        for item in json.loads(path.read_text(encoding='utf8'))['records']:
            records.append(evaluate(provider,item,item['text']))
    failures=[]
    for tier in ['basic','standard']:
        paths=sorted(BASE.glob(f'mineru-{tier}-expanded-*/report.json'),key=lambda p:p.stat().st_mtime)
        if not paths:continue
        path=paths[-1];sources.append(str(path))
        for item in json.loads(path.read_text(encoding='utf8'))['records']:
            if item['exit_code']!=0 or not item['outputs']:
                failures.append(dict(provider='mineru-'+tier,**item));continue
            raw=json.loads(Path(item['outputs'][0]).read_text(encoding='utf8'))
            text='\n'.join(unescape(re.sub('<[^>]+>',' ',s)) for s in strings(raw))
            records.append(evaluate('mineru-'+tier,item,text,raw))
    summary=[]
    for provider in sorted({r['provider'] for r in records}):
        items=[r for r in records if r['provider']==provider]
        summary.append({'provider':provider,'runs':len(items),'mean_seconds':round(mean(r['seconds'] for r in items),3),
            'sampled_fields_found':sum(r['sampled_fields_found'] for r in items),'sampled_fields_expected':sum(r['sampled_fields_expected'] for r in items),
            'stable_text_by_sample':{name:(len({r['text'] for r in items if r['image']==name})==1
                if len([r for r in items if r['image']==name])>=2 else None) for name in CHECKS}})
    output=BASE/'repeated-comparison.json'
    output.write_text(json.dumps({'not_official_benchmark':True,'small_selected_field_coverage_not_accuracy':True,
        'source_reports':sources,'summary':summary,'records':records,'failures':failures},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False));print(output)


if __name__=='__main__':main()
