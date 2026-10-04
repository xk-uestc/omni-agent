"""Model runner loads only system input; never opens scoring/reference files."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import threading
import time

from cross_schema_round5_common import (DEFAULT, EVALUATION_SUBDIR, READINESS_FILE, MODEL, ROOT, canonical, digest,
    forbidden_payload_fields, implementation_hashes, now, value_digest, verify_public, write_new)


class PayloadLedger:
    def __init__(self, cases):
        self.questions = {c['case_id']: c['question'] for c in cases}
        self.completed_ids = set()
        self.current = None
        self.records = []

    def attach(self, client):
        generate = client.generate
        def guarded(instructions, context, schema, **kwargs):
            payload = {'instructions': instructions, 'context': context, 'schema': schema, 'options': kwargs}
            if self.current is None or forbidden_payload_fields(payload):
                raise ValueError('private_reference_payload_rejected')
            encoded = canonical(payload).decode('utf-8')
            future = [q for identifier, q in self.questions.items()
                      if identifier != self.current and identifier not in self.completed_ids]
            if any(q in encoded for q in future):
                raise ValueError('future_question_payload_rejected')
            self.records.append({'case_id': self.current, 'operation': kwargs.get('name'),
                'payload_sha256': value_digest(payload), 'context_keys': sorted(context),
                'private_fields_absent': True, 'future_question_absent': True})
            return generate(instructions, context, schema, **kwargs)
        client.generate = guarded


def partition_cases(cases, workers):
    """Keep each persisted session in one ordered unit, then balance turn counts."""
    if workers not in (1, 2, 3):
        raise ValueError('workers_must_be_one_two_or_three')
    units = []
    sessions = {}
    for case in cases:
        if case['kind'] == 'single':
            units.append([case])
        elif case['kind'] == 'session':
            identifier = case['session_id']
            if identifier not in sessions:
                sessions[identifier] = []
                units.append(sessions[identifier])
            sessions[identifier].append(case)
        else:
            raise ValueError('unknown_case_kind')
    if any([c['turn_index'] for c in unit] != [1, 2, 3, 4, 5]
           for unit in sessions.values()):
        raise ValueError('session_order_must_be_five_original_turns')
    buckets = [[] for _ in range(workers)]
    counts = [0] * workers
    for unit in units:
        target = min(range(workers), key=lambda index: counts[index])
        buckets[target].append(unit)
        counts[target] += len(unit)
    return buckets


def execute_partitions(partitions, execute):
    """One worker owns one independent runtime; caller handles its stop event."""
    with ThreadPoolExecutor(max_workers=len(partitions), thread_name_prefix='cross-schema') as pool:
        futures = {pool.submit(execute, index, units): index for index, units in enumerate(partitions)}
        results, errors = {}, {}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                errors[index] = type(exc).__name__
    return results, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--run-model', action='store_true', help='Explicit opt-in; default is no API preflight')
    parser.add_argument('--output-directory', type=Path)
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    source, database, public, bundle = verify_public(args.directory)
    if not args.run_model:
        readiness = Path(args.directory) / EVALUATION_SUBDIR / READINESS_FILE
        ready = readiness.exists() and load_readiness(readiness, public)
        print(json.dumps({'mode': 'offline_preflight_no_credentials_no_api', 'planned_total': 56,
            'standalone_total': 36, 'sessions': 4, 'turns_per_session': 5,
            'source_commit': source['source_commit'], 'database_sha256': public['database_sha256'],
            'schema_sha256': public['schema_sha256'], 'system_input_sha256': public['system_input_sha256'],
            'reference_opened': False, 'model_api_calls': 0, 'baseline_ready': ready, 'workers': args.workers}))
        return 0
    readiness = Path(args.directory) / EVALUATION_SUBDIR / READINESS_FILE
    if not readiness.exists() or load_readiness(readiness, public) is not True:
        parser.error('Offline source/reference/scorer validation must pass before model run')
    sys.path.insert(0, str(ROOT / 'ict-track8'))
    from model_runtime import enable_local_model, local_model_headers
    from backend.responses_client import StructuredResponses
    from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    from backend.knowledge_store import KnowledgeStore
    config = enable_local_model(MODEL)
    start_hashes = implementation_hashes()
    output = args.output_directory.resolve() if args.output_directory else (
        Path(args.directory) / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    if output.exists():
        parser.error('Run directory exists; refusing overwrite')
    output.mkdir(parents=True)
    reference_date = date.fromisoformat(bundle['reference_date'])
    started = time.perf_counter()
    observed = output / 'observed.jsonl'
    stop = threading.Event()
    mutex = threading.Lock()
    stop_reasons = []
    worker_files = {}
    def halt(reason):
        with mutex:
            stop_reasons.append(reason)
            stop.set()
    def execute_worker(index, units):
        # Each thread constructs and exclusively owns every runtime object.
        worker_root = output / ('worker-' + str(index + 1))
        worker_root.mkdir()
        worker_observed = worker_root / 'observed.jsonl'
        try:
            provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
                model=MODEL, reasoning_effort=config['reasoning'], reference_date=reference_date, http_headers=local_model_headers())
            client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
                model=MODEL, reasoning=config['reasoning'], http_headers=local_model_headers())
            engine = Nl2SqlEngine(database, model_plan_provider=provider, reference_date=reference_date, max_seconds=5.0,
                metric_catalog_path=worker_root / 'absent_catalog.json')
            if engine.metric_catalog is not None or engine.value_aliases_path is not None:
                raise ValueError('domain_catalog_or_value_alias_not_allowed')
            knowledge = KnowledgeStore(worker_root / 'empty_knowledge')
            sessions = ConversationStore(storage_path=worker_root / 'sessions.sqlite')
            agent = OmniAgent(engine, knowledge, sessions, client)
            ledger = PayloadLedger(bundle['cases'])
            ledger.attach(provider.client)
            ledger.attach(client)
            with worker_observed.open('x', encoding='utf-8', newline='\n') as stream:
                for unit in units:
                    for case in unit:
                        if stop.is_set():
                            break
                        if implementation_hashes() != start_hashes or digest(database) != public['database_sha256']:
                            halt('source_or_implementation_changed'); break
                        provider.reset_audit(); client.reset_audit()
                        ledger.current = case['case_id']; ledger.records = []
                        history = sessions.context(case['session_id']) if case['kind'] == 'session' else ()
                        turn_started = time.perf_counter()
                        try:
                            response = (engine.answer(case['question']).to_dict() if case['kind'] == 'single'
                                else agent.query(case['question'], session_id=case['session_id']))
                            error = None
                            safety_diagnostics = None
                        except Exception as exc:
                            response = None; error = type(exc).__name__
                            safety_diagnostics = getattr(exc, 'safety_diagnostics', None)
                        audits = [{**a, 'component': name} for name, api in [('sql', provider), ('router', client)] for a in api.audit_history]
                        dropped = provider.audit_dropped_count + client.audit_dropped_count
                        row = {'case_id': case['case_id'], 'kind': case['kind'], 'session_id': case['session_id'],
                            'turn_index': case['turn_index'], 'worker': index + 1, 'history_before_count': len(history),
                            'history_before_sha256': value_digest([{'question': h.question,
                                'effective_question': h.effective_question, 'state': h.state} for h in history]),
                            'response': response, 'error_type': error, 'safety_diagnostics': safety_diagnostics,
                            'api_audits': audits, 'api_audit_dropped': dropped,
                            'payload_audit': ledger.records, 'latency_ms': round((time.perf_counter()-turn_started)*1000,3)}
                        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n'); stream.flush()
                        ledger.completed_ids.add(case['case_id'])
                        with mutex:
                            print(json.dumps({'case_id': case['case_id'], 'worker': index + 1,
                                'status': response.get('status') if response else 'error',
                                'api_calls': len(audits)+dropped, 'latency_ms': row['latency_ms']}), flush=True)
                        if any(a.get('http_status') in {401,403} for a in audits):
                            halt('authentication_or_access_rejected')
                        if implementation_hashes() != start_hashes or digest(database) != public['database_sha256']:
                            halt('source_or_implementation_changed')
                    if stop.is_set():
                        break
        except Exception:
            halt('worker_runtime_error')
            raise
        finally:
            if worker_observed.exists():
                with mutex:
                    worker_files[worker_observed.relative_to(output).as_posix()] = digest(worker_observed)
        return True
    partitions = partition_cases(bundle['cases'], args.workers)
    _, worker_errors = execute_partitions(partitions, execute_worker)
    rows = {}
    for relative in sorted(worker_files):
        for line in (output / relative).read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row['case_id'] in rows:
                raise ValueError('duplicate_worker_observation')
            rows[row['case_id']] = row
    attempted = [case['case_id'] for case in bundle['cases'] if case['case_id'] in rows]
    with observed.open('x', encoding='utf-8', newline='\n') as stream:
        for identifier in attempted:
            stream.write(json.dumps(rows[identifier], ensure_ascii=False, allow_nan=False) + '\n')
    status = sorted(set(stop_reasons))[0] if stop_reasons else 'completed'
    end_hashes = implementation_hashes()
    manifest = {'created_at': now(), 'mode': 'real_model', 'requested_model': MODEL,
        'reasoning': config['reasoning'], 'status': status, 'planned_total': 56, 'executed_total': len(attempted),
        'not_run': [c['case_id'] for c in bundle['cases'] if c['case_id'] not in attempted],
        'source_manifest_sha256': public['source_manifest_sha256'], 'database_sha256': public['database_sha256'],
        'schema_sha256': public['schema_sha256'], 'system_input_sha256': public['system_input_sha256'],
        'observed_sha256': digest(observed), 'implementation_file_sha256': start_hashes,
        'implementation_file_sha256_end': end_hashes, 'implementation_stable': start_hashes == end_hashes,
        'source_stable': digest(database) == public['database_sha256'],
        'wall_ms': round((time.perf_counter()-started)*1000,3), 'reference_opened': False,
        'few_shot_examples': 0, 'domain_alias_rules': 0, 'metric_catalog': None,
        'workers': args.workers, 'worker_errors': worker_errors, 'stop_reasons': sorted(set(stop_reasons)),
        'worker_observations_sha256': worker_files,
        'worker_case_ids': [[c['case_id'] for unit in units for c in unit] for units in partitions],
        'worker_isolation': 'Independent provider/client/ledger/engine/KnowledgeStore/ConversationStore per worker; one ordered session per unit.',
        'limits': {'max_rows': 100, 'max_seconds': 5.0, 'max_steps': 50_000_000},
        'history_policy': 'Actual persisted system state only; failures retained by product behavior, never patched from reference.',
        'official_benchmark_score': False}
    write_new(output / 'RUN_MANIFEST.json', manifest)
    print(json.dumps({'status': status, 'planned_total': 56, 'executed_total': len(attempted),
        'implementation_stable': manifest['implementation_stable'], 'reference_opened': False,
        'run_directory': str(output)}))
    return 0 if status == 'completed' else 1


def load_readiness(path, public):
    from cross_schema_round5_common import load
    readiness = load(path)
    return (readiness.get('baseline_ready') is True and readiness['system_input_sha256'] == public['system_input_sha256']
        and readiness['oracle_sha256'] == public['oracle_sha256']
        and readiness['tool_hashes'] == {name: digest(ROOT/'tools'/name) for name in readiness['tool_hashes']})


if __name__ == '__main__':
    raise SystemExit(main())
