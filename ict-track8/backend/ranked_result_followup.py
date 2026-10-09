"""Resolve bounded entity/value questions from a verified prior SQL result."""
from __future__ import annotations

import hashlib
import json
import math
import re

from .conversation_comparison import ComparisonError, _catalog_digest, seal, unseal
from .nl2sql.schema import normalize_text


_RANK_QUESTION = re.compile(r'排名|排行|名次|第几(?:名|位)?|排(?:在)?第几|排(?:名)?多少|位居第几')
_MAX_SNAPSHOT_BYTES = 12000
_MAX_ROWS = 128
_MAX_COLUMNS = 16
_ORDINAL = re.compile(r'第([1-9]\d?|[一二三四五六七八九十]{1,3})名')


def build_query_result_snapshot(result, execution_record, engine):
    """Save a small, source-bound table for immediate conversational lookup."""
    try:
        provenance = result['provenance']
        rows, columns, plan = result['rows'], result['columns'], result['plan']
        complete = provenance.get('result_completeness') == 'within_return_limit'
        explicit_subset = type(plan.get('top_n')) is int and plan['top_n'] > 0
        record_payload = execution_record['payload']
        if (result.get('status') != 'ok' or result.get('result_state') == 'partial_rows'
                or not result.get('sql') or not (complete or explicit_subset)
                or not 0 < len(rows) <= _MAX_ROWS or not 0 < len(columns) <= _MAX_COLUMNS
                or provenance.get('source_revision') != record_payload.get('source_revision')):
            return None
        if any(set(row) != set(columns) or any(type(value) not in (str, int, float, type(None))
                or isinstance(value, float) and not math.isfinite(value)
                or isinstance(value, str) and len(value) > 256 for value in row.values()) for row in rows):
            return None
        payload = {'version': 1, 'question': record_payload['question'],
                   'execution_context_sha256': execution_record['sha256'],
                   'source_revision': record_payload['source_revision'],
                   'catalog_digest': _catalog_digest(engine),
                   'query_hash': provenance.get('query_hash'), 'columns': list(columns),
                   'rows': rows, 'plan': {'analysis_mode': plan.get('analysis_mode'),
                       'order_desc': plan.get('order_desc'), 'order_metric': plan.get('order_metric'),
                       'grain_audit': plan.get('grain_audit', {}),
                       'dimensions': list(plan.get('dimensions', [])),
                       'dimension_labels': dict(plan.get('dimension_labels', {})),
                       'metric_label': plan.get('metric_label'),
                       'metrics': [dict(item) for item in plan.get('metrics', [])]},
                   'scope': 'explicit_ranked_subset' if explicit_subset else 'complete_displayed_rows',
                   'ordered_by_metric': False}
        # Only server compiler SQL can certify implicit ranking. Arbitrary
        # relational/model output order is not a ranking contract.
        if (plan.get('planner_source') != 'complex_model_reviewed' and plan.get('dimensions')
                and not plan.get('dimension_transforms')):
            label = plan.get('metric_label')
            metrics = plan.get('metrics', [])
            if metrics:
                key = plan.get('order_metric') or (plan.get('output_metrics') or [metrics[0]['id']])[0]
                label = next((m['label'] for m in metrics if m['id']==key), None)
            if label in columns and all(type(row.get(label)) in (int,float) for row in rows):
                amounts=[row[label] for row in rows]
                descending=plan.get('order_desc') is True
                if amounts==sorted(amounts,reverse=descending) and 'ORDER BY' in result['sql'].upper():
                    payload['ordered_by_metric']=True
                    payload['rank_metric_label']=label
        snapshot = seal(payload)
        return snapshot if len(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode()) <= _MAX_SNAPSHOT_BYTES else None
    except (ComparisonError, KeyError, TypeError, ValueError):
        return None




