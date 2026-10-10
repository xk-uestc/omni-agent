"""Independent frozen formation scorer: Gold never reaches extraction/planning."""
from score_memory_sensitive_m1a import score as score_existing


def score(gold,response,database,documents):
    response=response or {};inner=response.get('result') or {}
    if gold['kind']=='no_sql':
        no_sql=not inner.get('sql') and not inner.get('rows')
        consumed=(response.get('memory') or {}).get('consumed',[])
        return no_sql and not consumed,{'no_sql':no_sql,'consumed':consumed,'status':response.get('status')}
    return score_existing(gold,response,database,documents)


def formation_score(expected,candidate,validation,review):
    """Source-reference semantics, independently authored in frozen Gold."""
    accurate=(candidate['term']==expected['term'] and candidate.get('binding')==expected['binding'])
    promoted=review.get('decision')=='confirm' if review else False
    return {'candidate_accurate':accurate,'validation_passed':validation.get('valid',False),
        'promoted':promoted,'wrong_promotion':promoted and not expected['promotion_allowed']}
