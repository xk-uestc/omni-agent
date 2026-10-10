"""Typed, source-pinned planning advice. No historical graph is executable here.

Capture is an internal/local research ingestion API, not an HTTP endpoint.
The independent verifier is supplied by trusted server/test code, never by the
client or planner. Local review remains required after successful verification.
"""
from dataclasses import asdict, replace
import json
import re
import sqlite3
import time
import uuid
from .core import MemoryRecord, RecallContext, encoded
from .extraction import candidate_digest, digest
from .formation import MemoryFormation
from ..dependency_agent import DependencyAgent
from ..plan_requirements import requested_operations, completion_errors

WORKFLOWS = {'formula_sql', 'sql_document', 'cell_sql', 'multi_document'}
REQUIRED_FEEDBACK = {'task_success', 'operation_coverage', 'result_correct', 'source_correct'}


def task_features(question):
    """Conservative routing features, not a substitute for intent validation."""
    if re.search(r'比较|对比', question) and re.search(r'文档|计划|政策', question):
        workflow = 'multi_document'
    elif re.search(r'排名|最高|最低|前\d|第一', question) and re.search(r'检索|方法|资料', question):
        workflow = 'sql_document'
    elif re.search(r'Excel|表格|单元格', question, re.I) and '过滤' in question:
        workflow = 'cell_sql'
    elif '公式' in question and re.search(r'数据库|SQL|销售额', question):
        workflow = 'formula_sql'
    else:
        workflow = None
    return {'workflow':workflow, 'years':sorted(set(re.findall(r'(?<!\d)(20\d{2})年',question))),
            'required_operations':requested_operations(question)}


def abstract_trace(tasks, result, workflow):
    """Lossy abstraction: no literal arguments, SQL, numbers, or answers survive."""
    if workflow not in WORKFLOWS:raise ValueError('unsupported experience workflow')
    ids={t['id']:f'step_{i}' for i,t in enumerate(tasks)}
    steps=[]
    for t in tasks:
        refs=DependencyAgent.references(t['args'])
        steps.append({'role':ids[t['id']], 'tool':t['tool'],
                      'depends_on':sorted(ids[r] for r in refs),
                      'argument_sources':sorted(set(['current_question_or_current_source'] +
                          [ids[r]+'.verified_output' for r in refs]))})
    docs=sorted(result['source_validation']['documents'])
    return {'version':1,'workflow':workflow,'steps':steps,'documents':docs,
            'rebinding':['current_request_time_region_metric_and_all_explicit_operations',
                         'fresh_read_only_sql_results','current_document_formula_and_typed_cells',
                         'verified_output_references_no_literal_historical_values'],
            'on_missing_or_ambiguous':'clarify', 'execution_authority':'none'}


