"""No-model diagnosis: raw executor and original-question constraints are separate."""
import argparse
from copy import deepcopy
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import subprocess
from evaluate_memory_m1b1 import ROOT, documents, dump, digest, source_hashes
from evaluate_context_semantics_20261009 import build_fixtures
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.nl2sql.engine import Nl2SqlEngine
from backend.omni_agent import OmniAgent, _normalize_model_tasks
from backend.dependency_agent import DependencyAgent
from backend.session import ConversationStore
from backend.plan_requirements import requested_operations, completion_errors


def environment(root):
    source=json.loads((ROOT/'benchmarks/memory_sensitive_m1a_20261010/sources.json').read_text())
    db,aliases=build_fixtures(root,initialize_rich_demo_data)['mixed']
    knowledge=documents(root/'knowledge',source,'original')
    knowledge.ingest('华东冠军团队的方法：每日核验库存，按客户需求安排补货。\n华南冠军团队的方法：每周复核渠道订单。'.encode(),document_id='methods',title='区域团队方法',modality='txt',filename='methods.txt')
    from openpyxl import Workbook
    book=Workbook();book.active.append(['地区','年份','目标增长率']);book.active.append(['华东',2026,.18]);book.active.append(['华南',2026,.16])
    raw=BytesIO();book.save(raw)
    knowledge.ingest(raw.getvalue(),document_id='revised',title='修订计划',modality='xlsx',filename='revised.xlsx')
    engine=Nl2SqlEngine(db,aliases_path=aliases,reference_date=date(2026,10,9),metric_catalog_path=root/'no-catalog.json')
    return engine,knowledge


def cases():
    ref=lambda ident,path=[]:{'ref':ident,'path':path}
    formula=[{'id':'formula','tool':'document_formula','args':{'document_id':'policy','label':'目标销售额'}},
        {'id':'base','tool':'sql','args':{'question':'2025年华东销售额'}},
        {'id':'growth','tool':'document_cell','args':{'document_id':'targets','where':{'地区':'华东','年份':2026},'column':'目标增长率'}},
        {'id':'forecast','tool':'calculate','args':{'formula':ref('formula'),'parameters':{'基准销售额':ref('base',['rows',0,'销售额']),'目标增长率':ref('growth')}}}]
    return [
      {'workflow':'A','question':'根据《指标与计划》目标销售额公式和《区域计划》2026年华东目标增长率，以及数据库2025年华东销售额，计算2026年目标销售额。','tasks':formula},
      {'workflow':'B','question':'先从数据库查2025年销售额排名第一的地区，再根据该地区检索冠军团队的方法，保留来源。','tasks':[
        {'id':'winner','tool':'sql','args':{'question':'2025年销售额排名第一的地区'}},
        {'id':'method','tool':'search','args':{'query':[ref('winner',['rows',0,'地区']),'冠军团队的方法']}}]},
      {'workflow':'C','question':'从区域计划Excel中定位2026年目标增长率为0.12的地区，作为过滤条件查询数据库中该地区2025年的销售额合计。','tasks':[
        {'id':'region','tool':'document_cell','args':{'document_id':'targets','where':{'年份':2026,'目标增长率':.12},'column':'地区'}},
        {'id':'sales','tool':'sql','args':{'question':['2025年销售额合计，地区为',ref('region',['value'])]}}]},
      {'workflow':'D','question':'比较《区域计划》和《修订计划》中2026年华东目标增长率是否相同，并查询数据库2025年华东销售额。','tasks':[
        {'id':'old','tool':'document_cell','args':{'document_id':'targets','where':{'地区':'华东','年份':2026},'column':'目标增长率'}},
        {'id':'new','tool':'document_cell','args':{'document_id':'revised','where':{'地区':'华东','年份':2026},'column':'目标增长率'}},
        {'id':'comparison','tool':'compare','args':{'left':ref('old'),'right':ref('new'),'operator':'eq'}},
        {'id':'sales','tool':'sql','args':{'question':'2025年华东销售额'}}]}]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--label',default='m1c-diagnosis-20261010');args=parser.parse_args()
    runtime=ROOT/'runtime'/args.label;out=ROOT/'docs/memory_rl/runs'/args.label
    if runtime.exists() or out.exists():raise ValueError('preserve previous run')
    runtime.mkdir(parents=True);out.mkdir(parents=True);engine,knowledge=environment(runtime);before=digest(engine.database_path);records=[]
    for c in cases():
        agent=OmniAgent(engine,knowledge,ConversationStore());executor=DependencyAgent(engine,knowledge)
        basic=agent.basic_plan(c['question'],());response=agent.query(c['question'],session_id='independent')
        graph=deepcopy(c['tasks']);errors=completion_errors(requested_operations(c['question']),'fusion',graph)
        try:
            normalized,_=_normalize_model_tasks({'route':'fusion','effective_question':c['question'],'tasks_json':json.dumps(graph),'clarification':''},graph,engine,knowledge,c['question'])
            graph=json.loads(normalized['tasks_json']);protocol={'valid':True,'required_operation_errors':errors}
        except ValueError as exc:protocol={'valid':False,'reason':type(exc).__name__,'code':getattr(exc,'plan_rejection_code',None),'required_operation_errors':errors}
        for_original=executor.run(graph,original_question=c['question'])
        raw=executor.run(graph)
        record={**c,'basic_plan':basic,'agent_no_model':response,'protocol':protocol,'normalized_tasks':graph,'executor_with_original_question':for_original,'executor_without_user_constraints':raw}
        records.append(record)
        print(json.dumps({'workflow':c['workflow'],'agent':response['status'],'protocol':protocol,'full_executor':for_original['status'],'full_error':for_original.get('error_code'),'raw_executor':raw['status']},ensure_ascii=False),flush=True)
    dump(out/'diagnosis.json',{'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes':source_hashes(),'db_before':before,'db_after':digest(engine.database_path),'records':records,'model':{'status':'not_run','reason':'No model endpoint/name/call cap provided; local free model requested; approval pending details','calls':0},'scope':'developer legal graphs, no autonomous model or experience performance claim'})

if __name__=='__main__':main()
