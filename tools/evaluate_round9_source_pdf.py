"""Replay native facts against original PDF bytes, never benchmark answers."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
import sys

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.native_text_tables import extract_native_text_tables, column_unit_declaration
from backend.native_table_question import annotation_arithmetic


def hashes():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    original=Path('D:/ICT8-OfficialDatasets/ohr-bench/pdfs/news/DUDE_70285d99e1ed228c46f12da196c27a81.pdf')
    raw=original.read_bytes()
    sha=hashlib.sha256(raw).hexdigest()
    if sha!='d57cef27fb630cbd561f9b39e719f7da1c265ee27f5c37b273479db06deb8554':
        raise ValueError('original_pdf_identity_changed')
    before=hashes()
    manifest=extract_native_text_tables(raw,page_no=3,expected_source_sha256=sha)
    replay=extract_native_text_tables(original.read_bytes(),page_no=3,expected_source_sha256=sha)
    with fitz.open(stream=raw,filetype='pdf') as document:
        page=document[2]
        words=page.get_text('words')
        def literal_from_box(box, separator):
            included=[]
            for word in words:
                display=list(fitz.Rect(word[:4])*page.rotation_matrix)
                if (display[0]>=box[0]-.001 and display[1]>=box[1]-.001
                        and display[2]<=box[2]+.001 and display[3]<=box[3]+.001):
                    included.append((display,word[4]))
            text=separator.join(text for _,text in sorted(included,key=lambda item:item[0][0]))
            union=([min(b[0] for b,_ in included),min(b[1] for b,_ in included),
                    max(b[2] for b,_ in included),max(b[3] for b,_ in included)] if included else None)
            exact=union is not None and max(abs(a-b) for a,b in zip(union,box))<.001
            return text,exact
        checked=[]
        for fact in manifest['facts']:
            # Match the actual native amount glyph and displayed rectangle.
            box=fact['bbox_display_pt']
            literal,exact=literal_from_box(box,'')
            matches=exact and literal==fact['raw_value']
            label,label_exact=literal_from_box(fact['row_header_bbox_display_pt'],' ')
            headers=[literal_from_box(b,' ') for b in fact['column_header_bboxes_display_pt']]
            replayed_headers=[text for text,_ in headers]
            header_valid=all(exact for _,exact in headers) and replayed_headers==fact['column_header_path']
            declaration=column_unit_declaration(replayed_headers)
            checked.append({'fact_id':fact['fact_id'],'row_header':fact['row_header'],
                'raw_value':fact['raw_value'],'amount_glyph_and_display_bbox_verified':bool(matches),
                'row_label_and_display_bbox_verified':label_exact and label==fact['row_header'],
                'column_header_glyphs_and_bboxes_verified':header_valid,
                'unit_and_scale_rederived_from_original_header':header_valid and declaration['unit']==fact['unit']
                    and declaration['scale']==fact.get('scale'),
                'unit':fact['unit'],'scale':fact.get('scale'),
                'header_path':fact['column_header_path'],
                'own_column_unit_binding':fact.get('unit_evidence',{}).get('binding')=='own_explicit_column_header_only'})
    selected=[]
    for label in ('Top 20 CDS Total','Total Collateral Postings'):
        candidates=[f for f in manifest['facts'] if f['row_header']==label]
        if len(candidates)!=1:
            raise ValueError('original_row_label_not_unique')
        selected.append(candidates[0])
    computation=annotation_arithmetic(selected,'percentage')
    independent_fraction=Fraction(Decimal(selected[0]['raw_value'].removeprefix('$')))/Fraction(
        Decimal(selected[1]['raw_value'].removeprefix('$')))*100
    after=hashes()
    checks={'original_hash_verified':True,'native_replay_equal':manifest==replay,
        'single_table':len(manifest['tables'])==1,'native_facts':len(checked)==23,
        'all_amount_glyphs_verified':all(c['amount_glyph_and_display_bbox_verified'] for c in checked),
        'all_row_labels_verified':all(c['row_label_and_display_bbox_verified'] for c in checked),
        'all_column_headers_verified':all(c['column_header_glyphs_and_bboxes_verified'] for c in checked),
        'all_units_rederived_from_original_headers':all(c['unit_and_scale_rederived_from_original_header'] for c in checked),
        'percentage_exact_fraction_independently_checked':computation['exact_fraction']=={
            'numerator':str(independent_fraction.numerator),'denominator':str(independent_fraction.denominator)},
        'all_units_bound_to_own_column':all(c['own_column_unit_binding'] for c in checked),
        'all_column_scales_preserved':all(c['scale']=='1000000000' for c in checked)}
    report={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'original_pdf_native_geometry_and_annotation_arithmetic_replay_not_question_accuracy',
        'model_called':False,'benchmark_gold_opened':False,'original_path':str(original),
        'original_sha256':sha,'page_no':3,'checks':checks,'facts':checked,
        'selected_annotation_computation':computation,
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,
        'implementation_stable':before==after,'ok':all(checks.values()) and before==after}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'ok':report['ok'],'checks':checks,'native_facts':len(checked)}))
    return 0 if report['ok'] else 1


if __name__=='__main__':
    raise SystemExit(main())
