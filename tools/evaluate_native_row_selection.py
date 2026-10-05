"""New randomized anonymous PDF row queries; reference values score only.

This is a development generalization check, not an official/held-out benchmark.
The ordinary answer pipeline sees only the question and its original PDF.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import uuid
import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses
from backend.native_row_selection import replay_selection
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def build_case(index, rng):
    tag = uuid.uuid4().hex[:7].upper()
    operator, other = 'PERSON_'+tag, 'PERSON_OTHER_'+tag
    method = 'METHOD_'+tag
    date = '2025-02-14'
    names = ['ASSET_'+uuid.uuid4().hex[:8].upper() for _ in range(8)]
    if index % 2 == 0:
        names[0] += ' *'  # Visible original name annotation, never silently cropped.
    source_rows = [[name, '* 17.50 UG/L' if i == 0 else '<20 UG/L' if i == 1 else f'{i+1}.25 UG/L',
        date if i < 6 else '2025-02-15', operator if i < 5 else other, method if i != 4 else method+'X']
        for i, name in enumerate(names)]
    # Independent reference inventory: rows 0..3 have exact method and actor;
    # row 4 is a prefix trap, rows 5..7 have a different actor.
    chosen = [source_rows[i] for i in (0, 1, 2, 3)]
    rng.shuffle(source_rows)
    kind = index % 4
    if kind == 0:
        question = f'Which items used method {method} and were handled by {operator}, and by whom?'
        columns = [0, 3]; status = 'ok'
    elif kind == 1:
        question = f'Which items used method {method} and were handled by {operator}, and what were their reported Result values?'
        columns = [0, 1]; status = 'ok'
    elif kind == 2:
        question = f'Which items used method {method} on {date} and were handled by {operator}?'
        columns = [0]; status = 'ok'
    else:
        question = f'Which two items used method {method} and were handled by {operator}?'
        columns = [0]; status = 'clarification'
    expected = {row[0]: {i: row[i] for i in columns} for row in chosen}
    with fitz.open() as doc:
        for pno in range(2):
            page = doc.new_page(width=1000, height=430)
            offset = [0, 20, 35][index % 3]
            page.insert_text((30+offset,35), 'Record ID: '+tag, fontsize=9)
            xs = [30+offset, 230+offset, 395+offset, 555+offset, 790+offset]
            for x, header in zip(xs,['Item' if index%2 else 'Description','Result','Date','Operator','Method']):
                page.insert_text((x,70), header, fontsize=9)
            for ri, row in enumerate(source_rows[pno*4:pno*4+4]):
                for x, value in zip(xs,row):
                    page.insert_text((x,88+ri*20),value,fontsize=9)
        raw = doc.tobytes()
    return {'case_id': f'anonymous-{index+1}', 'question': question, 'pdf': raw,
        'reference_status': status, 'reference_fields_scoring_only': expected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists():
        parser.error('New output directly in docs required')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    run = ROOT/'runtime'/('native-row-generalization-'+uuid.uuid4().hex); run.mkdir()
    cases = [build_case(i, random.Random(804+i)) for i in range(8)]
    def execute(case):
        work = run/case['case_id']; work.mkdir()
        (work/'original.pdf').write_bytes(case['pdf'])
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
        # Preserve actual first-attempt plan/review values for failure diagnosis.
        # Observe only: do not replace a model result, inject reference fields,
        # retry a rejection, or change the ordinary production answer pipeline.
        observations = []
        generate = client.generate
        def capture(*positional, **keywords):
            value = generate(*positional, **keywords)
            if keywords.get('name') in {'native_row_selection_plan', 'native_row_selection_independent_review'}:
                observations.append({'operation': keywords['name'], 'model_result': value})
            return value
        client.generate = capture
        store = KnowledgeStore(work/'knowledge',generator=GroundedGenerator(client))
        document = store.ingest(case['pdf'],document_id='anonymous',title='Anonymous source records',modality='pdf',filename='records.pdf')
        result = store.answer(case['question'],top_k=4)
        expected = case['reference_fields_scoring_only']; actual = {}; errors = []
        proof = result.get('native_row_proof')
        replayed = bool(proof) and replay_selection(store,result)
        if proof:
            rows = {r['row_id']: r for r in proof['selected_rows']}
            for row in proof['projection']:
                source = rows[row['row_id']]
                actual[source['fields'][0]['text']] = {f['column_index']: f['quote'] for f in row['fields']}
        if result['status'] != case['reference_status']:
            errors.append('status_mismatch')
        if set(actual) != set(expected):
            errors.append('matching_inventory_mismatch')
        # A genuine ambiguity may preserve extra displayed fields in proof.
        # Successful answers must contain exactly the requested field set.
        if result['status'] == 'ok' and actual != expected:
            errors.append('projected_fields_or_qualifiers_mismatch')
        if not replayed:
            errors.append('source_replay_failed_or_mode_absent')
        report = {k:v for k,v in case.items() if k!='pdf'}
        report.update(passed=not errors,errors=errors,result=result,source_replay_passed=replayed,
            source_sha256=document['sha256'],api_audits=safe_audits(client),actual_fields=actual,
            first_attempt_selection_observations=observations,
            question_only_no_reference_sent_to_model=True)
        (work/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'case':case['case_id'],'status':result['status'],'passed':not errors,'errors':errors}),flush=True)
        return report
    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(execute,cases))
    after = implementation_snapshot()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'new_randomized_anonymous_development_PDF_rows_not_official_accuracy',
        'scenario_revision':'anonymous-rows-with-original-name-footnotes-v2',
        'scenario_generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'model':'gpt-6-luna','reasoning':'medium','gold_sent_to_model':False,
        'planned':len(cases),'passed':sum(r['passed'] for r in reports),
        'substantive_answers':sum(r['passed'] and r['reference_status']=='ok' for r in reports),
        'supported_clarifications':sum(r['passed'] and r['reference_status']=='clarification' for r in reports),
        'implementation_stable':before==after,'implementation_file_sha256_start':before,
        'implementation_file_sha256_end':after,'run_directory':str(run),'cases':reports}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:report[k] for k in ('planned','passed','substantive_answers','supported_clarifications','implementation_stable')}))


if __name__=='__main__':
    main()
