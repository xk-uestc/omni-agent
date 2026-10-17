"""Independent legal-plan probe; no production task-ID branch or memory injection."""
import json
from pathlib import Path
from datetime import date
import subprocess
from evaluate_memory_m1b1 import ROOT,documents,dump,digest,source_hashes
from evaluate_context_semantics_20261009 import build_fixtures
from score_memory_sensitive_m1a import score
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.nl2sql.engine import Nl2SqlEngine
from backend.dependency_agent import DependencyAgent
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


def main():
    label='m1b2-cross-source-probe-20261010';runtime=ROOT/'runtime'/label;out=ROOT/'docs/memory_rl/runs'/label
    if runtime.exists() or out.exists():raise ValueError('preserve evidence: probe already exists')
    runtime.mkdir(parents=True);out.mkdir(parents=True)
    source=json.loads((ROOT/'benchmarks/memory_sensitive_m1a_20261010/sources.json').read_text())
    db,aliases=build_fixtures(runtime,initialize_rich_demo_data)['mixed'];before=digest(db)
    records=[]
    for region,base_year,variant,rate in [('华东',2025,'original',.12),('华南',2025,'targets_changed',.18),('华南',2024,'original',.10)]:
        knowledge=documents(runtime/(region+str(base_year)+variant),source,variant)
        engine=Nl2SqlEngine(db,aliases_path=aliases,reference_date=date(2026,10,9),metric_catalog_path=runtime/'no-catalog.json')
        question=f'根据《指标与计划》和《区域计划》，给出2026年{region}目标销售额，基准为{base_year}年{region}销售额'
        baseline=OmniAgent(engine,knowledge,ConversationStore()).query(question,session_id='fresh')
        graph=[{'id':'formula','tool':'document_formula','args':{'document_id':'policy','label':'目标销售额'}},
            {'id':'base','tool':'sql','args':{'question':f'{base_year}年{region}销售额'}},
            {'id':'growth','tool':'document_cell','args':{'document_id':'targets','where':{'地区':region,'年份':2026},'column':'目标增长率'}},
            {'id':'forecast','tool':'calculate','args':{'formula':{'ref':'formula','path':[]},'parameters':{
                '基准销售额':{'ref':'base','path':['rows',0,'销售额']},'目标增长率':{'ref':'growth','path':[]}}}}]
        executed=DependencyAgent(engine,knowledge).run(graph)
        versions={d['document_id']:d['sha256'] for d in knowledge.list_documents()}
        gold={'kind':'fusion','baseline_sql':f'SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>=\'{base_year}-01-01\' AND order_date<\'{base_year+1}-01-01\' AND region=\'{region}\'',
            'rate':rate,'region':region,'documents':['policy','targets']}
        passed,detail=score(gold,{'status':executed['status'],'result':executed},db,versions)
        records.append({'question':question,'variant':variant,'baseline':baseline,'independent_plan':graph,'executed':executed,'score':detail,'pass':passed,
            'proposed_future_memory':{'type':'task_experience_candidate','operations':['document_formula','sql','document_cell','calculate'],
                'parameters_from_current_request':['region','base_year','target_year'],
                'constraints':['current explicit authorized documents','formula parameters from verified refs','fresh SQL through safety executor','no cached answers'],
                'verification_state':'research_candidate_not_promoted'},
            'controller_research_record':{'state':{'source_versions':versions,'model_configured':False,'scope_valid':True},
                'legal_actions':['abstain_and_clarify','revalidate_and_propose_parameterized_experience'],
                'feedback':{'independent_success':passed,'answer_not_available_to_policy':True},'policy_trained':False}})
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes':source_hashes(),
        'database_sha256_before':before,'database_sha256_after':digest(db),'records':records,'model_calls':0,'tokens':0,
        'remote_model':{'status':'not_run','reason':'No authorized remote endpoint/budget configured'},
        'interpretation':'Manual independently constructed legal plans, not learned/planned by Omni and not task-experience memory performance.'}
    dump(out/'probe.json',report)
    print(json.dumps({'baseline_statuses':[r['baseline']['status'] for r in records],'legal_plan_passed':sum(r['pass'] for r in records),'total':len(records),'database_unchanged':before==digest(db)},ensure_ascii=False))


if __name__=='__main__':main()
