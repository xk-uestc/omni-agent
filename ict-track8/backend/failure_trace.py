"""Structured first failure attribution; never parses exception text for secrets."""


def failure(category, phase, code, *, task_id=None, retryable=False):
    return {'version': 'foundation-failure-v1', 'category': category, 'phase': phase,
            'code': code, 'task_id': task_id, 'retryable': retryable,
            'independent_task_success': 'not_verified'}


def for_code(code, *, phase='execution', task_id=None):
    code = code or 'unclassified_incomplete'
    if code == 'read_only_query_required': category = 'safety'
    elif code in {'evidence_revision_changed','evidence_integrity_failed','source_missing','source_changed'}: category = 'source_binding'
    elif 'source' in code or 'binding' in code or 'constraint' in code: category = 'source_binding'
    elif code in {'model_output_protocol','model_plan_shape_invalid','requested_operations_incomplete'}: category = 'planner'
    elif code in {'provider_unavailable','model_unavailable','model_provider_failed'}: category = 'provider_transport'
    elif code in {'timeout','stream_interrupted'}: category = 'provider_transport'; phase = 'transport'
    elif code in {'evidence_missing','no_document_evidence'}: category = 'retrieval'
    elif code in {'document_answer_incomplete','evidence_scope_incomplete'}: category = 'evidence_coverage'
    else: category = 'executor' if phase == 'execution' else 'planner' if phase == 'planning' else 'answer_verification'
    return failure(category, phase, code, task_id=task_id,
                   retryable=code in {'provider_unavailable','model_provider_failed','storage_unavailable'})


def attach(response):
    """Preserve chronological planning failure even when a fallback returns ok."""
    rows = []
    for event in response.get('trace', []):
        if not isinstance(event, dict): continue
        detail = event.get('failure')
        if isinstance(detail, dict): rows.append(detail)
        if event.get('stage') in {'planning', 'intent_planning'} and event.get('error'):
            code = ('model_output_protocol' if event['error'] in {'ValueError','TypeError','KeyError','JSONDecodeError'}
                    else 'model_provider_failed')
            rows.append(for_code(event.get('rejection_code') or code, phase='planning'))
        if event.get('status') in {'provider_unavailable', 'dossier_provider_failed', 'multi_provider_failed'}:
            rows.append(for_code('model_provider_failed', phase='generation'))
    result = response.get('result') or {}
    if response.get('status') != 'ok':
        code = result.get('error_code') or result.get('clarification_code')
        if code:
            rows.append(for_code(code, task_id=result.get('failed_task')))
        elif not rows:
            rows.append(failure('answer_verification', 'answer', 'unclassified_clarification'))
    unique = []
    for row in rows:
        if row not in unique: unique.append(row)
    response['failure_trace'] = unique
    response['task_verification'] = {'execution_status': response.get('status'),
        'independent_whole_question_success': 'not_verified_by_status',
        'first_failure': unique[0] if unique else None}
    return response
