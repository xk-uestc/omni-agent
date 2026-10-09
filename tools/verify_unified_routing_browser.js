/* Real HTTP pages, replaying saved live-model receipts; no new model calls. */
const fs=require('node:fs'),path=require('node:path'),{chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..'),reportPath=process.argv[2];
async function main(){
  if(!reportPath)throw Error('Provide a live HTTP report.json');
  const report=JSON.parse(fs.readFileSync(reportPath,'utf8'));
  const record=report.records.find(item=>item.response?.route==='fusion'&&item.response.status==='ok');
  if(!record)throw Error('No completed live fusion receipt');
  const output=path.join(path.dirname(reportPath),'browser');fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const checks=[],errors=[];
  try{
    for(const entry of ['index.html','knowledge.html']){
      const page=await browser.newPage({viewport:{width:1440,height:1100}});
      page.on('pageerror',error=>errors.push({entry,error:error.message}));
      await page.goto(report.base+'/'+entry,{waitUntil:'networkidle'});
      await page.evaluate(record=>{
        if(typeof beginTurn==='function')renderResult(beginTurn(record.question),record.response,record.question);
        else {const box=document.createElement('article');document.getElementById('dialogue').append(box);renderOmni(box,record.question,record.response,sessionId);}
      },record);
      const text=await page.locator('body').innerText();
      checks.push({entry,name:'routing label and winning entity',passed:text.includes('数据与资料联合查询')&&text.includes('东南')});
      checks.push({entry,name:'database result displayed as table',passed:await page.locator(entry==='index.html'?'.answer table':'#dialogue table').count()>0});
      checks.push({entry,name:'retrieved original source links',passed:await page.locator(entry==='index.html'?'.answer a.source-original':'#dialogue a').count()>0});
      checks.push({entry,name:'no missing-answer fallback',passed:!text.includes('本次未返回文字说明')});
      if(record.response.result.answer_status==='insufficient_evidence')
        checks.push({entry,name:'missing entity evidence is explicitly shown',passed:text.includes('暂不能给出')});
      await page.screenshot({path:path.join(output,entry+'.png'),fullPage:true});
      await page.close();
    }
    checks.push({name:'no JavaScript page errors',passed:errors.length===0});
    const result={source_report:reportPath,mode:'saved_live_receipt_ui_replay',checks,errors,
      all_passed:checks.every(item=>item.passed)};
    fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result,null,2));
    if(!result.all_passed)process.exitCode=1;
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error.message);process.exitCode=1;});
