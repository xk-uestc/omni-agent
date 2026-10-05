/* Real HTTP/Edge verification of React tools; isolated DB, no model API. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process'),net=require('node:net');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..'),output=path.join(ROOT,'runtime','tool-timeline-browser-'+crypto.randomUUID());
fs.mkdirSync(output);let browser,server,log,base;const checks=[],errors=[];
async function check(name,fn){try{await fn();checks.push({name,passed:true});console.log(JSON.stringify({name,passed:true}));}
catch(error){checks.push({name,passed:false,error:error.message});console.log(JSON.stringify({name,passed:false,error:error.message}));}}
function assert(ok,message){if(!ok)throw Error(message);}
async function main(){
  const python='C:/Users/lenovo/AppData/Local/Programs/Python/Python312/python.exe';
  execFileSync(python,['-c',"import sys;from pathlib import Path;sys.path.insert(0,'ict-track8');from backend.knowledge_store import KnowledgeStore;from backend.nl2sql.seed import initialize_database;p=Path(sys.argv[1]);KnowledgeStore(p/'knowledge');initialize_database(p/'business.sqlite')",output],{cwd:ROOT});
  const port=await new Promise(resolve=>{const socket=net.createServer();socket.listen(0,'127.0.0.1',()=>{const port=socket.address().port;socket.close(()=>resolve(port));});});
  base=`http://127.0.0.1:${port}`;log=fs.openSync(path.join(output,'server.log'),'w');
  const env={...process.env,ICT8_KNOWLEDGE_ROOT:path.join(output,'knowledge'),ICT8_DB_PATH:path.join(output,'business.sqlite'),
    ICT8_SESSION_DB:path.join(output,'sessions.sqlite'),ICT8_DENSE_MODEL_PATH:'',ICT8_SCHEMA_ALIASES:'',PYTHONIOENCODING:'utf-8'};
  delete env.ICT8_API_KEY;delete env.ICT8_MODEL_API_KEY;delete env.ICT8_OPENAI_API_KEY;
  server=spawn(python,['tools/run_server.py','--port',String(port)],{cwd:ROOT,env,windowsHide:true,stdio:['ignore',log,log]});
  const deadline=Date.now()+45000;
  while(Date.now()<deadline){if(server.exitCode!==null)throw Error('isolated server exited');
    try{if((await fetch(base+'/health',{signal:AbortSignal.timeout(1000)})).ok)break;}catch{}
    await new Promise(resolve=>setTimeout(resolve,200));}
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/?tool-ui-check=20261006');
  await check('local React and tool component loaded',async()=>assert(await page.evaluate(()=>Boolean(window.React&&window.ReactDOM&&window.ToolTimeline)),'React missing'));
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/v1/omni/query/stream'));
  await page.locator('#q').fill('2025年各地区销售额排名');await page.locator('#send').click();
  const response=await responsePromise;await page.locator('#send:not(:disabled)').waitFor({timeout:45000});
  const raw=await response.text();
  fs.writeFileSync(path.join(output,'sql-stream.txt'),raw);
  await check('real SSE running and success before done',async()=>{
    assert(response.ok(),'stream HTTP failed');assert(raw.includes('"status": "running"'),'no actual running event');
    assert(raw.includes('event: done'),'no final snapshot');assert(raw.includes('"tool": "nl2sql"'),'no NL2SQL tool');});
  await check('old numbered placeholders removed',async()=>assert(await page.locator('.query-live,.query-live-rail,.thought').count()===0,'old process rail still exists'));
  await check('tools use SVG icons and real successful database result',async()=>{
    assert(await page.locator('.agent-tool-row[data-tool="nl2sql"][data-status="success"]').count()===1,'SQL call duplicated or missing');
    assert(await page.locator('.agent-tool-row[data-tool="database.read"][data-status="success"]').count()===1,'verified DB read missing');
    assert(await page.locator('.agent-tool-summary>.agent-icon').count()>=2,'tool icons missing');
    assert(await page.locator('.answer table').count()>0,'real result table missing');});
  await check('Codex-style call previews and per-tool feedback icons',async()=>{
    const read=page.locator('.agent-tool-row[data-tool="database.read"]');
    assert((await read.locator('.agent-tool-preview').innerText()).includes('SELECT'),'SQL call preview missing');
    const paths=await read.locator('.agent-action-note>.agent-icon path,.agent-tool-summary>.agent-icon:first-child path').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('d')));
    assert(paths.length===2&&paths[0]===paths[1],'feedback does not use the database tool icon');
    assert(!(await page.locator('#feed').innerText()).includes('01理解问题'),'numbered placeholder remains');});
  await check('SQL feedback has syntax colors and input/output tabs',async()=>{
    const tool=page.locator('.agent-tool-row[data-tool="nl2sql"]');await tool.locator('.agent-tool-summary').click();
    assert(await tool.locator('.agent-tool-code .tok-keyword').count()>0,'SQL syntax tokens absent');
    await tool.getByRole('tab',{name:'输入',exact:true}).click();assert((await tool.locator('.agent-tool-code').innerText()).includes('2025年'),'input not shown');
    await tool.getByRole('tab',{name:'反馈',exact:true}).click();assert((await tool.locator('.agent-tool-code').innerText()).includes('SELECT'),'SQL not shown');});
  await check('schema SVG and result visualization preserved',async()=>{
    const sqlTool=page.locator('.agent-tool-row[data-tool="nl2sql"]');
    const inspect=sqlTool.locator('.agent-tool-attachment>.agent-inspector');await inspect.locator(':scope>summary').click();
    assert(await inspect.locator('.schema-explorer svg').count()>0,'schema SVG lost');
    assert(await page.locator('.agent-tool-row[data-tool="visualization.build"][data-status="success"]').count()===1,'visualization receipt absent');
    await inspect.locator(':scope>summary').click();});
  await check('database structure has its own tool feedback and complete field SVG',async()=>{
    const schema=page.locator('.agent-tool-row[data-tool="database.schema"][data-status="success"]');
    assert(await schema.count()===1,'database structure tool absent or duplicated');
    await schema.locator('.agent-tool-summary').click();
    assert((await schema.locator('.agent-tool-code').innerText()).includes('本步未重新请求数据库'),'cached metadata misrepresented as new execution');
    await schema.locator('.agent-tool-attachment summary').click();
    assert(await schema.locator('.schema-explorer svg').isVisible(),'complete schema SVG is not visible');
    await schema.locator('.agent-tool-summary').click();});
  await check('chart belongs to the visualization tool rather than the answer',async()=>{
    const chart=page.locator('.agent-tool-row[data-tool="visualization.build"]');
    await chart.locator('.agent-tool-summary').click();
    await chart.locator('.agent-tool-attachment summary').click();
    assert(await chart.locator('.query-result-viz').isVisible(),'chart output is not visible');
    assert(await page.locator('.answer .query-result-viz').count()===0,'chart duplicated in the answer');
    await chart.locator('.agent-tool-summary').click();});
  await check('the composer never covers the last answer at the end of the thread',async()=>{
    await page.locator('#thread').evaluate(element=>element.scrollTop=element.scrollHeight);
    const answer=await page.locator('.answer').boundingBox(),composer=await page.locator('#askComposer').boundingBox();
    assert(answer.y+answer.height<=composer.y+2,'last answer obscured by composer');});
  await page.locator('#thread').evaluate(element=>element.scrollTop=0);
  await page.screenshot({path:path.join(output,'light.png')});
  await page.locator('#theme').click();await page.screenshot({path:path.join(output,'dark.png')});
  await check('dark and mobile layouts have no horizontal overflow',async()=>{
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile horizontal overflow');
    await page.screenshot({path:path.join(output,'mobile.png')});});
  await page.setViewportSize({width:1440,height:1100});
  await page.locator('#newChat').click();await page.locator('#q').fill('2025年华东地区的情况');await page.locator('#send').click();
  await page.locator('#send:not(:disabled)').waitFor({timeout:45000});
  await check('clarification never fabricates a successful SQL/write/chart',async()=>{
    assert(await page.locator('.agent-tool-row[data-status="success"][data-tool="database.read"],.agent-tool-row[data-tool="database.write"],.agent-tool-row[data-tool="visualization.build"]').count()===0,'unexecuted tool was marked complete');
    assert(await page.locator('.clarify').count()>0,'clarification choices lost');});
  await page.route('**/api/v1/omni/query/stream',route=>route.fulfill({status:200,contentType:'text/event-stream',body:
    'event: trace\ndata: {"call_id":"fault-test","tool":"nl2sql","status":"running","executed":false}\n\n'+
    'event: error\ndata: {"detail":{"message":"测试连接中断"}}\n\n'}));
  await page.locator('#q').fill('2025年销售额');await page.locator('#send').click();await page.locator('#send:not(:disabled)').waitFor();
  await check('connection error stops running calls instead of claiming success',async()=>{
    assert(await page.locator('.agent-tool-row[data-status="stopped"]').count()>0,'in-flight call not stopped');
    assert((await page.locator('.error-line').innerText()).includes('测试连接中断'),'error feedback missing');});
  await page.unroute('**/api/v1/omni/query/stream');
  await page.goto(base+'/knowledge.html?tool-ui-check=20261006');
  await page.locator('#omni-question').fill('2025年华东地区的销售额');
  await page.locator('#omni button:not([type="button"])').click();
  await page.locator('#omni button:not([type="button"]):not(:disabled)').waitFor({timeout:45000});
  await check('knowledge chat shares the real React tool timeline',async()=>{
    assert(await page.locator('#dialogue .agent-tool-row[data-tool="nl2sql"][data-status="success"]').count()===1,'knowledge tool receipt lost or duplicate');
    assert(await page.locator('#dialogue .agent-tool-summary>.agent-icon').count()>0,'knowledge tool icons absent');});
  await page.locator('#reset-dialogue').click();
  await check('switching knowledge chat unmounts old tools',async()=>assert(await page.locator('#dialogue .agent-timeline').count()===0,'old React timeline survived reset'));
  const amountReport=path.join(ROOT,'docs','FRESH_NATIVE_TOTALS_REPLAY_ROUND33_20261006.json');
  if(fs.existsSync(amountReport)){
    const retained=JSON.parse(fs.readFileSync(amountReport,'utf8')),result=retained.records[0].result;
    const originalPath=execFileSync(python,['-c',"import sys;from pathlib import Path;sys.path.insert(0,'ict-track8');from backend.knowledge_store import KnowledgeStore;s=KnowledgeStore(Path(sys.argv[1])/'knowledge');print(s.verify_source(sys.argv[2],expected_sha256=sys.argv[3]))",
      retained.run_directory,result.answer_scope.document_id,result.citations[0].metadata.source_sha256],{cwd:ROOT,encoding:'utf8'}).trim();
    await check('retained real-model amount tools render as separate successful receipts',async()=>{
      await page.evaluate(data=>{const t=window.ToolTimeline.create();document.getElementById('dialogue').append(t.root);t.finish(data);window.amountCheckTimeline=t;},result);
      assert(await page.locator('[data-tool="document.amount.read"][data-status="success"]').count()===1,'amount tool missing');
      assert(await page.locator('[data-tool="calculate"][data-status="success"]').count()===1,'calculation tool missing');});
    await check('amount label and value map onto the actual SHA-verified PDF render',async()=>{
      const did=result.answer_scope.document_id;
      const ingest=await fetch(base+'/api/v1/knowledge/ingest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({document_id:did,title:'Retained amount original',filename:did+'.pdf',modality:'pdf',file_base64:fs.readFileSync(originalPath).toString('base64')})});
      assert(ingest.ok,'amount original ingestion failed');
      const evidence=await fetch(base+`/api/v1/knowledge/documents/${did}/visual-evidence`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page_no:result.answer_scope.page_no,expected_source_sha256:result.citations[0].metadata.source_sha256})});
      assert(evidence.ok,'original rendered evidence failed');const manifest=await evidence.json();
      const boxes=await page.evaluate(({data,manifest})=>window.NativeRowOverlay.totalModel(data.citations[0].metadata,manifest,data.native_total_proof.selected_annotations),{data:result,manifest});
      assert(boxes.length===2&&boxes[0].role==='subject'&&boxes[1].role==='value','label/value locations missing');
      assert(boxes.every(b=>b.bbox_normalized.every(v=>v>=0&&v<=1)),'amount outside actual rendered original');});
    await page.evaluate(()=>{window.amountCheckTimeline.dispose();delete window.amountCheckTimeline;});
  }
  await check('no React or page runtime errors',async()=>assert(errors.length===0,JSON.stringify(errors)));
}
(async()=>{try{await main();}catch(error){errors.push(error.message);console.log(JSON.stringify({fatal:error.message}));}
finally{await browser?.close();if(server&&server.exitCode===null)server.kill();if(log!==undefined)fs.closeSync(log);
  const report={created_at:new Date().toISOString(),isolated:true,model_calls:0,base,checks,errors,
    passed:errors.length===0&&checks.length>=9&&checks.every(c=>c.passed),output};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
  if(!report.passed)process.exitCode=1;}})();
