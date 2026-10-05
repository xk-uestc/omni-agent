/* Render a retained, source-replayed model receipt against REAL original PNGs.
 * Model generation is not repeated here: answering is a recorded-response replay.
 * Page manifests/PNG bytes and their hashes are served by the production backend.
 */
const fs=require('node:fs'),path=require('node:path'),net=require('node:net'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..'),python='C:/Users/lenovo/AppData/Local/Programs/Python/Python312/python.exe';
const receiptPath=path.resolve(ROOT,process.argv[2]||'docs/NATIVE_ROW_COMPARISON_PRODUCTION_ROUND24_REPLAY_20261006.json');
const baselinePath=path.join(ROOT,'docs/OHR_ROUND9_FINAL_20261004.json');
const baseline=JSON.parse(fs.readFileSync(baselinePath)),receipt=JSON.parse(fs.readFileSync(receiptPath));
const output=path.join(ROOT,'runtime','native-row-browser-'+crypto.randomUUID());fs.mkdirSync(output);
let browser,server,log;const checks=[],errors=[];
const assert=(ok,message)=>{if(!ok)throw Error(message);};
async function check(name,run){await run();checks.push({name,passed:true});console.log(JSON.stringify({name,passed:true}));}
async function main(){
  assert(receipt.status==='ok'&&receipt.implementation_stable===true,'successful stable model receipt required');
  execFileSync(python,['-c',"import sys,json;sys.path.insert(0,'ict-track8');from backend.knowledge_store import KnowledgeStore;from backend.native_row_comparison import replay_comparison;r=json.load(open(sys.argv[1],encoding='utf-8'));assert replay_comparison(KnowledgeStore(sys.argv[2]),r)",receiptPath,baseline.store_path],{cwd:ROOT});
  checks.push({name:'retained model proof replays against unchanged originals',passed:true});
  const port=await new Promise(resolve=>{const socket=net.createServer();socket.listen(0,'127.0.0.1',()=>{const port=socket.address().port;socket.close(()=>resolve(port));});});
  const base=`http://127.0.0.1:${port}`;
  execFileSync(python,['-c',"import sys;from pathlib import Path;sys.path.insert(0,'ict-track8');from backend.nl2sql.seed import initialize_database;initialize_database(Path(sys.argv[1]))",path.join(output,'business.sqlite')],{cwd:ROOT});
  log=fs.openSync(path.join(output,'server.log'),'w');
  const env={...process.env,ICT8_KNOWLEDGE_ROOT:baseline.store_path,ICT8_DB_PATH:path.join(output,'business.sqlite'),ICT8_SESSION_DB:path.join(output,'sessions.sqlite'),ICT8_DENSE_MODEL_PATH:'',PYTHONIOENCODING:'utf-8'};
  server=spawn(python,['tools/run_server.py','--port',String(port)],{cwd:ROOT,env,windowsHide:true,stdio:['ignore',log,log]});
  const deadline=Date.now()+45000;let ready=false;
  while(Date.now()<deadline){if(server.exitCode!==null)throw Error('isolated server exited');try{if((await fetch(base+'/health',{signal:AbortSignal.timeout(1000)})).ok){ready=true;break;}}catch{}await new Promise(resolve=>setTimeout(resolve,200));}
  assert(ready,'server never became ready');
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});page.on('pageerror',error=>errors.push(error.message));
  const result={...receipt,retrieval:{mode:'recorded_model_result_ui_replay'}};
  const omni={status:'ok',route:'document',question:receipt.question,effective_question:receipt.question,result,trace:receipt.trace,latency_ms:0};
  await page.route('**/api/v1/omni/query/stream',route=>route.fulfill({status:200,contentType:'text/event-stream',body:'event: done\ndata: '+JSON.stringify(omni)+'\n\n'}));
  await page.goto(base+'/');await page.locator('#q').fill(receipt.question);await page.locator('#send').click();await page.locator('#send:not(:disabled)').waitFor();
  await check('native table reading and comparison appear as distinct actual receipts',async()=>{
    assert(await page.locator('.agent-tool-row[data-tool="document.table.read"][data-status="success"]').count()===1,'native read receipt missing');
    assert(await page.locator('.agent-tool-row[data-tool="document.compare"][data-status="success"]').count()===1,'comparison receipt missing');
  });
  for(let i=0;i<2;i++)await check('main page source '+(i+1)+' shows both subject and queried value',async()=>{
    const viewer=page.locator('.source-page-viewer').nth(i);await viewer.locator(':scope>summary').click();
    await viewer.locator('.source-page-stage svg rect').nth(1).waitFor();
    assert(await viewer.locator('rect').count()===2,'two exact field rectangles required');
    assert((await viewer.locator('.schema-caption').innerText()).includes('坐标及页面映射已核对'),'unverified overlay');
    assert(await viewer.locator('svg title').count()===2,'field text labels missing');
    await viewer.screenshot({path:path.join(output,'main-source-'+(i+1)+'.png')});
  });
  await page.goto(base+'/knowledge.html');
  await page.waitForFunction(()=>document.querySelector('#query-document').options.length>1);
  await page.route('**/api/v1/knowledge/query',route=>route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)}));
  await page.locator('#question').fill(receipt.question);await page.locator('#ask button').click();await page.locator('#ask button:not(:disabled)').waitFor();
  await check('direct document query also displays React tool feedback',async()=>{
    assert(await page.locator('#answer .agent-tool-row[data-tool="knowledge.answer"][data-status="success"]').count()===1,'direct query receipt missing');
    assert(await page.locator('#answer .agent-tool-row[data-tool="document.compare"][data-status="success"]').count()===1,'direct comparison receipt missing');
  });
  for(let i=0;i<2;i++)await check('knowledge page source '+(i+1)+' maps exact fields to verified PNG',async()=>{
    await page.locator('#answer .visual-action').nth(i).click();await page.locator('#visual-source-highlight rect').nth(1).waitFor();
    assert(await page.locator('#visual-source-highlight rect').count()===2,'knowledge field overlay missing');
    assert((await page.locator('#visual-summary').innerText()).includes('主体与数值字段已定位'),'field coordinates not verified');
    await page.locator('#visual-dialog').screenshot({path:path.join(output,'knowledge-source-'+(i+1)+'.png')});
    await page.locator('#visual-close').click();
  });
  await check('switching to an unselected page removes stale field highlights',async()=>{
    await page.locator('#answer .visual-action').first().click();await page.locator('#visual-source-highlight rect').nth(1).waitFor();
    await page.locator('#visual-page').fill('2');await page.locator('#visual-page-form button').click();
    await page.waitForFunction(()=>document.querySelector('#visual-summary').textContent.startsWith('第 2 /'));
    assert(await page.locator('#visual-source-highlight rect').count()===0,'old page field locations survived navigation');
  });
  await check('no page or React runtime errors',async()=>assert(errors.length===0,JSON.stringify(errors)));
}
(async()=>{try{await main();}catch(error){errors.push(error.message);console.log(JSON.stringify({fatal:error.message}));}
finally{await browser?.close();if(server&&server.exitCode===null)server.kill();if(log!==undefined)fs.closeSync(log);
  const report={created_at:new Date().toISOString(),scope:'recorded_model_receipt_ui_replay_real_original_page_rendering_not_new_model_accuracy',model_calls:0,
    receipt:receiptPath,receipt_sha256:crypto.createHash('sha256').update(fs.readFileSync(receiptPath)).digest('hex'),checks,errors,output,passed:checks.length===9&&errors.length===0};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));if(!report.passed)process.exitCode=1;}})();
