"""Bridge source-verified SQL entities into document retrieval, before planning."""
from copy import deepcopy
import re

from .ranked_result_followup import VerifiedQueryResultFollowupAgent
from .unified_routing import DOCUMENT_INTENT


def sql_entity_document_scope(question, history, engine, conversations):
    """Resolve a literal entity pointer; never infer causes from sales figures."""
    if not history or not DOCUMENT_INTENT.search(question):
        return None
    if re.search(r'计算|预测|目标|增长率|公式|比较|对比|超过|核对|是多少', question):
        return None  # Arithmetic and source comparisons require a full DAG.
    pointer = re.match(r'^\s*(?:查一下|查询|查|那么|那)?(它|该地区|这个地区|该客户|这个客户|该产品|这个产品|第一名|排名第一的地区)(?:的)?', question)
    if pointer is None:
        return None
    previous = history[-1]
    if (previous.state or {}).get('route') != 'sql':
        return None  # Document pronouns belong to the document dialogue resolver.
    snapshot = VerifiedQueryResultFollowupAgent(engine, conversations)._snapshot(previous)
    if snapshot is None:
        return {'reason':'sql_entity_reference_unverified',
                'message':'当前指代没有可核验的查询结果，请写出具体对象和需要查询的资料内容。'}
    dimensions = snapshot['plan'].get('dimensions', [])
    labels = snapshot['plan'].get('dimension_labels', {})
    dimension_columns = [labels.get(column,column) for column in dimensions]
    entity = (previous.state or {}).get('selected_result_entity')
    rows = snapshot['rows']
    if pointer[1] in {'第一名','排名第一的地区'}:
        ordinal=VerifiedQueryResultFollowupAgent.ordinal_entity('第一名',snapshot)
        rows = [rows[ordinal[0]]] if ordinal else [row for row in rows if row.get('排名') == 1]
        entity = None
    if entity is None:
        if len(rows) != 1:
            return {'reason':'sql_entity_reference_ambiguous',
                    'message':'查询结果中有多个对象或并列第一，请明确要查哪个对象的资料。'}
        values = list(dict.fromkeys(rows[0].get(column) for column in dimension_columns
                    if isinstance(rows[0].get(column),str)))
        if len(values) != 1:
            return {'reason':'sql_entity_reference_ambiguous',
                    'message':'结果包含多个分组维度，请写出具体对象。'}
        entity = values[0]
    tail = question[pointer.end():].strip(' ，,。？?')
    if not tail:
        return None
    scope = entity + ('的' if not tail.startswith('在') else '') + tail
    named=re.findall(r'《([^》]+)》',tail)
    # The caller binds the literal title through the knowledge catalogue.
    return {'question':scope, 'entity':entity, 'source_question':snapshot['question'],
            'query_hash':snapshot.get('query_hash'),
            'source_revision':snapshot['source_revision'],
            'verification':'verified_execution_snapshot_unique_entity_reference',
            'sql_result':deepcopy(next(row for row in rows if entity in row.values())),
            'source_titles':named,
            'sql_bridge_context':{'question':previous.question,'effective_question':previous.effective_question,
                'state':deepcopy(previous.state)}}


def bind_bridge_document(bridge,knowledge):
    titles=bridge.get('source_titles',[])
    if not titles:return None
    documents=[d for d in knowledge.list_documents() if d['title'] in titles]
    if len(titles)!=1 or len(documents)!=1:
        return {'reason':'sql_document_title_not_unique','message':'资料名称不能唯一定位，请明确要查询的文档。'}
    bridge['document_id']=documents[0]['document_id']
    document=documents[0]
    if document['modality'] not in {'txt','md'}:return None
    # Literal absence is checked against the entire verified text original,
    # never a truncated preview or a failed top-k retrieval.
    raw=knowledge.verify_source(document['document_id'],expected_sha256=document['sha256']).read_bytes()
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:return None
    from .nl2sql.schema import normalize_text
    entity=bridge['entity']
    if normalize_text(entity) in normalize_text(text):return None
    hits=knowledge.search(document['title'],top_k=1,document_id=document['document_id'])
    if not hits:return None
    return {'status':'ok','question':bridge['question'],
        'answer':f'《{document["title"]}》的完整原文中没有找到“{entity}”的直接记载，无法确认该对象的相关方法。',
        'citations':[hit.to_dict() for hit in hits], 'claims':[],
        'answer_mode':'verified_source_literal_absence',
        'source_absence_audit':{'document_id':document['document_id'],'source_sha256':document['sha256'],
            'entity':entity,'scope':'entire_utf8_text_original','semantic_absence_proven':False},
        'trace':[{'stage':'source_entity_presence','status':'literal_entity_not_found','model_called':False}]}


