"""Original-page spatial references; failures remain visible, not official scores."""
from pathlib import Path
import json
import argparse
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--source',default='public-merged-amount-final-pages-20261005.json')
parser.add_argument('--output',default='public-merged-amount-reference-20261005.json')
parser.add_argument('--include-close-pair',action='store_true')
args=parser.parse_args()
source=ROOT/'runtime'/args.source
output=ROOT/'runtime'/args.output
if source.parent.resolve()!=(ROOT/'runtime').resolve() or output.parent.resolve()!=(ROOT/'runtime').resolve():
    parser.error('Reports must be filenames within runtime.')
if output.exists():raise SystemExit('Preserve existing evidence.')
case=json.loads(source.read_text(encoding='utf-8'))['cases'][1]
assert case['source_pdf_sha256']=='b36413960ff43c854cdd8022721f27678de9733e2ac679eabd0ca1cb1813009a'
layout=case['ocr']['metadata']['borderless_table_evidence']['table_layout']
previous=json.loads((ROOT/'runtime/public-sparse-reference-20261005.json').read_text(encoding='utf-8'))
checks=[]
references=[(check['region'],check['reference_box_px'],check['expected'],check['sparse']) for check in previous['checks']]
# Independently transcribed from the original budget page. The Commerce
# amount must remain missing if crop OCR disagrees; never repair O to 0 here.
references.extend([
    ('subtotal_USAID',(300,505,450,532),['$22.56'],True),
    ('subtotal_left_STATE',(300,690,450,720),['$5.60'],True),
    ('subtotal_Commerce',(270,835,450,865),['$0.17'],True),
    ('non_FSA',(480,290,865,325),['$14.51m'],True),
    ('subtotal_right_STATE',(710,550,865,585),['$10.83'],True),
])
if args.include_close_pair:references.append(('left_FSA',(110,290,450,325),['$29.03m'],True))
for name,box,expected,sparse in references:
    matches=[]
    for region in layout['sparse_observations' if sparse else 'regional_candidates']:
        locations=[cell.get('original_geometry',{}) for row in region['rows'] for cell in row]
        if not locations or not all(location.get('original_bbox_eligible') and
            location.get('source_sha256')==case['render_sha256'] for location in locations):continue
        centers=[[(location['bbox_px'][0]+location['bbox_px'][2])/2,(location['bbox_px'][1]+location['bbox_px'][3])/2] for location in locations]
        if all(box[0]<=x<=box[2] and box[1]<=y<=box[3] for x,y in centers):matches.append(region)
    observed=[row[-1]['text'] for row in matches[0]['rows']] if len(matches)==1 else []
    checks.append({'region':name,'reference_box_px':box,'expected':expected,'observed':observed,'passed':observed==expected})
matched=sum(len(check['expected']) for check in checks if check['passed'])
expected_count=sum(len(check['expected']) for check in checks)
report={'not_official_benchmark':True,'scope':'selected_original_page_regions_not_complete_page',
    'source_pdf_sha256':case['source_pdf_sha256'],'source_report':str(source),
    'matched_value_count':matched,'expected_value_count':expected_count,'complete':all(check['passed'] for check in checks),
    'labels_semantically_verified':False,'checks':checks}
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'matched':matched,'expected':expected_count,'missing':[check['region'] for check in checks if not check['passed']],
                  'report':str(output)},ensure_ascii=False))