class VerifiedQueryResultFollowupAgent:
    """Answer a short entity/value followup from the previous verified table."""
    _VALUE_WORDS = re.compile(r'多少|是多少|数值|金额|销售额|订单数|数量|值|几位|第几|排名|名次')

    def __init__(self, engine, conversations):
        self.engine, self.conversations = engine, conversations

    def _snapshot(self, turn):
        state = turn.state or {}
        record = state.get('executed_sql_context')
        snapshot_record = state.get('sql_result_snapshot')
        try:
            payload = record['payload']; snapshot = unseal(snapshot_record)
            expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            if (state.get('route') != 'sql' or state.get('pending_question') is not None
                    or state.get('clarification_code') or record.get('sha256') != expected
                    or payload.get('question') != turn.effective_question
                    or payload.get('source_revision') != self.engine.current_source_revision()
                    or snapshot.get('execution_context_sha256') != record.get('sha256')
                    or snapshot.get('source_revision') != payload.get('source_revision')
                    or snapshot.get('catalog_digest') != _catalog_digest(self.engine)
                    or not isinstance(snapshot.get('rows'), list) or not snapshot['rows']
                    or not isinstance(snapshot.get('columns'), list)):
                return None
            return snapshot
        except (ComparisonError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _match_entity(question, snapshot):
        text = normalize_text(question); choices=[]
        dimensions = set(snapshot.get('plan', {}).get('dimensions', []))
        labels = snapshot.get('plan', {}).get('dimension_labels', {})
        dimension_labels = {labels.get(column, column) for column in dimensions}
        for index, row in enumerate(snapshot['rows']):
            for column in snapshot['columns']:
                if column not in dimension_labels and column not in dimensions: continue
                value=row.get(column)
                if isinstance(value,str) and len(normalize_text(value)) >= 2 and normalize_text(value) in text:
                    choices.append((len(normalize_text(value)), index, value))
        if not choices: return None
        longest=max(item[0] for item in choices); best=[item for item in choices if item[0]==longest]
        return None if len({item[1] for item in best}) != 1 else (best[0][1], best[0][2])

    @staticmethod
    def ordinal_entity(question,snapshot):
        matches=list(_ORDINAL.finditer(question))
        if len(matches)!=1 or not snapshot.get('ordered_by_metric'):return None
        from .nl2sql.lexicon import parse_number
        raw=matches[0][1]
        rank=int(raw) if raw.isdigit() else parse_number(raw,None)
        if not rank:return None
        rank=int(rank)
        label=snapshot['rank_metric_label']
        amounts=list(dict.fromkeys(row[label] for row in snapshot['rows']))
        if rank>len(amounts):return None
        rows=[(i,row) for i,row in enumerate(snapshot['rows']) if row[label]==amounts[rank-1]]
        if len(rows)!=1:return None  # Tied entities require clarification.
        index,row=rows[0];plan=snapshot['plan']
        labels=plan.get('dimension_labels',{})
        values=list(dict.fromkeys(row.get(labels.get(d,d)) for d in plan['dimensions']))
        if len(values)!=1 or not isinstance(values[0],str):return None
        return index,values[0],matches[0].group()

    def run(self, question, history, *, session_id=None):
        if not history or not self._VALUE_WORDS.search(question): return None
        if re.search(r'文档|资料|政策|公式|为什么|原因|如何|解释|比较|对比|分析', question): return None
        previous=history[-1]; snapshot=self._snapshot(previous)
        if snapshot is None: return None
        match=self._match_entity(question, snapshot)
        ordinal=self.ordinal_entity(question,snapshot) if match is None else None
        if ordinal:match=ordinal[:2]
        if match is None: return None
        index, entity=match; row=snapshot['rows'][index]
        # Every semantic token must be covered. A new year, metric, exclusion,
        # comparison or second entity must proceed to full planning.
        text=normalize_text(question).strip('。？?！! ')
        text=re.sub(r'^(?:请问|请|那么|那|刚才|上面|上述|看一下|查看|看)', '', text)
        remaining=text.replace(normalize_text(entity), '', 1)
        if ordinal:remaining=remaining.replace(ordinal[2],'',1)
        requested=[]
        rank_request=bool(_RANK_QUESTION.search(remaining))
        if rank_request:
            if '排名' not in snapshot['columns']: return None
            requested.append('排名')
            remaining=_RANK_QUESTION.sub('',remaining)
        for column in sorted(snapshot['columns'], key=len, reverse=True):
            if column=='排名': continue
            if normalize_text(column) in remaining:
                requested.append(column); remaining=remaining.replace(normalize_text(column),'')
        remaining=re.sub(r'的|地区|谁|是|为|多少|几|名|位|数值|金额|呢|[，,\s]', '', remaining)
        if remaining: return None
        # A bare amount query is ambiguous if several measures are present.
        if not requested:
            measures=[column for column in snapshot['columns'] if type(row.get(column)) in (int,float)
                      and column!='排名']
            if len(measures)!=1: return None
            requested=measures
        answer='；'.join(f'{entity}排名第{row[column]}名' if column=='排名'
                        else f'{entity}{column}为{row[column]}' for column in requested)+'。'
        result={'status':'ok','result_state':'rows','notices':[], 'question':question,
                'rewritten_question':previous.effective_question, 'sql':None,'parameters':[],
                'columns':snapshot['columns'],'rows':[row],
                'plan':snapshot.get('plan',{}), 'answer':answer,
                'explanation':[f'从上一轮已核验查询结果中匹配到{entity}，返回该行结果；未重新执行 SQL。'],
                'provenance':{'source_type':'structured_database','execution_status':'reused_verified_result',
                    'source_revision':snapshot['source_revision'],'query_hash':snapshot.get('query_hash'),
                    'source_question':snapshot['question']},'clarification':None,
                'clarification_code':None,'clarification_options':[]}
        audit={'mode':'server_verified_sql_result_lookup','actual_question':question,
               'source_question':snapshot['question'],'source_revision':snapshot['source_revision'],
               'matched_entity':entity,'executed':False}
        state=dict(previous.state or {}); state['sql_result_followup']=True
        state['selected_result_entity'] = entity
        if session_id:self.conversations.remember(session_id,question=question,effective_question=previous.effective_question,state=state)
        return {'status':'ok','question':question,'effective_question':previous.effective_question,
            'route':'sql','planner_source':'VerifiedQueryResultFollowupAgent','session_id':session_id,
            'context_turns':len(history),'state':{key:value for key,value in state.items()
                if key not in {'comparison_snapshot','ranked_result_snapshot','sql_result_snapshot'}},
            'result':result,'trace':[{'stage':'sql_result_followup','source':'VerifiedQueryResultFollowupAgent',
                'status':'resolved','executed':False,'matched_entity':entity}],
            'context_resolution':audit,'audit_id':hashlib.sha256(json.dumps(audit,ensure_ascii=False,
                sort_keys=True).encode()).hexdigest()[:24]}
