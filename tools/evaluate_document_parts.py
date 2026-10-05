"""Fresh authored compound-document probes; references are scoring-only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers

CASES = [
    ('zh_three', '星河项目负责人是谁，预算是多少，截止日期是什么？',
     ['星河项目负责人是林青。', '星河项目预算为237万元。', '星河项目截止日期是2031年11月16日。'],
     ['林青', '237', '2031年11月16日'], False),
    ('zh_semicolon', '远帆项目使用哪些设备；由谁负责验收？',
     ['远帆项目使用的全部设备为光谱仪和离心机。', '远帆项目由周岚负责验收。'],
     ['光谱仪', '离心机', '周岚'], False),
    ('en_semicolon', 'Who is the research lead for Project-K73; what is its budget; when is its deadline?',
     ['The research lead for Project-K73 is Asha Patel.', 'The budget for Project-K73 is 319 thousand USD.',
      'The deadline for Project-K73 is November 17, 2032.'],
     ['Asha Patel', '319', 'November 17, 2032'], False),
    ('en_departments', 'Which departments participate in Project-J82?',
     ['The complete set of participating departments for Project-J82 is Optics, Logistics, and Compliance.'],
     ['Optics', 'Logistics', 'Compliance'], False),
    ('zh_periods', '云舟项目2031年负责人是谁，2032年负责人是谁？',
     ['云舟项目2031年负责人是许宁。', '云舟项目2032年负责人是孟遥。'],
     ['许宁', '孟遥'], False),
    ('en_newlines', 'Who is the lead for Project-T91?\nWhat is the deadline for Project-T91?',
     ['The lead for Project-T91 is Omar Reed.', 'The deadline for Project-T91 is June 19, 2033.'],
     ['Omar Reed', 'June 19, 2033'], False),
    ('missing', '清泉项目负责人是谁，预算是多少？',
     ['清泉项目负责人是沈墨。本记录没有记载预算。'], [], True),
    ('conflict', 'Project-Q64负责人是谁，预算是多少？',
     ['Project-Q64负责人是李川，预算为183万元。',
      'Project-Q64同一版本负责人是赵宁；两份记录没有优先顺序。'], [], True),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != ROOT / 'docs':
        parser.error('Use a new report in docs')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    directory = ROOT / 'runtime' / ('document-parts-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    directory.mkdir()

    def execute(row):
        identifier, question, sources, required, abstain = row
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        store = KnowledgeStore(directory / identifier, generator=GroundedGenerator(client))
        for index, source in enumerate(sources):
            store.ingest(source.encode(), document_id=f'record-{index}', title=f'Record {index}',
                         modality='txt', filename=f'record-{index}.txt')
        try:
            result = store.answer(question, top_k=1)
            audits = safe_audits(client)
            verified = bool(audits) and all(a.get('status') == 'completed' and a.get('model_verified') is True for a in audits)
            passed = verified and (result['status'] != 'ok' if abstain else
                result['status'] == 'ok' and result['answer_mode'] not in ('extractive_fallback', 'attributed_extracts')
                and all(value in result['answer'] for value in required))
            record = {'id': identifier, 'question': question, 'sources': sources, 'result': result,
                      'api_audits': audits, 'pass': passed,
                      'scoring_only': {'required': required, 'expected_abstention': abstain}}
        except Exception as exc:
            record = {'id': identifier, 'pass': False, 'error_type': type(exc).__name__,
                      'api_audits': safe_audits(client)}
        print(json.dumps({'id': identifier, 'pass': record['pass']}, ensure_ascii=False), flush=True)
        return record

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        cases = list(pool.map(execute, CASES))
    after = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'cases': cases,
        'passed': sum(case['pass'] for case in cases), 'total': len(cases),
        'suite_sha256': hashlib.sha256(json.dumps(CASES, ensure_ascii=False).encode()).hexdigest(),
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'reference_used_as_model_input': False,
        'implementation_file_sha256': before, 'implementation_file_sha256_end': after,
        'implementation_stable': before == after,
        'limitations': ['Eight authored text-only stress cases, top_k=1; not official accuracy.',
                        'Required-token scoring is not a proof of semantic correctness.',
                        'Two cases require refusal, not a substantive answer.']}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'total': report['total'], 'stable': before == after}))
    return 0 if before == after and report['passed'] == len(cases) else 1


if __name__ == '__main__':
    raise SystemExit(main())
