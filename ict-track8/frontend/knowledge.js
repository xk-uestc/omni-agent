const $=id=>document.getElementById(id);
const base=location.origin;
let apiToken='',visualBlobUrl=null,visualController=null,visualRevision=0,visualDocument=null,visualDisplayedPage=null,visualRenderSha=null;
const documentCatalog=new Map();
let excelRevision=0,excelFile=null,excelPayload=null,excelOptions=null,excelController=null;
const excelSection=element('section');excelSection.id='excel-reader';excelSection.style.cssText='max-width:1210px;margin:0 auto 30px';
const excelForm=element('form'),excelInput=element('input'),excelSend=element('button','识别与预览'),excelStatus=element('p',null,'muted'),excelResult=element('div');
excelInput.type='file';excelInput.accept='.xlsx';excelInput.required=true;excelInput.setAttribute('aria-label','不规则Excel文件');excelStatus.setAttribute('aria-live','polite');
excelForm.append(excelInput,excelSend);excelSection.append(element('h2','Excel · 表格识别与单元格核对'),
  element('p','识别分层表头、多个表块、独立单位行与合并关系；点击单元格可看原始坐标。空值、零、公式和合计分别展示。未知表头可以指定区域和层数，0表示无表头。预览不修改原文件。','muted'),excelForm,excelStatus,excelResult);
$('text-quality-form').closest('section').insertAdjacentElement('beforebegin',excelSection);
excelInput.addEventListener('change',()=>{excelRevision++;excelController?.abort();excelController=null;excelPayload=null;excelFile=null;excelOptions=null;excelSend.disabled=false;excelResult.replaceChildren();excelStatus.textContent='';});
function renderExcel(payload){
  const tables=window.ExcelPreview.tables(payload),controls=[];excelResult.replaceChildren();
  excelStatus.textContent=`${payload.stats.visible_sheet_count}个可见工作表 · ${tables.length}个表块 · ${payload.stats.preserved_visible_cells}个原始单元格已保留`;
  for(const table of tables){
    const section=element('div',null,'scan-table-structure');section.append(element('h3',`${table.sheet_name} · ${table.table_range}`),
      element('p',table.needsConfirmation?'表头尚需确认，原始数据均保留。':`已整理${table.header_rows.length}层表头，${table.row_count}行；小计、合计单独标记。`,'muted'));
    const grid=element('table'),head=element('thead'),hr=element('tr');hr.append(element('th','原行号'));table.headers.forEach(h=>hr.append(element('th',h)));head.append(hr);grid.append(head);
    const body=element('tbody');table.rows.forEach(row=>{const tr=element('tr');tr.append(element('td',`${row.row}${row.role==='summary'?' · 合计/小计':''}`));
      row.cells.forEach(cell=>{const td=element('td',cell.label);td.tabIndex=0;td.style.cursor='pointer';td.title=cell.coordinate||'原单元格为空';
        function inspect(){excelResult.querySelectorAll('td[data-selected]').forEach(old=>{old.removeAttribute('data-selected');old.style.background='';old.style.outline='';});
          td.dataset.selected='true';td.style.background='#e9f4ff';td.style.outline='2px solid #4285db';td.style.outlineOffset='-2px';
          excelStatus.textContent=`${table.sheet_name}!${cell.coordinate||'空单元格'} · ${cell.label}`;detail.textContent=JSON.stringify(cell.raw,null,2);details.open=true;}
        td.addEventListener('click',inspect);td.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();inspect();}});tr.append(td);});body.append(tr);});grid.append(body);section.append(grid);
    if(table.hiddenPreviewRows||table.hiddenPreviewColumns)section.append(element('p',`仅预览前12行、20列；另有${table.hiddenPreviewRows}行、${table.hiddenPreviewColumns}列未展开，后端读取结果保留。`,'muted'));
    const settings=element('div',null,'toolbar'),sheet=element('input'),range=element('input'),count=element('input');
    sheet.value=table.sheet_name;sheet.setAttribute('aria-label','工作表名称');range.value=table.table_range;range.setAttribute('aria-label','表区域');count.type='number';count.min='0';count.max='8';count.value=table.header_rows.length;count.setAttribute('aria-label','表头层数');
    settings.append(element('span','工作表'),sheet,element('span','区域'),range,element('span','表头层数'),count);section.append(settings);controls.push({sheet,range,count});excelResult.append(section);
  }
  const details=element('details'),detail=element('pre');details.append(element('summary','查看选中单元格的原值、格式与合并关系'),detail);excelResult.append(details);
  const actions=element('div',null,'toolbar'),confirm=element('button','按指定表头重新识别'),ingest=element('button','将当前识别结果加入资料库');confirm.type=ingest.type='button';confirm.disabled=!controls.length;
  confirm.addEventListener('click',()=>{try{const selected=window.ExcelPreview.selections(controls.map(c=>({sheet_name:c.sheet.value,range:c.range.value,header_rows:c.count.value})));previewExcel(selected);}catch(error){excelStatus.textContent=error.message;}});
  ingest.addEventListener('click',async()=>{if(!excelPayload||!excelFile)return;const revision=excelRevision;ingest.disabled=true;
    try{await request('/api/v1/knowledge/ingest',{...excelPayload,document_id:'excel-'+crypto.randomUUID(),title:excelFile.name,filename:excelFile.name,excel_tables:excelOptions||[]});
      if(revision===excelRevision){excelStatus.textContent='已将当前识别结果加入资料库；原Excel保持不变。';await refresh();}}
    catch(error){if(revision===excelRevision)excelStatus.textContent=error.message;}finally{if(revision===excelRevision)ingest.disabled=false;}});
  actions.append(confirm,ingest);excelResult.append(actions);
  if(payload.warnings.length)excelResult.append(element('p',payload.warnings.map(w=>w.startsWith('header_ambiguous:')?'表头无法明确，请确认区域和表头层数':w.startsWith('formula_cache_')?'公式未重算，缓存不可作为已核验结果':w.startsWith('inherited_header_requires_review:')?'空行后暂沿用前表字段，请核对':w.startsWith('merged_cells:')?'已保留合并单元格及其原始锚点':w.startsWith('hidden_')?'已略过隐藏内容，原文件仍保留':w.startsWith('cell_error:')?'存在Excel错误值，不能作为计算输入':w.startsWith('repeated_header_removed:')?'重复表头已与业务记录区分':w.startsWith('section_header_inferred:')?'已按不同表头拆分连续表块，请核对':w).filter((v,i,a)=>a.indexOf(v)===i).join('；'),'muted'));
}
async function previewExcel(options=null){
  if(!excelPayload)return;excelController?.abort();const controller=new AbortController(),revision=++excelRevision;excelController=controller;excelSend.disabled=true;excelStatus.textContent='正在核对表格与原始单元格…';
  try{const result=await request('/api/v1/documents/chunks-preview',{...excelPayload,excel_tables:options||[]},controller.signal);
    if(revision!==excelRevision)return;excelOptions=options;renderExcel(result);}
  catch(error){if(revision===excelRevision&&!controller.signal.aborted)excelStatus.textContent=error.message;}
  finally{if(revision===excelRevision){excelSend.disabled=false;excelController=null;}}
}
excelForm.addEventListener('submit',async event=>{event.preventDefault();const file=excelInput.files[0];if(!file)return;
  const revision=++excelRevision;excelSend.disabled=true;
  try{if(!file.name.toLowerCase().endsWith('.xlsx'))throw Error('此入口支持.xlsx；旧.xls请另存为.xlsx');if(file.size>20*1024*1024)throw Error('单个文件不能超过20 MiB');
    const bytes=new Uint8Array(await file.arrayBuffer());if(revision!==excelRevision)return;let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    excelFile=file;excelPayload={document_id:'excel-preview-'+crypto.randomUUID(),modality:'xlsx',file_base64:btoa(binary)};await previewExcel();}
  catch(error){if(revision===excelRevision){excelStatus.textContent=error.message;excelSend.disabled=false;}}});
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
const conversationSessions=window.ConversationSessions.create({scope:window.ConversationSessions.scopeFor(base,location.href),
  legacyKeys:[{key:'ict8.omni-session',label:'原文档会话'},{key:'ict8_lattice_session',label:'原问数会话'}]});
