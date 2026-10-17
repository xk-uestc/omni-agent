const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'knowledge.js'),'utf8');
function harness(){
  const send={disabled:false},controls=[{disabled:false}];
  const context={AbortController,send,controls,$:()=>({querySelector:()=>send}),
    document:{querySelectorAll:()=>controls},request:async()=>({}),results:[],errors:[]};
  vm.createContext(context);
  vm.runInContext("let sessionId='old',conversationController=null;"+source.slice(source.indexOf('async function runConversation('),source.indexOf("$('omni').addEventListener")),context);
  return context;
}
test('conversation requests lock old choices and pass abort signal',async()=>{
  const h=harness();let signal;
  h.request=async(path,payload,value)=>{signal=value;return {ok:true};};
  await vm.runInContext('runConversation("/query",{},data=>results.push(data),error=>errors.push(error))',h);
  assert.ok(signal instanceof AbortSignal);assert.equal(h.controls[0].disabled,true);
  assert.equal(h.send.disabled,false);assert.equal(h.results.length,1);
});
test('switch away and back cannot render an older request or unlock the new one',async()=>{
  const h=harness();h.request=()=>new Promise(resolve=>h.finish=resolve);
  const old=vm.runInContext('runConversation("/query",{},data=>results.push("old"),error=>errors.push(error))',h);
  const finishOld=h.finish;
  vm.runInContext("conversationController.abort();conversationController=null;sessionId='new';sessionId='old';",h);
  const current=vm.runInContext('runConversation("/query",{},data=>results.push("new"),error=>errors.push(error))',h);
  const finishNew=h.finish;finishOld({});await old;
  assert.equal(h.results.length,0);assert.equal(h.send.disabled,true);
  finishNew({});await current;assert.deepEqual(Array.from(h.results),['new']);assert.equal(h.send.disabled,false);
});
test('a second conversation request does not race the first request',async()=>{
  const h=harness();let calls=0,finish;h.request=()=>{calls++;return new Promise(resolve=>finish=resolve);};
  const first=vm.runInContext('runConversation("/query",{},()=>{},()=>{})',h);
  await vm.runInContext('runConversation("/clarify",{},()=>{},()=>{})',h);
  assert.equal(calls,1);finish({});await first;
});
test('source conflict recovery clears the selected file and restores input without submitting',()=>{
  const events=[],input={value:'',focus:()=>events.push('focus'),scrollIntoView:()=>events.push('scroll')};
  const context={selectDocument:id=>events.push(['select',id]),$:id=>{assert.equal(id,'omni-question');return input;},
    request:()=>assert.fail('Recovery must not spend a model call'),fetch:()=>assert.fail('Recovery must not submit')};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function prepareUnscopedQuestion('),source.indexOf('function updateTaskScopeHint(')),context);
  context.prepareUnscopedQuestion('根据查询编号 q_abc，继续分析。');
  assert.equal(input.value,'根据查询编号 q_abc，继续分析。');
  assert.deepEqual(events,[['select',''],'focus','scroll']);
});