class ExperienceFormation(MemoryFormation):
    memory_type = 'task_experience'

    def capture_verified_run(self, question, tasks, *, verifier, verifier_id, origin='developer_verified_seed'):
        """Actually execute the graph with the ORIGINAL question, then score.

        The verifier receives real results and must independently check the whole
        task. Its code identity and feedback enter the immutable source receipt.
        Even an accepted candidate still needs validate + local admin review.
        """
        if origin not in {'developer_verified_seed','agent_execution'}:raise ValueError('explicit trace origin required')
        if not re.fullmatch(r'[a-f0-9]{64}',verifier_id):raise ValueError('independent verifier code SHA256 required')
        executor=DependencyAgent(self.adapter.engine,self.adapter.knowledge)
        executor.validate(tasks)
        if completion_errors(requested_operations(question),'fusion',tasks):raise ValueError('required operations missing')
        result=executor.run(tasks,original_question=question)
        if result.get('status')!='ok':raise ValueError('whole execution not successful')
        feedback=verifier(question,tasks,result)
        if not isinstance(feedback,dict) or any(feedback.get(k) is not True for k in REQUIRED_FEEDBACK):
            raise ValueError('independent whole-task verification required')
        workflow=task_features(question)['workflow']
        asset=abstract_trace(tasks,result,workflow)
        version=self.adapter.source_version(asset['documents'])
        if any(version['sources'].get(k)!=v for k,v in result['source_validation']['documents'].items()):
            raise ValueError('execution source version changed')
        event={'kind':'independently_verified_task_execution','origin':origin,'question':question,
               'tasks':tasks,'result':result,'feedback':feedback,'verifier_sha256':verifier_id,
               'source_version':version}
        event_id='trace-'+digest(event)
        content={'memory_type':self.memory_type,'term':'experience_'+workflow,'definition':'Source-bound method; replan from current request.',
                 'binding':{},'experience':asset,'scope':asdict(self.scope),
                 'evidence':{'event_id':event_id,'trace_sha256':digest(event),'verifier_sha256':verifier_id},
                 'source_version':version,'valid_from':self.adapter.clock(),'valid_to':None,'supersedes':None,
                 'reason':origin}
        content['digest']=candidate_digest(content);content['candidate_id']='cand-'+content['digest'][:32]
        content.update(created_at=self.adapter.clock(),source_event_id=event_id,verification_state='candidate')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO memory_source_events VALUES(?,?,?,?)',
                       (self.scope.key,event_id,'task_trace',encoded(event)))
            db.execute('INSERT OR IGNORE INTO memory_candidates VALUES(?,?,?,?,?,?)',
                       (self.scope.key,content['candidate_id'],content['digest'],encoded(content),'candidate','{}'))
            db.execute('INSERT OR IGNORE INTO memory_candidate_events VALUES(?,?,?)',
                       (self.scope.key,content['candidate_id'],event_id))
        return content

    def _record(self,c,state='confirmed',review=None):
        review=review or {}
        return MemoryRecord(memory_id='mem-'+c['candidate_id'][5:],memory_type=self.memory_type,term=c['term'],content=c['definition'],
            binding={},experience=c['experience'],scope=self.scope,
            provenance={'authority':'trusted_local_review' if review else 'validation_only_not_confirmation',
                'confirmation_id':review.get('request_id','not-confirmed'),'verification_id':c['digest'],
                'evidence_id':c['evidence']['event_id'],'candidate_id':c['candidate_id'],'candidate_digest':c['digest'],
                'evidence':c['evidence'],'origin':c['reason'],'review':review},
            source_version=c['source_version'],valid_from=c['valid_from'],valid_to=c['valid_to'],verification_state=state,
            created_at=c['created_at'],updated_at=self.adapter.clock(),task_trace_id=c['source_event_id'])

    def _check(self,c,*,ignore_memory=None,check_conflicts=True):
        try:
            schema,sources,metrics,values,*_=self.adapter._snapshot()
            context=RecallContext('',self.adapter.clock(),schema,sources,metrics,values)
            r=self._record(c)
            reason=self.adapter.core.invalid_context_reason(r,context,self.scope)
            if reason:return [reason]
            if c['binding']!={} or not isinstance(c.get('experience'),dict):return ['invalid_experience_type']
            evidence=c['evidence']
            with self.store.connect() as db:
                row=db.execute('SELECT payload FROM memory_source_events WHERE scope=? AND event_id=? AND document_id=?',
                    (self.scope.key,evidence['event_id'],'task_trace')).fetchone()
            if not row:return ['missing_verified_trace']
            event=json.loads(row[0])
            if digest(event)!=evidence['trace_sha256'] or evidence['event_id']!='trace-'+digest(event):return ['trace_digest_changed']
            if (event['source_version']!=c['source_version'] or event['verifier_sha256']!=evidence['verifier_sha256']
                or event['origin']!=c['reason']):return ['trace_contract_changed']
            if event['result']['status']!='ok' or any(event['feedback'].get(k) is not True for k in REQUIRED_FEEDBACK):return ['independent_verification_missing']
            tasks=event['tasks'];DependencyAgent(self.adapter.engine,self.adapter.knowledge).validate(tasks)
            if any(t['tool'] not in DependencyAgent.TOOLS for t in tasks):return ['tool_unavailable']
            if abstract_trace(tasks,event['result'],task_features(event['question'])['workflow'])!=c['experience']:return ['experience_abstraction_changed']
            if len(encoded(c['experience']))>3000:return ['experience_budget_exceeded']
        except (ValueError,KeyError,TypeError,OSError,sqlite3.Error):return ['experience_unverifiable']
        return []

    def record_reason(self,record,context):
        reason=self.adapter.core.invalid_context_reason(record,context,self.scope)
        if reason:return reason
        if record.memory_type!=self.memory_type:return 'unsupported_type'
        try:
            candidate=self.candidate(record.provenance['candidate_id'])
            if candidate['verification_state']!='confirmed':return 'candidate_not_confirmed'
            expected=self._record(candidate,review=record.provenance['review'])
            if (candidate['digest']!=record.provenance['candidate_digest'] or record.experience!=expected.experience
                or record.binding!={} or record.source_version!=expected.source_version):return 'record_contract_changed'
            checks=self._check(candidate)
            return checks[0] if checks else None
        except (ValueError,KeyError,TypeError):return 'candidate_unverifiable'


