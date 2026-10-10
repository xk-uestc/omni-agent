"""Request-local, Schema-anchored business semantics for the Omni entrypoint.

Memory supplies no SQL and no prompt instructions. A canonical phrase must come
from existing Schema aliases and pass the existing rule planner with the exact
physical metric and conditions before the original query chain runs.
"""
from __future__ import annotations
from dataclasses import asdict
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
from itertools import product
import re
import sqlite3
import time
import uuid

from .core import Budget, RecallContext, encoded, occurrences


def _metrics(plan):
    if isinstance(plan, dict):
        result={(m['table'],m['column'],m['function']) for m in plan.get('metrics',[])}
        if plan.get('metric_column'): result.add((plan.get('metric_table') or plan.get('table'),plan['metric_column'],plan.get('metric_function')))
    else:
        result={(m.table,m.column,m.function) for m in plan.metrics}
        if plan.metric_column: result.add((plan.metric_table or plan.table,plan.metric_column,plan.metric_function))
    return result


def _matches(plan, records):
    metrics=_metrics(plan)
    filters=plan.get('filters',[]) if isinstance(plan,dict) else [asdict(f) for f in plan.filters]
    for r in records:
        b=r.binding
        if (b['table'],b['column'],b['function']) not in metrics:return False
        for f in b.get('filters',[]):
            if not any(x.get('table')==b['table'] and x['column']==f['column'] and
                       (x['operator']=='=' and x['value']==f['value'] or x['operator']=='IN' and x['value']==[f['value']]) for x in filters):return False
    return True


def classify_outcome(response, error=None):
    if error:
        name=type(error).__name__
        if 'Source' in name:return 'source_conflict'
        if name=='SqlSafetyError':return 'sql_safety_rejected'
        if name=='GenerationError':return 'model_failure'
        return 'tool_failure'
    response=response or {};r=response.get('result') or {}
    code=str(r.get('clarification_code') or r.get('error_code') or '')
    if any(x in code for x in ('source_changed','schema_changed','revision','integrity','conflict')):return 'source_conflict'
    if any(x in code for x in ('read_only','safety','sql_non_read')):return 'sql_safety_rejected'
    if 'model' in code or response.get('planner_source')=='model_failed':return 'model_failure'
    if response.get('status') in {'incomplete','error','failed'}:return 'tool_failure'
    if response.get('status')!='ok':return 'clarification'
    if r.get('sql') and r.get('provenance',{}).get('source_revision'):return 'verified_result'
    if r.get('source_validation',{}).get('status')=='verified':return 'verified_result'
    if r.get('citations') and all(c.get('metadata',{}).get('source_sha256') for c in r['citations']):return 'verified_result'
    return 'candidate_result'


