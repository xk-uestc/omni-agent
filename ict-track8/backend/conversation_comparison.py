"""Server-owned result comparison, independent of model planning and SQL execution.

Snapshots describe bounded, complete displayed results. Digests detect inconsistent
server records; they are not signatures or a proof against a database administrator.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, asdict, replace
from decimal import Decimal, localcontext
import hashlib
import json
import math
import re
from .history_reference import ConversationReferenceAgent,HistoryReference

MAX_ROWS = 128
MAX_COLUMNS = 16


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()


def seal(payload):
    return {'payload':deepcopy(payload),'sha256':digest(payload)}


def unseal(record):
    if not isinstance(record,dict) or not isinstance(record.get('payload'),dict):
        raise ComparisonError('comparison_snapshot_unavailable')
    if record.get('sha256') != digest(record['payload']):
        raise ComparisonError('comparison_snapshot_invalid')
    return deepcopy(record['payload'])


class ComparisonError(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)


@dataclass(frozen=True)
class ComparisonRequest:
    sql_only: bool = False
    successful_only: bool = False
    reverse: bool = False
    month_alignment: bool = False
    reuse: bool = False
    direction_explicit: bool = False
    alignment_explicit: bool = False
    references: tuple[HistoryReference,...] = ()
    reference_error: str | None = None


def parse_request(question):
    text=question.strip().rstrip('。？?')
    reverse=False
    month=False
    suffix=re.search(r'[，,\s]+(?:以|用)(较早|较晚|前一次|后一次)(?:结果|查询)?(?:为|作)基准$',text)
    if suffix:
        reverse=suffix[1] in {'较晚','后一次'}
        text=text[:suffix.start()]
    if text.startswith('按月份对齐'):
        month=True
        text=re.sub(r'^按月份对齐[，,\s]*','',text)
    elif re.search(r'[，,\s]+按月份对齐$',text):
        month=True
        text=re.sub(r'[，,\s]+按月份对齐$','',text)
    if re.fullmatch(r'(?:请)?把基准(?:换成|改成)(较早|较晚)(?:结果|查询)?',text):
        return ComparisonRequest(reverse='较晚' in text,reuse=True,direction_explicit=True)
    if not text and month:
        return ComparisonRequest(month_alignment=True,reuse=True,alignment_explicit=True)
    numbered=re.fullmatch(r'(?:请|帮我)?(?:比较|对比)(?:一下)?倒数第'
        r'([0-9]{1,3}|[一二三四五六七八九十两]{1,3})(?:次|条)(?:SQL查询)?和倒数第'
        r'([0-9]{1,3}|[一二三四五六七八九十两]{1,3})(?:次|条)(成功的)?SQL查询'
        r'(?:的)?(?:差值|变化率|差多少|相差多少)?',text,re.I)
    quote=r'(?:“[^”]+”|「[^」]+」|"[^"]+")'
    quoted=re.fullmatch(r'(?:请|帮我)?(?:比较|对比)(?:一下)?(成功的)?SQL查询('+quote+')和('+quote+')'
        r'(?:的)?(?:差值|变化率|差多少|相差多少)?',text,re.I)
    references=()
    identified=re.fullmatch(r'(?:请|帮我)?(?:比较|对比)(?:一下)?SQL查询编号\s*(q_[a-f0-9]{32})和(?:SQL查询编号\s*)?(q_[a-f0-9]{32})',text,re.I)
    if identified:
        references=tuple(HistoryReference('explicit_id_sql','',turn_id=identified[i].lower()) for i in (1,2))
    elif numbered:
        references=tuple(HistoryReference('explicit_backward_sql','',bool(numbered[3]),
            ConversationReferenceAgent._number(numbered[i])) for i in (1,2))
    elif quoted:
        references=tuple(HistoryReference('explicit_quoted_sql','',bool(quoted[1]),question=quoted[i][1:-1]) for i in (2,3))
    if references:
        return ComparisonRequest(sql_only=True,reverse=reverse,month_alignment=month,
            direction_explicit=bool(suffix),alignment_explicit=month,references=references)
    if re.match(r'(?:请|帮我)?(?:比较|对比)(?:一下)?(?:倒数第|SQL查询编号|(?:成功的)?SQL查询[“「"])',text,re.I):
        return ComparisonRequest(sql_only=True,reference_error='comparison_reference_expression_unsupported')
    explicit=re.fullmatch(r'(?:请|帮我)?(?:比较|对比|计算)(?:一下)?(?:刚才|最近|前面|上面)(?:的)?'
        r'(?:两次|两轮|两个)(成功的)?(SQL)?(?:查询结果|查询|结果)(?:的)?(?:差值|变化率|差多少|相差多少)?',text,re.I)
    pronoun=re.fullmatch(r'(?:这)?(?:两者|两个结果|两次查询|两次结果|它们)(?:相差|差|差值是|变化率是)多少',text)
    if explicit:
        return ComparisonRequest(sql_only=bool(explicit[2]),successful_only=bool(explicit[1]),
                                 reverse=reverse,month_alignment=month,direction_explicit=bool(suffix),alignment_explicit=month)
    if pronoun:
        return ComparisonRequest(reverse=reverse,month_alignment=month,direction_explicit=bool(suffix),alignment_explicit=month)
    return None


def _catalog_digest(engine):
    catalog=getattr(engine,'metric_catalog',None)
    return digest({'declared_digest':catalog.digest,
        'sources':{key:asdict(value) for key,value in catalog.sources.items()},
        'derived':{key:asdict(value) for key,value in catalog.derived.items()}}) if catalog else None


def _base_contract(metric,engine):
    identity=(metric.get('table'),metric.get('column'),metric.get('function'))
    if not all(isinstance(x,str) and x for x in identity):
        raise ComparisonError('comparison_metric_contract_missing')
    catalog=getattr(engine,'metric_catalog',None)
    matches=[m for m in catalog.sources.values() if (m.table,m.column,m.function)==identity] if catalog else []
    contract={'kind':'physical','table':identity[0],'column':identity[1],'function':identity[2],
        'unit':metric.get('unit',matches[0].unit if len(matches)==1 else 'unknown'),
        'currency':metric.get('currency',matches[0].currency if len(matches)==1 else None),
        'missing':metric.get('missing','null'),
        'filters':[{key:f.get(key) for key in ('table','column','operator','value')} for f in metric.get('filters',[])]}
    contract['filters'].sort(key=digest)
    return contract


def _slots(plan,columns,engine):
    metrics=plan.get('metrics') or [{'table':plan.get('metric_table') or plan.get('table'),
        'column':plan.get('metric_column'),'function':plan.get('metric_function'),'label':plan.get('metric_label')}]
    contracts={m.get('id',str(i)):_base_contract(m,engine) for i,m in enumerate(metrics)}
    if len(contracts)!=len(metrics):raise ComparisonError('comparison_metric_contract_missing')
    derived_items=plan.get('derived_metrics',[])
    derived_by_id={item['id']:item for item in derived_items}
    if len(derived_by_id)!=len(derived_items) or set(contracts)&set(derived_by_id):
        raise ComparisonError('comparison_metric_contract_missing')
    formulas=plan.get('grain_audit',{}).get('formulas',[])
    expanded_nodes=0
    slots=[{'column':m['label'],'contract':contracts[m.get('id',str(i))]} for i,m in enumerate(metrics) if m.get('label') in columns]
    def derived_contract(metric_id,depth,stack):
        if metric_id in stack or depth>16:raise ComparisonError('comparison_metric_contract_missing')
        derived=derived_by_id[metric_id]
        actual=[f for f in formulas if f.get('metric_id')==metric_id and f.get('expression')==derived['expression']]
        if len(actual)!=1:raise ComparisonError('comparison_metric_contract_missing')
        return {'kind':'derived','expression':expression(derived['expression'],depth+1,(*stack,metric_id)),
            'unit':actual[0]['unit'],'currency':actual[0].get('currency'),
            'missing':actual[0].get('zero_denominator','null')}
    def expression(node,depth=0,stack=()):
        nonlocal expanded_nodes
        expanded_nodes+=1
        if not isinstance(node,dict) or depth>16 or expanded_nodes>512:
            raise ComparisonError('comparison_metric_contract_missing')
        if set(node)=={'ref'} and isinstance(node['ref'],str):
            if node['ref'] in contracts:return contracts[node['ref']]
            if node['ref'] in derived_by_id:return derived_contract(node['ref'],depth+1,stack)
        if set(node)=={'constant'} and type(node['constant']) in (int,float) and math.isfinite(node['constant']):
            return {'constant':node['constant']}
        if set(node)=={'op','left','right'} and node['op'] in {'add','subtract','multiply','divide'}:
            return {'op':node['op'],'left':expression(node['left'],depth+1,stack),'right':expression(node['right'],depth+1,stack)}
        raise ComparisonError('comparison_metric_contract_missing')
    for derived in derived_items:
        if derived['label'] not in columns:continue
        slots.append({'column':derived['label'],'contract':derived_contract(derived['id'],0,())})
    dimensions=[{'column':plan.get('dimension_labels',{}).get(column,column),
        'contract':{'table':plan.get('dimension_tables',{}).get(column,plan.get('table')),
                    'column':column,'transform':plan.get('dimension_transforms',{}).get(column,'identity')}}
        for column in plan.get('dimensions',[])]
    if not slots or len(slots)>8 or len(dimensions)>4:
        raise ComparisonError('comparison_metric_contract_missing')
    if sorted(s['column'] for s in slots+dimensions)!=sorted(columns):
        raise ComparisonError('comparison_output_mapping_missing')
    if len({digest(s['contract']) for s in slots})!=len(slots):
        raise ComparisonError('comparison_metric_contract_missing')
    return slots,dimensions


def build_snapshot(result,execution_record,engine):
    """Save only independently addressable full rows, never an unknown preview."""
    try:
        proof=unseal(execution_record)
        columns=result['columns']
        rows=result['rows']
        plan=result['plan']
        provenance=result['provenance']
        complete=provenance.get('result_completeness')=='within_return_limit'
        if provenance.get('result_completeness')=='complete':
            artifact=provenance.get('complete_result',{})
            complete=artifact.get('status')=='complete' and artifact.get('row_count')==len(rows)
        explicit_subset=type(plan.get('top_n')) is int and plan['top_n']>0
        if (result['status']!='ok' or result.get('result_state')=='partial_rows'
            or not (complete or explicit_subset) or len(rows)>MAX_ROWS or not 0<len(columns)<=MAX_COLUMNS
            or len(columns)!=len(set(columns))):return None
        slots,dimensions=_slots(plan,columns,engine)
        if any(set(row)!=set(columns) or any(type(value) not in (str,int,float,type(None))
               or isinstance(value,float) and not math.isfinite(value)
               or isinstance(value,str) and len(value)>256 for value in row.values()) for row in rows):return None
        if any(row[m['column']] is not None and type(row[m['column']]) not in (int,float) for row in rows for m in slots):return None
        return seal({'version':1,'question':proof['question'],'execution_context_sha256':execution_record['sha256'],
            'source_revision':proof['source_revision'],'catalog_digest':_catalog_digest(engine),
            'query_hash':provenance.get('query_hash'),'sql':proof['sql'],'parameters':proof['parameters'],
            'metrics':slots,'dimensions':dimensions,'rows':rows,'columns':columns,
            'scope':'explicit_ranked_subset' if explicit_subset else 'complete_displayed_rows'})
    except (ComparisonError,KeyError,ValueError,TypeError):
        return None


def _operand(turn):
    state=turn.state or {}
    if state.get('route')!='sql' or state.get('pending_question') is not None or state.get('clarification_code'):
        raise ComparisonError('comparison_reference_not_successful')
    return {'question':turn.effective_question,'created_at':turn.created_at,
            'query_state':{key:deepcopy(state.get(key)) for key in ('metrics','filters','dimensions')},
            'execution_record':deepcopy(state.get('executed_sql_context')),
            'snapshot':deepcopy(state.get('comparison_snapshot'))}


def _verify(operand,engine,expected_revision):
    proof=unseal(operand['execution_record'])
    snapshot=unseal(operand['snapshot'])
    if (snapshot.get('version')!=1 or snapshot.get('question')!=operand['question'] or proof.get('question')!=operand['question']
        or snapshot.get('execution_context_sha256')!=operand['execution_record']['sha256']
        or snapshot.get('sql')!=proof.get('sql') or snapshot.get('parameters')!=proof.get('parameters')
        or snapshot.get('source_revision')!=proof.get('source_revision')):
        raise ComparisonError('comparison_snapshot_invalid')
    if snapshot['source_revision']!=expected_revision:
        raise ComparisonError('comparison_source_changed')
    if snapshot.get('catalog_digest')!=_catalog_digest(engine):
        raise ComparisonError('comparison_metric_definition_changed')
    if (not isinstance(snapshot.get('rows'),list) or len(snapshot['rows'])>MAX_ROWS
        or not isinstance(snapshot.get('metrics'),list) or not 0<len(snapshot['metrics'])<=8
        or not isinstance(snapshot.get('dimensions'),list) or len(snapshot['dimensions'])>4
        or not isinstance(snapshot.get('columns'),list) or len(snapshot['columns'])>MAX_COLUMNS
        or any(not isinstance(row,dict) or set(row)!=set(snapshot['columns']) for row in snapshot['rows'])):
        raise ComparisonError('comparison_snapshot_invalid')
    for row in snapshot['rows']:
        for metric in snapshot['metrics']:
            value=row[metric['column']]
            if value is not None and (type(value) not in (int,float) or not math.isfinite(value)):
                raise ComparisonError('comparison_value_invalid')
    return snapshot


def _number(value):
    if value is None:return None
    value=Decimal(str(value))
    if not value.is_finite():raise ComparisonError('comparison_value_invalid')
    if abs(value)>Decimal(2**53-1):return str(value)
    if value==value.to_integral_value():return int(value)
    output=float(value)
    return str(value) if value and output==0 else output


def _index(snapshot,ordered_dimensions,month_alignment):
    index={}
    for row in snapshot['rows']:
        key=[row[dimension['column']] for dimension in ordered_dimensions]
        if month_alignment:
            monthly=[i for i,d in enumerate(ordered_dimensions) if d['contract']['transform']=='month']
            if len(monthly)!=1:raise ComparisonError('comparison_month_alignment_unavailable')
            i=monthly[0]
            value=key[i]
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])',value):
                raise ComparisonError('comparison_month_alignment_unavailable')
            key[i]=value[-2:]
        token=json.dumps(key,ensure_ascii=False,allow_nan=False)
        if token in index:raise ComparisonError('comparison_duplicate_group')
        index[token]=(key,row)
    return index


MESSAGES={
    'comparison_selected_reference_unavailable':'指定查询不在当前会话保留的历史中，请重新执行完整问题。不会改用最近两次结果。',
    'comparison_reference_ambiguous':'引用的完整问题对应多次查询，请用倒数第几次SQL查询指定具体来源。',
    'comparison_same_reference':'两项引用指向同一次查询，请选择两个不同的查询结果。',
    'comparison_reference_expression_unsupported':'请用“比较倒数第三次和倒数第一次SQL查询”，或“比较SQL查询“完整问题一”和“完整问题二””。默认以较早查询为基准。',
    'comparison_reference_unavailable':'当前会话没有两次可引用的查询，请先执行两次查询。',
    'comparison_reference_not_successful':'所指查询尚未成功或仍待澄清。请先完成查询，或明确比较最近两次成功的SQL查询。',
    'comparison_snapshot_unavailable':'所指结果没有可完整比较的快照，可能是旧会话、部分预览或未支持的输出口径。请缩小范围后重新查询。',
    'comparison_snapshot_invalid':'历史结果记录不一致，请重新执行所指查询后比较。',
    'comparison_source_changed':'数据源版本已变化，请重新执行两次查询，避免混用旧结果。',
    'comparison_metric_definition_changed':'指标定义已变化，请重新查询后比较。',
    'comparison_metric_mismatch':'两次查询的指标、计算方式或单位不同，请先统一统计口径。',
    'comparison_dimension_mismatch':'两次查询的分组字段或时间粒度不同，请先统一分组。',
    'comparison_duplicate_group':'分组键不唯一，无法逐组对应。请保留明确的年份或其他区分字段。',
    'comparison_month_alignment_unavailable':'当前结果没有唯一的YYYY-MM月份字段，不能按月份对齐。',
    'comparison_no_pairs':'两次结果没有可计算的对应值。请明确如何对齐分组，或补充非空结果。',
}


class ConversationComparisonAgent:
    def __init__(self,engine):self.engine=engine

    def run(self,request,history):
        sources=[]
        try:
            expected_revision=self.engine.current_source_revision()
            previous=(history[-1].state or {}) if history else {}
            if request.reference_error:raise ComparisonError(request.reference_error)
            if request.references:
                if len(request.references)!=2:raise ComparisonError('comparison_reference_expression_unsupported')
                selector=ConversationReferenceAgent()
                chosen=[selector.select_source(reference,history) for reference in request.references]
                for selection in chosen:
                    if selection.reason:
                        code={'requested_sql_reference_ambiguous':'comparison_reference_ambiguous',
                              'requested_sql_reference_not_available':'comparison_selected_reference_unavailable'}.get(
                                  selection.reason,'comparison_reference_expression_unsupported')
                        raise ComparisonError(code)
                indices=sorted(selection.history_index for selection in chosen)
                if indices[0]==indices[1]:raise ComparisonError('comparison_same_reference')
                sources=[_operand(history[index]) for index in indices]
                for source,index in zip(sources,indices):
                    source['history_reference']={'history_index':index,'question':history[index].question,
                        'turn_id':history[index].turn_id,
                        'effective_question':history[index].effective_question}
            elif request.reuse or previous.get('route')=='comparison' and not request.sql_only:
                context=unseal(previous.get('comparison_context'))
                sources=context['sources']
                request=replace(request,reverse=request.reverse if request.direction_explicit else context.get('reverse',False),
                    month_alignment=request.month_alignment if request.alignment_explicit else context.get('month_alignment',False))
            else:
                selected=[turn for turn in history if (turn.state or {}).get('route')=='sql'] if request.sql_only else list(history)
                if request.successful_only:
                    selected=[turn for turn in selected if (turn.state or {}).get('executed_sql_context')]
                if len(selected)<2:raise ComparisonError('comparison_reference_unavailable')
                sources=[_operand(turn) for turn in selected[-2:]]
            if not isinstance(sources,list) or len(sources)!=2:raise ComparisonError('comparison_snapshot_invalid')
            verified=[_verify(source,self.engine,expected_revision) for source in sources]
            left,right=(verified[::-1] if request.reverse else verified)
            metrics=[{digest(m['contract']):m for m in snapshot['metrics']} for snapshot in (left,right)]
            if set(metrics[0])!=set(metrics[1]):raise ComparisonError('comparison_metric_mismatch')
            dimensions=[{digest(d['contract']):d for d in snapshot['dimensions']} for snapshot in (left,right)]
            if set(dimensions[0])!=set(dimensions[1]):raise ComparisonError('comparison_dimension_mismatch')
            dimension_keys=sorted(dimensions[0])
            ordered=[[dimensions[i][key] for key in dimension_keys] for i in (0,1)]
            indexes=[_index(snapshot,dim,request.month_alignment) for snapshot,dim in zip((left,right),ordered)]
            rows=[]
            paired=0
            nulls=0
            nonpositive=0
            for key in sorted(set(indexes[0])|set(indexes[1])):
                group=(indexes[0].get(key) or indexes[1][key])[0]
                for metric_key in sorted(metrics[0]):
                    labels=[metrics[i][metric_key]['column'] for i in (0,1)]
                    values=[indexes[i][key][1][labels[i]] if key in indexes[i] else None for i in (0,1)]
                    delta=percent=None
                    status='可比较'
                    if key not in indexes[0] or key not in indexes[1]:status='仅比较结果有此组' if key not in indexes[0] else '仅基准结果有此组'
                    elif None in values:status='观察值为空';nulls+=1
                    else:
                        with localcontext() as arithmetic:
                            arithmetic.prec=34
                            baseline,current=map(lambda v:Decimal(str(v)),values)
                            delta=current-baseline
                            if baseline>0:percent=delta/baseline*100
                            else:nonpositive+=1
                        paired+=1
                    contract=metrics[0][metric_key]['contract']
                    group_labels={ordered[0][i]['column']:value for i,value in enumerate(group)}
                    rows.append({'分组':json.dumps(group_labels,ensure_ascii=False) if group else '全部',
                        '指标':labels[0],'基准值':_number(values[0]),'比较值':_number(values[1]),
                        '差值':_number(delta),'变化率(%)':_number(percent),
                        '单位':contract.get('currency') or contract.get('unit','unknown'),'对应状态':status})
            notices=['差值=比较值−基准值；变化率=(比较值−基准值)/基准值×100%。未重新执行SQL。']
            if request.references:notices.append('指定的两次查询按发生先后排列；当前以'+('较晚' if request.reverse else '较早')+'查询为基准，与引用短句中的书写顺序无关。')
            if len(indexes[0])!=len(indexes[1]) or set(indexes[0])!=set(indexes[1]):notices.append('只在一侧出现的分组保留空值，不按零处理。')
            if nulls:notices.append('空值不作为零，也不参与差值计算。')
            if nonpositive:notices.append('基准值为零或负数时保留差值，不给出默认变化率。')
            if any(s['scope']=='explicit_ranked_subset' for s in verified):notices.append('比较对象是原问题明确指定的排名子集，不代表全部数据。')
            if not paired:raise ComparisonError('comparison_no_pairs')
            if self.engine.current_source_revision()!=expected_revision:raise ComparisonError('comparison_source_changed')
            context=seal({'version':1,'sources':sources,'reverse':request.reverse,'month_alignment':request.month_alignment})
            source_details=[{'question':s['question'],'query_hash':s['query_hash'],'snapshot_sha256':o['snapshot']['sha256'],
                'sql':s['sql'],'parameters':s['parameters'],'source_revision':s['source_revision'],
                'scope':s['scope'],'role':'比较值' if (i==0)==request.reverse else '基准值',
                'history_reference':deepcopy(o.get('history_reference'))}
                for i,(s,o) in enumerate(zip(verified,sources))]
            return {'status':'ok','answer':f'已按相同指标与分组比较 {paired} 个对应值。'+notices[0],
                'columns':['分组','指标','基准值','比较值','差值','变化率(%)','单位','对应状态'],'rows':rows,
                'plan':{},'notices':notices,'comparison_evidence':{'sources':source_details,
                    'alignment':'month_of_year' if request.month_alignment else 'exact_group_keys',
                    'can_align_months':all(sum(d['contract']['transform']=='month' for d in s['dimensions'])==1 for s in verified),
                    'baseline':'较晚查询' if request.reverse else '较早查询','computed_pair_count':paired},
                'comparison_context':context,'trace':[{'stage':'conversation_comparison','status':'ok',
                    'checks':['execution_record','result_snapshot','source_revision','metric_contract','group_alignment']} ]}
        except (ComparisonError,KeyError,TypeError,ValueError) as exc:
            code=getattr(exc,'code','comparison_snapshot_invalid')
            actions=[]
            if code=='comparison_no_pairs' and not request.month_alignment:
                snapshots=[unseal(source['snapshot']) for source in sources]
                if all(sum(d['contract']['transform']=='month' for d in s['dimensions'])==1 for s in snapshots):
                    actions=[{'label':'按月份对齐后比较','question':'按月份对齐'}]
            return {'status':'clarification','clarification_code':code,
                'answer':MESSAGES.get(code,'结果尚不能可靠比较，请明确所指查询和统计口径。'),
                'clarification':MESSAGES.get(code,'结果尚不能可靠比较，请明确所指查询和统计口径。'),
                'columns':[],'rows':[],'comparison_actions':actions,
                'comparison_context':seal({'version':1,'sources':sources,'reverse':request.reverse,
                    'month_alignment':request.month_alignment}) if len(sources)==2 else None,
                'trace':[{'stage':'conversation_comparison','status':'clarification','reason':code}]}


class ConversationComparisonEditAgent:
    """Resolve a selected operand, execute through Omni, then verify and compare.

    An unspecified operand remains a question to the user, never a model guess.
    Failed replacement queries retain the last verified comparison sources.
    """
    def __init__(self,engine):self.engine=engine

    def run(self,question,history,execute,*,confirmed_scope=None):
        from .session import ConversationTurn
        from .sql_history_scope import resolve_sql_followup_scope, VERIFIED_SQL_CONTEXT_MODES
        previous=history[-1].state or {} if history else {}
        role_match=re.fullmatch(r'(?:请)?(?:把)?(基准值?|比较值|基准查询|比较查询|较早查询|较晚查询)(?:的条件)?(?:改成|换成|改为)[，,:：\s]*(.+)',question.strip())
        selection=re.fullmatch(r'(?:修改|改|选)?(基准值?|比较值|基准查询|比较查询|较早查询|较晚查询)',question.strip())
        pending=previous.get('comparison_pending_edit')
        pending_query=previous.get('comparison_pending_query')
        from .clarification_continuation import ClarificationContinuationAgent
        reply_kind=ClarificationContinuationAgent.reply_kind(question)
        cancel=question.strip() in {'取消修改','放弃修改'}
        if pending_query and confirmed_scope is None and not role_match and not (selection and pending):
            slots=self.engine.analyze_slots(question)
            if (re.match(r'^(?:换个主题|换个问题|新问题|重新查询)',question)
                or re.search(r'保修|政策|文档|手册|操作方法|概念区别',question)
                or slots['metrics'] and (slots['time_spans'] or slots['values'])):
                return None
        short=bool(re.search(r'^(?:那|那么|改成|换成|再查|再看|同样)|呢[？?]?$|^按.+分组',question))
        if not role_match and not (selection and pending) and not pending_query and not (cancel and pending) and not (pending and reply_kind) and not (previous.get('route')=='comparison' and short):
            return None
        sources=[]
        context=None
        edit_question=question
        try:
            context=unseal(previous.get('comparison_context'))
            sources=context['sources']
            if len(sources)!=2:raise ComparisonError('comparison_reference_unavailable')
            revision=self.engine.current_source_revision()
            for source in sources:_verify(source,self.engine,revision)
            if cancel and (pending_query or pending):
                synthetic=ConversationTurn(question,question,0,{'route':'comparison','comparison_context':seal(context)})
                return ConversationComparisonAgent(self.engine).run(ComparisonRequest(reuse=True),[synthetic])
            if pending and reply_kind and not role_match and not selection:
                message=('基准查询提供差值计算的起点，比较查询提供要对比的值；差值=比较值−基准值。请选择要修改哪一侧，另一侧保持不变。'
                         if reply_kind=='clarification_help_requested' else
                         '还需要确认要修改哪一侧：基准查询或比较查询。两侧原结果已保留，当前没有执行新查询。')
                return {'status':'clarification','clarification_code':'comparison_edit_role_required',
                    'answer':message,'clarification':message,'columns':[],'rows':[],'_effective_question':pending,
                    'comparison_context':seal(context),'comparison_pending_edit':pending,
                    'comparison_actions':[{'label':'修改基准查询','question':'基准值'},
                                          {'label':'修改比较查询','question':'比较值'},
                                          {'label':'取消本次修改','question':'取消修改'}],
                    'trace':[{'stage':'comparison_clarification','source':'ClarificationContinuationAgent',
                              'reason':reply_kind,'executed':False,'original_comparison_retained':True}]}
            role=None
            resume=None
            if role_match:
                role=role_match[1];edit_question='换成'+role_match[2]
            elif selection and pending:
                role=selection[1];edit_question=pending
            elif pending_query:
                resume=unseal(pending_query)
                if (resume.get('version')!=1 or type(resume.get('index')) is not int or resume['index'] not in (0,1)
                    or resume.get('context_sha256')!=seal(context)['sha256'] or resume.get('source_revision')!=revision):
                    raise ComparisonError('comparison_snapshot_invalid')
            else:
                # A complete new question is independent even if ending in 呢.
                slots=self.engine.analyze_slots(question)
                if slots['metrics'] and (slots['time_spans'] or slots['values']):return None
                if not any(slots[key] for key in ('metrics','values','time_spans','dimensions')):return None
                raise ComparisonError('comparison_edit_role_required')
            reverse=context.get('reverse',False)
            index=resume['index'] if resume else (1 if reverse else 0) if role.startswith('基准') else (0 if reverse else 1)
            if role=='较早查询':index=0
            elif role=='较晚查询':index=1
            source=sources[index]
            state={'route':'sql','pending_question':None,'clarification_code':None,
                **source.get('query_state',{}),'executed_sql_context':source['execution_record']}
            if not source.get('query_state'):
                original=self.engine.extract_required_intent(source['question']).to_dict()
                state.update({key:original.get(key,[]) for key in ('metrics','filters','dimensions')})
            turn=ConversationTurn(source['question'],source['question'],source.get('created_at',0),state)
            if resume:
                pending_turn=ConversationTurn(resume['question'],resume['question'],0,resume['state'])
                continuation=ClarificationContinuationAgent(self.engine).run(question,[pending_turn]) if confirmed_scope is None else None
                if continuation is not None:
                    required=continuation.plan
                    message=continuation.message+' '+required.clarification
                    return {'status':'clarification','clarification_code':required.clarification_code,
                        'answer':message,'clarification':message,'clarification_options':list(required.clarification_options),
                        'columns':[],'rows':[],'_effective_question':continuation.scope,
                        'comparison_context':seal(context),'comparison_pending_query':pending_query,
                        'comparison_pending_target':{'role':'基准查询' if index==(1 if reverse else 0) else '比较查询',
                                                     'original_question':source['question']},
                        'comparison_actions':[{'label':'取消本次修改','question':'取消修改'}],
                        'trace':[{'stage':'comparison_clarification','source':'ClarificationContinuationAgent',
                                  'reason':continuation.reason,'executed':False,'original_comparison_retained':True}]}
                if confirmed_scope is not None:
                    effective,audit=confirmed_scope,{'mode':'server_confirmed_comparison_clarification','scope_question':confirmed_scope}
                else:
                    effective,audit=resolve_sql_followup_scope(question,[pending_turn],self.engine)
                    if audit.get('mode') not in VERIFIED_SQL_CONTEXT_MODES:
                        slots=self.engine.analyze_slots(question)
                        if slots['metrics'] and (slots['time_spans'] or slots['values']):return None
                        raise ComparisonError('comparison_edit_reply_unverified')
            else:
                effective,audit=resolve_sql_followup_scope(edit_question,[turn],self.engine)
            explicit_full=role_match and re.match(r'^完整问题[：:]\s*(.+)$',role_match[2])
            if explicit_full:
                effective=explicit_full[1].strip()
                audit={'mode':'independent','reason':'explicit_complete_operand_replacement','scope_question':effective}
            elif audit.get('reason')=='self_contained_sql' and role_match:
                # Explicit complete replacement owns its new scope; do not
                # merge old filters back into a fully specified user question.
                effective=role_match[2].strip()
                audit={**audit,'reason':'explicit_complete_operand_replacement','scope_question':effective}
            elif not resume and audit.get('mode') not in VERIFIED_SQL_CONTEXT_MODES:
                raise ComparisonError('comparison_edit_scope_unverified')
            response=execute(effective)
            result=response.get('result',{})
            if response.get('route')=='sql' and result.get('status')=='clarification' and result.get('clarification_options'):
                waiting=seal({'version':1,'index':index,'question':effective,'state':response['state'],
                    'context_sha256':seal(context)['sha256'],'source_revision':revision})
                return {**result,'answer':result.get('clarification'),'_effective_question':effective,
                    'comparison_context':seal(context),'comparison_pending_query':waiting,
                    'comparison_pending_target':{'role':'基准查询' if index==(1 if reverse else 0) else '比较查询',
                        'original_question':source['question']},
                    'comparison_actions':[{'label':'取消本次修改','question':'取消修改'}],
                    'trace':[{'stage':'comparison_operand_edit','status':'clarification','source_index':index,
                              'reason':result['clarification_code'],'original_comparison_retained':True}]}
            proof=result.get('sql') and response.get('state',{}).get('executed_sql_context')
            snapshot=build_snapshot(result,proof,self.engine) if proof else None
            if response.get('route')!='sql' or result.get('status')!='ok' or not snapshot:
                raise ComparisonError('comparison_edit_query_not_complete')
            if self.engine.current_source_revision()!=revision:raise ComparisonError('comparison_source_changed')
            replacement={'question':effective,'created_at':source.get('created_at',0),
                'query_state':{key:deepcopy(response['state'].get(key)) for key in ('metrics','filters','dimensions')},
                'execution_record':proof,'snapshot':snapshot}
            updated=deepcopy(context);updated['sources'][index]=replacement
            synthetic=ConversationTurn(question,question,0,{'route':'comparison','comparison_context':seal(updated)})
            output=ConversationComparisonAgent(self.engine).run(ComparisonRequest(reuse=True),[synthetic])
            output['edit_evidence']={'source_index':index,'previous_question':source['question'],
                'replacement_question':effective,'query_status':result['status'],'scope_resolution':audit,
                'planner_source':response.get('planner_source')}
            if output['status']=='ok':
                output['answer']='已更新所选查询并完成比较；另一侧保留原结果。差值=比较值−基准值。'
                output['notices'][0]='本轮重新执行所选查询，另一侧使用已保存结果；差值=比较值−基准值。'
            output['trace'].insert(0,{'stage':'comparison_operand_edit','status':'ok','source_index':index,
                'replacement_question':effective,'query_audit_id':response.get('audit_id')})
            return output
        except (ComparisonError,ValueError,KeyError,TypeError) as exc:
            code=getattr(exc,'code','comparison_snapshot_invalid')
            messages={
                'comparison_edit_role_required':'你要修改基准查询还是比较查询？请选择对象；未执行新的查询。',
                'comparison_edit_scope_unverified':'无法确认该改动保留原查询的全部条件，请明确要替换的条件。',
                'comparison_edit_query_not_complete':'替换查询未得到可完整比较的结果，原比较来源已保留。请补充完整条件后重新修改。'}
            messages['comparison_edit_reply_unverified']='该补充条件尚未确认，请选择当前提供的字段或时间选项；原比较查询保持不变。'
            actions=[{'label':'修改基准查询','question':'基准值'},{'label':'修改比较查询','question':'比较值'}] if code=='comparison_edit_role_required' else []
            output={'status':'clarification','clarification_code':code,'columns':[],'rows':[],
                'answer':messages.get(code,MESSAGES.get(code,'比较上下文无法确认，请重新查询。')),
                'clarification':messages.get(code,MESSAGES.get(code,'比较上下文无法确认，请重新查询。')),
                'comparison_context':seal(context) if context else None,
                'comparison_pending_edit':edit_question if code=='comparison_edit_role_required' else None,
                'comparison_actions':actions,
                'trace':[{'stage':'comparison_operand_edit','status':'clarification','reason':code}]}
            if code=='comparison_edit_reply_unverified' and pending_query:
                waiting=unseal(pending_query)
                required=self.engine.extract_required_intent(waiting['question'])
                output.update({'clarification_code':required.clarification_code,
                    'clarification_options':list(required.clarification_options),
                    '_effective_question':waiting['question'],'comparison_pending_query':pending_query,
                    'comparison_actions':[{'label':'取消本次修改','question':'取消修改'}]})
                output['comparison_pending_target']={'role':'基准查询' if waiting['index']==(1 if context.get('reverse') else 0) else '比较查询',
                    'original_question':context['sources'][waiting['index']]['question']}
            return output
