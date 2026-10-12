"""Real public textbook formula + transparent SQL business mapping, no API.

The graph and SQL rows below are engineering inputs, NOT autonomous planning
or a publicly paired SQL/document benchmark. Source bytes are frozen originals.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--code-root',type=Path,default=ROOT)
    p.add_argument('--data-root',type=Path,default=ROOT/'runtime/real_document_upgrade/data')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--model-config',type=Path)
    p.add_argument('--ledger',type=Path)
    p.add_argument('--question',action='append')
    args=p.parse_args();sys.path.insert(0,str(args.code_root/'ict-track8'))
    from backend.knowledge_store import KnowledgeStore
    from backend.dependency_agent import DependencyAgent
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    manifest=json.loads((ROOT/'benchmarks/real_document_20261011/odb-manifest.json').read_text())
    source=next(d for d in manifest['documents'] if d['split']=='dev' and '61520788' in d['image_name'])
    raw=(args.data_root/source['pdf_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==source['pdf_sha256']
    with tempfile.TemporaryDirectory(prefix='omni-public-formula-') as tmp:
        root=Path(tmp);db=root/'materials.sqlite'
        with sqlite3.connect(db) as c:
            c.execute('CREATE TABLE 材料采购(记录ID INTEGER PRIMARY KEY, 材料原价 REAL)')
            c.execute('INSERT INTO 材料采购 VALUES(1,500)')
            c.execute('CREATE TABLE 保险参数(记录ID INTEGER PRIMARY KEY, 材料运输保险费率 REAL)')
            c.execute('INSERT INTO 保险参数 VALUES(1,0.02)')
        catalog={'version':'transparent_engineering_mapping_v1','metrics':[
            {'id':'price','table':'材料采购','column':'材料原价','function':'SUM','label':'材料原价',
             'query_alias':'材料原价','aliases':['材料原价'],'unit':'currency','currency':'CNY'},
            {'id':'rate','table':'保险参数','column':'材料运输保险费率','function':'SUM','label':'材料运输保险费率',
             'query_alias':'材料运输保险费率','aliases':['材料运输保险费率'],'unit':'ratio'}]}
        cat=root/'catalog.json';cat.write_text(json.dumps(catalog,ensure_ascii=False))
        engine=Nl2SqlEngine(db,metric_catalog_path=cat);store=KnowledgeStore(root/'knowledge')
        store.ingest(raw,document_id='cost-textbook',title='工程成本教材',modality='pdf',filename=Path(source['pdf_path']).name)
        tasks=[{'id':'f','tool':'document_formula','args':{'document_id':'cost-textbook','label':'运输保险费'}},
            {'id':'price','tool':'sql','args':{'question':'材料采购材料原价合计'}},
            {'id':'rate','tool':'sql','args':{'question':'保险参数材料运输保险费率合计'}},
            {'id':'cost','tool':'calculate','args':{'formula':{'ref':'f','path':[]},'parameters':{
                '材料原价':{'ref':'price','path':['aggregate_cells','材料采购','材料原价','SUM',0]},
                '材料运输保险费率':{'ref':'rate','path':['aggregate_cells','保险参数','材料运输保险费率','SUM',0]}}}}]
        question='根据文档《工程成本教材》的运输保险费公式，从数据库查询材料采购材料原价合计作为参数，从数据库查询保险参数材料运输保险费率合计作为参数，计算运输保险费。'
        explicit='根据文档《工程成本教材》公式计算运输保险费；从数据库查询材料采购材料原价合计；从数据库查询保险参数材料运输保险费率合计。'
        client=None
        if args.model_config:
            if args.ledger is None:p.error('real model requires this round shared ledger')
            from m1c_model_client import BudgetedPlanner
            client=BudgetedPlanner(args.model_config,args.ledger,max_calls=20)
            client.supports_visual_source_review=False
        runs=[]
        for q in args.question or ((question,explicit) if client else (None,question,explicit)):
            import time
            started=time.perf_counter();events=[]
            result=(OmniAgent(engine,store,ConversationStore(),client).query(q,reset_context=True,trace_callback=events.append)
                    if client else DependencyAgent(engine,store).run(tasks,original_question=q))
            runs.append({'question':q,'result':result,'events':events,'seconds':time.perf_counter()-started})
        expected=Decimal('500')*Decimal('0.02')
        report={'classification':('real_text_model_autonomous_if_planner_source_model_validated' if client else 'transparent_integration_manual_graph_not_autonomous'),
            'model_calls':client.calls if client else [],'original_source':source,'database_rows':{'材料采购':[[1,500]],'保险参数':[[1,0.02]]},
            'database_classification':'artificial_business_mapping_to_public_original',
            'metric_catalog':catalog,'runs':runs,'independent_decimal_result':str(expected)}
        if client is None:report['tasks']=tasks
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        for run in runs:
            r=run['result'];nested=r.get('result',r)
            print(r['status'],r.get('planner_source'),nested.get('clarification_code'),nested.get('answer'),flush=True)


if __name__=='__main__':main()
