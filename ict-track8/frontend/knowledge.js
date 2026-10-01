const $=id=>document.getElementById(id);
const base=location.origin;
let apiToken='',visualBlobUrl=null,visualController=null,visualRevision=0,visualDocument=null,visualDisplayedPage=null,visualRenderSha=null;
const documentCatalog=new Map();
async function authFetch(path,options={}){
  const url=new URL(path,base);
  if(url.origin!==base||!url.pathname.startsWith('/api/v1/'))throw Error('只允许当前项目的资料接口');
  const headers=new Headers(options.headers||{});if(apiToken)headers.set('Authorization','Bearer '+apiToken);
  return fetch(url,{...options,headers,credentials:'same-origin'});
}
$('api-authorize').addEventListener('submit',event=>{
  event.preventDefault();apiToken=$('api-token').value.trim();$('api-token').value='';
  $('api-auth-status').textContent=apiToken?'已设置当前页面访问凭据；刷新或关闭后清除。':'已清除当前页面访问凭据。';
  refresh().catch(error=>{$('status').textContent=error.message;});
});
function rememberedSession(){try{return sessionStorage.getItem('ict8.omni-session');}catch{return null;}}
function retainSession(){try{sessionStorage.setItem('ict8.omni-session',sessionId);}catch{/* Queries still work when browser storage is disabled. */}}
const savedSession=rememberedSession();
let sessionId=savedSession&&/^[A-Za-z0-9._:-]{1,128}$/.test(savedSession)?savedSession:'omni-'+crypto.randomUUID();
retainSession();
$('reset-dialogue').addEventListener('click',()=>{sessionId='omni-'+crypto.randomUUID();retainSession();$('dialogue').replaceChildren(element('p','已开始新对话，不继承之前的条件。','muted'));});
$('omni').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
  const question=$('omni-question').value, activeSession=sessionId;
  const box=element('article',null,'citation');box.append(element('b',question),element('p','正在处理…'));$('dialogue').append(box);
  try{
    const data=await request('/api/v1/omni/query',{question,session_id:activeSession});
    if(sessionId===activeSession){renderOmni(box,question,data,activeSession);$('omni-question').value='';}
  }catch(error){if(sessionId===activeSession)showError(box,error);}finally{button.disabled=false;}
});
function renderOmni(box,question,data,activeSession){
  box.replaceChildren(element('b',question),element('small',`${data.route} · ${data.planner_source} · 已继承${data.context_turns}轮上下文`));
  const r=data.result;box.append(element('p',`独立问题：${data.effective_question}`,'muted'));
  if(r.answer)box.append(element('p',r.answer));
  if(r.rows?.length)box.append(element('pre',JSON.stringify(r.rows,null,2)));
  if(r.clarification)box.append(element('p',r.clarification));
  if(r.status==='clarification'){
    const choices=element('div',null,'toolbar');choices.setAttribute('aria-label','澄清选项');
    async function confirmSelection(option,time){
        if(sessionId!==activeSession)return;
        choices.querySelectorAll('button').forEach(button=>button.disabled=true);
        try{
          const result=await request('/api/v1/omni/clarify',{
            original_question:data.effective_question,clarification_code:r.clarification_code,
            selected_value:option.value,selected_time:time,session_id:activeSession});
          if(sessionId!==activeSession)return;
          const answer=element('article',null,'citation');$('dialogue').append(answer);
          renderOmni(answer,`选择：${option.label||option.value}`,result,activeSession);
        }catch(error){
          if(sessionId===activeSession){box.append(element('p',error.message,'error'));choices.querySelectorAll('button').forEach(button=>button.disabled=false);}
        }
    }
    (r.clarification_options||[]).forEach(option=>{
      const choice=element('button',option.label||option.value);choice.type='button';
      choice.addEventListener('click',()=>{
        if(['year','month','time_range'].includes(option.value)){
          choices.querySelector('form')?.remove();
          const editor=element('form',null,'toolbar'),input=element('input'),submit=element('button','确认时间');
          input.type='text';input.required=true;input.setAttribute('aria-label','澄清时间');
          input.placeholder=option.value==='month'?'例如 2025-03':'例如 2025';input.maxLength=32;
          submit.type='submit';editor.append(input,submit);choices.append(editor);input.focus();
          editor.addEventListener('submit',event=>{event.preventDefault();confirmSelection(option,input.value);});
        }else confirmSelection(option);
      });choices.append(choice);
    });
    if(choices.childElementCount)box.append(choices);
    else box.append(element('p','请在输入框补充具体时间、字段或统计口径。','muted'));
  }
  if(r.results){
    if(r.status==='ok')box.append(element('pre',JSON.stringify(Object.values(r.results).at(-1),null,2)));
    else box.append(element('p','计算尚未完成，中间结果仅保留于审计详情。','muted'));
  }
  if(r.error)box.append(element('p',r.error,'error'));
  if(r.citations?.length){const sources=element('div',null,'toolbar');r.citations.forEach(hit=>addVisualAction(sources,hit.metadata?.document_id,hit.metadata?.page_no,hit.metadata?.source_sha256));if(sources.childElementCount)box.append(sources);}
  const details=element('details');details.append(element('summary','查看真实工具步骤、SQL、引用和状态'),element('pre',JSON.stringify(data,null,2)));box.append(details);
}
function element(tag,text,className){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(className)e.className=className;return e;}
async function responseError(response){let data;try{data=await response.json();}catch{return Error(`服务请求失败（HTTP ${response.status}）`);}return Error(response.status===401?'需要服务访问授权，请在页首填写当前服务的访问令牌。':typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));}
async function request(path,payload){const r=await authFetch(path,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined});if(!r.ok)throw await responseError(r);return r.json();}
function originalLink(documentId,label){const a=element('a',label);a.href=`/api/v1/knowledge/documents/${encodeURIComponent(documentId)}/original`;a.target='_blank';a.rel='noopener';return a;}
async function refresh(){const data=await request('/api/v1/knowledge/documents');$('documents').replaceChildren();documentCatalog.clear();data.documents.forEach(doc=>{documentCatalog.set(doc.document_id,doc);const item=element('div',null,'doc');item.append(element('b',doc.title),element('span',doc.modality.toUpperCase(),'tag'),element('small',` · ${doc.chunk_count} 个片段 · `),originalLink(doc.document_id,'原文件'));addVisualAction(item,doc.document_id,1,doc.sha256,doc.modality);if(doc.warnings.length)item.append(element('p',doc.warnings.join('；'),'muted'));const quality=doc.analysis?.metrics?.text_quality;if(quality&&(quality.changed_lines||quality.typo_candidate_count))item.append(element('p',`文字质量：繁简 ${quality.changed_lines} 行 · 疑似错字 ${quality.typo_candidate_count} 处（请核对原文）`,'muted'));$('documents').append(item);});$('status').textContent=`${data.documents.length} 份资料 · 本项目独立知识库`;}

