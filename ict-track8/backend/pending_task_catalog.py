"""Inspect unfinished task roots without changing conversation or execution state."""
import re
from .pending_task_resume import PendingTaskResumeAgent


class PendingTaskCatalogAgent:
    _REQUEST=re.compile(r'^(?:查看待补问题|查看未完成问题|查看待补查询|查看未完成查询|有哪些问题没完成|未完成的查询有哪些|'
                        r'(?:继续|恢复)(?:刚才)?(?:(?:没完成|未完成)(?:的)?(?:问题|查询)|待补问题))[。？！!?]*$')

    def __init__(self,engine,store):self.engine=engine;self.store=store

    def run(self,question,session_id):
        if not self._REQUEST.fullmatch(question.strip()):return None
        tasks=[]
        snapshots=self.store.list_open(session_id) if session_id else []
        resume_agent=PendingTaskResumeAgent(self.engine)
        for snapshot in snapshots:
            turn=snapshot['turn']
            if (turn.state or {}).get('route') == 'comparison':
                from .pending_comparison_resume import PendingComparisonResumeAgent
                selection=PendingComparisonResumeAgent(self.engine).inspect(turn)
                tasks.append({'query_reference_id':turn.turn_id,'question':turn.effective_question,
                    'task_type':'comparison','clarification_code':(selection.preview or {}).get('clarification_code'),
                    'clarification':(selection.preview or {}).get('clarification') or selection.message,
                    'available':selection.turn is not None,'reason':selection.reason,'expires_at':snapshot['expires_at']})
                continue
            selection=resume_agent.run(f'继续待补查询编号{turn.turn_id}',(turn,),
                resolver=lambda identifier:self.store.resolve(session_id,identifier))
            ready=selection.turn is not None and selection.plan is not None
            tasks.append({'query_reference_id':turn.turn_id,'question':turn.effective_question,
                'clarification_code':(turn.state or {}).get('clarification_code'),
                'clarification':selection.plan.clarification if ready else selection.message,
                'available':ready,'reason':selection.reason,'expires_at':snapshot['expires_at']})
        message=('下面是当前会话尚未执行的待补问题；每项展示最新保留条件。请选择一项恢复，查看列表不会执行查询或切换当前问题。'
                 if tasks else '当前会话没有保留的待补问题。已完成、过期或清空的任务不会出现在这里。')
        if not session_id:message='请在同一会话中查看待补问题；未提供会话标识时无法读取历史任务。'
        return {'status':'ok','route':'tasks','question':question,'effective_question':question,
            'planner_source':'PendingTaskCatalogAgent','session_id':session_id,
            'pending_tasks':tasks,'result':{'status':'ok','answer':message},
            'context_resolution':{'mode':'pending_task_catalog','executed':False,'history_unchanged':True},
            'trace':[{'stage':'pending_task_catalog','source':'PendingTaskCatalogAgent',
                      'status':'listed','task_count':len(tasks),'executed':False}]}
