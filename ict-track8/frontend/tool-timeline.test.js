const { test } = require('node:test');
const assert = require('node:assert/strict');
const { normalizeEvent, fromResult, reduceEvents, invocationVerb, create } = require('./tool-timeline.js');

test('exports the normalization and live timeline contract', () => {
  [normalizeEvent, fromResult, reduceEvents, create].forEach(fn => assert.equal(typeof fn, 'function'));
});

test('invocation wording distinguishes execution, failure and unverified records', () => {
  assert.equal(invocationVerb('running'),'正在调用');
  assert.equal(invocationVerb('success'),'已调用');
  assert.equal(invocationVerb('error'),'调用失败');
  assert.equal(invocationVerb('reported'),'调用记录');
  assert.equal(invocationVerb('stopped'),'调用已停止');
});

test('explicit running and completed invocation states remain distinct', () => {
  const running = normalizeEvent({ task_id:'read', tool:'sql', status:'running', args:{question:'Sales'} });
  const done = normalizeEvent({ task_id:'read', tool:'sql', status:'complete', latency_ms:12 });
  assert.equal(running.status, 'running');
  assert.equal(done.status, 'success');
  assert.equal(done.kind, 'tool');
  assert.ok(done.id && done.title && done.tool);
});

test('actual tool failures are not reported as successful output', () => {
  const event = normalizeEvent({task_id:'lookup',tool:'document_cell',status:'failed',error:'source changed',error_code:'evidence_revision_changed'});
  assert.equal(event.status, 'error');
  assert.equal(event.kind, 'tool');
});

test('an unlabelled intent trace never invents execution success', () => {
  const event = normalizeEvent({stage:'intent_planning',source:'model',route:'fusion',latency_ms:20,attempts:1});
  assert.notEqual(event.status, 'success');
  assert.equal(event.kind, 'commentary');
});

test('actual intent and knowledge invocations without status remain reported', () => {
  for (const tool of ['intent.plan','knowledge.answer']) {
    const event = normalizeEvent({call_id:`call:${tool}`,tool,input:{question:'Sales'}});
    assert.equal(event.kind,'tool');
    assert.equal(event.status,'reported');
    assert.equal(event.tool,tool);
  }
});

test('a late running event cannot overwrite a completed receipt', () => {
  let rows = reduceEvents([],{call_id:'stable',tool:'knowledge.answer',status:'success'});
  rows = reduceEvents(rows,{call_id:'stable',tool:'knowledge.answer',status:'running'});
  assert.equal(rows.length,1);
  assert.equal(rows[0].status,'success');
});

test('an unexecuted scope change is not a database write invocation', () => {
  const event = normalizeEvent({stage:'pending_scope_edit',source:'PendingScopeEditAgent',status:'verified',executed:false});
  assert.notEqual(event.status, 'success');
  assert.notEqual(event.tool, 'database.write');
  assert.doesNotMatch(event.title, /写入|更新数据库|修改数据库/);
});

test('an executed scope edit means query condition editing rather than a write', () => {
  const event = normalizeEvent({stage:'relational_scope_edit',source:'RelationalScopeEditAgent',status:'executed'});
  assert.notEqual(event.tool, 'database.write');
  assert.doesNotMatch(event.title, /写入|更新数据库|修改数据库/);
});

test('database write success requires explicit execution and success status', () => {
  assert.equal(normalizeEvent({tool:'database.write',executed:true,status:'success'}).status,'success');
  for (const event of [
    {tool:'database.write',status:'success'},
    {tool:'database.write',executed:false,status:'success'},
    {tool:'database.write',executed:true},
    {tool:'database.write',executed:true,status:'running'},
  ]) assert.notEqual(normalizeEvent(event).status,'success');
});

test('planned and skipped steps never enter the execution timeline as successes', () => {
  const rows = fromResult({route:'fusion',status:'clarification',trace:[],result:{
    status:'clarification',execution_plan:[{id:'read',tool:'sql',args:{question:'Sales'}}],
    skipped_tasks:['read'],results:{},trace:[],
  }});
  assert.ok(Array.isArray(rows));
  assert.equal(rows.filter(r=>r.kind==='tool' && r.status==='success').length,0);
  const normalized = normalizeEvent({task_id:'read',tool:'sql',status:'not_executed'});
  assert.equal(normalized.status,'skipped');
  const reduced = reduceEvents([],{task_id:'read',tool:'sql',status:'not_executed'});
  assert.equal(reduced.length,0);
});

test('repeated lifecycle events update an invocation without duplicating it', () => {
  let rows = reduceEvents([],{task_id:'read',tool:'sql',status:'running'});
  rows = reduceEvents(rows,{task_id:'read',tool:'sql',status:'complete',latency_ms:12});
  assert.equal(rows.length,1);
  assert.equal(rows[0].status,'success');
});

test('top-level and nested identical trace events are deduplicated', () => {
  const event = {trace_id:'t',task_id:'lookup',tool:'document_cell',status:'complete',latency_ms:5};
  const rows = fromResult({route:'fusion',status:'ok',trace:[event],result:{status:'ok',trace:[{...event}],results:{lookup:{value:'5/6'}}}});
  assert.equal(rows.filter(r=>r.kind==='tool' && r.status==='success').length,1);
});

test('confirmed structured SQL maps to a successful database read', () => {
  const rows = fromResult({status:'ok',structured:{status:'ok',sql:'SELECT amount FROM sales LIMIT 10',parameters:[],columns:['amount'],rows:[[5]],provenance:{execution_status:'executed'}}});
  assert.ok(rows.some(r=>r.tool==='database.read' && r.kind==='tool' && r.status==='success'));
  assert.ok(!rows.some(r=>r.tool==='database.write'));
});

test('omni SQL clarification with no executed SQL cannot become a successful read', () => {
  const rows = fromResult({status:'clarification',route:'sql',trace:[],result:{
    status:'clarification',sql:null,rows:[],columns:[],result_state:'unexecuted',
    provenance:{execution_status:'not_executed'},
  }});
  assert.equal(rows.filter(r=>r.kind==='tool' && r.status==='success').length,0);
});

test('an incomplete SQL result cannot be inferred as successful execution', () => {
  const rows = fromResult({status:'incomplete',structured:{status:'incomplete',sql:'SELECT amount FROM sales',rows:[],provenance:{execution_error_code:'sql_execution_rejected'}}});
  assert.ok(!rows.some(r=>r.tool==='database.read' && r.status==='success'));
});

test('normalization leaves original source trace data intact', () => {
  const event = {task_id:'read',tool:'sql',status:'failed',args:{question:'Sales'},error:'blocked'};
  const original = structuredClone(event);
  normalizeEvent(event);
  fromResult({route:'fusion',trace:[event],result:{trace:[event],status:'incomplete'}});
  assert.deepEqual(event,original);
});

test('contradictory not-executed receipt never becomes a successful database read',()=>{
  const rows=fromResult({structured:{status:'ok',sql:'SELECT amount FROM sales',rows:[],
    provenance:{execution_status:'not_executed'}}});
  assert.ok(!rows.some(row=>row.tool==='database.read'&&row.status==='success'));
});

test('a started call with no completed execution is still running, while its failure stays visible',()=>{
  assert.equal(normalizeEvent({tool:'nl2sql',status:'running',executed:false}).status,'running');
  assert.equal(normalizeEvent({tool:'nl2sql',status:'error',executed:false}).status,'error');
});