function addVisualAction(host,documentId,pageNo,sourceSha,modality){
  const record=documentCatalog.get(documentId);
  if((modality||record?.modality)!=='pdf'||!Number.isInteger(pageNo)||pageNo<1||!/^[a-f0-9]{64}$/.test(sourceSha||''))return;
  const button=element('button','查看页图证据','visual-action');button.type='button';
  button.addEventListener('click',()=>{
    visualDocument={documentId,sourceSha,title:record?.title||documentId,pageCount:record?.stats?.page_count};
    $('visual-title').textContent=visualDocument.title;$('visual-page').value=pageNo;
    $('visual-page').max=visualDocument.pageCount||1000;
    if(!$('visual-dialog').open)$('visual-dialog').showModal();
    loadVisualPage(pageNo);
  });host.append(button);
}
function releaseVisualImage(){
  $('visual-image').removeAttribute('src');$('visual-image').hidden=true;
  if(visualBlobUrl){URL.revokeObjectURL(visualBlobUrl);visualBlobUrl=null;}
}
function closeVisual(){visualRevision++;visualController?.abort();visualController=null;releaseVisualImage();visualDocument=null;visualDisplayedPage=null;visualRenderSha=null;$('visual-table-submit').disabled=true;$('visual-table-result').replaceChildren();}
$('visual-dialog').addEventListener('close',closeVisual);
$('visual-close').addEventListener('click',()=>$('visual-dialog').close());
$('visual-page-form').addEventListener('submit',event=>{event.preventDefault();loadVisualPage(Number($('visual-page').value));});
window.addEventListener('pagehide',closeVisual);
async function loadVisualPage(pageNo){
  if(!visualDocument)return;
  const snapshot={...visualDocument},revision=++visualRevision;
  visualController?.abort();visualController=new AbortController();const signal=visualController.signal;
  visualDisplayedPage=null;visualRenderSha=null;$('visual-table-submit').disabled=true;$('visual-table-result').replaceChildren();
  releaseVisualImage();$('visual-summary').textContent='正在核对原文件并生成页图…';$('visual-detail').textContent='';
  try{
    if(!Number.isInteger(pageNo)||pageNo<1||pageNo>(snapshot.pageCount||1000))throw Error('请输入有效页码');
    const response=await authFetch(`/api/v1/knowledge/documents/${encodeURIComponent(snapshot.documentId)}/visual-evidence`,{
      method:'POST',headers:{'Content-Type':'application/json'},signal,
      body:JSON.stringify({page_no:pageNo,expected_source_sha256:snapshot.sourceSha})});
    if(!response.ok)throw await responseError(response);const manifest=await response.json();
    if(manifest.document_id!==snapshot.documentId||manifest.page_no!==pageNo||manifest.source_sha256!==snapshot.sourceSha||!/^[a-f0-9]{64}$/.test(manifest.render_sha256||''))throw Error('页图来源与本次资料不一致');
    const pngUri=new URL(manifest.png_uri,base);
    if(pngUri.origin!==base||pngUri.pathname!==`/api/v1/knowledge/documents/${encodeURIComponent(snapshot.documentId)}/pages/${pageNo}/visual.png`||pngUri.searchParams.get('source_sha256')!==snapshot.sourceSha||pngUri.searchParams.get('render_sha256')!==manifest.render_sha256)throw Error('页图地址未绑定当前来源');
    const png=await authFetch(pngUri.href,{signal});if(!png.ok)throw await responseError(png);
    if(!png.headers.get('Content-Type')?.startsWith('image/png'))throw Error('服务未返回PNG页图');
    const blob=await png.blob();if(blob.size>12*1024*1024)throw Error('页图超过显示预算');
    const digest=await crypto.subtle.digest('SHA-256',await blob.arrayBuffer());
    const sha=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,'0')).join('');
    if(sha!==manifest.render_sha256)throw Error('页图哈希核验失败，请重新加载');
    if(revision!==visualRevision||signal.aborted||!$('visual-dialog').open)return;
    visualBlobUrl=URL.createObjectURL(blob);$('visual-image').src=visualBlobUrl;
    visualDisplayedPage=pageNo;visualRenderSha=manifest.render_sha256;$('visual-table-submit').disabled=false;
    $('visual-image').alt=`${snapshot.title} · 原PDF第${pageNo}页`;$('visual-image').hidden=false;
    $('visual-summary').textContent=`第 ${pageNo} / ${manifest.page_count} 页 · ${manifest.size_px.join(' × ')} 像素 · 原文件 SHA-256 ${snapshot.sourceSha.slice(0,16)}…`;
    $('visual-detail').textContent=JSON.stringify({document_id:manifest.document_id,page_no:manifest.page_no,source_sha256:manifest.source_sha256,render_sha256:manifest.render_sha256,renderer:manifest.renderer,rotation_degrees:manifest.rotation_degrees,coordinate_frame:manifest.coordinate_frame,warnings:manifest.warnings},null,2);
  }catch(error){if(revision===visualRevision&&!signal.aborted)$('visual-summary').textContent=error.message;}
}
const visualTableReasons={
  no_verified_native_grid:'本页未检测到可核验的有线原生表格。无边框表格、扫描件和图表暂不能走此单元格链路。',
  table_selection_budget_exceeded:'本页表格过多或过大，超出本轮有界处理范围。请缩小资料范围。',
  single_cell_lookup_requires_explicit_scope:'目前只支持读取一个明确单元格，暂不支持合计、比较、差值或排除条件。请提供完整行名和列名。',
  single_cell_question_has_unbound_terms:'请明确单个行名和完整列名；带条件或计算的问题需进一步处理，不能直接按单值回答。',
  cell_scope_ambiguous_or_not_explicit:'行或列范围不明确，或匹配多个单元格。请在问题中写出唯一行名及完整列标题（含年份、指标）。',
  cell_period_scope_mismatch:'问题中的年份与所选列不一致，请明确完整的年份和列标题。',
  visual_selection_abstained:'模型未能可靠选定本页单元格，当前不会给出推测数值。',
  visual_selection_not_bound:'视觉选择未能绑定到原表格单元格，当前不会给出推测数值。',
    visual_selection_source_scope_mismatch:'视觉选择与原问题指定的行列不一致，已停止输出数值。',
    visible_cell_ocr_conflict:'可见页图与PDF文字层的行、列或数值不一致，已停止输出，请查看原页。',
    visible_cell_ocr_unverified:'选中单元格的独立OCR未达到核验要求，当前不输出推测数值。'
};
$('visual-table-form').addEventListener('submit',async event=>{
  event.preventDefault();const host=$('visual-table-result'),button=$('visual-table-submit');
  if(!visualDocument||visualDisplayedPage===null)return;
  if(Number($('visual-page').value)!==visualDisplayedPage){host.replaceChildren(element('p','页码已修改，请先点击“查看此页”，再对显示的页面问数。','error'));return;}
  const snapshot={...visualDocument,pageNo:visualDisplayedPage,renderSha:visualRenderSha},question=$('visual-table-question').value.trim();
  const revision=++visualRevision;visualController?.abort();visualController=new AbortController();const signal=visualController.signal;
  button.disabled=true;host.replaceChildren(element('p',`正在核对第 ${snapshot.pageNo} 页的表格行列…`));
  try{
    const response=await authFetch(`/api/v1/knowledge/documents/${encodeURIComponent(snapshot.documentId)}/visual-table-query`,{
      method:'POST',headers:{'Content-Type':'application/json'},signal,
      body:JSON.stringify({question,page_no:snapshot.pageNo,expected_source_sha256:snapshot.sourceSha})});
    if(!response.ok)throw await responseError(response);const data=await response.json();
    if(data.document_id!==snapshot.documentId||data.source_sha256!==snapshot.sourceSha||data.page_no!==snapshot.pageNo||data.render_sha256!==snapshot.renderSha)throw Error('问数结果与当前原文件或显示页图不一致，请重新加载');
    if(revision!==visualRevision||signal.aborted||!$('visual-dialog').open)return;
    host.replaceChildren();
    if(data.status==='ok'){
      host.append(element('b',data.answer||'已定位单元格'));
      host.append(element('p',`第 ${data.page_no} 页 · 数值来源：${data.value_source==='server_extracted_literal_grid_cell'?'服务端提取的原表格单元格':'未识别的来源'}。`,'muted'));
    }else if(data.status==='incomplete'){
      host.append(element('p',visualTableReasons[data.clarification_code]||'本页证据不足以核验所问单元格，请补充明确的行、列与年份。','error'));
    }else throw Error('服务返回了未知问数状态');
      host.append(element('p',data.visible_cell_verification?.status==='corroborated'
        ? '原生网格与选中行头、列头和数值的独立OCR一致。OCR一致不等于事实真实性；单位尚未绑定，当前不开放计算输入。'
        : '核验边界：有线原生网格布局与明确行列选择。未完成可见像素OCR对照，不等于图表识别或事实真实性。当前不开放计算输入。','muted'));
    const details=element('details');details.append(element('summary','核对单元格事实、来源与验证范围'),element('pre',JSON.stringify(data,null,2)));host.append(details);
  }catch(error){if(revision===visualRevision&&!signal.aborted)showError(host,error);}
  finally{if(revision===visualRevision&&!signal.aborted&&$('visual-dialog').open)button.disabled=false;}
});
const qualityLabels={page_orientation_detected:'检测到颠倒或侧转，请核对原图',page_skew_detected:'检测到页面倾斜',page_orientation_undetermined:'文字不足或方向混杂，无法判定页面方向',page_exif_orientation_corrected:'已按EXIF方向信息校正OCR输入'};
function qualityMessage(code){return qualityLabels[code]||code;}
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
      addVisualAction(box,hit.metadata.document_id,hit.metadata.page_no,hit.metadata.source_sha256);
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
return[task('policy','search',{query:'紧急工单首次响应时间'}),task('fact','search_fact',{evidence:ref('policy'),scope:'紧急工单',label:'首次响应',unit:'小时'}),task('standard','document_cell',{document_id:'service-thresholds',where:{工单优先级:'紧急',适用版本:'2025'},column:'首次响应小时'}),task('result','compare',{left:ref('fact'),right:ref('standard'),operator:'le'})];}
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
    if(final?.left&&final?.right){const symbols={eq:'=',ne:'≠',lt:'<',le:'≤',gt:'>',ge:'≥'};const message=final.operator==='le'?`文档要求 ${final.left.value} ${final.unit} ≤ 标准 ${final.right.value} ${final.unit} · ${final.matched?'符合':'不符合'}`:`${final.left.value} ${symbols[final.operator]??'↔'} ${final.right.value} · ${final.status==='equal'?'未变化':'已变化'}`;$('fusion').append(element('p',message,'value'));}
    if(final?.rows)$('fusion').append(element('pre',JSON.stringify(final.rows,null,2)));
    if(final?.hits)final.hits.forEach(hit=>{const box=element('article',null,'citation');box.append(element('b',hit.title),element('pre',hit.snippet),originalLink(hit.metadata.document_id,'原文'));addVisualAction(box,hit.metadata.document_id,hit.metadata.page_no,hit.metadata.source_sha256);$('fusion').append(box);});
    const detail=element('details');detail.append(element('summary','核对计算输入、SQL、来源及依赖关系'),element('pre',JSON.stringify(data,null,2)));$('fusion').append(detail);
  }catch(e){showError($('fusion'),e);}finally{$('execute').disabled=false;}
});
refresh().catch(e=>{$('status').textContent=e.message;});

