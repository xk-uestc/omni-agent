"""Original page label transcription compared across two public OCR runs."""
from pathlib import Path
import json,re
import argparse
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--source',default='public-region-selection-pages-20261005.json')
parser.add_argument('--output',default='public-region-label-reference-20261005.json')
args=parser.parse_args()
output=ROOT/'runtime'/args.output
if output.parent.resolve()!=(ROOT/'runtime').resolve() or (ROOT/'runtime'/args.source).parent.resolve()!=(ROOT/'runtime').resolve():
    parser.error('Reports must be filenames within runtime.')
if output.exists():raise SystemExit('Preserve prior evidence.')
expected=[
    ['Democratic Reform','Environmental Management','Private Sector Initiatives','Social Sector Reform',
     'Special/Cross-Cutting Initiatives','x Community Connections','x Internet Access & Training Program (IATP)',
     'x Parking Fine Withholding','x Peace Corps Small Project Assistance (SPA)'],
    ['Anti-Terrorism Assistance (ATA)','Export Control & Related Border Security Asst. (EXBS)',
     'Foreign Military Financing (FMF)','Human Rights & Democracy Fund (HRDF)','Humanitarian Assistance',
     'International Information Programs (IIP)','International Military Educ. & Training (IMET)','Public Diplomacy Exchanges'],
    ['EUR Democracy Programs','EUR Exchanges','Export Control & Related Border Security Asst. (EXBS)',
     'Humanitarian Transport','International Information Programs (IIP)','Law Enforcement Assistance',
     'National Endowment for Democracy (NED)','Public Diplomacy Exchanges','Television Cooperatives'],
]
def normalize(text):return re.sub(r'[^a-z0-9]','',text.lower().replace('×','x'))
def distance(a,b):
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1):current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]
reports=[]
for name in ['public-merged-amount-final-pages-20261005.json',args.source]:
    case=json.loads((ROOT/'runtime'/name).read_text(encoding='utf-8'))['cases'][1]
    assert case['source_pdf_sha256']=='b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a'
    evidence=case['ocr']['metadata']['borderless_table_evidence']
    regions=evidence['table_layout']['regional_candidates'][1:]
    assert [len(region['rows']) for region in regions]==[9,8,9]
    checks=[]
    for reference,region in zip(expected,regions):
        for label,row in zip(reference,region['rows']):
            actual=row[0]['text'];a,b=normalize(label),normalize(actual)
            checks.append({'reference':label,'observed':actual,'edit_distance':distance(a,b),'reference_characters':len(a)})
    errors=sum(check['edit_distance'] for check in checks);characters=sum(check['reference_characters'] for check in checks)
    reports.append({'source_report':name,'normalized_CER':errors/characters,'edit_distance':errors,
        'reference_characters':characters,'region_selection':evidence.get('region_selection'),'checks':checks})
result={'not_official_benchmark':True,'scope':'26_preexposed_labels_on_original_budget_page',
    'normalization':'lowercase_alphanumeric_and_multiplication_mark_to_x','reports':reports,
    'improved':reports[1]['edit_distance']<reports[0]['edit_distance']}
output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'before_CER':reports[0]['normalized_CER'],'after_CER':reports[1]['normalized_CER'],
    'improved':result['improved'],'report':str(output)},ensure_ascii=False))
