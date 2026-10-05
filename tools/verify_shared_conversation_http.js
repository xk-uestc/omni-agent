// Production session registry plus live API; not a browser-click acceptance test.
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const assert=require('node:assert/strict'),{execFileSync}=require('node:child_process');
const ROOT=path.resolve(__dirname,'..');
const sessions=require('../ict-track8/frontend/conversation-session.js');
const names=['frontend/conversation-session.js','frontend/app.js','frontend/knowledge.js','frontend/index.html','frontend/knowledge.html'];
const hash=name=>crypto.createHash('sha256').update(fs.readFileSync(path.join(ROOT,'ict-track8',name))).digest('hex');
const hashes=Object.fromEntries(names.map(name=>[name,hash(name)]));
const id=crypto.randomUUID(),mainId='shared-main-'+id,docsId='shared-docs-'+id;
const values=new Map([['ict8_lattice_session',mainId],['ict8.omni-session',docsId]]);
const storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};
const scope=sessions.scopeFor('http://127.0.0.1:8030','http://127.0.0.1:8030/');
const main=sessions.create({storage,scope,legacyKeys:[{key:'ict8_lattice_session',label:'旧问数'},{key:'ict8.omni-session',label:'旧文档'}]});
const turns=[],checks=[];
function check(label,condition){checks.push({label,passed:!!condition});assert.ok(condition,label);}
async function send(label,endpoint,payload,predicate){
  const response=await fetch('http://127.0.0.1:8030/api/v1/omni/'+endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:AbortSignal.timeout(300000)});
  const body=await response.json(),passed=response.ok&&predicate(body);
  turns.push({label,endpoint,payload,response:body,passed});console.log(JSON.stringify({label,passed,status:body.status}));assert.ok(passed,label);return body;
}
const pending=body=>body.status==='clarification'&&body.result?.clarification_code==='missing_time_grain';
const catalog=ids=>body=>body.route==='tasks'&&body.status==='ok'&&JSON.stringify(body.pending_tasks.map(item=>item.query_reference_id).sort())===JSON.stringify([...ids].sort());
function gold(year,region,metric){
  const sql="SELECT strftime('%Y-%m',order_date) AS 月份,"+(metric==='sales'?'SUM(sales_amount) AS 销售额':'COUNT(order_id) AS 订单数')+' FROM sales_orders WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1';
  const script='import sys,json;sys.path.insert(0,"tools");from verify_pending_task_resume_http import reference;print(json.dumps(reference(sys.argv[1],tuple(json.loads(sys.argv[2]))),ensure_ascii=False))';
  return JSON.parse(execFileSync('C:\\Users\\lenovo\\AppData\\Local\\Programs\\Python\\Python312\\python.exe',['-c',script,sql,JSON.stringify([`${year}-01-01`,`${year+1}-01-01`,region])],{cwd:ROOT,encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'}}));
}
async function run(){
  check('main preserves its legacy identity',main.current()===mainId);
  const a=await send('main pending','query',{session_id:mainId,question:'2025年华东销售额趋势'},pending);
  const b=await send('legacy docs pending','query',{session_id:docsId,question:'2024年华南订单数趋势'},pending);
  const docs=sessions.create({storage,scope,legacyKeys:[{key:'ict8.omni-session',label:'旧文档'},{key:'ict8_lattice_session',label:'旧问数'}]});
  check('docs navigation retains canonical main identity',docs.current()===mainId);
  check('both old identities retained',docs.list().some(item=>item.id===mainId)&&docs.list().some(item=>item.id===docsId));
  await send('docs sees main pending only','query',{session_id:docs.current(),question:'查看待补问题'},catalog([a.query_reference_id]));
  const expectedA=gold(2025,'华东','sales');
  await send('docs completes main pending','clarify',{session_id:docs.current(),original_question:a.effective_question,clarification_code:'missing_time_grain',selected_value:'monthly_trend'},body=>body.status==='ok'&&JSON.stringify(body.result?.rows)===JSON.stringify(expectedA));
  docs.activate(docsId);check('main reads docs selection',main.current()===docsId);
  await send('main sees docs pending only','query',{session_id:main.current(),question:'查看待补问题'},catalog([b.query_reference_id]));
  const expectedB=gold(2024,'华南','count');
  await send('main completes docs pending','query',{session_id:main.current(),question:`继续待补查询编号${b.query_reference_id}，按月统计`},body=>body.status==='ok'&&JSON.stringify(body.result?.rows)===JSON.stringify(expectedB));
  const next=main.start();check('new identity visible to docs',docs.current()===next);
  await send('new conversation has no pending tasks','query',{session_id:docs.current(),question:'查看待补问题'},catalog([]));
  docs.activate(mainId);check('old session still selectable',main.current()===mainId);
  await send('completed task remains closed in old session','query',{session_id:main.current(),question:`继续待补查询编号${a.query_reference_id}`},body=>body.result?.clarification_code==='pending_reference_completed');
  for(const name of names){
    const response=await fetch('http://127.0.0.1:8030/'+name.replace('frontend/',''));
    check('served asset '+name,response.ok&&crypto.createHash('sha256').update(Buffer.from(await response.arrayBuffer())).digest('hex')===hashes[name]);
  }
}
run().catch(error=>{process.exitCode=1;console.error(error.message);}).finally(()=>{
  const stable=names.every(name=>hash(name)===hashes[name]);if(!stable)process.exitCode=1;
  const report=path.join(ROOT,'runtime','shared-conversation-http-'+id+'.json');
  fs.writeFileSync(report,JSON.stringify({not_official_benchmark:true,browser_click_test:false,source_hashes:hashes,source_stable:stable,checks,turns},null,2));console.log(report);
});