let sessionId=conversationSessions.current(),conversationController=null,conversationSessionPicker=null;
const conversationTimelineCleanups=new Set();
function createConversationTimeline(){const timeline=window.ToolTimeline.create();conversationTimelineCleanups.add(timeline.dispose);return timeline;}
function clearConversationTimelines(){for(const dispose of conversationTimelineCleanups)dispose();conversationTimelineCleanups.clear();}
window.addEventListener('pagehide',clearConversationTimelines);
function switchConversation(next){conversationController?.abort();conversationController=null;sessionId=next;
  clearConversationTimelines();
  $('omni').querySelector('button').disabled=false;
  $('dialogue').replaceChildren(element('p','已切换会话；可以继续提问或输入“查看待补问题”。旧会话记录保留。','muted'));conversationSessionPicker?.refresh();}
$('reset-dialogue').addEventListener('click',()=>{try{switchConversation(conversationSessions.start());}catch(error){$('dialogue').append(element('p',error.message,'error'));}});
const conversationSessionHost=element('div');$('omni').insertAdjacentElement('beforebegin',conversationSessionHost);
conversationSessionPicker=window.ConversationSessions.mount(conversationSessions,conversationSessionHost,switchConversation);
window.addEventListener('pageshow',()=>{const current=conversationSessions.current();if(current!==sessionId)switchConversation(current);else conversationSessionPicker.refresh();});
async function runConversation(path,payload,onResult,onError,timeline=null){
  if(conversationController)return;
  const controller=new AbortController(),activeSession=sessionId,send=$('omni').querySelector('button');
  conversationController=controller;send.disabled=true;
  document.querySelectorAll('#dialogue button, #dialogue input').forEach(control=>control.disabled=true);
  try{
    const data=timeline&&path==='/api/v1/omni/query'?
      await streamConversation(payload,controller.signal,timeline):await request(path,payload,controller.signal);
    if(conversationController===controller&&sessionId===activeSession&&!controller.signal.aborted)onResult(data);
  }catch(error){if(conversationController===controller&&sessionId===activeSession&&!controller.signal.aborted)onError(error);}
  finally{if(conversationController===controller){conversationController=null;send.disabled=false;}}
}
async function streamConversation(payload,signal,timeline){
  const response=await authFetch('/api/v1/omni/query/stream',{method:'POST',headers:{'Content-Type':'application/json',Accept:'text/event-stream'},body:JSON.stringify(payload),signal});
  if(!response.ok)throw await responseError(response);
  if(!response.body)throw Error('连接没有返回工具执行事件。');
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',result=null;
  while(true){
    const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done}).replace(/\r\n/g,'\n');
    let boundary;
    while((boundary=buffer.indexOf('\n\n'))>=0){
      const block=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);
      let type='message';const data=[];
      for(const line of block.split('\n')){if(line.startsWith('event:'))type=line.slice(6).trim();if(line.startsWith('data:'))data.push(line.slice(5).trimStart());}
      if(!data.length)continue;const event=JSON.parse(data.join('\n'));
      if(type==='trace')timeline.update(event);if(type==='done')result=event;
      if(type==='error')throw Error(event.detail?.message||'工具执行失败');
    }
    if(done)break;
  }
  if(!result)throw Error('连接结束，但没有返回完整结果。');return result;
}
$('omni').addEventListener('submit',async event=>{
  event.preventDefault();if(conversationController)return;
  const question=$('omni-question').value, activeSession=sessionId;
  const box=element('article',null,'citation'),timeline=createConversationTimeline();
  box.append(element('b',question),timeline.root);$('dialogue').append(box);
  await runConversation('/api/v1/omni/query',{question,session_id:activeSession},data=>{
    renderOmni(box,question,data,activeSession,timeline);$('omni-question').value='';
  },error=>{timeline.fail(error.message);box.append(element('p',error.message,'error'));},timeline);
});
function renderOmni(box,question,data,activeSession,timeline=null){
  timeline=timeline||createConversationTimeline();timeline.finish(data);
  box.replaceChildren(element('b',question),timeline.root);
  const r=data.result;box.append(element('p',`独立问题：${data.effective_question}`,'muted'));
  appendAnswer(box,r);
  if(data.route==='tasks'){
    const list=element('div');
    for(const item of window.ConversationContext.catalogItems(data)){
      const row=element('div');row.style.borderBottom='1px solid #dde3dc';row.style.padding='12px 0';
      row.append(element('b',item.question),element('p',item.clarification,'muted'));
      if(item.resumeQuestion){
        const button=element('button','恢复这个问题');button.type='button';
        button.addEventListener('click',async()=>{
          if(sessionId!==activeSession||button.disabled)return;
          await runConversation('/api/v1/omni/query',{question:item.resumeQuestion,session_id:activeSession},restored=>{
            const answer=element('article',null,'citation');$('dialogue').append(answer);
            renderOmni(answer,'恢复：'+item.question,restored,activeSession);
          },error=>{row.append(element('p',error.message,'error'));button.disabled=false;});
        });row.append(button);
      }else row.append(element('small','当前无法恢复，请重新确认完整问题。','muted'));
      list.append(row);
    }box.append(list);
  }
  if(r.rows?.length)box.append(element('pre',JSON.stringify(r.rows,null,2)));
  if(r.clarification)box.append(element('p',r.clarification));
  if(r.status==='clarification'){
    const choices=element('div',null,'toolbar');choices.setAttribute('aria-label','澄清选项');
    async function confirmSelection(option,time){
        if(sessionId!==activeSession)return;
        if(/^选择对话来源编号q_[a-f0-9]{32}$/.test(option.question||'')){
          await runConversation('/api/v1/omni/query',{question:option.question,session_id:activeSession},result=>{
            const answer=element('article',null,'citation');$('dialogue').append(answer);
            renderOmni(answer,`选择：${option.label||option.value}`,result,activeSession);
          },error=>box.append(element('p',error.message,'error')));return;
        }
        await runConversation('/api/v1/omni/clarify',{
            original_question:data.effective_question,clarification_code:r.clarification_code,
            selected_value:option.value,selected_time:time,session_id:activeSession},result=>{
          const answer=element('article',null,'citation');$('dialogue').append(answer);
          renderOmni(answer,`选择：${option.label||option.value}`,result,activeSession);
        },error=>{box.append(element('p',error.message,'error'));choices.querySelectorAll('button').forEach(button=>button.disabled=false);});
    }
    (r.clarification_options||[]).forEach((option,index)=>{
      const choice=element('button',window.ConversationContext.optionLabel(option,index));choice.type='button';
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
  if(r.citations?.length){const sources=element('div',null,'toolbar');r.citations.forEach(hit=>addVisualAction(sources,hit.metadata?.document_id,hit.metadata?.page_no,hit.metadata?.source_sha256,undefined,r.visual_source_proof?.parts?.[hit.citation_id-1],{metadata:hit.metadata,selection:r.native_row_proof?.selection}));if(sources.childElementCount)box.append(sources);}
  const details=element('details');details.append(element('summary','查看真实工具步骤、SQL、引用和状态'),element('pre',JSON.stringify(data,null,2)));box.append(details);
}
function element(tag,text,className){const e=document.createElement(tag);if(text!=null)e.textContent=String(text);if(className)e.className=className;return e;}
function appendAnswer(host,result){
  if(result.status==='insufficient_evidence'){
    host.append(element('p','资料不足或存在冲突，暂不能给出完整答案。下方内容是相关原文，供核对和补充资料。','error'));
  }
  const projection=result.answer_projection;
  if(projection?.status==='verified'&&typeof projection.answer_value==='string'){
    const card=element('section',null,'citation');
    card.append(element('small','已核对来源关系的精确答案','muted'),element('p',projection.answer_value));
    const labels={entity:'对象',subject:'主体',object:'客体',metric:'指标',field:'字段',role:'角色',year:'年份',years:'年份',conditions:'适用条件',modality:'数据口径',unit:'单位',currency:'币种'};
    const scope=projection.answer_scope||{};
    for(const [key,value] of Object.entries(scope)){
      const label=labels[key]||key;
      if(value!=null&&value!==''&&(!Array.isArray(value)||value.length))card.append(element('small',`${label}：${Array.isArray(value)?value.join('；'):typeof value==='object'?JSON.stringify(value):value}`,'muted'));
    }
    card.append(element('p','唯一性限于本次已核验的证据；完整原文和引用保留在下方。','muted'));
    if(result.full_fact_answer||result.answer){const fact=element('details');fact.append(element('summary','完整事实与引用'),element('p',result.full_fact_answer||result.answer));card.append(fact);}
    host.append(card);
  }else if(result.answer_span_result?.status==='model_reviewed'&&result.answer){
    const span=result.answer_span_result,card=element('section',null,'citation');
    const execution=span.answer_proof?.execution;
    card.append(element('small',execution?'原文阈值比较 · 程序计算与独立模型复核':
      span.answer_type==='multi_source_literal'?'逐项回答 · 来源与完整性复核':'原文短答案 · 独立模型复核','muted'),element('p',result.answer));
    if(execution?.answer_type==='boolean'){
      const operators={le:'≤',ge:'≥',lt:'<',gt:'>'};
      card.append(element('p',`问题给定值 ${execution.question_observation.quote}；原文要求 ${execution.threshold_operator_quote} ${execution.threshold_value}%。`));
      card.append(element('p',`${execution.question_observation.value}% ${operators[execution.operation]||execution.operation} ${execution.threshold_value}% → ${execution.outcome?'满足要求':'不满足要求'}。`));
    }
    for(const scope of span.answer_scope||[])card.append(element('small',`引用 ${scope.citation_id}：${scope.quote}`,'muted'));
    card.append(element('p','已核对原文位置并进行第二次模型复核；模型语义判断仍可能出错。','muted'));
    const sourceOnly=span.evidence_contract==='raw_source_only_no_validated_facts';
    const facts=element('details');facts.append(element('summary',sourceOnly?'原文位置、来源和复核记录':'完整事实、来源和复核记录'));
    if(result.full_fact_answer)facts.append(element('p',result.full_fact_answer));
    if(sourceOnly&&result.prior_generation_output){const prior=element('details');prior.append(element('summary','首次生成或摘录记录'),element('p',result.prior_generation_output));facts.append(prior);}
    facts.append(element('pre',JSON.stringify(span.answer_proof,null,2)));card.append(facts);host.append(card);
  }else if(result.answer){
    host.append(element('p',result.answer));
    if(result.answer_mode==='visual_source_model_reviewed'){
      host.append(element('small','原页图像读取 · 独立模型复核','muted'));
      const details=element('details');details.append(element('summary','原页位置与复核记录'),element('pre',JSON.stringify(result.visual_source_proof,null,2)));host.append(details);
    }
    if(result.answer_mode==='native_table_model_reviewed'){
      host.append(element('p','原生表格行列经独立模型复核；金额由服务器按原文数字精确计算。未声明的币种、倍率保持未知。','muted'));
      const details=element('details');details.append(element('summary','表格行列、期间和计算步骤'),element('pre',JSON.stringify({scope:result.answer_scope,computation:result.computation,review:result.semantic_review},null,2)));host.append(details);
    }
    if(result.answer_mode==='visual_chart_native_annotated'){
      const scope=result.answer_scope||{};
      const computed=result.answer_strategy==='model_reviewed_native_chart_arithmetic';
      const computation=result.computation||{};
      const percentage=['percentage_change','percentage_decline'].includes(computation.operation);
      host.append(element('p',`原生图表标签 · 系列：${scope.series||'见操作数'} · 年份：${scope.year??'见操作数'} · 单位：${scope.unit==='unknown'?'原图未明确声明':scope.unit||'未声明'}`,'muted'));
      host.append(element('p',percentage?'程序用原生标注计算比例，保留精确分数并按显示规则舍入；基期、分母和完整问题经独立模型复核。':computed?'程序由原生数值标注精确计算，完整问题和操作数经独立模型复核；不作为已证明物理量输入。':'数值来自原页文字标签，模型核对图表、系列和年份；不作为物理量计算输入。','muted'));
      if(percentage){
        const policy=computation.display_policy||{};
        const rounding={ROUND_HALF_UP:'四舍五入',ROUND_HALF_EVEN:'五成双舍入',ROUND_DOWN:'向零截断',ROUND_FLOOR:'向下舍入',ROUND_CEILING:'向上舍入'};
        host.append(element('p',`基期 ${computation.baseline_year} 年 → ${computation.later_year} 年；分母 ${computation.denominator_raw_value}。显示 ${policy.decimal_places} 位小数，${rounding[policy.rounding_mode]||'见记录'}${computation.display_is_rounded?'（已舍入）':''}。`,'muted'));
      }
      const context=element('details');context.append(element('summary','图表标题、范围和完整核验记录'),element('pre',JSON.stringify({scope,computation:result.computation,review:result.semantic_review},null,2)));host.append(context);
    }
  }
}
async function responseError(response){let data;try{data=await response.json();}catch{return Error(`服务请求失败（HTTP ${response.status}）`);}return Error(response.status===401?'需要服务访问授权，请在页首填写当前服务的访问令牌。':typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));}
async function request(path,payload,signal){const r=await authFetch(path,{method:payload?'POST':'GET',headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):undefined,signal});if(!r.ok)throw await responseError(r);return r.json();}
function originalLink(documentId,label){const a=element('a',label);a.href=`/api/v1/knowledge/documents/${encodeURIComponent(documentId)}/original`;a.target='_blank';a.rel='noopener';return a;}
async function refresh(){const data=await request('/api/v1/knowledge/documents');$('documents').replaceChildren();documentCatalog.clear();const previousSource=$('query-document').value;$('query-document').replaceChildren(element('option','由检索定位资料'));$('query-document').firstChild.value='';data.documents.forEach(doc=>{const option=element('option',doc.title);option.value=doc.document_id;$('query-document').append(option);documentCatalog.set(doc.document_id,doc);const item=element('div',null,'doc');item.append(element('b',doc.title),element('span',doc.modality.toUpperCase(),'tag'),element('small',` · ${doc.chunk_count} 个片段 · `),originalLink(doc.document_id,'原文件'));addVisualAction(item,doc.document_id,1,doc.sha256,doc.modality);if(doc.warnings.length)item.append(element('p',doc.warnings.join('；'),'muted'));const quality=doc.analysis?.metrics?.text_quality;if(quality&&(quality.changed_lines||quality.typo_candidate_count))item.append(element('p',`文字质量：繁简 ${quality.changed_lines} 行 · 疑似错字 ${quality.typo_candidate_count} 处（请核对原文）`,'muted'));$('documents').append(item);});if(documentCatalog.has(previousSource))$('query-document').value=previousSource;$('status').textContent=`${data.documents.length} 份资料 · 本项目独立知识库`;}

function addVisualAction(host,documentId,pageNo,sourceSha,modality,visualPart,nativeContext){
  const record=documentCatalog.get(documentId);
  if((modality||record?.modality)!=='pdf'||!Number.isInteger(pageNo)||pageNo<1||!/^[a-f0-9]{64}$/.test(sourceSha||''))return;
  const button=element('button','查看页图证据','visual-action');button.type='button';
  button.addEventListener('click',()=>{
    visualDocument={documentId,sourceSha,title:record?.title||documentId,pageCount:record?.stats?.page_count,visualPart,nativeContext};
    $('visual-title').textContent=visualDocument.title;$('visual-page').value=pageNo;
    $('visual-page').max=visualDocument.pageCount||1000;
    if(!$('visual-dialog').open)$('visual-dialog').showModal();
    loadVisualPage(pageNo);
  });host.append(button);
}
function releaseVisualImage(){
  document.getElementById('visual-source-highlight')?.replaceChildren();
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
    const part=snapshot.visualPart;
    const native=snapshot.nativeContext;
    const nativeBoxes=native?.metadata?.native_row&&native.selection&&native.metadata.page_no===pageNo?
      window.NativeRowOverlay.model(native.metadata,manifest,native.selection):
      native?.metadata?.native_total_annotation&&native.totalSelection&&native.metadata.page_no===pageNo?
        window.NativeRowOverlay.totalModel(native.metadata,manifest,native.totalSelection):
      native?.metadata?.fact?.value_kind==='native_grouped_financial_cell_literal'&&native.metadata.page_no===pageNo?
        window.NativeRowOverlay.factModel(native.metadata,manifest,[native.metadata.fact]):null;
    if(nativeBoxes?.length||(part&&part.page_no===pageNo&&part.source_sha256===snapshot.sourceSha&&part.render_sha256===manifest.render_sha256)){
      const box=part?.bbox_normalized;
      if(nativeBoxes?.length||(Array.isArray(box)&&box.length===4&&box.every(v=>Number.isFinite(v)&&v>=0&&v<=1)&&box[0]<box[2]&&box[1]<box[3])){
        const image=$('visual-image');let overlay=document.getElementById('visual-source-highlight');
        if(!overlay){const stage=element('div');stage.style.cssText='position:relative;display:inline-block;max-width:100%';image.parentNode.insertBefore(stage,image);stage.append(image);overlay=document.createElementNS('http://www.w3.org/2000/svg','svg');overlay.id='visual-source-highlight';overlay.setAttribute('viewBox','0 0 1 1');overlay.setAttribute('preserveAspectRatio','none');overlay.style.cssText='position:absolute;inset:0;width:100%;height:100%;pointer-events:none';stage.append(overlay);}
        overlay.replaceChildren();overlay.setAttribute('role','img');overlay.setAttribute('aria-label','本次引用的原页字段位置');
        for(const item of nativeBoxes||[{bbox_normalized:box,role:'value'}]){
          const bounds=item.bbox_normalized,rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
          Object.entries({x:bounds[0],y:bounds[1],width:bounds[2]-bounds[0],height:bounds[3]-bounds[1],fill:item.role==='subject'?'rgba(70,130,220,.12)':'rgba(255,199,0,.18)',stroke:item.role==='subject'?'#4c83cd':'#d49a00','stroke-width':.003}).forEach(([key,value])=>rect.setAttribute(key,String(value)));
          if(item.label){const title=document.createElementNS(rect.namespaceURI,'title');title.textContent=`${item.label}: ${item.text}`;rect.append(title);}
          overlay.append(rect);
        }
      }
    }
    $('visual-summary').textContent=`第 ${pageNo} / ${manifest.page_count} 页 · ${manifest.size_px.join(' × ')} 像素 · 原文件 SHA-256 ${snapshot.sourceSha.slice(0,16)}…${nativeBoxes?.length?' · 主体与查询字段已定位；原件坐标与显示映射已核对。':''}`;
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
let documentQueryTimeline=null;
window.addEventListener('pagehide',()=>documentQueryTimeline?.dispose());
$('upload').addEventListener('submit',async event=>{event.preventDefault();const file=$('file').files[0];if(!file)return;const button=event.target.querySelector('button');button.disabled=true;try{if(file.size>20*1024*1024)throw Error('单个文件不能超过20 MiB');const ext=file.name.split('.').pop().toLowerCase();const modality=['png','jpg','jpeg','webp'].includes(ext)?'image':ext;const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));await request('/api/v1/knowledge/ingest',{document_id:'upload-'+crypto.randomUUID(),title:$('title').value||file.name,filename:file.name,modality,file_base64:btoa(binary),language:'chi_sim+eng'});await refresh();event.target.reset();}catch(e){$('status').textContent=e.message;}finally{button.disabled=false;}});
$('ask').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button');button.disabled=true;
  documentQueryTimeline?.dispose();documentQueryTimeline=window.ToolTimeline.create();
  const timeline=documentQueryTimeline;
  $('answer').replaceChildren(timeline.root);
  timeline.update({call_id:'document-query',tool:'knowledge.answer',status:'running',
    input:{question:$('question').value,document_id:$('query-document').value||null,page_no:$('query-page').value||null}});
  try{
    const selectedDocument=$('query-document').value;
    const selectedPage=$('query-page').value;
    if(selectedPage&&(!selectedDocument||documentCatalog.get(selectedDocument)?.modality!=='pdf'))throw Error('指定页码时请先选择PDF资料。');
    const data=await request('/api/v1/knowledge/query',{question:$('question').value,top_k:4,...(selectedDocument?{document_id:selectedDocument}:{}),...(selectedPage?{page_no:Number(selectedPage)}:{})});
    timeline.update({call_id:'document-query',tool:'knowledge.answer',status:data.status,executed:true,
      summary:data.status==='ok'?'文档处理已返回，下面保留回答及原文来源。':'文档处理已返回，当前仍需补充条件或证据。',
      output:{status:data.status,answer_mode:data.answer_mode,citation_count:data.citations?.length||0}});
    timeline.finish({trace:data.trace||[]});
    $('answer').append(element('small',`检索：${data.retrieval.mode} · 回答：${data.answer_mode}`,'muted'));
    appendAnswer($('answer'),data);
    if(data.clarification)$('answer').append(element('p',data.clarification,'error'));
    if(data.answer_mode==='visual_grid_routed')$('answer').append(element('p','已走原页表格核验链路；唯一性限于本轮候选资料，未声明全库唯一。','muted'));
    (data.source_options||[]).forEach(option=>{const source=element('div',null,'toolbar');source.append(element('span',`${option.title} · 第${option.page_no}页`));addVisualAction(source,option.document_id,option.page_no,option.source_sha256);$('answer').append(source);});
    data.citations.forEach(hit=>{
      const box=element('article',null,'citation');
      box.append(element('b',`[${hit.citation_id}] ${hit.title}`),element('pre',hit.snippet),element('small',`${hit.metadata.source_locator} · ${hit.metadata.retrieval_channel} · BM25 ${hit.metadata.bm25_raw??'—'} · Dense ${hit.metadata.dense_cosine??'—'} · `),originalLink(hit.metadata.document_id,'查看原文件'));
      addVisualAction(box,hit.metadata.document_id,hit.metadata.page_no,hit.metadata.source_sha256,undefined,data.visual_source_proof?.parts?.[hit.citation_id-1],{metadata:hit.metadata,selection:data.native_row_proof?.selection,totalSelection:data.native_total_proof?.selected_annotations});
      $('answer').append(box);
    });
    const trace=element('details');trace.append(element('summary','查看路由、范围与实际核验记录'),element('pre',JSON.stringify(data,null,2)));$('answer').append(trace);
  }catch(e){timeline.fail(e.message);$('answer').append(element('p',e.message,'error'));}finally{button.disabled=false;}
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

