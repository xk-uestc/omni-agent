/* Real Edge browser file-upload and field-confirmation checks; never ingests production fixtures. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const {spawn,execFileSync}=require('node:child_process'),net=require('node:net');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..'),source='D:/ICT8-OfficialDatasets/excel-irregular-20261005/final/samples';
const output=path.join(ROOT,'runtime','excel-preview-browser-'+crypto.randomUUID());fs.mkdirSync(output);
const checks=[],errors=[];let browser,server,serverLog,base='http://127.0.0.1:8030',productionIngest=0;
const isolated=process.argv.includes('--isolated-ingest');
async function startIsolated(){
  const python='C:/Users/lenovo/AppData/Local/Programs/Python/Python312/python.exe';
  execFileSync(python,['-c',"import sys;from pathlib import Path;sys.path.insert(0,'ict-track8');from backend.knowledge_store import KnowledgeStore;from backend.nl2sql.seed import initialize_database;p=Path(sys.argv[1]);KnowledgeStore(p/'knowledge');initialize_database(p/'business.sqlite')",output],{cwd:ROOT});
  const port=await new Promise(resolve=>{const socket=net.createServer();socket.listen(0,'127.0.0.1',()=>{const port=socket.address().port;socket.close(()=>resolve(port));});});
  base=`http://127.0.0.1:${port}`;serverLog=fs.openSync(path.join(output,'server.log'),'w');
  const env={...process.env,ICT8_KNOWLEDGE_ROOT:path.join(output,'knowledge'),ICT8_DB_PATH:path.join(output,'business.sqlite'),
    ICT8_SESSION_DB:path.join(output,'sessions.sqlite'),ICT8_DENSE_MODEL_PATH:'',ICT8_SCHEMA_ALIASES:'',PYTHONIOENCODING:'utf-8'};
  delete env.ICT8_API_KEY;delete env.ICT8_MODEL_API_KEY;
  server=spawn(python,['tools/run_server.py','--port',String(port)],{cwd:ROOT,env,windowsHide:true,stdio:['ignore',serverLog,serverLog]});
  const deadline=Date.now()+45000;
  while(Date.now()<deadline){if(server.exitCode!==null)throw Error('isolated browser server exited');
    try{const response=await fetch(base+'/health',{signal:AbortSignal.timeout(1000)});if(response.ok)return;}catch{}
    await new Promise(resolve=>setTimeout(resolve,200));}
  throw Error('isolated browser server timeout');
}
async function check(name,fn){try{await fn();checks.push({name,passed:true});console.log(JSON.stringify({name,passed:true}));}
  catch(error){checks.push({name,passed:false,error:error.message});console.log(JSON.stringify({name,passed:false,error:error.message}));}}
async function run(){
  if(isolated)await startIsolated();
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:1100}});page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/knowledge.html?excel-browser-check=20261005');
  await page.locator('#excel-reader').waitFor();
  const manifest=JSON.parse(fs.readFileSync(path.join(source,'expected.json'),'utf8'));
  for(const sample of manifest.cases){
    await check(sample.name,async()=>{
      await page.locator('#excel-reader input[type=file]').setInputFiles(path.join(source,sample.file));
      const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/documents/chunks-preview')&&r.request().method()==='POST');
      await page.locator('#excel-reader').getByRole('button',{name:'识别与预览',exact:true}).click();
      const result=await response;if(!result.ok())throw Error('preview HTTP '+result.status());
      await page.locator('#excel-reader').getByRole('button',{name:'按指定表头重新识别',exact:true}).waitFor();
      const body=await page.locator('#excel-reader').innerText();
      for(const record of sample.records){
        for(const header of record.headers){if(!body.includes(header))throw Error('missing field '+header);}
        for(const value of record.values){
          if(value===null)continue;
          let text=String(value);if(sample.name==='12_typed_formats'&&value===.125)text='12.5%';
          if(!body.includes(text))throw Error('missing visible value '+text);
        }
      }
      const coordinate=page.locator('#excel-reader table tbody td[tabindex]').first();
      if(await coordinate.count()){
        await coordinate.click();await page.locator('#excel-reader details[open]').waitFor();
        if(await page.locator('#excel-reader td[data-selected]').count()!==1)throw Error('selected source cell not highlighted');
      }
      if(['03_merged_header','06_side_by_side','20_three_header_levels'].includes(sample.name)){
        await page.locator('#excel-reader').screenshot({path:path.join(output,sample.name+'.png')});
      }
    });
  }
  await check('confirm-unknown-text-header',async()=>{
    await page.locator('#excel-reader input[type=file]').setInputFiles(path.join(source,'10_headerless_text.xlsx'));
    const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/documents/chunks-preview'));
    await page.locator('#excel-reader').getByRole('button',{name:'识别与预览',exact:true}).click();await response;
    await page.locator('#excel-reader input[aria-label="表头层数"]').fill('1');
    const confirmed=page.waitForResponse(r=>r.url().endsWith('/api/v1/documents/chunks-preview'));
    await page.locator('#excel-reader').getByRole('button',{name:'按指定表头重新识别',exact:true}).click();
    const result=await (await confirmed).json();if(result.stats.table_layouts[0].header_decision!=='user_selected')throw Error('header confirmation not applied');
    await page.waitForFunction(()=>document.querySelector('#excel-reader th')&&Array.from(document.querySelectorAll('#excel-reader th')).some(e=>e.textContent==='华东旗舰'));
  });
  if(isolated)await check('browser-ingest-confirmed-layout',async()=>{
    const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/knowledge/ingest'));
    await page.locator('#excel-reader').getByRole('button',{name:'将当前识别结果加入资料库',exact:true}).click();
    const result=await response;if(!result.ok())throw Error('ingest HTTP '+result.status());const record=await result.json();
    if(record.stats.table_layouts[0].header_decision!=='user_selected')throw Error('ingested layout differs from confirmed preview');
    const stored=await (await fetch(base+'/api/v1/knowledge/documents/'+record.document_id)).json();
    if(stored.stats.table_layouts[0].headers[0]!=='华东旗舰')throw Error('persisted header differs');
    await page.waitForFunction(()=>document.querySelector('#documents').textContent.includes('10_headerless_text.xlsx'));
  });
  await check('no-browser-javascript-errors',async()=>{if(errors.length)throw Error(errors.join(';'));});
}
run().catch(error=>{checks.push({name:'browser-startup',passed:false,error:error.message});process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();if(server){server.kill();await new Promise(resolve=>{if(server.exitCode!==null)return resolve();server.once('exit',resolve);});}if(serverLog!==undefined)fs.closeSync(serverLog);
  const report={browser_click_test:true,isolated_server:isolated,production_ingest_requests:productionIngest,passed:checks.filter(c=>c.passed).length,total:checks.length,checks,errors};
  fs.writeFileSync(path.join(output,'browser-report.json'),JSON.stringify(report,null,2));console.log(path.join(output,'browser-report.json'));
  if(report.passed!==report.total)process.exitCode=1;
});
