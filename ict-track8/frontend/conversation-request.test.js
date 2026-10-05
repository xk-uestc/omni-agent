const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'app.js'),'utf8');
function harness(){
  const controls=[{disabled:false},{disabled:false}],send={disabled:false};
  const sandbox={AbortController,controls,send,document:{querySelectorAll:()=>controls},
    $:()=>send,updateContextComposer:()=>{},beginTurn:()=>({}),renderResult:()=>{},
    renderTurns:()=>{},el:()=>({}),post:async()=>({})};
  vm.createContext(sandbox);
  vm.runInContext('let busy=false,activeController=null;'+source.slice(source.indexOf('async function run('),source.indexOf('function ask(')),sandbox);
  return sandbox;
}
test('previous choices are locked before the next request is sent',async()=>{
  const h=harness();let finish;
  const task=vm.runInContext('run("追问",()=>new Promise(resolve=>finishRequest=resolve))',h);
  assert.ok(h.controls.every(c=>c.disabled));assert.equal(h.send.disabled,true);
  h.finishRequest({});await task;assert.equal(h.send.disabled,false);
});
test('an aborted previous chat cannot unlock a new in-flight request',async()=>{
  const h=harness();
  const previous=vm.runInContext('run("旧问题",()=>new Promise(resolve=>finishOld=resolve))',h);
  vm.runInContext('activeController.abort();activeController=null;busy=false;',h);
  const current=vm.runInContext('run("新问题",()=>new Promise(resolve=>finishNew=resolve))',h);
  h.finishOld({});await previous;assert.equal(h.send.disabled,true);
  assert.equal(vm.runInContext('busy',h),true);
  h.finishNew({});await current;assert.equal(h.send.disabled,false);
});
test('independent question sends reset_context while preserving the current session',async()=>{
  let body;
  const h={sessionId:'current-session',independentNext:true,$:()=>({checked:true}),
    run:async(q,request)=>request({},undefined),post:async(path,payload)=>{body=payload;}};
  vm.createContext(h);
  vm.runInContext(source.slice(source.indexOf('function ask('),source.indexOf('function clarify(')),h);
  await h.ask('2024年订单数');
  assert.equal(body.reset_context,true);assert.equal(body.session_id,'current-session');
  h.independentNext=false;await h.ask('那华南呢');assert.equal(body.reset_context,false);
});
