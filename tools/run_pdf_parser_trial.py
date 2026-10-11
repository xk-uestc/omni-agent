"""Parse frozen original inputs without loading annotations, QA answers or Gold.

Run in the chosen parser's isolated environment. The output is observation data,
not verified facts or executable formulas. Retained inputs require an explicit flag.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import resource
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--data-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--backend', choices=('native', 'mineru', 'docling'), required=True)
    p.add_argument('--split', choices=('dev', 'retained'), default='dev')
    p.add_argument('--allow-retained', action='store_true')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--page-limit', type=int, default=40)
    p.add_argument('--timeout', type=int, default=300)
    p.add_argument('--native-ocr', action='store_true')
    p.add_argument('--skip-formula-enrichment', action='store_true')
    args = p.parse_args()
    if args.split == 'retained' and not args.allow_retained:
        p.error('Retained evaluation only after candidate freeze')
    if args.output.exists():
        p.error('Do not overwrite a saved run')
    args.output.mkdir(parents=True)
    docs = [d for d in json.loads(args.manifest.read_text())['documents'] if d['split'] == args.split]
    if args.limit:
        docs = docs[:args.limit]
    versions = {'python': sys.version.split()[0]}
    if args.backend != 'native':
        versions[args.backend] = importlib.metadata.version(args.backend)
    converter = None
    records = []
    for index, doc in enumerate(docs):
        source = (args.data_root/doc['pdf_path']).resolve()
        if not source.is_relative_to(args.data_root.resolve()):
            raise ValueError('input_outside_root')
        raw = source.read_bytes()
        if digest(raw) != doc['pdf_sha256']:
            raise ValueError('input_sha_mismatch')
        name = f'{index:02d}-{source.stem}'
        out = args.output/name
        out.mkdir()
        start = time.perf_counter()
        result = {'source': doc, 'backend': args.backend, 'versions': versions, 'status': 'failed',
                  'scope': 'first_pages_budget', 'page_limit': args.page_limit,
                  'gold_loaded_by_parser': False, 'generation': 'not_run'}
        try:
            if args.backend == 'native':
                from backend.knowledge_store import KnowledgeStore
                pipeline = None
                if args.native_ocr:
                    from backend.ocr import OcrPipeline, RapidOcrExecutor
                    pipeline = OcrPipeline(RapidOcrExecutor())
                store = KnowledgeStore(out/'knowledge', ocr_pipeline=pipeline, generator=None)
                receipt = store.ingest(raw, document_id='trial', title=source.stem[:200], modality='pdf', filename=source.name)
                document = store.document('trial')
                (out/'native.json').write_text(json.dumps(document, ensure_ascii=False, indent=2))
                result.update(page_count=receipt['stats'].get('page_count'),
                              chunk_count=receipt['chunk_count'], scope='whole_document_native',
                              native_ocr=args.native_ocr)
            elif args.backend == 'mineru':
                cmd = [str(Path(sys.executable).parent/'mineru-kit'), 'parse', str(source),
                       '--pages', f'1-{args.page_limit}', '--tier', 'basic', '--ocr-mode', 'auto',
                       '--format', 'middle_json', '--output', str(out/'mineru')]
                result['command'] = cmd
                with (out/'parser.txt').open('w') as log:
                    proc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout,
                                          env={**os.environ, 'MINERU_HOME': '/tmp/omni-mineru-models',
                                               'HF_HOME': '/tmp/omni-parser-hf', 'OMP_NUM_THREADS': '4',
                                               'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_XET': '1'})
                if proc.returncode:
                    raise RuntimeError('mineru_exit_'+str(proc.returncode))
                files = sorted((out/'mineru').rglob('*.json'))
                result['raw_files'] = [str(f.relative_to(args.output)) for f in files]
                if not files:
                    raise ValueError('mineru_no_structured_output')
            else:
                cmd = [sys.executable, str(ROOT/'tools/docling_pdf_worker.py'), '--source', str(source),
                       '--output', str(out), '--pages', str(args.page_limit), '--timeout', str(args.timeout)]
                if args.skip_formula_enrichment:
                    cmd.append('--skip-formula')
                result.update(command=cmd, process_policy='isolated_cold_worker_hard_timeout')
                with (out/'parser.txt').open('w') as log:
                    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                        env={**os.environ,'OMP_NUM_THREADS':'4','MKL_NUM_THREADS':'4','HF_HUB_OFFLINE':'1'})
                    try:
                        code = proc.wait(timeout=args.timeout)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                        raise RuntimeError('docling_hard_timeout')
                result['raw_files'] = [str((out/'docling.json').relative_to(args.output))] if (out/'docling.json').exists() else []
                if (out/'worker-status.json').exists():
                    result.update(json.loads((out/'worker-status.json').read_text()))
                if code:
                    raise RuntimeError('docling_partial_or_failed')
            result['status'] = 'ok'
        except Exception:
            result['error'] = traceback.format_exc(limit=6)
        result['seconds'] = round(time.perf_counter()-start, 4)
        result['process_peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2)
        result['children_peak_rss_mb'] = round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/1024, 2)
        records.append(result)
        (args.output/'run.json').write_text(json.dumps({'backend': args.backend, 'versions': versions,
            'planned_input_count':len(docs),'finished':len(records)==len(docs),
            'split': args.split, 'gold_isolation': 'input_manifest_and_original_bytes_only', 'records': records},
            ensure_ascii=False, indent=2))
        print(json.dumps({k: result[k] for k in ('backend', 'status', 'seconds', 'process_peak_rss_mb', 'children_peak_rss_mb')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
