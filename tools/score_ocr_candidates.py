"""Small sample checks; do not interpret as official benchmark accuracy."""
import json
import re
from html import unescape
from pathlib import Path

BASE=Path(r'D:\ICT8-OfficialDatasets\ocr-candidates')
GOLD=[('Personnel','$47,000'),('Staff Mileage','$1,000'),('Office Supplies','$2,000'),
      ('Pre-Natal/Diagnostic Services','$35,000'),('Marketing & Media Activities','$2,000'),
      ('Participant Education','$8,000'),('Participant Support','$1,000'),('Equipment','$2,000'),
      ('Other: Formula','$1,000'),('Other: Diapers','$1,000'),('TOTAL:','$100,000')]


def strings(obj):
    if isinstance(obj,dict):
        for key,value in obj.items():
            if key=='content' and isinstance(value,str):yield value
            elif isinstance(value,(dict,list)):yield from strings(value)
    elif isinstance(obj,list):
        for item in obj:yield from strings(item)


def main():
    reports=[]
    for provider in ['legacy','rapid','paddle','paddle-oriented']:
        path=BASE/f'{provider}-trial.json'
        if not path.exists():continue
        data=json.loads(path.read_text(encoding='utf8'))
        for item in data['records']:
            text=item['text']
            # Only presence of each distinct label/amount, never cell pairing.
            compact=re.sub(r'\s+','',text).casefold()
            reports.append({'provider':provider,'sample':item['image'],'seconds':item['seconds'],
                'labels_found':sum(re.sub(r'\s+','',label).casefold() in compact for label,_ in GOLD),
                'labels_expected':len(GOLD),'table_pairing_evaluated':False,
                'office_supplies_comma_present':'$2,000' in text,'total_present':'$100,000' in text})
    for provider in ['mineru-basic','mineru-standard']:
        for path in sorted((BASE/provider).glob('*.json')):
            tables=[s for s in strings(json.loads(path.read_text(encoding='utf8'))) if s.startswith('<table')]
            pairs=[]
            for table in tables:
                for row in re.findall(r'<tr[^>]*>(.*?)</tr>',table,re.S):
                    cells=[unescape(re.sub('<[^>]+>','',cell)).strip() for cell in re.findall(r'<td[^>]*>(.*?)</td>',row,re.S)]
                    if len(cells)==2:pairs.append(cells)
            normalize=lambda s:re.sub(r'\s+','',s).casefold()
            matched=sum(any(normalize(a)==normalize(label) and b==amount for a,b in pairs) for label,amount in GOLD)
            reports.append({'provider':provider,'sample':path.name,'table_pairing_evaluated':True,
                'correct_label_amount_pairs':matched,'expected_pairs':len(GOLD),'parsed_pairs':pairs})
    output=BASE/'comparison.json'
    output.write_text(json.dumps({'not_official_benchmark':True,'single_public_page_variants':True,
        'timings_not_controlled_due_to_parallel_trials':True,'reference_pairs':GOLD,'results':reports},ensure_ascii=False,indent=2),encoding='utf8')
    for record in reports:print(json.dumps({k:v for k,v in record.items() if k!='parsed_pairs'},ensure_ascii=False))
    print(output)


if __name__=='__main__':main()
