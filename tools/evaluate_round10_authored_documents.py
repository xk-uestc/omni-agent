"""Fresh compound/list prose probes through production retrieval and answering."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    suffix = secrets.token_hex(3).upper()
    label = 'Campaign-' + suffix
    first, second, third = ['Test-' + name + '-' + suffix for name in ('Spectrum', 'Redox', 'Flame')]
    definitions = [
        ('cross_sources', 'Who is the research lead and who is the finance lead for ' + label + '?',
         [f'For {label}, the research lead is Mira Chen.', f'For {label}, the finance lead is Noah Lee.'],
         ['Mira Chen', 'Noah Lee'], False),
        ('three_tests', f'Which three tests are used in {label}?',
         [f'The complete list of tests for {label} is {first}, {second}, {third}.'],
         [first, second, third], False),
        ('enumeration_and_role', f'Which tests are used in {label} and who is the analyst?',
         [f'The complete list of tests for {label} is {first}, {second}, {third}.',
          f'For {label}, the analyst is Priya Shah.'], [first, second, third, 'Priya Shah'], False),
        ('missing_part', f'Who is the research lead and who is the finance lead for {label}?',
         [f'For {label}, the research lead is Mira Chen. No finance lead is stated in this record.'], [], True),
        ('conflicting_roles', f'Who is the research lead and who is the finance lead for {label}?',
         [f'For {label}, the research lead is Mira Chen and the finance lead is Noah Lee.',
          f'For the same {label}, the research lead is Priya Shah. Neither record overrides the other.'], [], True),
        ('period_qualified', f'Who is the research lead in 2031 and who is the research lead in 2032 for {label}?',
         [f'For {label}, the research lead in 2031 is Mira Chen.',
          f'For {label}, the research lead in 2032 is Noah Lee.'], ['Mira Chen', 'Noah Lee'], False),
    ]
    directory = ROOT / 'runtime' / ('round10-prose-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir()
    stop = threading.Event()
    def execute(row):
        identifier, question, sources, required, abstention = row
        if stop.is_set():
            return {'id': identifier, 'question': question, 'pass': False, 'status': 'not_run', 'api_audits': []}
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        store = KnowledgeStore(directory / identifier, generator=GroundedGenerator(client))
        pins = []
        for index, text in enumerate(sources):
            raw = text.encode('utf-8')
            did = 'record-' + str(index)
            store.ingest(raw, document_id=did, title=label + ' record', modality='txt', filename=did + '.txt')
            pins.append({'id': did, 'sha256': hashlib.sha256(raw).hexdigest(), 'text': text})
        try:
            result = store.answer(question, top_k=8)
            audits = safe_audits(client)
            verified = bool(audits) and all(a.get('status') == 'completed' and a.get('model_verified') is True for a in audits)
            passed = verified and (result['status'] != 'ok' if abstention else
                result['status'] == 'ok' and result['answer_mode'] not in ('extractive_fallback', 'attributed_extracts')
                and all(value in result['answer'] for value in required))
            case = {'id': identifier, 'question': question, 'sources': pins, 'result': result, 'api_audits': audits,
                'pass': passed, 'scoring_only': {'required': required, 'expected_abstention': abstention}}
        except Exception as exc:
            case = {'id': identifier, 'question': question, 'pass': False, 'error_type': type(exc).__name__,
                'sources': pins, 'api_audits': safe_audits(client)}
        if any(a.get('http_status') in (401, 403) for a in case['api_audits']):
            stop.set()
        print(json.dumps({'id': identifier, 'pass': case['pass']}, ensure_ascii=False), flush=True)
        return case
    with ThreadPoolExecutor(max_workers=2) as pool:
        cases = list(pool.map(execute, definitions))
    after = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'authored_small_prose_compound_cases_with_random_labels_not_blind_or_official_accuracy',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'reference_used_as_model_input': False,
        'implementation_file_sha256': before, 'implementation_file_sha256_end': after,
        'implementation_stable': before == after, 'cases': cases, 'passed': sum(c['pass'] for c in cases), 'total': 6,
        'limitations': ['Required-token scoring does not prove general semantic correctness.',
            'Two planned cases require abstention; six passes would not mean six substantive answers.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'passed': report['passed'], 'total': 6, 'stable': before == after}))
    return 0 if before == after and report['passed'] == 6 else 1


if __name__ == '__main__':
    raise SystemExit(main())