class MemoryAdapter:
    def __init__(self, core, engine, knowledge, *, database_source='database', clock=None, budget=Budget(), formation_enabled=False):
        if core.enabled and database_source not in core.scope.data_sources:
            raise ValueError('database source not in trusted server scope')
        self.core,self.engine,self.knowledge=core,engine,knowledge
        self.database_source=database_source
        self.clock=clock or (lambda:datetime.now(timezone.utc).isoformat())
        self.budget=budget
        self.formation_enabled=bool(formation_enabled)
        self.request_receipts=ContextVar("memory_request_receipts",default=())

    @property
    def enabled(self):return self.core.enabled

    def _snapshot(self):
        with self.engine._connect() as db:
            tables,index,_=self.engine._snapshot_for(db)
            rules=self.engine.planner.linker.rules_for(tables)
        schema=hashlib.sha256(encoded({'tables':[t.to_dict() for t in tables],
            'aliases':[asdict(r) for r in rules], 'catalog':getattr(self.engine.metric_catalog,'digest',None)}).encode()).hexdigest()
        sources={self.database_source:self.engine.current_source_revision()}
        # Only configured sources are inspected. Document text is never injected.
        for doc in self.knowledge.list_documents():
            ident=doc['document_id']
            if ident not in self.core.scope.data_sources or ident==self.database_source:continue
            try:
                self.knowledge.verify_source(ident,expected_sha256=doc['sha256'])
                sources[ident]=doc['sha256']
            except (OSError,ValueError,KeyError):pass
        columns={(t.name,c.name):c for t in tables for c in t.columns}
        metrics=set()
        for r in rules:
            if r.role!='metric' or (r.table,r.column) not in columns:continue
            numeric=any(x in columns[r.table,r.column].data_type.upper() for x in ('INT','REAL','NUM','DEC','FLOAT','DOUBLE'))
            function=r.metric_function or ('SUM' if numeric else 'COUNT')
            if function in {'SUM','AVG','MIN','MAX','COUNT','COUNT_DISTINCT'}:metrics.add((r.table,r.column,function))
        # Existing value index anchors filters even without explicit alias rules.
        values=frozenset((v.table,v.column,encoded(v.value)) for v in index.entries if (v.table,v.column) in columns)
        return schema,sources,frozenset(metrics),values,rules,index

    def source_version(self, document_ids):
        """Trusted provisioning helper; no confirmation is inferred here."""
        with self.engine.consistent_reads():
            schema,sources,*_=self._snapshot()
        requested={self.database_source,*document_ids}
        if not requested<=sources.keys():raise ValueError('provisioning source not available or authorized')
        return {'schema':schema,'sources':{k:sources[k] for k in sorted(requested)}}

    def _filter_conflict(self, question, records, rules):
        if not records:return False
        slots=self.engine.analyze_slots(question,preferred_tables=tuple({r.binding['table'] for r in records}))
        for r in records:
            b=r.binding
            for f in b.get('filters',[]):
                explicit=[v for v in slots['values'] if (v.table,v.column)==(b['table'],f['column'])]
                labels={f['column'],*(a for rule in rules if (rule.table,rule.column)==(b['table'],f['column']) for a in rule.aliases)}
                removes=any(re.search(r'(?:不限|全部|所有|删除|移除|取消).{0,8}'+re.escape(a),question) for a in labels)
                if removes or any(v.negated or v.value!=f['value'] for v in explicit):return True
        return False

    def prepare(self, question, *, skip=False, inherited=()):
        started=time.perf_counter()
        audit={'recall_count':1,'selected':[],'consumed':[],'decisions':[],'degraded':False,'bindings':[]}
        try:
            schema,sources,metrics,values,rules,index=self._snapshot()
            protected=[m.span() for m in re.finditer(r'"[^"\n]*"|\x27[^\x27\n]*\x27|“[^”]*”|‘[^’]*’|`[^`]*`',question)]
            # An absent literal cannot have a regex match. Filter before compiling
            # thousands of value patterns; retain identical spans, no new cache.
            protected += [m.span() for entry in index.entries if entry.value and str(entry.value) in question
                          for m in re.finditer(re.escape(str(entry.value)),question)]
            from ..nl2sql.planner import _strip_unsafe_instruction_noise
            from ..unified_routing import term_definition_request
            unsafe=bool(_strip_unsafe_instruction_noise(question)[1] or re.search(r'\b(?:DELETE|DROP|INSERT|UPDATE|ALTER|TRUNCATE)\b',question,re.I))
            if skip or unsafe or term_definition_request(question):protected=[(0,len(question))]
            context=RecallContext(question,self.clock(),schema,sources,metrics,values,tuple(protected))
        except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):
            # Still exactly one core recall; empty trust context selects nothing.
            context=RecallContext(question,self.clock(),'',{},frozenset(),frozenset(),((0,len(question)),))
            rules=();sources={};unsafe=False
            audit['degraded']=True
        recalled=self.core.recall(context,self.core.scope,self.budget)
        audit['decisions']=recalled.decisions
        audit['degraded'] |= recalled.degraded
        audit['recall_ms']=round((time.perf_counter()-started)*1000,3)
        records=recalled.selected
        audit['selected']=[r.memory_id for r in records]
        if unsafe:audit['decisions'].append({'reason':'unsafe_request_not_rewritten'})
        if audit['degraded']:return question,(),audit,'source_changed' if inherited else None
        selected_terms={r.term for r in records}
        blockers=[d for d in recalled.decisions if d.get('term') and d['term'] not in selected_terms and d['reason']!='unsupported_type']
        if blockers:return question,(),audit,blockers[0]['reason']
        # Do not inherit a revoked semantic binding through ordinary SQL history.
        if inherited:
            try:
                stored={r.memory_id:r for r in self.core.store.scan(self.core.scope)[0]}
                inherited_records=[]
                for receipt in inherited:
                    r=stored.get(receipt['memory_id'])
                    reason=self.core.invalid_reason(r,context,self.core.scope) if r else 'memory_removed'
                    if reason or r.source_version!=receipt['source_version'] or r.binding!=receipt['binding']:
                        audit['decisions'].append({'reason':'history_memory_source_changed'})
                        return question,(),audit,'source_changed'
                    inherited_records.append(r)
                    if any(other.term==r.term and self.core.invalid_reason(other,context,self.core.scope) is None
                           and encoded(other.binding)!=encoded(r.binding) for other in stored.values()):
                        audit['decisions'].append({'memory_id':r.memory_id,'reason':'history_memory_conflict'})
                        return question,(),audit,'conflict'
                if self._filter_conflict(question,inherited_records,rules):
                    audit['decisions'].append({'reason':'explicit_condition_conflict'})
                    return question,(),audit,'explicit_condition_conflict'
            except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):
                audit['degraded']=True
                # A history bound to uncheckable memory must not execute silently.
                return question,(),audit,'source_changed'
        if not records:return question,(),audit,None
        if self._filter_conflict(question,records,rules):
            audit['decisions'].append({'reason':'explicit_condition_conflict'})
            return question,(),audit,'explicit_condition_conflict'
        aliases=[];prefixes=[]
        slots=self.engine.analyze_slots(question,preferred_tables=tuple({r.binding['table'] for r in records}))
        for r in records:
            b=r.binding
            choices=sorted({a for rule in rules if rule.role=='metric' and (rule.table,rule.column)==(b['table'],b['column'])
                for a in rule.aliases if re.fullmatch(r'[\w\u3400-\u9fff]{1,80}',a)},key=lambda x:(-len(x),x))[:8]
            if not choices:return question,(),audit,'invalid_metric'
            aliases.append(choices)
            for f in b.get('filters',[]):
                explicit=[v for v in slots['values'] if (v.table,v.column)==(b['table'],f['column'])]
                if not explicit:
                    if not isinstance(f['value'],str) or not re.fullmatch(r'[\w\u3400-\u9fff]{1,80}',f['value']):return question,(),audit,'unsupported_filter_literal'
                    prefixes.append(f['value'])
        for i,choices in enumerate(product(*aliases)):
            if i>=64:break
            edits=[]
            for r,alias in zip(records,choices):
                edits.extend((a,b,alias) for a,b in occurrences(question,r.term,context.protected_spans))
            if any(a2<b1 for (_,b1,_),(a2,_,_) in zip(sorted(edits),sorted(edits)[1:])):return question,(),audit,'overlapping_terms'
            canonical=question
            for a,b,alias in sorted(edits,reverse=True):canonical=canonical[:a]+alias+canonical[b:]
            canonical=''.join(dict.fromkeys(prefixes))+canonical
            if len(canonical)>1000:return question,(),audit,'canonical_budget'
            plan=self.engine.extract_required_intent(canonical)
            if plan.clarification or plan.coverage.get('unresolved') or not _matches(plan,records):continue
            audit['bindings']=[{'memory_id':r.memory_id,'term':r.term,'binding':r.binding,
                'source_version':r.source_version,'provenance':r.provenance} for r in records]
            audit['canonical_question']=canonical
            return canonical,records,audit,None
        audit['decisions'].append({'reason':'canonical_binding_unverified'})
        return question,(),audit,'canonical_binding_unverified'

    @staticmethod
    def clarification(question, session_id, reason):
        message='记忆口径、当前来源或本轮条件存在未核实之处，请确认业务指标及筛选范围。'
        return {'status':'clarification','question':question,'effective_question':question,'route':'clarify',
            'session_id':session_id,'result':{'status':'clarification','clarification':message,
                'clarification_code':'memory_'+reason,'sql':None,'rows':[]},'trace':[],
            'context_resolution':{'mode':'memory_binding_rejected','executed':False,'context_preserved':True}}

    def run(self, agent, question, options):
        trace_id=uuid.uuid4().hex
        callback=options.get('trace_callback');held=[]
        def forward(event):
            if event.get('tool')=='query.complete':held.append(event)
            elif callback:callback(event)
        def emit(tool,output):
            if callback:callback({'stage':'memory','tool':tool,'status':'success','executed':True,
                'call_id':trace_id+':'+tool,'trace_id':trace_id,'output':output})
        audit={'recall_count':0,'selected':[],'consumed':[],'decisions':[],'degraded':False}
        response=None;error=None
        with agent.conversations.turn(options.get('session_id')),self.engine.consistent_reads():
            try:
                if not isinstance(question,str) or not question.strip() or len(question)>1000:raise ValueError('问题为空或过长')
                history=agent.conversations.context(options['session_id']) if options.get('session_id') and not options.get('reset_context') else ()
                inherited=()
                if history and not self.engine.analyze_slots(question)['metrics']:
                    inherited=tuple({r['memory_id']:r for turn in history
                        for r in (turn.state or {}).get('memory_bindings',())}.values())
                canonical,records,audit,reason=self.prepare(question,skip=bool(options.get('image_attachments')),inherited=inherited)
                audit['trace_id']=trace_id;emit('memory.recall',audit.copy())
                if reason:
                    if options.get('reset_context') and options.get('session_id'):agent.conversations.clear(options['session_id'])
                    response=self.clarification(question,options.get('session_id'),reason)
                else:
                    receipt=[{'memory_id':r.memory_id,'source_version':r.source_version,'binding':r.binding} for r in records] or list(inherited)
                    token=self.request_receipts.set(tuple(receipt))
                    try:
                        response=agent._query_without_memory(canonical,**{**options,'trace_callback':forward if callback else None},
                            _memory_original_question=question if records else None)
                    finally:self.request_receipts.reset(token)
                    response['question']=question
                    if isinstance(response.get('result'),dict):response['result']['question']=question
                    if records:
                        # Recheck evidence before publication; do not expose a stale answer.
                        current_schema,current_sources,*_=self._snapshot()
                        stale=any(r.source_version['schema']!=current_schema or any(current_sources.get(k)!=v for k,v in r.source_version['sources'].items()) for r in records)
                        if stale:response=self.clarification(question,options.get('session_id'),'source_changed')
                        elif response.get('status')=='ok' and response.get('result',{}).get('sql'):
                            if _matches(response['result'].get('plan',{}),records):audit['consumed']=[r.memory_id for r in records]
                            else:response=self.clarification(question,options.get('session_id'),'execution_binding_mismatch')
                    elif inherited and response.get('status')=='ok' and response.get('result',{}).get('sql'):
                        audit['inherited_memory_ids']=[r['memory_id'] for r in inherited]
                response['memory']=audit
                return response
            except Exception as exc:
                error=exc
                raise
            finally:
                category=classify_outcome(response,error)
                final_plan=((response or {}).get('result') or {}).get('plan') or {}
                model_failed=(response or {}).get('planner_source')=='rules_fallback' or final_plan.get('planner_source')=='rules_fallback'
                event={'event_id':trace_id,'question_sha256':hashlib.sha256(str(question).encode()).hexdigest(),
                    'category':category,'model_planning_failed':model_failed,'selected':audit['selected'],'consumed':audit['consumed'],
                    'source_versions':{b['memory_id']:b['source_version'] for b in audit.get('bindings',[])},
                    'rejected':[{'memory_id':d.get('memory_id'),'reason':d['reason']} for d in audit['decisions'] if d['reason']!='selected']}
                observe_started=time.perf_counter()
                observed=self.core.observe(event,{'execution_verified':category=='verified_result','independent_task_verified':False})
                audit['observe_ms']=round((time.perf_counter()-observe_started)*1000,3)
                audit['observe']={**observed,'category':category,'model_planning_failed':model_failed}
                if self.formation_enabled and response is not None and observed.get('stored'):
                    try:
                        from .formation import MemoryFormation
                        audit['formation']={'candidate_ids':MemoryFormation(self).capture(response,trace_id),'promotion':'none'}
                    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):
                        audit['formation']={'candidate_ids':[],'promotion':'none','error':'source_capture_unavailable'}
                emit('memory.observe',audit['observe'])
                if callback:
                    if response is not None and (not held or response.get('status')!='ok'):
                        forward_complete={'stage':'query_complete','tool':'query.complete','status':'success' if response.get('status')=='ok' else 'attention','executed':False,'output':{'status':response.get('status'),'route':response.get('route')}}
                        callback(forward_complete)
                    else:
                        for event in held:callback(event)