const scanHeaderRows=element('input'),scanTableNumber=element('input');
const scanPdfPage=element('input'),scanPdfPageLabel=element('label','PDF页码');
const scanPdfEnd=element('input'),scanPdfEndLabel=element('label','结束页码（可选，最多连续4页）');
const scanNextHeaders=element('input'),scanNextHeadersLabel=element('label','后续页表头行数（可选，无表头填0）');
scanPdfEnd.type='number';scanPdfEnd.min='1';scanPdfEnd.max='1000';scanPdfEndLabel.append(scanPdfEnd);
scanNextHeaders.type='number';scanNextHeaders.min='0';scanNextHeaders.max='5';scanNextHeadersLabel.append(scanNextHeaders);
scanPdfPage.type='number';scanPdfPage.min='1';scanPdfPage.max='1000';scanPdfPage.value='1';
scanPdfPage.setAttribute('aria-label','PDF表格预览页码，从1开始');scanPdfPageLabel.append(scanPdfPage);
$('scan-file').accept='.png,.jpg,.jpeg,.webp,.pdf';
$('scan-quality-form').insertBefore(scanPdfPageLabel,$('scan-quality-form').querySelector('button'));
$('scan-quality-form').insertBefore(scanPdfEndLabel,$('scan-quality-form').querySelector('button'));
$('scan-quality-form').insertBefore(scanNextHeadersLabel,$('scan-quality-form').querySelector('button'));
scanHeaderRows.type='number';scanHeaderRows.min='0';scanHeaderRows.max='5';scanHeaderRows.placeholder='0表示无表头';
scanHeaderRows.setAttribute('aria-label','人工指定表头行数，0到5行，0表示无表头');
scanTableNumber.type='number';scanTableNumber.min='1';scanTableNumber.max='32';scanTableNumber.value='1';
scanTableNumber.setAttribute('aria-label','要整理的网格表格序号，从1开始');
const scanHeaderLabel=element('label','表头行数（可选）'),scanTableLabel=element('label','表格序号');
scanHeaderLabel.append(scanHeaderRows);scanTableLabel.append(scanTableNumber);
$('scan-quality-form').insertBefore(scanHeaderLabel,$('scan-quality-form').querySelector('button'));
$('scan-quality-form').insertBefore(scanTableLabel,$('scan-quality-form').querySelector('button'));

