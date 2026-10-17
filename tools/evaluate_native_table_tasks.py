"""Public original PDFs -> production ingestion -> OmniAgent -> literal answers.

No model/API calls. Questions are development engineering probes, not official
OmniDocBench QA. Independent cell annotations are loaded ONLY after predictions
are written, and never inserted into KnowledgeStore or planner context.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--code-root', type=Path, default=ROOT)
    p.add_argument('--data-root', type=Path, default=ROOT/'runtime/real_document_upgrade/data')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--score', action='store_true')
    args = p.parse_args()
    sys.path.insert(0, str(args.code_root/'ict-track8'))
    from backend.knowledge_store import KnowledgeStore
    from backend.omni_agent import OmniAgent
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.nl2sql.seed import initialize_database
    from backend.session import ConversationStore
    spec = json.loads((ROOT/'benchmarks/native_table_tasks_20261012.json').read_text())
    frozen = json.loads((ROOT/'benchmarks/real_document_20261011/odb-manifest.json').read_text())
    sources = {d['image_name']:d for d in frozen['documents'] if d['split'] == 'dev'}
    report = {'model_calls':0, 'gpu':False, 'planner':'production_rules_without_model',
              'benchmark_claim':'development_engineering_tasks_not_official_QA', 'documents':[], 'predictions':[]}
    with tempfile.TemporaryDirectory(prefix='omni-native-task-') as tmp:
        store = KnowledgeStore(Path(tmp)/'knowledge')
        engine = Nl2SqlEngine(initialize_database(Path(tmp)/'database.sqlite'))
        agent = OmniAgent(engine, store, ConversationStore())
        for source in spec['documents']:
            frozen_source = sources[source['image_name']]
            path = args.data_root/frozen_source['pdf_path']
            raw = path.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == frozen_source['pdf_sha256']
            start = time.perf_counter()
            record = store.ingest(raw, document_id=source['id'], title=source['title'], modality='pdf', filename=path.name)
            report['documents'].append({'id':source['id'], 'sha256':record['sha256'],
                'ingest_seconds':time.perf_counter()-start, 'stats':record['stats'], 'warnings':record['warnings']})
        for task in spec['tasks']:
            start = time.perf_counter()
            result = agent.query(task['question'], reset_context=True)
            direct = store.answer(task['question'], document_id=task['document_id'])
            report['predictions'].append({'id':task['id'], 'question':task['question'],
                'seconds':time.perf_counter()-start, 'omni':result, 'explicit_source_answer':direct})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    # Immutable prediction file first; scoring gets a separate output artifact.
    if args.score:
        from html.parser import HTMLParser
        gold_path = args.data_root/'gold/OmniDocBench-v1_0.json'
        raw = gold_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == frozen['gold_sha256']
        gold = {p['page_info']['image_path']:p for p in json.loads(raw)}
        class Cells(HTMLParser):
            def __init__(self):
                super().__init__(); self.rows=[]; self.cell=None
            def handle_starttag(self, tag, attrs):
                if tag=='tr': self.rows.append([])
                if tag in {'td','th'}: self.cell=''
            def handle_data(self, text):
                if self.cell is not None: self.cell+=text
            def handle_endtag(self,tag):
                if tag in {'td','th'}: self.rows[-1].append(self.cell); self.cell=None
        norm=lambda x: ''.join(str(x).split())
        scored=[]
        by_source={s['id']:s for s in spec['documents']}
        for task,prediction in zip(spec['tasks'],report['predictions']):
            if task.get('expected_abstention'):
                matched=prediction['omni']['status'] != 'ok'
                expected=None
            else:
                page=gold[by_source[task['document_id']]['image_name']]
                values=[]
                for item in page['layout_dets']:
                    if item['category_type']!='table': continue
                    parser=Cells();parser.feed(item['html']); rows=parser.rows
                    headers=[norm(h) for h in rows[0]]
                    if norm(task['gold_column']) not in headers:continue
                    col=headers.index(norm(task['gold_column']))
                    for row in rows[1:]:
                        if any(norm(v)==norm(task['gold_row_label']) for v in row):
                            # Span anchors are absent from later annotation rows;
                            # select from the right so missing leading cells do
                            # not shift the literal final output column.
                            offset=len(headers)-len(row)
                            if 0<=col-offset<len(row):values.append(row[col-offset])
                assert len(values)==1, (task['id'],values)
                expected=values[0]
                matched=prediction['omni']['status']=='ok' and norm(prediction['omni']['result']['answer'])==norm(expected)
            scored.append({'id':task['id'],'expected':expected, 'passed':matched,
                           'status':prediction['omni']['status'], 'answer':prediction['omni']['result'].get('answer')})
        score={'source':'unchanged_omnidocbench_cell_annotation', 'gold_sha256':frozen['gold_sha256'],
               'normalizer':'remove_whitespace_only', 'tasks':scored,
               'passed':sum(t['passed'] for t in scored), 'total':len(scored)}
        args.output.with_suffix('.score.json').write_text(json.dumps(score,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps(score,ensure_ascii=False))


if __name__=='__main__':main()
