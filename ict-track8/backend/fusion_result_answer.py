"""Present verified calculations and signed differences without another API."""
import re


def ranked_cell_answer(tasks, result, knowledge):
    """Render only complete, replay-verified ranked lookup evidence."""
    if result.get('status') != 'ok' or any(t['tool'] not in {'sql', 'document_cell'} for t in tasks):
        return None
    lines, citations = [], []
    for task in tasks:
        if task['tool'] != 'document_cell':
            continue
        step = result.get('results', {}).get(task['id'], {})
        proof = step.get('dependency_reference_validation', {})
        if proof.get('status') != 'verified' or proof.get('scope') != 'rank1_dimension_and_pinned_literal_document_cell':
            return None
        sql = result['results'][proof['sql_task_id']]
        plan = sql['plan']
        label = plan.get('metric_label') or plan.get('metric_column')
        row = sql['rows'][proof['row_index']]
        metric = row.get(label)
        if metric is None:
            return None
        title = knowledge.document(proof['document_id'])['title']
        lines.append(f'{proof["value"]}的{label}为{metric:g}，排名第一；《{title}》中对应的{proof["column"]}为{step["value"]}。')
        citations.append({'source_uri': step['source_uri'], 'snippet': f'{proof["value"]}：{step["value"]}',
            'metadata': {'document_id': proof['document_id'], 'source_sha256': step['sha256'],
                         'source_locator': step['locator'], 'page_no': step.get('page_no'),
                         'bbox_display_pt': step.get('bbox_display_pt'), 'title': title}})
    if not lines:
        return None
    result.update(answer_mode='verified_ranked_document_cells', answer_completeness='complete', citations=citations)
    return '\n'.join(lines)


def calculation_answer(tasks,result,knowledge):
    if result.get('status')!='ok':return None
    results=result.get('results',{});lines=[];interpretations=[]
    def amount(value,unit):
        if unit=='CNY':return f'{value:,.2f}元'
        return f'{value:g}'+('' if unit in ('unknown',None) else unit)
    for task in tasks:
        step=results.get(task['id'],{})
        if task['tool']=='calculate' and step.get('parameter_semantics_validation',{}).get('status')=='verified':
            formula=results[task['args']['formula']['ref']]
            selectors=[]
            for name,reference in task['args']['parameters'].items():
                fact=results.get(reference['ref'],{})
                if 'rows' in fact:continue
                source=re.fullmatch(r'/api/v1/knowledge/documents/([^/]+)/original',fact.get('source_uri',''))
                if not source:continue
                title=knowledge.document(source[1])['title']
                label=fact.get('label') or fact.get('locator','').rsplit('/column:',1)[-1]
                value=fact.get('value');unit=fact.get('unit')
                if type(value) not in (int,float):continue
                text=f'{value*100:g}%' if unit=='ratio' else amount(value,unit)
                selectors.append(f'《{title}》{label}={text}')
            prefix='；'.join(selectors)
            lines.append(f'{prefix+"：" if prefix else ""}{formula["label"]}为{amount(step["value"],step.get("result_unit"))}。')
            if step.get('result_interpretation'):
                interpretations.append(step['result_interpretation'])
        elif task['tool']=='compare' and type(step.get('difference')) in (int,float):
            lines.append(f'两种口径的差额（前者减后者）为{amount(step["difference"],step.get("unit"))}。')
    return '\n'.join(lines+list(dict.fromkeys(interpretations))) if lines else None
