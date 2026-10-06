/* Read-only browser profiling, with no model calls. */
const {chromium}=require('playwright'),fs=require('node:fs'),path=require('node:path');
async function main(){
 const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
 try{
  const page=await browser.newPage();
  await page.addInitScript(()=>{window.profileLongTasks=[];new PerformanceObserver(list=>window.profileLongTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask',buffered:true});});
  await page.goto('http://127.0.0.1:8030/',{waitUntil:'networkidle'});
  const load=await page.evaluate(()=>({navigation:performance.getEntriesByType('navigation').map(e=>({domReady:e.domContentLoadedEventEnd,load:e.loadEventEnd,transfer:e.transferSize})),resources:performance.getEntriesByType('resource').filter(e=>e.duration>30).map(e=>({name:new URL(e.name).pathname,duration:e.duration,bytes:e.transferSize})),longTasks:window.profileLongTasks}));
  const render=await page.evaluate(async()=>{
   const output={records:Array.from({length:1500},(_,i)=>({id:i,name:'原文来源条目 '+i,value:i,details:'Source evidence context '.repeat(20)}))};
   const original=JSON.stringify,tokenizer=window.LatticeSyntaxHighlight?.tokenize;
   let stringifyCalls=0,tokenizeCalls=0;
   JSON.stringify=function(...args){stringifyCalls++;return original.apply(this,args);};
   if(tokenizer)window.LatticeSyntaxHighlight.tokenize=function(...args){tokenizeCalls++;return tokenizer.apply(this,args);};
   const start=performance.now(),t=window.ToolTimeline.create();document.getElementById('feed').append(t.root);
   for(let i=0;i<25;i++)t.record({call_id:'profile-'+i,tool:'document.read',status:'success',executed:true,input:{question:'Profile only'},output});
   await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
   const result={elapsed:performance.now()-start,nodes:t.root.querySelectorAll('*').length,stringifyCalls,tokenizeCalls,closedCodePanels:t.root.querySelectorAll('details:not([open]) pre').length};
   const first=t.root.querySelector('details');first.open=true;first.dispatchEvent(new Event('toggle'));await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
   result.openCodePanels=t.root.querySelectorAll('details[open] pre').length;
   result.openFeedbackAvailable=first.innerText.includes('原文来源条目');
   t.dispose();JSON.stringify=original;if(tokenizer)window.LatticeSyntaxHighlight.tokenize=tokenizer;
   return result;
  });
  const result={created_at:new Date().toISOString(),model_calls:0,load,render};
  const output=process.argv[2];if(output)fs.writeFileSync(path.resolve(output),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
 }finally{await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
