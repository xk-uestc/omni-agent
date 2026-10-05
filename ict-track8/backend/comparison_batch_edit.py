"""Stage two read-only operand edits; publish only a verified complete pair."""
from copy import deepcopy
import json
import re
from .session import ConversationTurn
from .conversation_comparison import (ConversationComparisonEditAgent,ConversationComparisonAgent,
    ComparisonRequest,ComparisonError,seal,unseal,_verify,MESSAGES,parse_request)

ROLE=r'(?:基准值?|比较值|基准查询|比较查询|较早查询|较晚查询)'
CLAUSE=re.compile(r'^(?:请)?(?:把)?('+ROLE+r')(?:的条件)?(?:改成|换成|改为)[，,:：\s]*(.+)$')


class ConversationComparisonBatchEditAgent:
    def __init__(self,engine):self.engine=engine

    @staticmethod
    def commands(question):
        shared=re.fullmatch(r'(?:请)?(?:两边|两项查询|两个查询)(?:都|同时)?(?:改成|换成|改为)(.+)',question.strip())
        if shared:
            return [{'role':role,'question':'把'+role+'改成'+shared[1]} for role in ('基准查询','比较查询')]
        parts=re.split(r'[；;]|[，,](?=(?:把)?'+ROLE+r'(?:的条件)?(?:改成|换成|改为))',question.strip())
        if len(parts)==1:return None
        matches=[CLAUSE.fullmatch(part.strip()) for part in parts]
        if not any(matches):return None
        if len(parts)!=2 or not all(matches):return []
        return [{'role':match[1],'question':part.strip()} for match,part in zip(matches,parts)]

    @staticmethod
    def index(role,reverse):
        if role=='较早查询':return 0
        if role=='较晚查询':return 1
        return (1 if reverse else 0) if role.startswith('基准') else (0 if reverse else 1)

    @staticmethod
    def retain(output,original,batch,awaiting):
        output['comparison_context']=seal(original)
        output['comparison_pending_batch']=seal(batch)
        if len(json.dumps({'context':output['comparison_context'],'batch':output['comparison_pending_batch'],
                          'pending_query':output.get('comparison_pending_query')},ensure_ascii=False))>28000:
            raise ComparisonError('comparison_batch_state_budget_exceeded')
        output['comparison_batch_progress']={'completed_count':batch['cursor'],'total_count':2,
            'awaiting_role':awaiting,'published':False}
        output['comparison_actions']=[*(output.get('comparison_actions') or []),
            *([] if any(action.get('question')=='取消修改' for action in output.get('comparison_actions',[]))
              else [{'label':'取消双侧修改','question':'取消修改'}])]
        output['trace'].insert(0,{'stage':'comparison_batch_edit','status':'clarification',
            'staged_queries':batch['cursor'],'original_comparison_retained':True})
        return output

    @staticmethod
    def committed(output,batch):
        output.pop('edit_evidence',None)
        output['batch_edit_evidence']=batch['completed']
        output['answer']='两项查询均已更新并完成比较；未发布中途结果。'
        output['notices'][0]='本轮使用两项已重新执行的查询结果，核验后同时更新；差值=比较值−基准值。'
        output['trace'].insert(0,{'stage':'comparison_batch_edit','status':'ok','completed_queries':2})
        return output

    def run(self,question,history,execute,*,confirmed_scope=None):
        state=(history[-1].state or {}) if history else {}
        commands=self.commands(question)
        pending=state.get('comparison_pending_batch')
        if commands is None and not pending:return None
        cancel=question.strip() in {'取消修改','放弃修改'}
        requested_comparison=parse_request(question)
        if pending and commands is None and not cancel and confirmed_scope is None and not (requested_comparison and requested_comparison.reuse):
            slots=self.engine.analyze_slots(question)
            if (CLAUSE.fullmatch(question.strip()) or re.match(r'^(?:换个主题|换个问题|新问题|重新查询)',question)
                or re.search(r'保修|政策|文档|手册|操作方法|概念区别',question)
                or slots['metrics'] and (slots['time_spans'] or slots['values'])):return None
        original=None
        try:
            original=unseal(state.get('comparison_context'))
            revision=self.engine.current_source_revision()
            if len(original['sources'])!=2:raise ComparisonError('comparison_reference_unavailable')
            for source in original['sources']:_verify(source,self.engine,revision)
            if cancel:
                turn=ConversationTurn(question,question,0,{'route':'comparison','comparison_context':seal(original)})
                result=ConversationComparisonAgent(self.engine).run(ComparisonRequest(reuse=True),[turn])
                result['answer']='已取消双侧修改，恢复原比较结果。'
                return result
            if commands is not None:
                if len(commands)!=2 or len({self.index(edit['role'],original.get('reverse',False)) for edit in commands})!=2:
                    raise ComparisonError('comparison_batch_two_roles_required')
                batch={'version':1,'context_sha256':seal(original)['sha256'],'source_revision':revision,
                    'commands':commands,'cursor':0,'staged_sources':{},'completed':[]}
                waiting=None
            else:
                batch=unseal(pending)
                if (batch.get('version')!=1 or batch.get('context_sha256')!=seal(original)['sha256']
                    or batch.get('source_revision')!=revision or type(batch.get('cursor')) is not int
                    or batch['cursor'] not in (0,1,2) or len(batch.get('commands',[]))!=2):
                    raise ComparisonError('comparison_snapshot_invalid')
                waiting=state.get('comparison_pending_query')
                if not waiting and not (batch['cursor']==2 and batch.get('phase')=='comparison'):
                    raise ComparisonError('comparison_snapshot_invalid')
            staged=deepcopy(original)
            for index,source in batch['staged_sources'].items():
                if index not in ('0','1'):raise ComparisonError('comparison_snapshot_invalid')
                _verify(source,self.engine,revision)
                staged['sources'][int(index)]=deepcopy(source)
            for key,value in batch.get('comparison_settings',{}).items():
                if key not in ('reverse','month_alignment') or type(value) is not bool:raise ComparisonError('comparison_snapshot_invalid')
                staged[key]=value
            if batch['cursor']==2:
                request=parse_request(question)
                if request is None or not request.reuse:raise ComparisonError('comparison_batch_edit_unresolved')
                turn=ConversationTurn(question,question,0,{'route':'comparison','comparison_context':seal(staged)})
                output=ConversationComparisonAgent(self.engine).run(request,[turn])
                if output['status']=='ok':return self.committed(output,batch)
                updated=unseal(output.get('comparison_context'))
                batch['comparison_settings']={key:updated.get(key,False) for key in ('reverse','month_alignment')}
                return self.retain(output,original,batch,'分组对齐')
            while batch['cursor']<2:
                edit=batch['commands'][batch['cursor']]
                single_state={'route':'comparison','comparison_context':seal(staged)}
                if waiting:single_state['comparison_pending_query']=waiting
                turn=ConversationTurn(question,question,0,single_state)
                output=ConversationComparisonEditAgent(self.engine).run(question if waiting else edit['question'],
                    [turn],execute,confirmed_scope=confirmed_scope if waiting else None)
                if not output:raise ComparisonError('comparison_batch_edit_unresolved')
                evidence=output.get('edit_evidence',{})
                if evidence.get('query_status')=='ok':
                    proposed=unseal(output.get('comparison_context'))
                    index=self.index(edit['role'],original.get('reverse',False))
                    _verify(proposed['sources'][index],self.engine,revision)
                    staged=proposed
                    batch['staged_sources'][str(index)]=deepcopy(staged['sources'][index])
                    batch['completed'].append(evidence)
                    batch['cursor']+=1
                    waiting=None
                    if batch['cursor']<2:continue
                    if output['status']!='ok':
                        if output.get('clarification_code')=='comparison_no_pairs' and output.get('comparison_actions'):
                            batch['phase']='comparison'
                            return self.retain(output,original,batch,'分组对齐')
                        raise ComparisonError(output.get('clarification_code','comparison_batch_edit_unresolved'))
                    return self.committed(output,batch)
                if output.get('comparison_pending_query'):
                    return self.retain(output,original,batch,edit['role'])
                raise ComparisonError(output.get('clarification_code','comparison_batch_edit_unresolved'))
        except (ComparisonError,KeyError,ValueError,TypeError) as exc:
            code=getattr(exc,'code','comparison_snapshot_invalid')
            message={'comparison_batch_two_roles_required':'请分别指定基准查询和比较查询，不能重复修改同一侧。',
                     'comparison_batch_state_budget_exceeded':'双侧暂存结果超过会话预算，请缩小查询范围。'}.get(code,MESSAGES.get(code,'请补充两项查询的完整条件。'))
            return {'status':'clarification','clarification_code':code,'columns':[],'rows':[],
                'answer':'双侧修改未完成，原比较来源保持不变。'+message,
                'clarification':'双侧修改未完成，原比较来源保持不变。'+message,
                'comparison_context':seal(original) if original else None,
                'trace':[{'stage':'comparison_batch_edit','status':'clarification','reason':code,'original_comparison_retained':True}]}