def rebound_rank_document_request(question,history,engine,knowledge):
    """Rebind an explicit ranking metric and the same pinned document."""
    if not history:return None
    match=re.fullmatch(r'把排名指标改成(.+?)[，,]找(.+?)第一的(.+?)[，,]再查它在同一(?:经验)?文档中的(.+?)[。？?]*',question)
    if not match or match[1]!=match[2]:return None
    state=history[-1].state or {}
    from .conversation_comparison import unseal,ComparisonError
    try:
        saved=unseal(state.get('sql_bridge_context'))
        context=unseal(state.get('document_context'))
        if context.get('question')!=history[-1].effective_question or len(context.get('documents',{}))!=1:return None
        identifier,digest=next(iter(context['documents'].items()))
        knowledge.verify_source(identifier,expected_sha256=digest)
        from .session import ConversationTurn
        turn=ConversationTurn(saved['question'],saved['effective_question'],history[-1].created_at,saved['state'])
        from .executed_scope_edit import ExecutedScopeEditAgent
        edit=ExecutedScopeEditAgent(engine).run('指标改成'+match[1],[turn])
        if edit is None or not edit.verified:return None
        labels=engine.analyze_slots(match[3],preferred_tables=(edit.plan.table,))['dimensions']
        field={(link.table,link.column) for link in labels
            if link.column in edit.plan.dimensions and link.table==edit.plan.dimension_tables.get(link.column,edit.plan.table)}
        if len(field)!=1 or len(edit.plan.dimensions)!=1:return None
        table,column=next(iter(field))
        if column!=edit.plan.dimensions[0] or table!=edit.plan.dimension_tables.get(column,edit.plan.table):return None
        sql_scope=re.sub(r'[，,]','',edit.scope.rstrip('。？?'))+'排名第一'
        plan=engine.extract_required_intent(sql_scope)
        if plan.clarification or plan.coverage.get('unresolved') or plan.top_n!=1:return None
        title=knowledge.document(identifier)['title'];target=f'《{title}》中的{match[4]}'
        scope=f'数据库{sql_scope}，再检索该{match[3]}{target}'
        return {'scope':scope,'tasks':[
            {'id':'ranked','tool':'sql','args':{'question':sql_scope}},
            {'id':'methods','tool':'search','args':{'query':[
                {'ref':'ranked','path':['dimension_values',table,column,0]},target], 'document_id':identifier}}],
            'source_question':saved['effective_question']}
    except (ComparisonError,KeyError,TypeError,ValueError,OSError):return None


def verified_evidence_review(question,history,engine,knowledge):
    if (not history or not re.search(r'因果|造成|导致',question)
            or not re.search(r'这些|上述|刚才',question)
            or re.search(r'重新查询|排除|不含|仅查|大于|小于|\d{4}',question)):
        return None
    from .conversation_comparison import unseal,ComparisonError
    try:
        turn=history[-1];saved=unseal((turn.state or {}).get('fusion_result_snapshot'))
        if saved['question']!=turn.effective_question or saved['source_revision']!=engine.current_source_revision():return None
        for identifier,digest in saved['documents'].items():knowledge.verify_source(identifier,expected_sha256=digest)
        if not saved['sql_facts'] or not saved['citations']:return None
        columns={column for fact in saved['sql_facts'] for column in fact['columns']}
        if any(m.matched_alias not in columns for m in engine.analyze_slots(question)['metrics']):return None
        rows=[row for fact in saved['sql_facts'] for row in fact['rows']]
        facts='；'.join('、'.join(f'{key}：{value}' for key,value in row.items()) for row in rows)
        entities={value for row in rows for value in row.values() if isinstance(value,str)}
        citations=[hit for hit in saved['citations'] if any(entity in hit.get('snippet','') for entity in entities)
            or not engine.analyze_slots(hit.get('snippet',''))['values']]
        excerpts='\n'.join(f'[{i}] {hit.get("snippet","")}' for i,hit in enumerate(citations,1))
        answer=('数据库确认的排名事实：'+facts+'。\n\n文档提供的方法记录：\n'+excerpts+
            '\n\n因果边界：仅凭这些排名结果和做法描述，不能证明做法造成了领先；因果结论还需要对照、实验或其他可核验的识别证据。')
        return {'status':'ok','route':'fusion','question':question,'effective_question':turn.effective_question,
            'context_turns':len(history),'planner_source':'VerifiedEvidenceReview',
            'context_resolution':{'mode':'server_verified_existing_evidence_review','source_question':turn.effective_question,'executed':False},
            'state':dict(turn.state), 'result':{'status':'ok','answer':answer,'citations':citations,
                'results':{f'verified_sql_{i}':fact for i,fact in enumerate(saved['sql_facts'])},'answer_mode':'verified_facts_with_causal_inference_boundary'},
            'trace':[{'stage':'existing_evidence_review','status':'verified','executed':False}]}
    except (ComparisonError,KeyError,TypeError,ValueError,OSError):return None
