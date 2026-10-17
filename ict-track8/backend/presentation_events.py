"""Bounded, public receipts for observed agent work, never model reasoning."""
from copy import deepcopy


LABELS = {
    'intent.plan': '规划任务', 'context.resolve': '核对对话条件',
    'knowledge.search': '检索资料', 'knowledge.answer': '依据文档回答',
    'document.overview': '读取文档结构', 'document.scope': '核验所选文档',
    'nl2sql': '查询业务数据', 'sql': '查询业务数据',
    'fusion.execute': '执行跨源任务', 'search': '检索相关原文',
    'search_fact': '提取原文事实', 'document_fact': '读取文档事实',
    'document_cell': '读取表格单元格', 'document_formula': '定位原文公式',
    'calculate': '绑定参数并计算', 'compare': '比较结果',
    'policy_select': '选择适用条款', 'query.failure': '结果说明',
    'query.complete': '本轮处理完成',
}


def _bounded(value, depth=0):
    if depth > 5:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1500]
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in value[:12]]
    if isinstance(value, dict):
        return {str(key)[:100]: _bounded(item, depth + 1)
                for key, item in list(value.items())[:32]}
    return None


def tool_receipt(tool, value):
    """Expose only tool facts already returned to the caller, with size bounds."""
    if hasattr(value, 'to_dict'):
        value = value.to_dict()
    if not isinstance(value, dict):
        return {}
    common = ('status', 'answer_mode', 'result_state')
    if tool in {'sql', 'nl2sql'}:
        fields = ('sql', 'parameters', 'columns', 'rows')
    elif tool == 'calculate':
        fields = ('value', 'result_unit', 'expression', 'parameters',
                  'formula_source', 'formula_locator', 'parameter_semantics_validation')
    elif tool in {'document_formula', 'document_cell', 'document_fact', 'search_fact', 'policy_select'}:
        fields = ('value', 'unit', 'label', 'expression', 'parameters', 'document_id',
                  'source_uri', 'locator', 'sha256', 'page_no', 'bbox_display_pt',
                  'matched_conditions', 'native_formula_proof', 'dependency_reference_validation')
    elif tool == 'compare':
        fields = ('value', 'difference', 'unit', 'operator', 'left', 'right')
    elif tool == 'document.overview':
        fields = ('document', 'coverage')
    elif tool == 'document.scope':
        fields = ('document_id', 'title', 'source_sha256', 'verification_scope')
    elif tool in {'search', 'knowledge.search'}:
        fields = ()
    else:
        fields = ()
    receipt = {key: _bounded(value[key]) for key in (*common, *fields) if key in value}
    if isinstance(value.get('rows'), (list, tuple)):
        receipt['row_count'] = len(value['rows'])
        receipt['preview_limited'] = len(value['rows']) > 12
    citations = value.get('citations', value.get('hits'))
    if isinstance(citations, list):
        receipt['citation_count'] = len(citations)
        receipt['sources'] = []
        for item in citations[:6]:
            metadata = item.get('metadata') or {}
            source = {key: metadata[key] for key in
                      ('document_id', 'source_sha256', 'source_locator', 'page_no') if key in metadata}
            source.update({key: item[key] for key in ('title', 'source_uri') if key in item})
            receipt['sources'].append(_bounded(source))
    return receipt


def presentation_event(event):
    """Add a versioned display envelope while keeping legacy trace fields intact."""
    item = deepcopy(event)
    tool = str(item.get('tool', item.get('stage', 'query')))
    raw_status = item.get('status')
    status = {'complete': 'success', 'completed': 'success', 'ok': 'success',
              'failed': 'error', 'rejected': 'attention'}.get(raw_status, raw_status or 'reported')
    if status == 'running':
        kind = 'tool_start'
    elif tool == 'query.complete':
        kind = 'query_complete'
    elif status == 'attention':
        kind = 'clarification'
    elif status == 'success' and tool == 'calculate':
        kind = 'calculation'
    elif status == 'success' and tool in {'document_formula', 'document_cell', 'document_fact', 'search_fact'}:
        kind = 'source_found'
    else:
        kind = 'tool_result'
    fallback_label = item.get('title') or ('处理资料' if tool.startswith('rag.') else '执行工具')
    item['presentation'] = {
        'version': 'agent-presentation-v1', 'type': kind,
        'label': LABELS.get(tool, fallback_label),
        'status': status, 'task_id': item.get('task_id'),
        'depends_on': list(item.get('dependencies') or []),
        'summary': str(item.get('summary') or '')[:500],
        'receipt': _bounded(item.get('output') or {}),
    }
    return item
