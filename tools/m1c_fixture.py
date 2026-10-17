"""Versioned synthetic competition assets and developer seed graphs, no target Gold."""
from datetime import date,datetime
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
from openpyxl import Workbook
from evaluate_memory_m1b1 import ROOT,digest
from evaluate_context_semantics_20261009 import build_fixtures
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.nl2sql.engine import Nl2SqlEngine
from backend.knowledge_store import KnowledgeStore

BENCH=ROOT/'benchmarks/task_experience_m1c_20261010'
TITLES={'policy':'指标与计划','alt_policy':'年度预算公式','methods':'区域团队方法','targets':'区域计划','revised':'修订计划','next_targets':'下一期计划'}

def setup(root):
    db,aliases=build_fixtures(root,initialize_rich_demo_data)['mixed']
    engine=Nl2SqlEngine(db,aliases_path=aliases,reference_date=date(2026,10,9),metric_catalog_path=root/'no-catalog.json')
    k=KnowledgeStore(root/'knowledge');source=json.loads((BENCH/'sources.json').read_text())
    for did,title in TITLES.items():
        raw=source[did]
        if isinstance(raw,str):k.ingest(raw.encode(),document_id=did,title=title,modality='txt',filename=did+'.txt')
        else:
            wb=Workbook();wb.properties.created=datetime(2026,10,10);wb.properties.modified=datetime(2026,10,10)
            for row in raw:wb.active.append(row)
            for row in wb.active.iter_rows(min_row=2):row[2].number_format='0.0%'
            buffer=BytesIO();wb.save(buffer);stable=BytesIO()
            with ZipFile(buffer) as src,ZipFile(stable,'w',ZIP_DEFLATED) as dest:
                for name in src.namelist():
                    z=ZipInfo(name,(2026,10,10,0,0,0));z.compress_type=ZIP_DEFLATED
                    dest.writestr(z,src.read(name))
            k.ingest(stable.getvalue(),document_id=did,title=title,modality='xlsx',filename=did+'.xlsx')
    return engine,k,aliases

def seed_cases():
    from diagnose_memory_m1c import cases
    a,b=cases()[:2]
    # Graphs are independently constructed/replayed development seeds, not a plan generator.
    ratio={'question':'2025年华东客单价，计算公式用文档，销售额和订单数从数据库取值。','tasks':[
        {'id':'f','tool':'document_formula','args':{'document_id':'policy','label':'客单价'}},
        {'id':'s','tool':'sql','args':{'question':'2025年华东销售额和订单数'}},
        {'id':'c','tool':'calculate','args':{'formula':{'ref':'f','path':[]},'parameters':{'销售额':{'ref':'s','path':['rows',0,'销售额']},'订单数':{'ref':'s','path':['rows',0,'订单数']}}}}]}
    return [a,b,ratio]
