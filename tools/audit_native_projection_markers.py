"""Read-only inspection of cropped source qualifiers; not a QA score."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.native_row_selection import replay_selection


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.output.resolve().parent!=(ROOT/'docs').resolve():
        parser.error('New output directly in docs required')
    raw=args.report.read_bytes();report=json.loads(raw)
    if report.get('implementation_stable') is not True:
        parser.error('Stable retained model report required')
    location=Path(report['store_path']).resolve()
    if not location.is_relative_to(Path('D:/ICT8-OfficialDatasets/ohr-bench/evaluations').resolve()):
        parser.error('Retained original PDF evaluation source required')
    store=KnowledgeStore(location);records=[]
    for case in report['cases']:
        result={'question':case['question'],**case['generation']}
        proof=result.get('native_row_proof')
        if result.get('answer_mode')!='native_row_selection_model_reviewed' or not proof:
            continue
        replayed=replay_selection(store,result)
        rows={r['row_id']:r for r in proof['selected_rows']}
        for projected in proof['projection']:
            for field in projected['fields']:
                original=rows[projected['row_id']]['fields'][field['column_index']]
                quote=field['quote'];source=original['text']
                # Visible boundary symbols are a diagnostic, not a guess at
                # what an unknown footnote means or whether QA is incorrect.
                boundary_markers=[]
                for pattern in (r'^\s*[<>≤≥*†‡]+',r'[*†‡]+\s*$'):
                    for match in re.finditer(pattern,source):
                        if not(field['start']<=match.start() and field['end']>=match.end()):
                            boundary_markers.append(match.group().strip())
                records.append({'ID':case['ID'],'row_id':projected['row_id'],
                    'column_index':field['column_index'],'header':original['header'],
                    'source_field':source,'display_quote':quote,'source_replay_passed':replayed,
                    'omitted_visible_boundary_markers':boundary_markers,
                    'numeric_field_cropped':bool(original['numeric_annotation']) and quote!=source})
    output={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'actual_source_projection_display_diagnostic_not_accuracy',
        'source_report_sha256':hashlib.sha256(raw).hexdigest(),'model_calls':0,
        'projection_fields':records,'fields_with_omitted_boundary_markers':sum(bool(r['omitted_visible_boundary_markers']) for r in records),
        'limitations':['Full original row remains in receipt/citation even if display quote is cropped.',
            'No footnote interpretation, gold correction, model retry or benchmark rescore.']}
    payload=json.dumps(output,ensure_ascii=False,indent=2)+'\n'
    with args.output.open('x',encoding='utf-8') as f:f.write(payload)
    print(json.dumps({'fields':len(records),'omitted_markers':output['fields_with_omitted_boundary_markers'],
        'all_source_replays_passed':all(r['source_replay_passed'] for r in records)}))


if __name__=='__main__':main()
