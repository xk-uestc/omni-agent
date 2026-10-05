const {test}=require('node:test');
const assert=require('node:assert/strict');
const {create,scopeFor}=require('./conversation-session.js');
function storage(initial={}){const map=new Map(Object.entries(initial));return {getItem:key=>map.get(key)||null,setItem:(key,value)=>map.set(key,value)};}
const legacy=[{key:'main',label:'原问数会话'},{key:'docs',label:'原文档会话'}];
function factory(store,options={}){let index=0;return create({storage:store,scope:'http://localhost:8030',legacyKeys:legacy,makeId:()=>`new-${++index}`,clock:()=>1234,...options});}
test('conflicting legacy conversations remain selectable and are never merged or overwritten',()=>{
  const store=storage({main:'main-id',docs:'docs-id'}),first=factory(store);
  assert.equal(first.current(),'main-id');assert.deepEqual(first.list().map(item=>item.id),['main-id','docs-id']);
  const docs=factory(store,{legacyKeys:legacy.slice().reverse()});assert.equal(docs.current(),'main-id');
  assert.equal(store.getItem('docs'),'docs-id');assert.equal(store.getItem('main'),'main-id');
  docs.activate('docs-id');assert.equal(first.current(),'docs-id');
});
test('first visit to documents keeps its old session; later main page follows canonical active session',()=>{
  const store=storage({main:'main-id',docs:'docs-id'});
  const docs=factory(store,{legacyKeys:legacy.slice().reverse()});assert.equal(docs.current(),'docs-id');
  assert.equal(factory(store).current(),'docs-id');
});
test('new conversation keeps old records and becomes active after navigation or reload',()=>{
  const store=storage({main:'main-id'}),first=factory(store);const id=first.start();
  const second=factory(store);assert.equal(second.current(),id);assert.ok(second.list().some(item=>item.id==='main-id'));
  second.activate('main-id');assert.equal(first.current(),'main-id');
});
test('API origin and proxy path scopes are isolated',()=>{
  const store=storage(),a=factory(store,{legacyKeys:[]});
  const b=factory(store,{scope:'http://localhost:8031',legacyKeys:[],makeId:()=> 'other-id'});
  assert.equal(b.current(),'other-id');assert.notEqual(a.current(),b.current());
  assert.throws(()=>b.activate(a.current()));
  assert.equal(scopeFor('http://localhost:8030/','http://localhost'),'http://localhost:8030');
  assert.equal(scopeFor('http://user:password@localhost:8030/proxy/?token=secret','http://localhost'),'http://localhost:8030/proxy');
});
test('blocked browser storage degrades to a usable page-local session',()=>{
  const broken={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
  const controller=factory(broken);assert.equal(controller.persistent,false);
  assert.equal(controller.current(),'new-1');assert.equal(controller.start(),'new-2');assert.equal(controller.current(),'new-2');
});
test('write-only storage failure cannot revert active session to stale stored data',()=>{
  const store=storage({main:'main-id'}),first=factory(store);
  store.setItem=()=>{throw Error('quota');};
  const next=first.start();assert.equal(first.current(),next);assert.equal(first.persistent,false);
  assert.ok(first.list().some(item=>item.id===next));
});
test('malformed registry and invalid legacy identifiers cannot become session IDs',()=>{
  const controller=factory(storage({'ict8.conversation.sessions.v1':'{broken',main:'bad/key',docs:'x'.repeat(129)}));
  assert.equal(controller.current(),'new-1');assert.equal(controller.list().length,1);
});
test('session capacity never silently deletes the old session list',()=>{
  const controller=factory(storage({main:'main-id'}),{maxSessions:2});controller.start();
  assert.throws(()=>controller.start());assert.equal(controller.list().length,2);
  controller.activate('main-id');assert.equal(controller.current(),'main-id');
});
test('duplicate or invalid generated identifiers are rejected without changing active session',()=>{
  const controller=factory(storage({main:'main-id'}),{makeId:()=> 'main-id'});
  assert.throws(()=>controller.start());assert.equal(controller.current(),'main-id');
});
test('corrupt active record after initialization cannot produce a null or malformed session ID',()=>{
  const store=storage(),controller=factory(store);
  store.setItem('ict8.conversation.sessions.v1',JSON.stringify({version:1,scopes:{'http://localhost:8030':{active:'bad/key',sessions:[]}}}));
  assert.equal(controller.current(),'new-2');assert.equal(controller.list().length,1);
});
test('rapidly created sessions have distinguishable labels even with the same clock value',()=>{
  const controller=factory(storage({main:'main-id'}));controller.start();controller.start();
  const labels=controller.list().map(item=>item.label);assert.equal(new Set(labels).size,labels.length);
});
