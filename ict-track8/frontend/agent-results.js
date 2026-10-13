(function(root,factory){
  const api=factory(root);
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root)root.AgentResults=api;
})(typeof window!=='undefined'?window:globalThis,function(root){
  'use strict';
  const TOOLS={sql:'查询数据库',nl2sql:'查询数据库',document_cell:'读取文档单元格',document_formula:'定位文档公式',document_search:'检索文档',search:'检索文档',calculate:'计算结果',compare:'比较结果'};
  const SUCCESS=new Set(['ok','complete','completed','success','verified']);
  const SHA=/^[a-f0-9]{64}$/;
  let sequence=0;
  const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
  const array=value=>Array.isArray(value)?value:[];
  const text=value=>typeof value==='string'?value:typeof value==='number'&&Number.isFinite(value)?String(value):'';
  const scalar=value=>typeof value==='string'||typeof value==='number'&&Number.isFinite(value)||typeof value==='boolean';
  const display=value=>value==null?'未提供':scalar(value)?String(value):'结构化数据';
  const unit=value=>value==='unknown'||value==null?'':({'CNY':'元','count':'计数','CNY*count^-1':'元 / 计数','ratio':'比例'})[value]||text(value);
  function unwrap(data){const value=object(data);return object(value.response).status?value.response:value;}
  function statusOf(data,result){
    if(result.status&&!SUCCESS.has(result.status))return text(result.status);
    return text(data.status||result.status)||'unverified';
  }
  function guidanceFor(status,result,failures){
    if(SUCCESS.has(status))return null;
    const code=text(result.clarification_code||result.error_code||failures.find(row=>row.code)?.code);
    if(code==='selected_source_reference_conflict')return {kind:'scope',title:'当前文件与问题引用不一致',
      next:'取消文件限定后使用历史查询编号，或保留当前文件并直接提问。',code,retryable:false,
      message:text(result.clarification),options:[]};
    const service=/provider|model_unavailable|visual_model_unavailable|timeout|stream_interrupted|budget/.test(code);
    const source=/revision_changed|integrity_failed|source_missing|source_changed|document_changed/.test(code);
    const evidence=status==='insufficient_evidence'||result.answer_completeness==='retrieval_only'||/evidence_missing|no_document_evidence|evidence_scope_incomplete|document_answer_incomplete/.test(code);
    const safety=code==='read_only_query_required';
    const retryable=failures.some(row=>row.retryable===true);
    const title=service?'模型服务暂未完成请求':source?'引用资料需要重新核对':safety?'此任务需要调整操作方式':evidence?'还缺少支持答案的资料':status==='clarification'?'补充一个条件，继续任务':'本次任务尚未完成';
    const next=service?'检查模型服务与调用额度后重新提交；已返回的资料仍可查看。':source?'重新选择当前版本的资料，再提交问题。':safety?'请改为查询或分析数据的请求。':evidence?'上传包含相关内容的资料，或在问题中指定文档、页码和需要查找的内容。':status==='clarification'?'选择下方可用条件，或在输入框补充对象、时间范围与所需指标。':'查看下方已完成的步骤与来源，补充缺失条件后重新提交。';
    return {kind:service?'service':source?'source':safety?'safety':evidence?'evidence':status==='clarification'?'clarification':'incomplete',title,next,code,retryable,
      message:text(result.clarification||result.error),options:array(result.clarification_options).filter(item=>item&&typeof item==='object')};
  }
  function sourceSummary(hit,verifiedDocuments){
    const metadata=object(hit.metadata),documentId=text(metadata.document_id||hit.document_id),sha=text(metadata.source_sha256||hit.sha256);
    const locator=text(metadata.source_locator||hit.locator),page=metadata.page_no||hit.page_no;
    return {hit,title:text(hit.title)||documentId||'引用资料',documentId,sha,locator,page:Number.isInteger(page)&&page>0?page:null,
      snippet:text(hit.snippet),citationId:Number.isInteger(hit.citation_id)&&hit.citation_id>0?hit.citation_id:null,
      versionBound:SHA.test(sha),versionVerified:SHA.test(sha)&&verifiedDocuments[documentId]===sha};
  }
  function factSource(value){
    const uri=text(value.source_uri),match=uri.match(/^\/api\/v1\/knowledge\/documents\/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\/original$/);
    const documentId=value.document_id||match?.[1];
    if(!documentId||!SHA.test(text(value.sha256))||(match&&match[1]!==documentId))return null;
    return {title:text(value.title)||(value.expression?'公式原文':documentId),snippet:text(value.native_formula_proof?.literal_text)||text(value.expression)||text(value.raw_value)||text(value.value),source_uri:uri,metadata:{
      document_id:documentId,source_sha256:value.sha256,source_locator:value.locator,page_no:value.page_no||value.native_formula_proof?.page_no,
      chunk_id:value.chunk_id,bbox_display_pt:value.bbox_display_pt}};
  }
  function summarize(input){
    const data=unwrap(input),result=Object.keys(object(data.result)).length?data.result:Object.keys(object(data.structured)).length?data.structured:data;
    const status=statusOf(data,result),ok=SUCCESS.has(status),route=text(data.route)||(result.results?'fusion':result.sql?'sql':'document');
    const plan=array(result.execution_plan),receipt=new Map();
    for(const event of [...array(data.trace),...array(result.trace)])if(event?.task_id)receipt.set(event.task_id,event);
    const steps=Object.entries(object(result.results)).map(([id,value])=>{
      value=object(value);const planned=plan.find(item=>item?.id===id)||{},event=receipt.get(id)||{};
      const tool=text(event.tool||planned.tool)||(value.sql?'sql':value.expression&&value.value!=null?'calculate':value.expression?'document_formula':value.raw_value!=null?'document_cell':'');
      return {id,tool,title:TOOLS[tool]||'任务结果',status:text(event.status||value.status)||'reported',value,question:text(value.question||planned.args?.question),
        label:text(value.label||planned.args?.label),dependencies:array(event.dependencies),latencyMs:Number.isFinite(event.latency_ms)?event.latency_ms:null};
    });
    const calculations=steps.filter(step=>step.tool==='calculate'&&scalar(step.value.value)).map(step=>({id:step.id,status:step.status,
      expression:text(step.value.expression),value:step.value.value,unit:unit(step.value.result_unit),unitVerified:step.value.unit_validation==='validated',
      semanticsVerified:step.value.parameter_semantics_validation?.status==='verified',parameters:Object.entries(object(step.value.parameters)).map(([name,p])=>({name,value:object(p).value,unit:unit(object(p).unit),source:text(object(p).source_uri),locator:text(object(p).locator)})),
      formulaLocator:text(step.value.formula_locator),formulaSource:text(step.value.formula_source)}));
    const consumed=new Set(array(result.edges).map(edge=>edge?.from));
    for(const step of steps)for(const id of step.dependencies)consumed.add(id);
    const metrics=[];
    if(ok){
      for(const step of steps.filter(item=>!consumed.has(item.id)&&SUCCESS.has(item.status))){
        const value=step.value;
        if(['calculate','document_cell'].includes(step.tool)&&scalar(value.value)){
          const formula=plan.find(item=>item?.id===step.id)?.args?.formula;
          const label=step.label||(formula?.ref?text(object(result.results[formula.ref]).label):'')||(step.tool==='calculate'?'计算结果':'文档原值');
          metrics.push({label,value:scalar(value.raw_value)?value.raw_value:value.value,unit:unit(value.result_unit||value.unit),unitUnknown:(value.result_unit||value.unit)==='unknown',kind:step.tool});
        }
      }
      const fact=object(result.table_fact);
      if(!metrics.length&&scalar(fact.value))metrics.push({label:Object.values(object(fact.matched_conditions)).filter(scalar).join(' · ')||'文档原值',value:scalar(fact.raw_value)?fact.raw_value:fact.value,unit:unit(fact.unit),unitUnknown:fact.unit==='unknown',kind:'document_cell'});
    }
    const verifiedDocuments=result.source_validation?.status==='verified'?object(result.source_validation.documents):{};
    const hits=[...array(result.citations),...array(data.document_evidence)];
    for(const step of steps){hits.push(...array(step.value.hits));const hit=factSource(step.value);if(hit)hits.push(hit);}
    const sources=[],seen=new Map();
    for(const hit of hits.filter(item=>item&&typeof item==='object')){
      const source=sourceSummary(hit,verifiedDocuments),key=[source.documentId,source.sha,source.locator,source.snippet].join('\u0000');
      if(seen.has(key)){
        const existing=seen.get(key);
        if(source.citationId&&!existing.citationIds.includes(source.citationId))existing.citationIds.push(source.citationId);
        continue;
      }
      source.citationIds=source.citationId?[source.citationId]:[];seen.set(key,source);sources.push(source);
    }
    let answer=text(result.answer||data.answer||result.clarification);
    if(!answer&&route==='sql'&&ok)answer=array(result.rows).length?`已查询到 ${array(result.rows).length} 行结果。`:'查询完成，当前条件下没有数据。';
    if(!answer&&ok)answer=metrics.length?'已完成任务，结果如下。':'任务已完成，执行结果见下方。';
    const guidance=guidanceFor(status,result,array(data.failure_trace));
    return {status,ok,route,title:ok?'任务结果':guidance?.title||'任务状态',answer,metrics,sources,calculations,steps,guidance,
      question:text(data.effective_question||data.question||result.question),warnings:[...array(data.warnings),...array(result.notices)].filter(value=>typeof value==='string'),
      sql:typeof result.sql==='string'?result:null,evidence:result};
  }
  function el(tag,className,value){const node=document.createElement(tag);if(className)node.className=className;if(value!=null)node.textContent=String(value);return node;}
  function inline(host,value,onCitation){
    const expression=/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\d+\])/g;let cursor=0,match;
    while((match=expression.exec(value))){
      host.append(document.createTextNode(value.slice(cursor,match.index)));const token=match[0];
      if(token[0]==='`')host.append(el('code','',token.slice(1,-1)));
      else if(token.startsWith('**'))host.append(el('strong','',token.slice(2,-2)));
      else if(onCitation&&onCitation.has(Number(token.slice(1,-1)))){
        const button=el('button','agent-citation-ref',token);button.type='button';button.setAttribute('aria-label',`查看来源 ${token.slice(1,-1)}`);button.onclick=onCitation.get(Number(token.slice(1,-1)));host.append(button);
      }else host.append(document.createTextNode(token));
      cursor=expression.lastIndex;
    }
    host.append(document.createTextNode(value.slice(cursor)));
  }
  // This deliberately small Markdown subset constructs text nodes only. HTML,
  // arbitrary links and model-provided image URLs are never interpreted.
  function markdown(value,onCitation){
    const host=el('div','agent-answer-markdown');let paragraph=[],list=null,fence=null;
    const flush=()=>{if(paragraph.length){const p=el('p');inline(p,paragraph.join('\n'),onCitation);host.append(p);paragraph=[];}list=null;};
    for(const line of text(value).replace(/\r\n?/g,'\n').split('\n')){
      if(/^\s*```/.test(line)){flush();if(fence){host.append(el('pre','agent-code',fence.join('\n')));fence=null;}else fence=[];continue;}
      if(fence){fence.push(line);continue;}
      if(!line.trim()){flush();continue;}
      const heading=line.match(/^#{1,6}\s+(.+)$/),item=line.match(/^\s*(?:([-*+])|\d+[.)])\s+(.+)$/),quote=line.match(/^>\s?(.*)$/);
      if(heading){flush();const h=el('h4');inline(h,heading[1],onCitation);host.append(h);}
      else if(item){if(paragraph.length)flush();const type=item[1]?'ul':'ol';if(!list||list.tagName.toLowerCase()!==type){list=el(type);host.append(list);}const li=el('li');inline(li,item[2],onCitation);list.append(li);}
      else if(quote){flush();const q=el('blockquote');inline(q,quote[1],onCitation);host.append(q);}
      else {list=null;paragraph.push(line);}
    }
    flush();if(fence)host.append(el('pre','agent-code',fence.join('\n')));return host;
  }
  function detail(label){const details=el('details','agent-result-details');details.append(el('summary','',label));return details;}
  function renderCalculation(calculation){
    const section=el('section','agent-calculation');section.append(el('h4','','公式与参数'),el('p','agent-formula',calculation.expression));
    const table=el('table','agent-parameter-table'),head=el('thead'),header=el('tr');['参数','取值','来源'].forEach(label=>header.append(el('th','',label)));head.append(header);table.append(head);
    const body=el('tbody');
    for(const parameter of calculation.parameters){const row=el('tr');row.append(el('th','',parameter.name),el('td','',display(parameter.value)+(parameter.unit?' '+parameter.unit:'')));
      const source=el('td');source.append(el('span','',parameter.source.startsWith('sql://')?'数据库查询结果':parameter.source?'文档或已绑定来源':'未返回来源'));
      if(parameter.locator)source.append(el('small','',parameter.locator));row.append(source);body.append(row);}
    table.append(body);section.append(table);
    if(SUCCESS.has(calculation.status))section.append(el('p','agent-calculation-return','计算返回：'+display(calculation.value)+(calculation.unit?' '+calculation.unit:'')));
    const checks=[];if(calculation.unitVerified)checks.push('单位已核对');if(calculation.semanticsVerified)checks.push('参数对应关系已核对');
    if(checks.length)section.append(el('p','agent-verification',checks.join(' · ')));
    if(calculation.formulaLocator)section.append(el('p','agent-source-location','公式位置：'+calculation.formulaLocator));return section;
  }
  function render(input,options={}){
    const model=summarize(input),card=el('section','agent-result-card'+(model.ok?'':' is-'+(model.guidance?.kind||'incomplete')));
    card.setAttribute('aria-label',model.title);
    const header=el('div','agent-result-heading');header.append(el('span','agent-result-eyebrow',model.title));
    if(model.route)header.append(el('span','agent-result-route',({document:'文档问答',sql:'数据查询',fusion:'跨源任务',comparison:'结果比较',tasks:'待补任务'})[model.route]||'任务输出'));
    card.append(header);
    const references=new Map(),sourceCards=[];
    for(const [index,source] of model.sources.entries()){
      const sourceCard=el('article','agent-source-card');sourceCard.id=`agent-source-${++sequence}`;sourceCard.tabIndex=-1;
      const heading=el('div','agent-source-heading');heading.append(el('span','agent-source-number',source.citationIds.length?source.citationIds.map(id=>'['+id+']').join(' '):'来源 '+(index+1)));
      if(source.versionVerified)heading.append(el('span','agent-source-bound','来源版本已核对'));
      else if(source.versionBound)heading.append(el('span','agent-source-bound','已绑定原件版本'));
      sourceCard.append(heading);
      if(options.renderEvidence)options.renderEvidence(sourceCard,{...model.evidence,document_evidence:[source.hit],document_evidence_context:model.sources.map(item=>item.hit)});
      else {
        sourceCard.append(el('h4','',source.title));if(source.page)sourceCard.append(el('p','agent-source-location',`第 ${source.page} 页`));
        if(source.snippet)sourceCard.append(el('blockquote','agent-source-quote',source.snippet));
        const evidence=root.SourceEvidence?.citation(source.hit,{api:options.api});if(evidence)sourceCard.append(evidence);
      }
      sourceCards.push(sourceCard);
      for(const citationId of source.citationIds)if(!references.has(citationId))references.set(citationId,()=>{sourceCard.scrollIntoView({behavior:'smooth',block:'center'});sourceCard.focus({preventScroll:true});});
    }
    if(model.metrics.length){
      const metrics=el('div','agent-result-metrics');for(const metric of model.metrics){const tile=el('div','agent-result-metric');tile.append(el('div','agent-metric-label',metric.label));
        const numeric=metric.kind==='calculate'&&typeof metric.value==='number'&&Number.isFinite(metric.value);
        const shown=numeric?metric.value.toLocaleString('zh-CN',Math.abs(metric.value)>0&&Math.abs(metric.value)<.01?{maximumSignificantDigits:4}:{maximumFractionDigits:2}):display(metric.value);
        const rounded=numeric&&Number(shown.replace(/,/g,''))!==metric.value;
        const number=el('div','agent-metric-value',(rounded?'≈ ':'')+shown);if(metric.unit)number.append(el('span','agent-metric-unit',metric.unit));tile.append(number);
        if(rounded)tile.append(el('p','agent-metric-note','显示值已取近似；完整精度见下方执行依据。'));
        if(metric.unitUnknown)tile.append(el('p','agent-metric-note','当前提取结果未核定单位，保留原值。'));metrics.append(tile);}card.append(metrics);
    }
    if(model.answer)card.append(markdown(model.answer,references));
    if(model.ok&&model.sql&&options.renderTable){const table=el('div','agent-query-table');options.renderTable(table,model.sql);card.append(table);}
    if(model.guidance){
      const guide=el('div','agent-result-guidance');
      if(model.guidance.message&&model.guidance.message!==model.answer)guide.append(markdown(model.guidance.message,references));
      guide.append(el('p','agent-guidance-next',model.guidance.next));
      const actions=el('div','agent-result-actions');
      if(options.onClarify&&!options.suppressGuidanceActions){
        for(const [index,option] of model.guidance.options.entries()){
          const button=el('button','agent-result-action',text(option.label||option.question||option.value)||`选项 ${index+1}`);button.type='button';button.onclick=()=>options.onClarify({kind:'option',option,code:model.guidance.code,question:model.question});actions.append(button);
        }
        if(!model.guidance.options.length){const button=el('button','agent-result-action',model.guidance.kind==='scope'?'取消文件限定，继续提问':'补充问题');button.type='button';button.onclick=()=>options.onClarify({kind:model.guidance.kind==='scope'?'clear_scope':'refine',code:model.guidance.code,question:model.question});actions.append(button);}
      }
      if(!options.suppressGuidanceActions&&(model.guidance.kind==='evidence'||model.guidance.kind==='source')){const link=el('a','agent-result-action','查看或上传资料');link.href='./knowledge.html';actions.append(link);}
      if(actions.childNodes.length)guide.append(actions);card.append(guide);
    }
    for(const warning of model.warnings)card.append(el('p','agent-result-warning',warning));
    if(sourceCards.length){const sources=el('section','agent-result-sources');sources.append(el('h3','',model.ok?'答案来源':'已找到的相关资料'),...sourceCards);card.append(sources);}
    if(model.steps.length||model.calculations.length||model.sql){
      const basis=detail('查看执行依据');
      for(const calculation of model.calculations)basis.append(renderCalculation(calculation));
      for(const step of model.steps){const row=el('section','agent-basis-step');row.append(el('h4','',step.title),el('span','agent-step-receipt',SUCCESS.has(step.status)?'已返回结果':step.status==='failed'||step.status==='error'?'执行失败':'已返回记录'));
        if(step.question||step.label)row.append(el('p','',step.question||step.label));
        const value=step.value;
        if(Array.isArray(value.rows))row.append(el('p','',`本步返回 ${value.rows.length} 行数据。`));
        if(value.expression&&step.tool!=='calculate')row.append(el('p','agent-formula',value.expression));
        if(value.sql){const sql=detail('查看本步 SQL');sql.append(el('pre','agent-code',value.sql));row.append(sql);}
        if(step.dependencies.length)row.append(el('p','agent-source-location','依赖任务：'+step.dependencies.join('、')));basis.append(row);
      }
      if(model.sql){const sql=detail('查看已执行 SQL');sql.append(el('pre','agent-code',model.sql.sql));basis.append(sql);}card.append(basis);
    }
    return card;
  }
  return {summarize,render,markdown};
});
