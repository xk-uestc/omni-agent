"""Offline diagnosis of authored followups, with actual source data (no LLM)."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.knowledge_store import KnowledgeStore
from backend.session import ConversationStore
from backend.omni_agent import OmniAgent

def main():
    agent = OmniAgent(Nl2SqlEngine(ROOT / 'ict-track8/data/demo_sales.sqlite'),
        KnowledgeStore(ROOT / 'runtime/knowledge'), ConversationStore())
    suite = json.loads((ROOT / 'benchmarks/authored_multiturn_20261008/questions.json').read_text(encoding='utf-8'))
    records = []
    for group in suite['groups'][:4]:
        for case in group['turns']:
            result = agent.query(case['question'], session_id='diagnose-' + group['id'])
            records.append(result)
            inner = result.get('result', {})
            print(json.dumps({'q': case['question'], 'status': result.get('status'),
                'route': result.get('route'), 'scope': result.get('effective_question'),
                'reason': result.get('context_resolution'), 'rows': inner.get('rows'),
                'answer': inner.get('answer'), 'clarification':inner.get('clarification_code')}, ensure_ascii=False), flush=True)
    (ROOT / 'runtime/generalization-rule-diagnosis.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
if __name__ == '__main__': main()