$('text-quality-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
  const source=$('quality-text').value,host=$('text-quality-result');host.replaceChildren(element('p','检查中…'));
  try{
    const report=await request('/api/v1/documents/text-quality',{text:source});
    host.replaceChildren(element('p',`繁简转换涉及 ${report.changed_lines} 行 · 疑似错字 ${report.typo_candidate_count} 处`));
    host.append(element('p','繁简预览（不是原文引用）','muted'),element('pre',report.simplified_preview));
    const options=element('div');
    report.typo_candidates.forEach(item=>{const label=element('label'),check=element('input');check.type='checkbox';check.value=item.id;
      label.append(check,document.createTextNode(`第${item.line_no}行：${item.before} → ${item.after}（需确认）`));options.append(label,element('br'));});
    host.append(options);const preview=element('button','生成已确认的校正预览');preview.type='button';host.append(preview);
    preview.addEventListener('click',async()=>{
      if($('quality-text').value!==source){host.append(element('p','输入已变化，请重新检查。','error'));return;}
      preview.disabled=true;
      try{const result=await request('/api/v1/documents/text-repair',{text:source,source_sha256:report.source_sha256,
        accepted_ids:[...options.querySelectorAll('input:checked')].map(item=>item.value),simplify:true});
        host.querySelector('[data-repair-preview]')?.remove();const box=element('div');box.dataset.repairPreview='true';
        box.append(element('p',result.warning,'muted'),element('pre',result.revised_text));host.append(box);
      }catch(error){host.append(element('p',error.message,'error'));}finally{preview.disabled=false;}
    });
  }catch(error){showError(host,error);}finally{button.disabled=false;}
});

