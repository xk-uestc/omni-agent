"""Source intent and bounded conversational fragments shared by all entry points.

These hints select processing paths; SQL and document execution guards remain
responsible for verifying facts, source versions and query semantics.
"""
from __future__ import annotations

import re

DOCUMENT_INTENT = re.compile(
    r'文档|资料|手册|知识库|政策|保修|退货|售后|条款|规定|标准|公式|'
    r'经验|方法|策略|流程|操作|定义|含义|意思|概念|口径|如何|为什么|原因|解释|'
    r'预测|目标|(?:PDF|DOCX|Excel|XLSX)', re.I)
DOCUMENT_ONLY = re.compile(r'是什么意思|是什么含义|如何定义|怎么定义|定义是什么|'
                          r'计算公式|统计口径|业务口径|概念|政策|保修|退货|条款')
FOLLOWUP = re.compile(r'^(?:那么|那|再看|再查|换成|改成|也|仍然)|呢[？?。！!]*$')
NEW_TOPIC = re.compile(r'^\s*(?:换个主题|换一个主题|换个问题|新问题|重新查询)[，,:：\s]*(.*)$')
RESERVED_REFERENCE = re.compile(r'^\s*(?:回到|选择对话来源|继续待补|恢复|比较|对比)')


def term_definition_request(question):
    """Recognize a standalone term/method question, not a ranked row lookup."""
    text = question.strip().rstrip('。？！!?')
    # A year may qualify a definition's version. It does not itself request
    # numerical aggregation, e.g. "2025年网页浏览量的指标定义".
    explicit = re.fullmatch(r'([^，,；;。？?]{1,80}?)(?:指标定义|统计口径|业务口径|'
        r'计算公式|是什么意思|是什么含义|如何定义|怎么定义)(?:是什么|说明)?', text)
    if explicit and not re.search(r'查询|统计|汇总|排名|排行|最高|最低|最多|最少|'
            r'取数|数据库|数据表|先|然后|再|同时|以及|\bSQL\b', explicit[1], re.I):
        return True
    if len(text) > 100 or re.search(r'\d|[一二三四五六七八九十〇零]{2,}年|统计|汇总|分组|'
            r'排名|排行|最高|最低|最多|最少|第[一二三四五六七八九十]|哪些|取数|'
            r'查询|数据库|数据表|\bSQL\b', text, re.I):
        return False
    return bool(re.fullmatch(r'(?:请问|请解释一下|请解释|解释一下|解释|告诉我)?'
        r'[^，,；;。？?]{1,60}?(?:是什么意思|是什么|是啥|啥意思|怎么计算|怎么算|'
        r'如何计算|计算方法是什么|计算方法)', text)
        or re.fullmatch(r'(?:什么叫|啥叫)[^，,；;。？?]{1,60}', text))


def source_hint(question, engine):
    if term_definition_request(question):
        return 'document'
    slots = engine.analyze_slots(question)
    structured = bool(slots['metrics'])
    document = bool(DOCUMENT_INTENT.search(question))
    # A metric's definition/formula/policy is a document question even though
    # the metric name also appears in the database business lexicon.
    data_operation = bool(re.search(r'查询|统计|汇总|排名|排行|最高|最低|前\d|数据库|SQL|先.+再', question, re.I)
                          or slots['time_spans'])
    if document and DOCUMENT_ONLY.search(question) and not data_operation:
        return 'document'
    if document and structured and data_operation:
        return 'fusion'
    if document:
        return 'document'
    if structured or slots['dimensions'] or slots['values']:
        return 'sql'
    return 'document'


def implicit_sql_fragment(question, engine, *, preferred_tables=()):
    """Recognize only fully covered slot fragments, never arbitrary prose."""
    if (len(question) > 80 or DOCUMENT_INTENT.search(question)
            or RESERVED_REFERENCE.search(question) or NEW_TOPIC.match(question)):
        return False
    slots = engine.analyze_slots(question, preferred_tables=preferred_tables)
    if not any(slots[key] for key in ('metrics', 'dimensions', 'values', 'time_spans')):
        return False
    if slots['metrics'] and any(slots[key] for key in ('values', 'time_spans', 'dimensions')):
        return False  # The existing complete-question policy owns this case.
    from .nl2sql.schema import normalize_text
    remaining = normalize_text(question)
    spans = [link.source_text for link in slots['metrics'] + slots['dimensions']]
    spans += [value.span for value in slots['values']] + list(slots['time_spans'])
    for span in sorted(set(spans), key=len, reverse=True):
        remaining = remaining.replace(normalize_text(span), '')
    remaining = re.sub(r'^(?:那么|那|再看|再查|改成|换成)', '', remaining)
    return not re.sub(r'的|地区|呢|是多少|多少|怎么样|查询|统计|查看|看|[，,。？?！!\s]', '', remaining)


def safe_history(history):
    """Compact navigation context; no duplicate tables or unverified answers."""
    hidden = {'comparison_snapshot', 'ranked_result_snapshot', 'sql_result_snapshot',
              'fusion_result_snapshot','sql_bridge_context',
              'comparison_context', 'comparison_pending_query', 'comparison_pending_batch',
              'pending_comparison_result'}
    return [{'question': turn.effective_question,
             'state': {key: value for key, value in (turn.state or {}).items() if key not in hidden}}
            for turn in history[-5:]]


def routing_receipt(response, *, model_enabled=False):
    route = response.get('route', 'clarify')
    mode = response.get('context_resolution', {}).get('mode', 'independent')
    labels = {'sql': '数据库问数', 'document': '资料问答', 'fusion': '数据与资料联合查询',
              'comparison': '已有结果比较', 'tasks': '待补任务', 'clarify': '补充条件'}
    strategy = ('verified_result_lookup' if mode == 'server_verified_sql_result_lookup' else
                'sql_result_to_document' if mode == 'server_verified_sql_entity_document_followup' else
                'bound_document_followup' if mode == 'server_verified_implicit_document_followup' else
                'dependency_workflow' if route == 'fusion' else 'source_query')
    reasons = {'verified_result_lookup': '从已核验的查询结果中读取所问字段。',
               'sql_result_to_document': '沿用查询确定的对象，检索相关资料。',
               'bound_document_followup': '沿用上一轮已核验的资料来源。',
               'dependency_workflow': '按依赖顺序查询数据、读取资料并核对结果。'}
    return {'selected_route': route, 'label': labels.get(route, '智能查询'),
            'strategy': strategy, 'reason': reasons.get(strategy,
                '查询所需指标和数据。' if route == 'sql' else '检索原文并核对来源。' if route == 'document'
                else '确认来源及查询范围后继续。'),
            'context_mode': mode, 'model_enabled': model_enabled}
