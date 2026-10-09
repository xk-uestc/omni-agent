"""Source-pinned parameter operations over a verified fusion baseline."""
import re
from copy import deepcopy
from .conversation_comparison import unseal,ComparisonError
from .fusion_constraints import SourceConstraintError


def _scenario_graph(origin, document, scenario, knowledge, expected_percent=None, include_original=False):
    """Edit one parameter selector in a sealed, successful calculation DAG.

    This is deliberately limited to a single SQL baseline, a single pinned
    formula and one document parameter. Values still come from fresh tools.
    """
    tasks=deepcopy(origin['tasks'])
    calculations=[t for t in tasks if t['tool']=='calculate']
    sqls=[t for t in tasks if t['tool']=='sql']
    formulas=[t for t in tasks if t['tool']=='document_formula']
    if len(calculations)!=1 or len(sqls)!=1 or len(formulas)!=1:return None
    bindings=origin.get('source_bindings',[])
    if len(bindings)!=1 or bindings[0].get('task_id')!=sqls[0]['id']:return None
    sqls[0]['args']['question']=bindings[0]['text']
    calculation=calculations[0]
    by_id={t['id']:t for t in tasks}
    parameters=calculation['args']['parameters']
    document_parameters=[(name,ref) for name,ref in parameters.items()
        if isinstance(ref,dict) and by_id.get(ref.get('ref'),{}).get('tool')=='document_cell']
    if len(document_parameters)!=1 or len(parameters)!=2:return None
    name,reference=document_parameters[0]
    label=scenario+name
    # Extract from every original chunk first, so ambiguity/conflicts cannot
    # be hidden by top-k retrieval. The runtime repeats extraction and hashes.
    from .evidence_fact import extract_search_fact
    evidence={'hits':[{'snippet':c['text'],
        'source_uri':f'/api/v1/knowledge/documents/{document["document_id"]}/original',
        'metadata':{'document_id':document['document_id'],'chunk_id':c['chunk_id'],
            'source_sha256':document['sha256'],'source_locator':c['source_locator']}}
        for c in document['chunks']]}
    try:fact=extract_search_fact(knowledge,evidence,scope=label,label=label,unit='%')
    except ValueError:raise SourceConstraintError('fusion_scenario_source_unverified')
    if expected_percent is not None and float(fact['value'])!=float(expected_percent):
        raise SourceConstraintError('fusion_scenario_value_mismatch')
    search_id='scenario_source';fact_id='scenario_rate';calc_id='scenario_target'
    if {search_id,fact_id,calc_id,'scenario_compare'} & set(by_id):return None
    selected=[t for t in tasks if t['tool'] in {'sql','document_formula'}]
    if include_original:selected=tasks
    selected.extend([
        {'id':search_id,'tool':'search','args':{'query':label,'document_id':document['document_id']}},
        {'id':fact_id,'tool':'search_fact','args':{'evidence':{'ref':search_id,'path':[]},
            'scope':label,'label':label,'unit':'%'}}])
    changed=deepcopy(calculation);changed['id']=calc_id
    changed['args']['parameters'][name]={'ref':fact_id,'path':[]}
    selected.append(changed)
    if include_original:
        selected.append({'id':'scenario_compare','tool':'compare','args':{
            'left':{'ref':calc_id,'path':[]},'right':{'ref':calculation['id'],'path':[]},'operator':'gt'}})
    return selected

