"""Compile explicitly scoped formula + document cell + SQL requests.

No answer values or document IDs are built in. This bounded grammar requires
two unique named originals, complete baseline coverage, a literal formula and
one uniquely selected document cell. General requests retain model planning.
"""
import re

from .dependency_agent import DependencyAgent
from .document_analysis import DocumentAnalyzer
from .excel_layout import query_literal
from .formula_parameter_binding import _parameter_candidates


def plan_explicit_formula_request(question,engine,knowledge):
    match=re.fullmatch(
        r'(?:先查|先查询|先统计)(.+?)(?:作|作为)(?:预测)?基准[，,](?:再)?结合《([^》]+)》的公式和《([^》]+)》中(.+?)[，,](?:算出|计算)(.+?)(?:[，,](并分别标明PDF、Excel和数据库来源))?[。]?',
        question.strip())
    if not match:return None
    baseline,formula_title,cell_title,selector,target,_=match.groups()
    try:
        docs=knowledge.list_documents()
        resolve=lambda title:[d for d in docs if d['title']==title]
        formula_docs,cell_docs=resolve(formula_title),resolve(cell_title)
        if len(formula_docs)!=1 or len(cell_docs)!=1:return None
        formula_doc=knowledge.document(formula_docs[0]['document_id'])
        cell_doc=knowledge.document(cell_docs[0]['document_id'])
        if cell_doc['modality']!='xlsx':return None
        for document in (formula_doc,cell_doc):
            knowledge.verify_source(document['document_id'],expected_sha256=document['sha256'])
        labels={f.label for c in formula_doc['chunks'] for f in DocumentAnalyzer().analyze(c['text']).formulas
                if f.status!='rejected' and f.label in target}
        if len(labels)!=1:return None
        label=next(iter(labels))
        formula=DependencyAgent(engine,knowledge).execute('document_formula',
            {'document_id':formula_doc['document_id'],'label':label},{},{})
        plan=engine.extract_required_intent(baseline)
        if (plan.clarification or plan.coverage.get('unresolved') or not plan.coverage
                or plan.dimensions or plan.join_tables or plan.comparison_mode!='none'
                or plan.derived_metrics or len(plan.metrics)>1):return None
        physical=(plan.metric_table or plan.table,plan.metric_column,plan.metric_function)
        if plan.metrics:
            metric=plan.metrics[0];physical=(metric.table,metric.column,metric.function)
        with engine._connect() as connection:tables,_,_=engine._snapshot_for(connection)
        sql_names=[name for name in formula['parameters'] if
            _parameter_candidates(engine,name,tables,{physical[0]})=={physical}]
        if len(sql_names)!=1 or len(formula['parameters'])!=2:return None
        sql_name=sql_names[0];cell_name=next(n for n in formula['parameters'] if n!=sql_name)
        if cell_name not in selector:return None
        selections=[]
        for chunk in cell_doc['chunks']:
            cells=dict(zip(chunk['metadata'].get('headers',[]),chunk['metadata'].get('values',[])))
            if cell_name not in cells:continue
            where={}
            rest=selector.replace(cell_name,'')
            for column,cell in cells.items():
                if column==cell_name:continue
                value=query_literal(cell)
                if isinstance(value,str) and len(value)>1 and value in rest:
                    where[column]=value;rest=rest.replace(value,'')
                elif type(value) is int and re.search(r'(?<!\d)'+str(value)+r'(?!\d)',rest):
                    where[column]=value;rest=re.sub(r'(?<!\d)'+str(value)+r'(?!\d)','',rest)
            if where and not re.sub(r'年|地区|的|[\s]','',rest):selections.append(where)
        unique={repr(sorted(s.items())):s for s in selections}
        if len(unique)!=1:return None
        where=next(iter(unique.values()))
        # Every baseline entity must appear in the selected row, and all
        # target text must be explained by the formula/selector literals.
        entities={str(v.value) for v in engine.analyze_slots(baseline)['values']}
        if not entities or not entities<={str(v) for v in where.values()}:return None
        remainder=target.replace(label,'')
        for value in where.values():remainder=remainder.replace(str(value),'')
        if re.sub(r'年|地区|的|[。\s]','',remainder):return None
        metric_label=plan.metrics[0].label if plan.metrics else plan.metric_label
        tasks=[{'id':'formula','tool':'document_formula','args':{'document_id':formula_doc['document_id'],'label':label}},
            {'id':'baseline','tool':'sql','args':{'question':baseline}},
            {'id':'parameter','tool':'document_cell','args':{'document_id':cell_doc['document_id'],'where':where,'column':cell_name}},
            {'id':'target','tool':'calculate','args':{'formula':{'ref':'formula','path':[]},'parameters':{
                sql_name:{'ref':'baseline','path':['rows',0,metric_label]},cell_name:{'ref':'parameter','path':[]}}}}]
        # Structural compilation confers no execution authority: the regular
        # DependencyAgent binds the entire original request again at runtime.
        return tasks
    except (ValueError,KeyError,TypeError,OSError):
        return None
