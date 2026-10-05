"""Spatially check actual original-page short entries and preserved regions.

Manual references cover 39 observations, not all text or complete table semantics.
"""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'runtime/public-sparse-pages-20261005.json'
output=ROOT/'runtime/public-sparse-reference-20261005.json'
if output.exists():raise SystemExit('Preserve prior report; use a new output name.')
case=json.loads(source.read_text(encoding='utf-8'))['cases'][1]
assert case['source_pdf_sha256']=='b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a'
layout=case['ocr']['metadata']['borderless_table_evidence']['table_layout']
references=[
    ('summary',(220,180,660,280),['$43.54m','$15.67m','$61.97m']),
    ('left_USAID',(50,350,455,515),['$5.49','$1.39','$10.37','$3.59','$1.48','$0.05','$0.15','$0.01','$0.04']),
    ('right_STATE',(465,430,865,565),['$3.29','$0.81','$1.88','$0.35','$0.00','$0.01','$0.89','$3.59']),
    ('left_STATE',(50,550,455,700),['$0.42','$0.44','$0.40','$0.75','$0.05','$3.30','$0.20','$0.01','$0.03']),
]
short_references=[
    ('right_USAID',(465,370,865,400),['$0.10']),
    ('right_USDA',(465,600,865,630),['$0.01']),
    ('right_Defense',(465,670,865,695),['$0.93']),
    ('right_Energy',(465,735,865,760),['$0.71']),
    ('left_USDA',(50,740,455,765),['$0.10']),
    ('right_PeaceCorps',(465,795,865,825),['$1.93']),
    ('left_Commerce',(50,805,455,840),['$0.07','$0.10']),
    ('left_Treasury',(50,885,455,915),['$0.25']),
    ('left_NSF',(50,945,455,975),['$0.35']),
]
checks=[]
for sparse,refs in [(False,references),(True,short_references)]:
    for name,box,expected in refs:
        matches=[]
        for region in layout['sparse_observations' if sparse else 'regional_candidates']:
            locations=[cell.get('original_geometry',{}) for row in region['rows'] for cell in row]
            if not locations or not all(location.get('original_bbox_eligible') and
                location.get('source_sha256')==case['render_sha256'] for location in locations):continue
            centers=[[(location['bbox_px'][0]+location['bbox_px'][2])/2,
                      (location['bbox_px'][1]+location['bbox_px'][3])/2] for location in locations]
            if all(box[0]<=x<=box[2] and box[1]<=y<=box[3] for x,y in centers):matches.append(region)
        observed=[row[-1]['text'] for row in matches[0]['rows']] if len(matches)==1 else []
        typed=not sparse or len(matches)==1 and matches[0].get('is_table') is False
        checks.append({'region':name,'reference_box_px':box,'expected':expected,'observed':observed,
                       'passed':observed==expected and typed,'sparse':sparse})
passed=all(check['passed'] for check in checks)
output.write_text(json.dumps({'not_official_benchmark':True,'scope':'selected_original_page_regions_only',
    'source_report':str(source),'source_pdf_sha256':case['source_pdf_sha256'],'page_no':1,
    'values_semantically_verified':False,'passed':passed,'reference_value_count':39,'checks':checks},
    ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':passed,'matched_values':sum(len(check['expected']) for check in checks if check['passed']),
                  'expected_values':39,'failed_regions':[check['region'] for check in checks if not check['passed']],
                  'report':str(output)},ensure_ascii=False))
if not passed:raise SystemExit(1)
