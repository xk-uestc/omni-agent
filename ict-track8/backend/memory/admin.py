"""Local OS-owner CLI. No operator/session_id argument or remote admin endpoint."""
import argparse
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
from .core import MemoryCore, MemoryStore, TrustedScope
from .adapter import MemoryAdapter
from .formation import LocalReviewContext, MemoryFormation
from .extraction import digest
from ..knowledge_store import KnowledgeStore
from ..nl2sql.engine import Nl2SqlEngine


def load(path, memory_type='business_semantics'):
    authority=LocalReviewContext.from_config(path)
    c=json.loads(Path(path).read_text())
    if digest(c)!=authority.configuration_sha256:raise ValueError('configuration changed')
    engine=Nl2SqlEngine(c['database'],aliases_path=c.get('aliases'),reference_date=date.fromisoformat(c['reference_date']),metric_catalog_path=c.get('catalog'))
    adapter=MemoryAdapter(MemoryCore(MemoryStore(c['memory_db']),scope=TrustedScope(**c['scope']),enabled=True),engine,
        KnowledgeStore(c['knowledge_root']),database_source=c['database_source'],clock=(lambda:c['reference_time']) if c.get('reference_time') else None)
    if memory_type == 'task_experience':
        from .experience import ExperienceFormation
        return ExperienceFormation(adapter),authority
    return MemoryFormation(adapter),authority


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--type',choices=['business_semantics','task_experience'],default='business_semantics')
    p.add_argument('action',choices=['list','validate','review','revoke']);p.add_argument('--candidate');p.add_argument('--digest')
    p.add_argument('--decision',choices=['confirm','reject']);p.add_argument('--request-id');p.add_argument('--reason');p.add_argument('--memory-id');p.add_argument('--supersedes')
    a=p.parse_args()
    try:
        formation,context=load(a.config,a.type)
        if a.action=='list':result=formation.candidates()
        elif a.action=='validate':result=formation.validate(a.candidate,a.digest)
        elif a.action=='review':result=formation.review(a.candidate,a.digest,context=context,request_id=a.request_id,
            decision=a.decision,reason=a.reason or '',supersedes=a.supersedes)
        else:result=formation.revoke(a.memory_id,a.digest,context=context,request_id=a.request_id,reason=a.reason or '')
        print(json.dumps(result,ensure_ascii=False))
    except (ValueError,PermissionError,KeyError,OSError) as exc:
        print(json.dumps({'status':'rejected','reason':str(exc)},ensure_ascii=False));return 2
    return 0


if __name__=='__main__':raise SystemExit(main())