def resolve_parameter_operation(question,saved,engine,knowledge):
    # These grammars describe parameter source changes, comparisons and
    # summaries. They cannot introduce new database filters or measures.
    switch=re.fullmatch(r'(?:基准年份和地区|地区和基准年份|年份和地区)(?:不变|沿用)[，,](?:把增长率)?(?:改用|换用|切换到)(.+?)的(.+?)情景(?:(\d+(?:\.\d+)?)%)?[，,](?:重算|重新计算)(.+?)目标[。？?]*',question)
    difference=re.fullmatch(r'(.+?)情景目标比(.+?)口径(?:高|多)多少金额[？?]保持相同的((?:19|20)\d{2})年(.+?)基准[。？?]*',question)
    summary=re.fullmatch(r'总结(.+?)((?:19|20)\d{2})目标额[：:](.+?)口径各是多少[？?](?:请)?注明两者都是预测目标[，,]不能当作((?:19|20)\d{2})实际销售额[。？?]*',question)
    if not any((switch,difference,summary)):return None
    if not saved or saved.get('status')!='verified':raise SourceConstraintError('fusion_history_unverified')
    try:
        template=unseal(saved.get('task_template'))
        for key in ('scope_question','documents','source_bindings','document_tasks','formula_contracts'):
            if template.get(key)!=saved.get(key):raise SourceConstraintError('fusion_history_unverified')
        for identifier,digest in saved['documents'].items():knowledge.verify_source(identifier,expected_sha256=digest)
        bindings=saved['source_bindings']
        if len(bindings)!=1:return None
        binding=bindings[0]
        plan=engine.extract_required_intent(binding['text'])
        if plan.clarification or plan.coverage.get('unresolved') or plan.dimensions:return None
        previous=engine.analyze_slots(binding['text'])
        values={(v.table,v.column,v.value) for v in previous['values']}
        current=engine.analyze_slots(question,preferred_tables=(plan.table,))
        if any((v.table,v.column,v.value) not in values for v in current['values']):
            raise SourceConstraintError('fusion_followup_ambiguous')
        contracts=saved.get('formula_contracts',[])
        if len(contracts)!=1:return None
        formula=contracts[0];period=formula.get('temporal_constraints',{})
        target_year=period.get('target_year');base_year=period.get('base_year')
        if not target_year or not base_year:return None
        actual_years=set(int(y) for y in re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',question))
        if not actual_years<={base_year,target_year}:raise SourceConstraintError('fusion_followup_time_ambiguous')
        if difference and int(difference[3])!=base_year:raise SourceConstraintError('fusion_followup_time_ambiguous')
        if summary and (int(summary[2])!=target_year or int(summary[4])!=target_year):raise SourceConstraintError('fusion_followup_time_ambiguous')
        origin_record=saved.get('parameter_origin',saved['task_template'])
        origin=unseal(origin_record)
        binding_contract=lambda items:[{key:item.get(key) for key in ('task_id','text','role')}
            for item in items]
        if binding_contract(origin.get('source_bindings',[]))!=binding_contract(bindings):
            raise SourceConstraintError('fusion_history_unverified')
        documents={**origin['documents'],**saved['documents']}
        for identifier,digest in documents.items():knowledge.verify_source(identifier,expected_sha256=digest)
        docs=[knowledge.document(identifier) for identifier in documents]
        formula_doc=next(d for d in docs if d['document_id']==formula['document_id'])
        if switch:
            # A short source name must uniquely match a pinned title. A scenario
            # name and its percentage still require fresh source extraction.
            matches=[d for d in docs if switch[1] in d['title']]
            if len(matches)!=1 or matches[0]['document_id']!=formula_doc['document_id']:return None
        entity=''.join(str(v.value) for v in previous['values'])
        scope=(f'数据库{binding["text"]}作为预测基准，根据文档《{formula_doc["title"]}》的公式'
            f'计算{target_year}年{entity}{formula["label"]}，'
            +'；'.join(f'文档《{d["title"]}》是已指定来源' for d in docs)+'，文档追问：（'+question+'）')
        scenario=switch[2] if switch else difference[1] if difference else None
        if summary:
            named=re.findall(r'([\u4e00-\u9fff]{1,12})情景',summary[3])
            # The scenario name is read from the literal source labels, not
            # inferred from numbers. Match a suffix in the user's phrase.
            candidates={m.group(1) for c in formula_doc['chunks'] for m in
                re.finditer(r'([\u4e00-\u9fff]{1,12})目标增长率',c['text'])
                if any(token.endswith(m.group(1)) for token in named)}
            if len(candidates)!=1:return None
            scenario=next(iter(candidates))
        tasks=_scenario_graph(origin,formula_doc,scenario,knowledge,
            expected_percent=float(switch[3]) if switch and switch[3] else None,include_original=not bool(switch))
        if tasks is None:return None
        constraints={'parameter_operation':True,'documents':documents,
            'formula_tasks':[task for task in saved['document_tasks'] if task['tool']=='document_formula'],
            'required_documents':([formula_doc['document_id']] if switch else list(saved['documents'])),
            'parameter_source_id':formula_doc['document_id'] if switch else None,
            'resolved_tasks':tasks,'parameter_origin':origin_record}
        return scope,{'mode':'server_verified_fusion_parameter_operation','actual_question':question,
            'base_scope_question':saved['scope_question'],'scope_question':scope,
            'baseline_scope':binding['text'],'document_versions':saved['documents'],
            'verification':'sealed_executed_baseline_and_fresh_document_parameter_reads'},constraints
    except (ComparisonError,KeyError,TypeError,ValueError,OSError) as exc:
        if isinstance(exc,SourceConstraintError):raise
        raise SourceConstraintError('fusion_history_unverified') from exc