class ExperienceSelector:
    """Deterministic advice selection; never returns executable tasks."""
    def __init__(self,adapter,*,max_characters=3000):
        self.adapter=adapter;self.formation=ExperienceFormation(adapter);self.max_characters=max_characters
        if not 256<=max_characters<=6000:raise ValueError('bounded experience prompt required')

    def select(self,question,*,choose=None):
        started=time.perf_counter();features=task_features(question)
        state={'task_features':features,'available_tools':sorted(DependencyAgent.TOOLS),'candidates':[],
               'completed_steps':[],'budget':{'max_items':1,'max_characters':self.max_characters},'failure':None}
        decision={'state':state,'action':{'type':'no_experience'},'legal_actions':[{'type':'no_experience'},{'type':'request_clarification'}],
                  'selected':[],'advice':None}
        try:
            schema,sources,metrics,values,*_=self.adapter._snapshot();state['source_versions']=sources
            context=RecallContext(question,self.adapter.clock(),schema,sources,metrics,values)
            records,_=self.adapter.core.store.scan(self.adapter.core.scope);eligible=[]
            for record in records:
                if record.memory_type!='task_experience':continue
                reason=self.formation.record_reason(record,context)
                if not reason and record.experience['workflow']!=features['workflow']:reason='task_not_applicable'
                # Explicit documents in current task must be covered by this experience.
                explicit={d['document_id'] for d in self.adapter.knowledge.list_documents()
                    if d['document_id'] in question or d['title'] in question}
                if not reason and not explicit<=set(record.experience['documents']):reason='different_document_scope'
                state['candidates'].append({'memory_id':record.memory_id,'reason':reason or 'eligible','experience':record.experience if not reason else None})
                if not reason:eligible.append(record)
            legal=[{'type':'select_experience','memory_id':r.memory_id} for r in eligible]
            decision['legal_actions']+=legal
            # An experimental selector may ONLY choose among validated legal IDs.
            selected_id=choose(tuple(r.memory_id for r in eligible)) if choose else (eligible[0].memory_id if eligible else None)
            chosen=next((r for r in eligible if r.memory_id==selected_id),None)
            if selected_id and chosen is None:raise ValueError('selector chose illegal experience')
            if chosen:
                advice={'kind':'advisory_task_experience','memory_id':chosen.memory_id,'method':chosen.experience,
                        'trust':'planning_data_only_generate_new_plan_and_preserve_all_current_user_constraints'}
                if len(encoded(advice))>self.max_characters:state['failure']='prompt_budget_exceeded'
                else:decision.update(action={'type':'select_experience','memory_id':chosen.memory_id},selected=[chosen.memory_id],advice=advice)
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):state['failure']='experience_unavailable'
        decision['retrieval_ms']=round((time.perf_counter()-started)*1000,3)
        return decision

    def observe(self,decision,response):
        """Execution feedback is not an independent task score or promotion."""
        event={'event_id':'experience-'+uuid.uuid4().hex,'kind':'task_experience_decision',
               'state':decision['state'],'action':decision['action'],'legal_actions':decision['legal_actions'],
               'feedback':{'execution_status':response.get('status'),'planner_source':response.get('planner_source'),
                           'independent_task_verified':False,'retrieval_ms':decision['retrieval_ms'],
                           'tool_calls':len(response.get('result',{}).get('trace',[])),
                           'failure_reason':response.get('result',{}).get('error_code')},'promotion':'none'}
        try:
            self.adapter.core.store.append_event(self.adapter.core.scope,event)
            return {'event_id':event['event_id'],'stored':True}
        except (OSError,ValueError,TypeError,sqlite3.Error):return {'stored':False}