let scanRevision=0;
$('scan-file').addEventListener('change',()=>{scanRevision++;$('scan-quality-form').querySelector('button').disabled=false;$('scan-quality-result').replaceChildren(element('p','已更换文件，请重新预览。','muted'));});
const continuityReasons={incomplete_or_unlocated_table:'表格不完整或缺少原页位置',different_pdf_sources:'来源不是同一PDF',
  nonadjacent_pages:'页面不相邻',column_count_changed:'列数不一致',first_page_header_missing:'首段缺少可对应的表头',
  ambiguous_header_paths:'表头为空或存在重复含义',header_paths_changed:'表头不一致'};
function appendAmountCheck(td,cell){
  const presentation=window.PdfAmountCheck.describe(cell);if(!presentation)return;
  const label=element('small',presentation.label);label.style.display='block';label.style.color=presentation.color;
  label.title=presentation.detail;td.append(label);
}
function renderArithmeticCheck(report,host){
  if(!report||report.status==='not_applicable')return;
  const reasons={invalid_or_over_budget_table:'表格不完整或超过检查范围',ambiguous_total_scope:'总计所在范围不明确',
    subtotal_or_carry_forward_present:'存在小计或结转，不能直接加总',merged_body_cells_require_review:'合并数据格需先核对',
    missing_detail_label:'部分明细缺少名称',unit_scale_requires_confirmation:'金额单位或倍率需确认',
    amount_missing_or_ambiguous:'存在空白或无法明确解析的金额',mixed_or_unspecified_currency_identity:'币种不一致或身份未确认'};
  const label=report.status==='arithmetic_consistent'?'观察金额的合计一致':report.status==='conflict'?'观察金额的合计不一致':'合计尚不能核对';
  const title=element('p',label);title.style.color=report.status==='conflict'?'#c62828':report.status==='arithmetic_consistent'?'#16803c':'#737373';host.append(title);
  if(report.reason in reasons)host.append(element('p',reasons[report.reason],'muted'));
  for(const check of report.checks||[]){
    if(check.computed_total!=null)host.append(element('p',`第${check.column+1}列：${check.detail_count}项明细合计 ${check.currency} ${check.computed_total}；观察总计 ${check.observed_total}；差额 ${check.difference}`));
    else host.append(element('p',`第${check.column+1}列：${reasons[check.reason]||'请对照原表复核'}。`,'muted'));
    if(check.review_candidate){const candidate=check.review_candidate;
      host.append(element('p',`待核对候选：原第${candidate.cell.original_row+1}行 ${candidate.cell.text||'空白'}，由观察总计减其余明细得 ${candidate.currency} ${candidate.candidate_amount}。${candidate.warning}`,'muted'));}
  }host.append(element('small',report.warning,'muted'));
}
function renderAmountCellReview(report,host){
  if(!report||report.status==='not_needed')return;
  host.append(element('p',`金额局部复核：${report.accepted_count||0} 格有一致候选；原OCR值保留。`));
  for(const review of report.reviews||[]){
    const label=review.accepted?`候选 ${review.candidate_text}`:'未得到可靠一致候选';
    host.append(element('p',`原第${review.row+1}行、第${review.column+1}列：${review.original_text} → ${label}`,'muted'));
    const detail=element('details');detail.append(element('summary','查看裁剪来源与每次识别'),element('pre',JSON.stringify(review,null,2)));host.append(detail);
  }
  host.append(element('small',report.warning,'muted'));
  if(report.revised_preview){host.append(element('p','局部OCR候选的合计预览（与原OCR分开，不自动入库）','muted'));renderArithmeticCheck(report.revised_preview.arithmetic_check,host);}
}
function renderPdfContinuity(data,payload,host,file,revision){
  if($('scan-file').files[0]!==file||revision!==scanRevision)return;
  host.replaceChildren(element('p','PDF逐页表格预览；确认续表关系后可拼接。原文和资料库保持原样。'));
  (data.table_structures||[]).forEach(structure=>renderScanStructure(structure,host));
  for(const assembly of data.table_continuity?.assemblies||[]){
    if(assembly.status!=='assembled_preview'){host.append(element('p',`续表未连接：${continuityReasons[assembly.reason]||'请核对逐页结构'}`,'muted'));continue;}
    const table=element('table'),head=element('tr');head.append(element('th','原页'));
    assembly.columns.forEach(column=>head.append(element('th',column.header_path.join(' / ')||`第${column.column+1}列`)));
    const thead=element('thead');thead.append(head);table.append(thead);const body=element('tbody');
    for(const row of assembly.rows){
      const tr=element('tr');tr.append(element('td',`${row.page_no} · 行${row.original_row+1}`));
      for(const cell of row.cells){if(cell.status==='covered_by_merged_cell')continue;
        const td=element('td',cell.text||'未识别或空白');td.rowSpan=cell.rowspan;td.colSpan=cell.colspan;
        td.title=`原PDF第${row.page_no}页；坐标：${JSON.stringify(cell.original_geometry?.pdf_geometry?.pdf_user_polygon_pt)}`;appendAmountCheck(td,cell);tr.append(td);}
      body.append(tr);
    }table.append(body);host.append(element('h3',`跨页续表 · ${assembly.row_count}行`),element('p',assembly.warnings.join(' '),'muted'),table);renderArithmeticCheck(assembly.arithmetic_check,host);
  }
  const existing=payload.pdf_table_links||[];
  for(const candidate of data.table_continuity?.candidates||[]){
    const label=`第${candidate.from_page}页表${candidate.from_table+1} → 第${candidate.to_page}页表${candidate.to_table+1}`;
    if(existing.some(edge=>edge.from_page===candidate.from_page&&edge.from_table===candidate.from_table&&edge.to_page===candidate.to_page&&edge.to_table===candidate.to_table))continue;
    if(candidate.status!=='needs_confirmation'){host.append(element('p',`${label}：未连接（${continuityReasons[candidate.reason]||'请核对逐页结构'}）`,'muted'));continue;}
    const button=element('button',`确认这是同一张表的续页：${label}`);button.type='button';host.append(button);
    button.addEventListener('click',async()=>{
      if($('scan-file').files[0]!==file||revision!==scanRevision)return;
      const controls=[...host.querySelectorAll('button')];controls.forEach(control=>control.disabled=true);
      try{const next={...payload,pdf_table_links:window.PdfTableContinuity.appendLink(existing,candidate)};
        const result=await request('/api/v1/documents/chunks-preview',next);renderPdfContinuity(result,next,host,file,revision);
      }catch(error){if(revision===scanRevision)host.append(element('p',error.message,'error'));}finally{controls.forEach(control=>control.disabled=false);}
    });
  }
  const details=element('details');details.append(element('summary','查看逐页来源、续表选择和坐标'),element('pre',JSON.stringify(data,null,2)));host.append(details);
}

