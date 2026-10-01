const $=id=>document.getElementById(id);
const base=location.origin;
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
  const details=element('details');details.append(element('summary','查看真实工具步骤、SQL、引用和状态'),element('pre',JSON.stringify(data,null,2)));box.append(details);
}
function element(tag,text,className){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(className)e.className=className;return e;}
async function request(path,payload){const r=await fetch(base+path,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined});const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;}
function originalLink(documentId,label){const a=element('a',label);a.href=`/api/v1/knowledge/documents/${encodeURIComponent(documentId)}/original`;a.target='_blank';a.rel='noopener';return a;}
async function refresh(){const data=await request('/api/v1/knowledge/documents');$('documents').replaceChildren();data.documents.forEach(doc=>{const item=element('div',null,'doc');item.append(element('b',doc.title),element('span',doc.modality.toUpperCase(),'tag'),element('small',` · ${doc.chunk_count} 个片段 · `),originalLink(doc.document_id,'原文件'));if(doc.warnings.length)item.append(element('p',doc.warnings.join('；'),'muted'));const quality=doc.analysis?.metrics?.text_quality;if(quality&&(quality.changed_lines||quality.typo_candidate_count))item.append(element('p',`文字质量：繁简 ${quality.changed_lines} 行 · 疑似错字 ${quality.typo_candidate_count} 处（请核对原文）`,'muted'));$('documents').append(item);});$('status').textContent=`${data.documents.length} 份资料 · 本项目独立知识库`;}
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
    if(final?.hits)final.hits.forEach(hit=>{const box=element('article',null,'citation');box.append(element('b',hit.title),element('pre',hit.snippet),originalLink(hit.metadata.document_id,'原文'));$('fusion').append(box);});
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
