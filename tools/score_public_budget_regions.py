"""Manual original-page regional references, not full-page or official scores."""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'runtime/public-multiregion-pages-20261005.json'
output=ROOT/'runtime/public-budget-regional-reference-20261005.json'
if output.exists():raise SystemExit('Preserve prior evidence; choose a new report filename before rerunning.')
report=json.loads(source.read_text(encoding='utf-8'))
case=report['cases'][1]
assert case['source_pdf_sha256']=='b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a'
metadata=case['ocr']['metadata']
layout=metadata.get('borderless_table_evidence',metadata)['table_layout']
# Transcribed and spatially located from the rendered original, not from OCR.
# The references only cover four >=3-row regions; smaller blocks/subtotals are
# explicitly outside this observation check and remain an unresolved gap.
references=[
    ('summary',(220,180,660,280),['$43.54m','$15.67m','$61.97m']),
    ('left_USAID',(50,350,455,515),['$5.49','$1.39','$10.37','$3.59','$1.48','$0.05','$0.15','$0.01','$0.04']),
    ('right_STATE',(465,430,865,565),['$3.29','$0.81','$1.88','$0.35','$0.00','$0.01','$0.89','$3.59']),
    ('left_STATE',(50,550,455,700),['$0.42','$0.44','$0.40','$0.75','$0.05','$3.30','$0.20','$0.01','$0.03']),
]
checks=[]
for name,box,expected in references:
    matches=[]
    for region in layout['regional_candidates']:
        cells=[cell for row in region['rows'] for cell in row]
        locations=[cell.get('original_geometry',{}) for cell in cells]
        valid=all(location.get('original_bbox_eligible') and location.get('source_sha256')==case['render_sha256'] for location in locations)
        if not valid:continue
        centers=[[(location['bbox_px'][0]+location['bbox_px'][2])/2,(location['bbox_px'][1]+location['bbox_px'][3])/2] for location in locations]
        if all(box[0]<=x<=box[2] and box[1]<=y<=box[3] for x,y in centers):matches.append(region)
    observed=[row[-1]['text'] for row in matches[0]['rows']] if len(matches)==1 else []
    checks.append({'region':name,'manual_original_reference_box_px':box,'expected':expected,'observed':observed,
                   'matching_region_count':len(matches),'exact_values_in_order':observed==expected,
                   'labels_semantically_verified':False})
passed=all(check['exact_values_in_order'] for check in checks)
output.write_text(json.dumps({'not_official_benchmark':True,'reference_kind':'manual_rendered_original_page',
    'source_report':str(source),'source_pdf_sha256':case['source_pdf_sha256'],'page_no':1,
    'scope':'four_selected_regions_not_complete_page','passed':passed,'reference_value_count':29,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'passed':passed,'checked_regions':len(checks),'reference_values':29,'report':str(output)},ensure_ascii=False))
if not passed:raise SystemExit(1)