$('scan-quality-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button'),host=$('scan-quality-result'),file=$('scan-file').files[0];
  if(!file)return;button.disabled=true;host.replaceChildren(element('p','本地OCR检测中…'));
  try{
    if(file.size>8*1024*1024)throw Error('检测图片不能超过8 MiB');
    const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
    for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    const source=btoa(binary),data=await request('/api/v1/documents/ocr',{image_base64:source,language:'chi_sim+eng',max_attempts:3});
    const orientation=data.metadata?.orientation,quality=data.metadata?.input_quality;
    host.replaceChildren(element('p',`识别状态：${data.status} · ${data.attempts.length}次有界尝试`));
    if(quality)host.append(element('p',`图像质量估计：${quality.quality_score}；${quality.width} × ${quality.height}。这是图像启发式分数，不是识别准确率。`,'muted'));
    if(orientation?.status==='estimated')host.append(element('p',`方向估计：逆时针${orientation.rotation_ccw_degrees}°；倾斜${orientation.skew_ccw_degrees}°。依据${orientation.eligible_lines}条可靠文字框。`));
    else host.append(element('p','方向无法判定，请核对原图；不会自动给出旋转预览。','muted'));
    if(data.warnings.length)host.append(element('p',data.warnings.map(qualityMessage).join('；'),'muted'));
    host.append(element('pre',data.text||'未识别到可靠文字'));
    if(orientation?.status==='estimated'&&Math.abs(orientation.correction_ccw_degrees)>=2){
      const preview=element('button','生成校正预览'),previewResult=element('div');preview.type='button';host.append(preview,previewResult);
      preview.addEventListener('click',async()=>{
        preview.disabled=true;previewResult.replaceChildren(element('p','正在生成校正预览…'));
        try{
          const corrected=await request('/api/v1/documents/image-enhance',{image_base64:source,transforms:['rotate_to_upright'],rotation_degrees:-orientation.correction_ccw_degrees});
          const figure=element('figure'),image=element('img'),caption=element('figcaption','校正预览：只改变显示方向，原文件与知识库保持原样。请人工核对。');
          image.src='data:image/png;base64,'+corrected.image_base64;image.alt='扫描件方向校正预览';image.style.maxWidth='100%';image.style.maxHeight='380px';
          figure.append(image,caption);previewResult.replaceChildren(figure);preview.remove();
        }catch(error){showError(previewResult,error);preview.disabled=false;}
      });
    }
    const details=element('details');details.append(element('summary','查看检测依据、坐标帧、原图SHA和尝试记录'),element('pre',JSON.stringify(data,null,2)));host.append(details);
  }catch(error){showError(host,error);}finally{button.disabled=false;}
});