function renderScanStructure(structure,host){
  if(structure?.status==='structure_observed'){
    const section=element('section'),table=element('table'),head=element('thead'),body=element('tbody'),heading=element('tr');
    section.className='scan-table-structure';
    if(structure.amount_verification){const counts=structure.amount_verification.counts||{};
      section.append(element('p',`金额核对：与原文字一致 ${counts.matched||0} 格；冲突 ${counts.conflict||0} 格；需复核 ${counts.needs_review||0} 格。${structure.amount_verification.warning}`,'muted'));}
    const page=structure.page_no?`PDF第${structure.page_no}页 · `:'';
    section.append(element('h3',page+'表头层级与数据预览'),element('p',`按你指定的前${structure.header_rows}行整理第${structure.table_index+1}个网格；OCR文字和字段含义仍需对照原图核对。空白不是零，合并数据不自动复制。`,'muted'));
    structure.columns.forEach(column=>heading.append(element('th',column.header_path.length?
      column.header_path.map(text=>text||'未识别').join(' / ')+(column.status==='observed'?'':'（待核对）'):`第${column.column+1}列（无表头）`)));head.append(heading);
    structure.body_cells.forEach(row=>{const tr=element('tr');row.forEach(cell=>{
      if(cell.status==='covered_by_merged_cell')return;
      const td=element('td',cell.text||'未识别或空白');td.rowSpan=cell.rowspan;td.colSpan=cell.colspan;
      const geometry=cell.original_geometry?.pdf_geometry;
      if(geometry?.pdf_highlight_eligible)td.title=`PDF第${geometry.page_no}页；原PDF坐标：${JSON.stringify(geometry.pdf_user_polygon_pt)}`;
      appendAmountCheck(td,cell);
      tr.append(td);
    });body.append(tr);});table.append(head,body);section.append(table);renderArithmeticCheck(structure.arithmetic_check,section);renderAmountCellReview(structure.amount_cell_review,section);
    if(structure.blank_cell_review?.reviews?.length){const review=structure.blank_cell_review;
      section.append(element('p',`空白格复核：${review.accepted_count||0}格有一致文字候选；原空白保留。`),element('small',review.warning,'muted'));
      for(const cell of review.reviews){section.append(element('p',`原第${cell.row+1}行、第${cell.column+1}列：${cell.accepted?cell.candidate_text:'未得到一致可靠候选'}${cell.candidate_kind==='ambiguous_amount_text'?'（金额仍有歧义）':''}`,'muted'));}
    }host.append(section);
  }else if(structure)host.append(element('p',`该表头选择不能整理：${structure.reason}。请对照原图检查页码、表格序号、表头行数或合并范围。`,'muted'));
}

