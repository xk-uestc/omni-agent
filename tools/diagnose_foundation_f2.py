"""Trace each frozen F2 required literal through the retrieval pipeline.

Gold is loaded only after source ingestion, candidate retrieval, Top-K
selection, and source-native dossier/table-chain construction have completed.
This is an evaluator, not production retrieval logic.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import tempfile
import time
import unicodedata
from pathlib import Path

from foundation_common import ROOT, dump, environment, sha, source_hashes
from evaluate_foundation_f2 import BENCH, raw_file
from backend.knowledge_store import KnowledgeStore
from backend.source_answer_dossier import build_dossier
from evaluate_foundation_f2_evidence import read_table_chain


_FIELD_LABEL = re.compile(r'(?:编号|区域|结论|日期)\s*[:：]\s*|读数(?:\([^)]*\))?\s*[:：]\s*')


def _norm_evidence(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = _FIELD_LABEL.sub(' ', text)
    return ' '.join(text.replace('|', ' ').split())


def _contains_evidence(text, literal):
    needle = _norm_evidence(literal)
    return bool(needle) and needle in _norm_evidence(text)


def _doc_text(document):
    return document.get('text') or '\n'.join(document.get('pages') or [])


def _first_loss(raw, parsed, bm25, reranked, reranked_topk, selected, dossier, table_chain,
                expected_abstain, wrong_source):
    if expected_abstain and not raw:
        return 'expected_no_evidence'
    if not raw:
        return 'gold_literal_not_in_source'
    if not parsed:
        return 'parser_chunk_loss'
    if not bm25:
        return 'wrong_source_binding' if wrong_source else 'bm25_candidate_miss'
    if not reranked:
        return 'rerank_loss'
    if not reranked_topk:
        if selected:
            return 'reranked_top_k_gap_recovered_by_coverage_selector'
        if dossier:
            return 'reranked_top_k_loss_recovered_by_dossier'
        if table_chain:
            return 'reranked_top_k_loss_recovered_by_table_chain'
        return 'reranked_top_k_loss'
    if not selected:
        if dossier:
            return 'top_k_loss_recovered_by_dossier'
        if table_chain:
            return 'top_k_loss_recovered_by_table_chain'
        return 'coverage_selector_or_top_k_loss'
    return 'survived_top_k'


def run(split: str, output: str):
    manifest = BENCH / 'manifest.json'
    manifest_obj = json.loads(manifest.read_text())
    assert all(sha(ROOT / name) == value for name, value in manifest_obj['files'].items())
    before = source_hashes()
    tasks = json.loads((BENCH / f'{split}.json').read_text())
    families = {task['source_family'] for task in tasks}
    docs = [doc for doc in json.loads((BENCH / 'documents.json').read_text())
            if doc['family'] in families]
    gold = json.loads((BENCH / 'scoring.json').read_text())
    records = []
    with tempfile.TemporaryDirectory(prefix='f3-f2-loss-') as temp:
        store = KnowledgeStore(temp)
        for doc in docs:
            store.ingest(raw_file(doc), document_id=doc['id'], title=doc['title'],
                         modality=doc['type'], filename=doc['id'] + '.' + doc['type'])
        chunks = store.records()
        by_chunk = {rec.document_id: rec for rec in chunks}
        parsed_by_source = {}
        for rec in chunks:
            parsed_by_source.setdefault(rec.metadata['document_id'], []).append(rec)

        # First produce all candidate and supplemental contexts without reading
        # the Gold file's per-task contents.
        predicted = {}
        for task in tasks:
            started = time.perf_counter()
            hits, audit = store.search(task['query'], top_k=task['top_k'], with_audit=True)
            search_ms = (time.perf_counter() - started) * 1000
            pages, dossier_audit, dossier_ms = [], None, 0.0
            if hits:
                t = time.perf_counter()
                try:
                    pages, dossier_audit = build_dossier(store, task['query'], hits)
                except (ValueError, OSError, RuntimeError) as exc:
                    dossier_audit = {'error': type(exc).__name__}
                dossier_ms = (time.perf_counter() - t) * 1000
            chain = None
            if split == 'dev' and task['kind'] in {'complete_table_enumeration', 'cross_page_header_unit'}:
                chain = read_table_chain(store, hits, task['query'], page_budget=6)
            predicted[task['id']] = {
                'hits': hits,
                'audit': audit,
                'search_ms': search_ms,
                'dossier_pages': pages,
                'dossier_audit': dossier_audit,
                'dossier_ms': dossier_ms,
                'table_chain': chain,
            }

        # Only after candidate/scope construction, compare against frozen Gold.
        stage_totals = {key: 0 for key in (
            'required_literals', 'raw_source', 'parsed_chunks', 'bm25_top20',
            'reranked_top20', 'reranked_topk', 'selected_topk', 'dossier_pages', 'table_chain_pages')}
        stage_totals['table_chain_required_literals'] = 0
        stage_totals['dossier_page_supplement_only'] = 0
        stage_totals['parsed_chunks_exact'] = 0
        stage_totals['selected_topk_exact'] = 0
        loss_counts = {}
        task_stage_recalls = []
        task_exact_recalls = []
        table_chain_task_recalls = []
        for task in tasks:
            expected = gold[task['id']]
            state = predicted[task['id']]
            raw_sources = {docid: next((d for d in docs if d['id'] == docid), None)
                           for docid in expected['required']}
            bm25_rows = state['audit'].get('candidates', [])
            bm25_ids = [row.get('chunk_id') for row in bm25_rows]
            rerank_rank = {str(row.get('chunk_id')): row.get('ranking_rank') for row in bm25_rows}
            ranked_topk_ids = {str(row.get('chunk_id')) for row in bm25_rows
                               if int(row.get('ranking_rank') or 10**9) <= task['top_k']}
            dossier_by_source = {}
            for page in state['dossier_pages']:
                dossier_by_source.setdefault(page['document_id'], []).append(page)
            chain = state['table_chain']
            chain_by_source = {}
            for page in (chain or {}).get('pages', []):
                chain_by_source.setdefault(page['document_id'], []).append(page)

            literals = []
            task_flags = {key: [] for key in (
                'raw_source', 'parsed_chunks', 'bm25_top20', 'reranked_top20',
                'reranked_topk', 'selected_topk', 'dossier_pages')}
            task_exact_flags = {'parsed_chunks': [], 'selected_topk': []}
            chain_task_flags = []
            for source_id, values in expected['required'].items():
                doc = raw_sources[source_id]
                raw_text = _doc_text(doc) if doc else ''
                source_chunks = parsed_by_source.get(source_id, [])
                source_bm25 = [cid for cid in bm25_ids if cid in by_chunk and
                               by_chunk[cid].metadata['document_id'] == source_id]
                source_selected = [hit.document_id for hit in state['hits'] if
                                   hit.metadata.get('document_id') == source_id]
                for literal in values:
                    raw_ok = literal in raw_text
                    parsed_exact_matches = [rec for rec in source_chunks if literal in rec.content]
                    parsed_matches = [rec for rec in source_chunks if _contains_evidence(rec.content, literal)]
                    parsed_ok = bool(parsed_matches)
                    candidate_matches = [cid for cid in source_bm25 if _contains_evidence(by_chunk[cid].content, literal)]
                    candidate_exact_matches = [cid for cid in source_bm25 if literal in by_chunk[cid].content]
                    bm25_ok = bool(candidate_matches)
                    reranked_ok = bm25_ok
                    reranked_topk_matches = [cid for cid in ranked_topk_ids
                        if cid in by_chunk and by_chunk[cid].metadata.get('document_id') == source_id
                        and _contains_evidence(by_chunk[cid].content, literal)]
                    reranked_topk_ok = bool(reranked_topk_matches)
                    selected_matches = [cid for cid in source_selected if _contains_evidence(by_chunk[cid].content, literal)]
                    selected_exact_matches = [cid for cid in source_selected if literal in by_chunk[cid].content]
                    selected_ok = bool(selected_matches)
                    dossier_matches = [p for p in dossier_by_source.get(source_id, [])
                                       if _contains_evidence(p.get('text', ''), literal)]
                    dossier_ok = bool(dossier_matches)
                    chain_matches = [p for p in chain_by_source.get(source_id, [])
                                     if _contains_evidence(p.get('text', ''), literal)]
                    chain_ok = bool(chain_matches)
                    dossier_context_ok = selected_ok or dossier_ok
                    table_chain_context_ok = dossier_context_ok or chain_ok
                    decoys = set(task.get('wrong_binding_sources', []))
                    wrong_source = any(
                        _contains_evidence(by_chunk[cid].content, literal) and
                        by_chunk[cid].metadata.get('document_id') in decoys
                        for cid in bm25_ids if cid in by_chunk)
                    loss = _first_loss(raw_ok, parsed_ok, bm25_ok, reranked_ok,
                                       reranked_topk_ok, selected_ok, dossier_ok, chain_ok,
                                       expected['expected_abstain'], wrong_source)
                    item = {
                        'source_document_id': source_id,
                        'literal': literal,
                        'raw_source_present': raw_ok,
                        'parsed_chunk_ids': [r.document_id for r in parsed_matches],
                        'parsed_present': parsed_ok,
                        'parsed_exact_chunk_ids': [r.document_id for r in parsed_exact_matches],
                        'parsed_exact_present': bool(parsed_exact_matches),
                        'bm25_top20_chunk_ids': candidate_matches,
                        'bm25_present': bm25_ok,
                        'bm25_exact_chunk_ids': candidate_exact_matches,
                        'bm25_exact_present': bool(candidate_exact_matches),
                        'reranked_top20_ranks': [rerank_rank.get(cid) for cid in candidate_matches],
                        'reranked_present': reranked_ok,
                        'reranked_topk_chunk_ids': reranked_topk_matches,
                        'reranked_topk_present': reranked_topk_ok,
                        'selected_topk_chunk_ids': selected_matches,
                        'selected_present': selected_ok,
                        'selected_topk_exact_chunk_ids': selected_exact_matches,
                        'selected_topk_exact_present': bool(selected_exact_matches),
                        'dossier_page_numbers': [p.get('page_no') for p in dossier_matches],
                        'dossier_page_present': dossier_ok,
                        'dossier_present': dossier_context_ok,
                        'dossier_added_after_topk': dossier_ok and not selected_ok,
                        'table_chain_page_numbers': [p.get('page_no') for p in chain_matches],
                        'table_chain_present': table_chain_context_ok,
                        'first_loss': loss,
                        'recovered_by_later_scope': bool((dossier_ok or chain_ok) and not selected_ok),
                    }
                    literals.append(item)
                    stage_totals['required_literals'] += 1
                    stage_totals['parsed_chunks_exact'] += int(bool(parsed_exact_matches))
                    stage_totals['selected_topk_exact'] += int(bool(selected_exact_matches))
                    task_exact_flags['parsed_chunks'].append(bool(parsed_exact_matches))
                    task_exact_flags['selected_topk'].append(bool(selected_exact_matches))
                    for key, flag in (
                        ('raw_source', raw_ok), ('parsed_chunks', parsed_ok),
                        ('bm25_top20', bm25_ok), ('reranked_top20', reranked_ok),
                        ('reranked_topk', reranked_topk_ok),
                        ('selected_topk', selected_ok), ('dossier_pages', dossier_context_ok)):
                        stage_totals[key] += int(flag)
                        task_flags[key].append(bool(flag))
                    stage_totals['dossier_page_supplement_only'] += int(dossier_ok and not selected_ok)
                    if state['table_chain'] is not None:
                        stage_totals['table_chain_pages'] += int(table_chain_context_ok)
                        stage_totals['table_chain_required_literals'] += 1
                        chain_task_flags.append(bool(table_chain_context_ok))
            losses = {}
            for item in literals:
                losses[item['first_loss']] = losses.get(item['first_loss'], 0) + 1
                loss_counts[item['first_loss']] = loss_counts.get(item['first_loss'], 0) + 1
            if literals:
                task_stage_recalls.append({key: sum(flags) / len(flags)
                                           for key, flags in task_flags.items()})
                task_exact_recalls.append({key: sum(flags) / len(flags)
                                           for key, flags in task_exact_flags.items()})
                if state['table_chain'] is not None:
                    table_chain_task_recalls.append({**{key: sum(flags) / len(flags)
                        for key, flags in task_flags.items()},
                        'table_chain_pages': sum(chain_task_flags) / len(chain_task_flags)})
            records.append({
                'id': task['id'], 'family': task['source_family'], 'kind': task['kind'],
                'query': task['query'], 'expected_abstain': expected['expected_abstain'],
                'required_evidence': literals,
                'retrieved_sources': list(dict.fromkeys(
                    h.metadata.get('document_id') for h in state['hits'])),
                'wrong_binding_sources': sorted(set(task.get('wrong_binding_sources', [])) &
                    {h.metadata.get('document_id') for h in state['hits']}),
                'candidate_pool_size': state['audit'].get('candidate_count', 0),
                'candidate_pool': state['audit'].get('candidates', []),
                'selected_topk_chunk_ids': [h.document_id for h in state['hits']],
                'dossier': {
                    'included_pages': [{'document_id': p['document_id'], 'page_no': p['page_no'],
                                        'source_sha256': p['source_sha256']} for p in state['dossier_pages']],
                    'audit': state['dossier_audit'],
                },
                'table_chain': ({'status': state['table_chain'].get('status'),
                    'reason': state['table_chain'].get('reason'),
                    'included_pages': [{'document_id': p['document_id'], 'page_no': p['page_no'],
                                        'source_sha256': p['source_sha256']}
                                       for p in state['table_chain'].get('pages', [])],
                    'scope': state['table_chain'].get('scope')} if state['table_chain'] else 'not_run'),
                'answerer_visible_context': 'not_run; F2 scorer does not execute answerer',
                'search_ms': round(state['search_ms'], 3),
                'dossier_ms': round(state['dossier_ms'], 3),
                'literal_first_loss_counts': losses,
                'task_stage_recall': ({key: sum(flags) / len(flags)
                    for key, flags in task_flags.items()} if literals else None),
            })

    for key in ('raw_source', 'parsed_chunks', 'bm25_top20', 'reranked_top20', 'reranked_topk', 'selected_topk', 'dossier_pages'):
        stage_totals[key + '_recall_micro'] = (stage_totals[key] / stage_totals['required_literals']
                                         if stage_totals['required_literals'] else None)
    stage_totals['table_chain_pages_recall_micro_on_eligible_tasks'] = (
        stage_totals['table_chain_pages'] / stage_totals['table_chain_required_literals']
        if stage_totals['table_chain_required_literals'] else None)
    out = {
        'schema': 'f3-f2-stage-loss-diagnostic-v1',
        'split': split,
        'source_split': 'family-disjoint frozen F2; no new query or source added',
        'input_manifest_sha256': sha(manifest),
        'scorer_sha256': sha(__file__),
        'source_hashes_before': before,
        'source_hashes_after': source_hashes(),
        'task_count': len(records),
        'required_literal_stage_counts': stage_totals,
        'answerable_task_stage_macro_recall': {
            key: statistics.mean(row[key] for row in task_stage_recalls)
            for key in task_stage_recalls[0]
        } if task_stage_recalls else {},
        'answerable_task_exact_literal_macro_recall': {
            key: statistics.mean(row[key] for row in task_exact_recalls)
            for key in task_exact_recalls[0]
        } if task_exact_recalls else {},
        'answerable_tasks_complete_by_stage': {
            key: sum(row[key] == 1.0 for row in task_stage_recalls)
            for key in task_stage_recalls[0]
        } if task_stage_recalls else {},
        'table_chain_task_stage_macro_recall': {
            key: statistics.mean(row[key] for row in table_chain_task_recalls)
            for key in table_chain_task_recalls[0]
        } if table_chain_task_recalls else {},
        'table_chain_tasks_complete_by_stage': {
            key: sum(row[key] == 1.0 for row in table_chain_task_recalls)
            for key in table_chain_task_recalls[0]
        } if table_chain_task_recalls else {},
        'first_loss_counts': loss_counts,
        'wrong_source_binding_tasks': sum(bool(r['wrong_binding_sources']) for r in records),
        'unsupported_retrieval_contamination_tasks': sum(
            bool(r['expected_abstain'] and r['retrieved_sources']) for r in records),
        'query_p50_ms': statistics.median(r['search_ms'] for r in records),
        'answer_metrics': 'not_run',
        'gold_policy': 'gold literals compared only after retrieval and supplemental scope construction',
        'records': records,
    }
    baseline_path = ROOT / f'docs/foundation/runs/f2-A-{split}-before.json'
    baseline = json.loads(baseline_path.read_text())
    selected_exact_macro = out['answerable_task_exact_literal_macro_recall'].get('selected_topk')
    frozen_macro = baseline['metrics']['evidence_recall_at_k']
    if abs(selected_exact_macro - frozen_macro) > 1e-9:
        raise AssertionError(f'exact Top-K scorer parity failed: {selected_exact_macro} != {frozen_macro}')
    out['frozen_baseline'] = {
        'path': str(baseline_path.relative_to(ROOT)),
        'sha256': sha(baseline_path),
        'evidence_recall_at_k': frozen_macro,
        'diagnostic_exact_topk_macro': selected_exact_macro,
        'scorer_parity': True,
    }
    assert before == source_hashes()
    dump(output, out)
    print(json.dumps({k: out[k] for k in ('split', 'task_count', 'required_literal_stage_counts',
                                          'answerable_task_stage_macro_recall',
                                          'answerable_task_exact_literal_macro_recall',
                                          'answerable_tasks_complete_by_stage',
                                          'table_chain_task_stage_macro_recall',
                                          'table_chain_tasks_complete_by_stage',
                                          'first_loss_counts', 'wrong_source_binding_tasks',
                                          'unsupported_retrieval_contamination_tasks')}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--split', choices=['dev', 'retained'], required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.split, args.output)
