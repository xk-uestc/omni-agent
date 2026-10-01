const $=id=>document.getElementById(id);
const base=location.origin;
let sessionId='omni-'+crypto.randomUUID();
$('reset-dialogue').addEventListener('click',()=>{sessionId='omni-'+crypto.randomUUID();$('dialogue').replaceChildren(element('p','已开始新对话，不继承之前的条件。','muted'));});
$('omni').addEventListener('submit',async event=>{event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;const question=$('omni-question').value;const box=element('article',null,'citation');box.append(element('b',question),element('p','正在处理…'));$('dialogue').append(box);try{const data=await request('/api/v1/omni/query',{question,session_id:sessionId});box.replaceChildren(element('b',question),element('small',`${data.route} · ${data.planner_source} · 已继承${data.context_turns}轮上下文`));const r=data.result;box.append(element('p',`独立问题：${data.effective_question}`,'muted'));if(r.answer)box.append(element('p',r.answer));if(r.rows)box.append(element('pre',JSON.stringify(r.rows,null,2)));if(r.clarification)box.append(element('p',r.clarification));if(r.results){const final=Object.values(r.results).at(-1);box.append(element('pre',JSON.stringify(final,null,2)));}if(r.error)box.append(element('p',r.error,'error'));const details=element('details');details.append(element('summary','查看真实工具步骤、SQL、引用和状态'),element('pre',JSON.stringify(data,null,2)));box.append(details);$('omni-question').value='';}catch(error){showError(box,error);}finally{button.disabled=false;}});
function element(tag,text,className){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(className)e.className=className;return e;}
async function request(path,payload){const r=await fetch(base+path,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;}
function originalLink(documentId,label){const a=element('a',label);a.href=`/api/v1/knowledge/documents/${encodeURIComponent(documentId)}/original`;a.target='_blank';a.rel='noopener';return a;}
async function refresh(){const data=await request('/api/v1/knowledge/documents');$('documents').replaceChildren();data.documents.forEach(doc=>{const item=element('div',null,'doc');item.append(element('b',doc.title),element('span',doc.modality.toUpperCase(),'tag'),element('small',` · ${doc.chunk_count} 个片段 · `),originalLink(doc.document_id,'原文件'));if(doc.warnings.length)item.append(element('p',doc.warnings.join('；'),'muted'));$('documents').append(item);});$('status').textContent=`${data.documents.length} 份资料 · 本项目独立知识库`;}
function showError(host,error){host.replaceChildren(element('p',error.message,'error'));}
$('upload').addEventListener('submit',async event=>{event.preventDefault();const file=$('file').files[0];if(!file)return;const button=event.target.querySelector('button');button.disabled=true;try{if(file.size>20*1024*1024)throw Error('单个文件不能超过20 MiB');const ext=file.name.split('.').pop().toLowerCase();const modality=['png','jpg','jpeg','webp'].includes(ext)?'image':ext;const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));await request('/api/v1/knowledge/ingest',{document_id:'upload-'+crypto.randomUUID(),title:$('title').value||file.name,filename:file.name,modality,file_base64:btoa(binary),language:'chi_sim+eng'});await refresh();event.target.reset();}catch(e){$('status').textContent=e.message;}finally{button.disabled=false;}});
$('ask').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
  $('answer').replaceChildren(element('p','检索中…'));
  try{
    const data=await request('/api/v1/knowledge/query',{question:$('question').value,top_k:4});
    $('answer').replaceChildren(element('small',`检索：${data.retrieval.mode} · 回答：${data.answer_mode}`,'muted'));
    if(data.answer)$('answer').append(element('p',data.answer));
    data.citations.forEach(hit=>{
      const box=element('article',null,'citation');
      box.append(element('b',`[${hit.citation_id}] ${hit.title}`),element('pre',hit.snippet),element('small',`${hit.metadata.source_locator} · ${hit.metadata.retrieval_channel} · BM25 ${hit.metadata.bm25_raw??'—'} · Dense ${hit.metadata.dense_cosine??'—'} · `),originalLink(hit.metadata.document_id,'查看原文件'));
      $('answer').append(box);
    });
  }catch(e){showError($('answer'),e);}finally{button.disabled=false;}
});
const ref=(node,path=[])=>({ref:node,path});const task=(id,tool,args)=>({id,tool,args});
function buildPlan(){const region=$('region').value,year=Number($('year').value),mode=$('workflow').value;
if(mode==='formula')return[task('definition','document_formula',{document_id:'metric-definitions',label:'客单价'}),task('values','sql',{question:`${year}年${region}地区销售额和订单数`}),task('result','calculate',{formula:ref('definition'),parameters:{销售额:ref('values',['rows',0,'销售额']),订单数:ref('values',['rows',0,'订单数'])}})];
if(mode==='forecast')return[task('definition','document_formula',{document_id:'forecast-report',label:'目标销售额'}),task('base','sql',{question:`${year}年${region}地区销售额`}),task('growth','document_cell',{document_id:'region-targets',where:{地区:region,年份:year+1},column:'目标增长率'}),task('result','calculate',{formula:ref('definition'),parameters:{基准销售额:ref('base',['rows',0,'销售额']),目标增长率:ref('growth')}})];
if(mode==='policy')return[task('previous','policy_select',{document_id:'policy-history',as_of:'2024-12-31',label:'无理由退货期限'}),task('current','policy_select',{document_id:'policy-history',as_of:'2025-01-01',label:'无理由退货期限'}),task('comparison','compare',{left:ref('previous'),right:ref('current')})];
if(mode==='doc_sql')return[task('region','document_cell',{document_id:'region-targets',where:{地区:region,年份:2026},column:'地区'}),task('result','sql',{question:[`${year}年`,ref('region',['value']),'地区销售额']})];
if(mode==='sql_doc')return[task('ranking','sql',{question:`${year}年各地区销售额排名`}),task('result','search',{query:[ref('ranking',['rows',0,'region']),'销售冠军经验']})];
return[task('policy','search',{query:'紧急工单首次响应时间'}),task('standard','document_cell',{document_id:'service-thresholds',where:{工单优先级:'紧急',适用版本:'2025'},column:'首次响应小时'})];}
$('execute').addEventListener('click',async()=>{
  $('execute').disabled=true;$('fusion').replaceChildren(element('p','工具执行中…'));
  try{
    const data=await request('/api/v1/fusion/execute',{tasks:buildPlan()});$('fusion').replaceChildren();
    const steps=element('div',null,'steps');
    data.trace.forEach(event=>steps.append(element('div',`${event.task_id} · ${event.status==='complete'?'完成':'失败'} · ${event.latency_ms}ms`,'step')));
    $('fusion').append(steps);
    if(data.status!=='ok')$('fusion').append(element('p',`计算未完成：${data.error}。中间结果仅供审计。`,'error'));
    const final=data.status==='ok'?Object.values(data.results).at(-1):null;
    if(final?.value!=null)$('fusion').append(element('p',`${final.value}${final.result_unit?' '+final.result_unit:''}`,'value'));
    if(final?.left&&final?.right)$('fusion').append(element('p',`${final.left.value} → ${final.right.value} · ${final.status==='equal'?'未变化':'已变化'}`,'value'));
    if(final?.rows)$('fusion').append(element('pre',JSON.stringify(final.rows,null,2)));
    if(final?.hits)final.hits.forEach(hit=>{const box=element('article',null,'citation');box.append(element('b',hit.title),element('pre',hit.snippet),originalLink(hit.metadata.document_id,'原文'));$('fusion').append(box);});
    const detail=element('details');detail.append(element('summary','核对计算输入、SQL、来源及依赖关系'),element('pre',JSON.stringify(data,null,2)));$('fusion').append(detail);
  }catch(e){showError($('fusion'),e);}finally{$('execute').disabled=false;}
});
refresh().catch(e=>{$('status').textContent=e.message;});
