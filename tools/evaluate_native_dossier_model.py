"""Paired real-model development ablation on ten newly generated source scopes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import sys
import tempfile

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses
from backend.source_answer_dossier import replay_context
from evaluate_ohr_bench import implementation_snapshot, safe_audits, answer_output_fields
from model_runtime import enable_local_model, local_model_headers


def make_pdf(texts):
    with fitz.open() as pdf:
        for text in texts:
            p = pdf.new_page()
            if p.insert_textbox(fitz.Rect(40, 40, 550, 740), text, fontsize=11) < 0:
                raise ValueError('fixture_does_not_fit')
        return pdf.tobytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay-report', type=Path,
        help='Reuse the exact retained paired questions and original PDF corpus')
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != ROOT/'docs':
        parser.error('New docs report required')
    enable_local_model('gpt-6-luna')
    snapshot = implementation_snapshot()
    retained = None
    if args.replay_report:
        if args.replay_report.resolve().parent != ROOT/'docs':
            parser.error('Retained docs report required')
        retained = json.loads(args.replay_report.read_bytes())
        directory = Path(retained['directory']).resolve()
        if not directory.is_relative_to((ROOT/'runtime').resolve()) or not (directory/'knowledge').is_dir():
            parser.error('Retained local corpus missing or outside runtime')
    else:
        directory = Path(tempfile.mkdtemp(prefix='native-dossier-', dir=ROOT/'runtime'))
    store = KnowledgeStore(directory/'knowledge')
    rng = random.SystemRandom()
    names = lambda: ''.join(rng.choice('abcdefghjkmnpqrstuvwxyz') for _ in range(9)).capitalize()
    cases = []
    if retained:
        cases = [{k: r[k] for k in ('id', 'question', 'expected_scoring_only')}
            for r in retained['records'] if r['dossier_enabled'] is False]
        if len(cases) != 10 or len({c['id'] for c in cases}) != 10:
            parser.error('Ten distinct retained paired cases required')
        for document in store.list_documents():
            store.verify_source(document['document_id'], expected_sha256=document['sha256'])
    for index in range(0 if retained else 10):
        label, a, b, excluded = 'Ledger'+names(), names()+' Agency', names()+' Office', names()+' Council'
        amount, threshold = str(rng.randint(12, 86))+'.7', str(rng.randint(21, 49))
        suffix = ' in '+label+'?'
        if index == 0:
            texts = [f'{label} Conservation Report FY 2046.\n{a} and {b} manage all river sites except Old Pool and Dry Lake.\nOld Pool and Dry Lake are managed by {excluded}.']
            q = 'Which agencies manage the river sites excluding Old Pool and Dry Lake'+suffix
            expected = a+' and '+b
        elif index == 1:
            texts = [f'{label} auction terms.\nA successful bidder must pay a deposit of no less than {threshold} percent of the winning bid.\nThe remaining balance is due in thirty days.']
            q = 'What is the minimum deposit percentage for a successful bidder'+suffix
            expected = threshold+' percent'
        elif index == 2:
            person = names()+' '+names()
            texts = [f'{label} Personnel Report.\n{a} administers the department.\n{person} is personally responsible for overseeing personnel in the west region.\n{b} processes equipment orders only.']
            q = 'Who is responsible for overseeing personnel in the west region'+suffix
            expected = person
        elif index == 3:
            texts = [f'{label} Payment notice.\nThe accepted deposit payment methods are certified check, postal money order, bank draft, and electronic transfer.\nCredit cards are not accepted.']
            q = 'What are all accepted deposit payment methods'+suffix
            expected = 'certified check, postal money order, bank draft, and electronic transfer'
        elif index == 4:
            texts = [f'{label} Emergency support release FY 2046.\nPublic support was used to satisfy guaranteed agreement obligations to municipalities.\nMunicipalities received a total of ${amount} billion in satisfaction of those guaranteed agreement obligations.',
                f'{label} Support attachment.\nThe diagram includes support allocations and equipment costs. The detailed prose is in the release.']
            q = 'How was public support used for guaranteed agreements and what was the total amount'+suffix
            expected = f'Public support satisfied guaranteed agreement obligations to municipalities, totaling ${amount} billion.'
        elif index == 5:
            texts = [f'{label} Habitat survey.\nAdults feed and spawn over gravel, cobble, and bedrock with no silt overlay.\nYoung animals instead shelter in vegetation.']
            q = 'What types of substrates do adults feed and spawn over'+suffix
            expected = 'gravel, cobble, and bedrock with no silt overlay'
        elif index == 6:
            texts = [f'{label} Services register FY 2046.\n{a} manages the upland sites except Low Spring, while {b} manages the remaining coastal sites.\n{excluded} is responsible only for Low Spring.']
            q = 'Which organizations together manage the upland and coastal sites except Low Spring'+suffix
            expected = a+' and '+b
        elif index == 7:
            texts = [f'{label} report edition 2046.\nThe authorized operator is {a}.\nThis replaces the prior edition.']
            store.ingest(make_pdf([f'{label} report edition 2045.\nThe authorized operator was {b}.\nThis old edition is no longer current.']),
                document_id=f'old-{index}', title=label+' old', modality='pdf', filename=f'old-{index}.pdf')
            q = 'Which operator is authorized in the 2046 report edition of '+label+'?'
            expected = a
        elif index == 8:
            texts = [f'{label} department roster.\n{a} is responsible for maintenance equipment.\nNo personnel supervision appointment is recorded.']
            q = 'Who is responsible for overseeing personnel'+suffix
            expected = None
        else:
            texts = [f'{label} FY 2046 services release.\nThe authorized operator is {a}.']
            store.ingest(make_pdf([f'{label} FY 2046 services release.\nThe authorized operator is {b}.']),
                document_id=f'conflict-{index}', title=label+' conflicting', modality='pdf', filename=f'conflict-{index}.pdf')
            q = 'Which operator is authorized in the FY 2046 release of '+label+'?'
            expected = None
        raw = make_pdf(texts)
        store.ingest(raw, document_id=f'dossier-{index}', title=label, modality='pdf', filename=f'{label}.pdf')
        cases.append({'id': str(index), 'question': q, 'expected_scoring_only': expected})
    def execute(item):
        case, enabled = item
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        client.supports_native_answer_dossier = enabled
        local = KnowledgeStore(store.root, generator=GroundedGenerator(client))
        result = local.answer(case['question'], top_k=4)
        reference = case['expected_scoring_only']
        scored = answer_output_fields(result, reference) if reference is not None else {'answer': result['answer']}
        passed = (result['status'] != 'ok' if reference is None else
            result['status'] == 'ok' and scored['scores']['normalized_exact_match'] == 1)
        replay = None
        if result.get('answer_mode') == 'native_page_dossier_model_reviewed':
            replay_context(local, result)
            replay = True
        record = {**case, 'dossier_enabled': enabled, 'status': result['status'],
            'mode': result['answer_mode'], **scored, 'passed': passed, 'original_replay': replay,
            'trace': result['trace'], 'citations': result['citations'],
            'native_dossier_proof': result.get('native_dossier_proof'), 'api_audits': safe_audits(client)}
        print(json.dumps({k: record[k] for k in ('id', 'dossier_enabled', 'status', 'mode', 'passed')}, ensure_ascii=False), flush=True)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(execute, [(case, enabled) for case in cases for enabled in (False, True)]))
    end = implementation_snapshot()
    summary = {name: {'passed': sum(r['passed'] for r in records if r['dossier_enabled'] == enabled),
        'total': 10, 'refusal_cases': 2, 'factual_answer_cases': 8}
        for name, enabled in [('disabled_ablation', False), ('enabled', True)]}
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'newly_generated_exposed_development_paired_adapter_ablation_not_official_accuracy',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'gold_or_document_hint_sent_to_production': False,
        'directory': str(directory), 'implementation_file_sha256': snapshot,
        'replayed_from': str(args.replay_report) if args.replay_report else None,
        'implementation_file_sha256_end': end, 'implementation_stable': snapshot == end,
        'summary': summary, 'records': records,
        'limitations': ['One paired run; model variation exists.', 'Two expected refusals are not factual QA accuracy.',
            'Disabled ablation uses the same code with only the new adapter capability turned off.']}
    with args.output.open('x', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if snapshot == end else 1


if __name__ == '__main__':
    raise SystemExit(main())