$('scan-quality-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.target.querySelector('button'),host=$('scan-quality-result'),file=$('scan-file').files[0];
  if(!file)return;const revision=++scanRevision;button.disabled=true;host.replaceChildren(element('p','本地OCR检测中…'));
  try{
    const isPdf=file.name.toLowerCase().endsWith('.pdf');
    if(file.size>(isPdf?20:8)*1024*1024)throw Error(isPdf?'PDF不能超过20 MiB':'检测图片不能超过8 MiB');
    const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
    for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    const source=btoa(binary);
    if(isPdf){
      if(scanHeaderRows.value==='')throw Error('请指定PDF表头行数；没有表头请填0。');
      host.replaceChildren(element('p','正在解析PDF并整理指定页表格…'));
      const payload={document_id:'pdf-table-preview-'+crypto.randomUUID(),modality:'pdf',file_base64:source,language:'chi_sim+eng',
        pdf_table_headers:window.PdfTableContinuity.selections(Number(scanPdfPage.value),Number(scanPdfEnd.value||scanPdfPage.value),
          Number(scanTableNumber.value),Number(scanHeaderRows.value),Number(scanNextHeaders.value===''?scanHeaderRows.value:scanNextHeaders.value))};
      const data=await request('/api/v1/documents/chunks-preview',payload);renderPdfContinuity(data,payload,host,file,revision);
      return;
    }
    const data=await request('/api/v1/documents/ocr',{image_base64:source,language:'chi_sim+eng',max_attempts:3,auto_perspective:$('scan-auto-perspective').checked,
      table_header_rows:scanHeaderRows.value===''?null:Number(scanHeaderRows.value),table_index:Number(scanTableNumber.value)-1});
    if(revision!==scanRevision)return;
    const orientation=data.metadata?.orientation,quality=data.metadata?.input_quality;
    host.replaceChildren(element('p',`识别状态：${data.status} · ${data.attempts.length}次有界尝试`));
    const boundary=data.metadata?.page_boundary_detection;
    if(boundary?.status==='attempted')host.append(element('p',`已检测页面轮廓并尝试透视校正；文字采用${boundary.selected_for_text?'校正图':'较可靠的原图或其他预处理结果'}，请对照原图核对。`));
    else if(boundary?.status==='rejected')host.append(element('p','页面轮廓不能覆盖已观察文字，保留原图范围。','muted'));
    else if(boundary?.status==='ambiguous')host.append(element('p','存在多张候选页面，未自动裁剪。','muted'));
    if(window.ScanSourceOverlay&&data.metadata?.original_pixel_mapping?.status==='mapped'){
      try{
        const sourceSha=await window.ScanSourceOverlay.digest(bytes);
        const normalized=await request('/api/v1/documents/image-enhance',{image_base64:source,transforms:[]});
        const overlay=await window.ScanSourceOverlay.render(data.metadata,normalized,sourceSha);
        if(revision!==scanRevision)return;host.append(overlay);
      }catch(error){if(revision!==scanRevision)return;host.append(element('p',error.message||'原图位置预览不可用。','muted'));}
    }
    if(quality)host.append(element('p',`图像质量估计：${quality.quality_score}；${quality.width} × ${quality.height}。这是图像启发式分数，不是识别准确率。`,'muted'));
    if(orientation?.status==='estimated')host.append(element('p',`方向估计：逆时针${orientation.rotation_ccw_degrees}°；倾斜${orientation.skew_ccw_degrees}°。依据${orientation.eligible_lines}条可靠文字框。`));
    else host.append(element('p','方向无法判定，请核对原图；不会自动给出旋转预览。','muted'));
    if(data.warnings.length)host.append(element('p',data.warnings.map(qualityMessage).join('；'),'muted'));
    host.append(element('pre',data.text||'未识别到可靠文字'));
    renderScanStructure(data.metadata?.table_structure,host);
    if(boundary?.status==='attempted'&&Array.isArray(boundary.quad_px)){
      const preview=element('button','查看页面校正与定位'),previewResult=element('div');preview.type='button';host.append(preview,previewResult);
      preview.addEventListener('click',async()=>{
        preview.disabled=true;previewResult.replaceChildren(element('p','正在生成页面校正预览…'));
        try{
          const sourceSha=await window.ScanSourceOverlay.digest(bytes);
          if(sourceSha!==boundary.source_image_sha256)throw Error('页面轮廓与当前原图来源不一致。');
          const corrected=await request('/api/v1/documents/image-enhance',{image_base64:source,transforms:['perspective_rectify'],perspective_quad:boundary.quad_px});
          previewResult.replaceChildren(element('p','页面校正预览；原文件保持不变。位置来自原图观察，不代表数值已经核验。','muted'));
          previewResult.append(await window.ScanSourceOverlay.render(data.metadata,corrected,sourceSha));
          preview.remove();
        }catch(error){showError(previewResult,error);preview.disabled=false;}
      });
    }
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
  }catch(error){if(revision===scanRevision)showError(host,error);}finally{if(revision===scanRevision)button.disabled=false;}
});
