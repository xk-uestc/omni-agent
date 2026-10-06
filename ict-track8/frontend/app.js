const API = new URLSearchParams(location.search).get("api") || window.ICT8_API_BASE || (location.protocol === "file:" ? "http://127.0.0.1:8030" : location.origin);
const STREAM = new URLSearchParams(location.search).get("stream") !== "false";
const $ = (id) => document.getElementById(id);
const capabilities = [
  {name:"文档问答",group:"资料",endpoint:"omni",hint:"原文片段、页码与位置",questions:["销售额的统计口径是什么","资料中列出了哪些考核要求"]},
  {name:"基础问数",group:"问数",hint:"指标、筛选与聚合",questions:["2025年华东地区的销售额是多少","2025年各地区销售额排名","2025年华南地区的订单数"]},
  {name:"多轮追问",group:"问数",hint:"沿用条件、补充条件与比较结果",questions:["2025年华东地区的销售额","那华南呢","比较刚才两次查询","换成2024年"]},
  {name:"比较分析",group:"分析",hint:"同比、环比与占比",questions:["2025年各地区销售额占比","2025年华东地区销售额同比","2025年3月销售额环比"]},
  {name:"跨源依据",group:"分析",hint:"结构化结果与文档证据",questions:["2025年华东地区的销售额政策","销售额的统计口径是什么"]},
  {name:"澄清补全",group:"分析",hint:"口径不足时由后端提示",questions:["增长率是多少","各地区排名","2025年销售额同比","查看待补问题"]},
];
const conversationScope=window.ConversationSessions.scopeFor(API,location.href);
const conversationSessions=window.ConversationSessions.create({scope:conversationScope,
  legacyKeys:conversationScope===location.origin?[{key:"ict8_lattice_session",label:"原问数会话"},{key:"ict8.omni-session",label:"原文档会话"}]:[]});
let sessionId = conversationSessions.current(),conversationSessionPicker=null;
let activeController = null, busy = false, activeCapability = capabilities[1], turns = [];
let latestContext = null, independentNext = false;
function updateContextComposer(){
  const host=$("contextComposer"),independent=independentNext||!$("useContext").checked;
  const state=window.ConversationContext.composerState(latestContext,independent);
  host.hidden=!latestContext&&!independent;host.replaceChildren();
  host.append(el("b","",state.title),el("span","context-composer-detail",state.detail));
  const action=el("button","context-mode-action",independent?"恢复追问":"独立提问");action.type="button";action.disabled=busy;
  action.onclick=()=>{if(busy)return;independentNext=!independent;$("useContext").checked=true;updateContextComposer();$("q").focus();};
  host.append(action);$("q").placeholder=state.placeholder;
  const catalog=el("button","context-mode-action","查看待补问题");catalog.type="button";catalog.disabled=busy;
  catalog.onclick=()=>{if(busy)return;independentNext=false;$("useContext").checked=true;ask("查看待补问题");};host.append(catalog);
}
let lastAskCapability = activeCapability;
let liveSchema = { tables: [], source: null };
let schemaLoadPromise = Promise.resolve();
const sourceViewerCleanups = new Map();
const toolTimelineCleanups = new Set();
function clearToolTimelines(){for(const cleanup of toolTimelineCleanups)cleanup();toolTimelineCleanups.clear();}
function clearSourceViewers(within){
  for(const [viewer,dispose] of sourceViewerCleanups){if(!within||within.contains(viewer))dispose();}
}
window.addEventListener("pagehide",()=>{clearSourceViewers();clearToolTimelines();});

function el(tag, cls, value) { const n=document.createElement(tag); if(cls)n.className=cls; if(value!==undefined)n.textContent=String(value); return n; }
function detailText(value) { return typeof value==="string"?value:value?.message||value?.code||"请求失败"; }
async function json(response) { const data=await response.json().catch(()=>({})); if(!response.ok)throw Error(detailText(data.detail)); return data; }
function post(path, body, signal) { return fetch(API+path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal}).then(json); }

function selectCapability(capability) {
  activeCapability=capability; $("capTitle").textContent=capability.name;
  if(capability.endpoint!=="omni")lastAskCapability=capability;
  setTab(false);
  const caps=$("caps"),plates=$("plates"),suggests=$("suggests"); caps.replaceChildren(); plates.replaceChildren(); suggests.replaceChildren();
  let group="";
  for(const c of capabilities) {
    if(c.group!==group){ group=c.group; caps.append(el("div","nav-label",group)); }
    const b=el("button","cap"+(c===capability?" on":"")); b.type="button";
    b.dataset.endpoint=c.endpoint||"agent";
    b.append(el("i","",c.group==="问数"?"SQL":c.group==="资料"?"文档":"分析"),el("span","",c.name)); b.onclick=()=>selectCapability(c); caps.append(b);
  }
  for(const question of capability.questions){
    const b=el("button","plate",question);b.type="button";b.onclick=()=>ask(question);plates.append(b);
    const s=el("button","suggest");s.type="button";s.append(el("b","",question),el("span","",capability.hint));s.onclick=()=>ask(question);suggests.append(s);
  }
}
async function loadMeta() {
  try {
    const [health,schema]=await Promise.all([fetch(API+"/health").then(json),fetch(API+"/api/v1/nl2sql/schema").then(json)]);
    liveSchema={tables:Array.isArray(schema.tables)?schema.tables:[],source:schema.source||null};
    $("apiHealth").dataset.state=health.ok?"ok":"error";$("healthText").textContent=health.ok?"API 已连接":"API 异常";
    const tables=schema.tables||[],box=$("schema");box.replaceChildren();
    box.append(el("b","",health.database||"数据库"),el("div","",`${tables.length} 张表 · ${schema.source||"当前数据源"}`));
    tables.slice(0,6).forEach(t=>box.append(el("div","",`${t.name} · ${t.row_count??"?"} 行`)));
  } catch { liveSchema={tables:[],source:null};$("apiHealth").dataset.state="error";$("healthText").textContent="API 不可达";$("schema").textContent="无法读取当前数据源"; }
}
function renderSchemaDiagram(host,plan,links){
  const draw=()=>{
    if(!host.isConnected)return;
    if(!window.SchemaSvg){host.replaceChildren(el("p","schema-empty","数据库结构图组件未加载。"));return;}
    host.replaceChildren(window.SchemaSvg.render(liveSchema,plan,links));
  };
  if(liveSchema.tables.length){requestAnimationFrame(draw);return;}
  host.replaceChildren(el("p","schema-empty","正在读取当前数据库字段…"));
  schemaLoadPromise.then(()=>requestAnimationFrame(draw));
}
function renderTurns(){ const list=$("turns");list.replaceChildren();turns.forEach((t,i)=>{
  const li=el("li"),b=el("button","turn-item");b.type="button";b.append(el("div","",`${i+1}. ${t.question}`),el("div","eff",t.status));
  b.onclick=()=>t.element.scrollIntoView({behavior:"smooth",block:"start"});li.append(b);list.append(li);
}); }
function beginTurn(question){
  $("empty").style.display="none";
  const user=el("div","turn user");user.append(el("div","bubble",question));
  const assistant=el("div","turn assistant"),body=el("div","agent-inspection-host"),title=el("span"),wait=el("span");
  const live=window.ToolTimeline.create();toolTimelineCleanups.add(live.dispose);
  const answer=el("div");assistant.append(live.root,body,answer);$("feed").append(user,assistant);
  const turn={question,status:"进行中",element:user};turns.push(turn);renderTurns();$("thread").scrollTop=$("thread").scrollHeight;
  return {body,wait,title,answer,turn,live};
}
function auditCodeValue(value,kind){
  return el("code",`audit-code-value audit-code-${kind}`,value);
}
function summaryRow(box,label,value,codeKind){
  const missing=value==null||value==="";
  const output=missing?"未返回":String(value);
  const rendered=codeKind&&!missing?auditCodeValue(output,codeKind):el("span","",output);
  box.append(el("span","key",label),rendered);
}
function renderAudit(body,data){
  if(data.route==="document")return;
  if(data.route==="comparison"){
    const evidence=data.structured?.comparison_evidence;
    if(!evidence)return;
    const section=el("section","conversation-scope"),comparisonSession=sessionId;section.append(el("b","","两次查询的比较依据"));
    const edit=data.structured?.edit_evidence;
    const batch=data.structured?.batch_edit_evidence;
    section.append(el("p","",`基准：${evidence.baseline}；分组：${evidence.alignment==="month_of_year"?"按月份对齐":"按相同分组键对应"}。${batch?"两项查询已完成并同时更新。":edit?"本轮已重新执行所选查询，另一侧保留原结果。":"使用已显示的结果，没有重新执行 SQL。"}`));
    if(edit)section.append(el("p","",`本轮修改：${edit.previous_question} → ${edit.replacement_question}`));
    for(const change of batch||[])section.append(el("p","",`来源 ${change.source_index+1}：${change.previous_question} → ${change.replacement_question}`));
    for(const source of evidence.sources||[]){
      const details=el("details");details.append(el("summary","",`${source.role} · ${source.question}`),el("pre","sqlbox",source.sql));
      const referenceLabel=window.ConversationContext?.comparisonReferenceLabel(source);
      if(referenceLabel)details.append(el("p","context-request",referenceLabel));
      details.append(el("p","",`原查询参数：${JSON.stringify(source.parameters||[])}`));section.append(details);
      const prefix=window.ConversationContext?.comparisonEditPrompt(source.role);
      if(prefix){
        const tools=el("div","comparison-controls"),button=el("button","opt",`修改${source.role==="基准值"?"基准":"比较"}查询`);button.type="button";
        button.onclick=()=>{if(busy||comparisonSession!==sessionId)return;$("q").value=prefix;$("q").focus();};
        tools.append(button);details.append(tools);
      }
    }
    const actions=el("div","clarification-options comparison-controls");
    for(const action of window.ConversationContext?.comparisonActions(data)||[]){
      const button=el("button","plate",action.label);button.type="button";
      button.onclick=()=>{if(!busy&&comparisonSession===sessionId)ask(action.question);};actions.append(button);
    }
    section.append(actions);
    body.append(section);return;
  }
  if(window.QueryJourney){
    if(!data.structured?.sql)return;
    body.append(window.QueryJourney.render(data,liveSchema,schemaLoadPromise.then(()=>liveSchema)));return;
  }
  const structured=data.structured||{},plan=structured.plan||{},provenance=structured.provenance||{},links=provenance.field_links||plan.links||[];
  const audit=el("div","audit"),intro=el("div","audit-intro");intro.append(el("b","","查询过程"),el("span","","本次查询的处理详情"));audit.append(intro);
  const mapping=el("section","astep"),mappingBody=el("div","bd");mapping.append(el("h4","","① 问题理解与字段选择"));
  mappingBody.append(el("div","qline",data.effective_question||structured.rewritten_question||data.question||""));
  if(links.length){
    const wrap=el("div","audit-table-wrap"),table=el("table","audit-table"),head=el("thead"),tr=el("tr");
    ["原词","角色","目标字段","分数"].forEach(x=>tr.append(el("th","",x)));head.append(tr);table.append(head);
    const tbody=el("tbody");links.forEach(link=>{
      const row=el("tr"),source=String(link.source_text??""),role=String(link.role??""),target=`${link.table||""}.${link.column||""}`;
      const sourceCell=el("td"),roleCell=el("td"),targetCell=el("td"),scoreCell=el("td");
      const machineToken=/^[A-Za-z_][A-Za-z0-9_.$:-]*$/;
      sourceCell.append(machineToken.test(source)?auditCodeValue(source,"identifier"):document.createTextNode(source));
      roleCell.append(machineToken.test(role)?auditCodeValue(role,"source"):document.createTextNode(role));
      targetCell.append(target!=="."?auditCodeValue(target,"identifier"):document.createTextNode(""));
      scoreCell.append(link.score==null?document.createTextNode(""):auditCodeValue(String(link.score),"number"));
      row.append(sourceCell,roleCell,targetCell,scoreCell);tbody.append(row);
    });
    table.append(tbody);wrap.append(table);mappingBody.append(wrap);
  }else mappingBody.append(el("p","empty-state","本次未返回字段映射"));mapping.append(mappingBody);audit.append(mapping);
  const planning=el("section","astep"),planningBody=el("div","bd"),summary=el("div","audit-summary");planning.append(el("h4","","② 查询方案与执行说明"));
  summaryRow(summary,"数据表",plan.table,"identifier");
  summaryRow(summary,"指标",plan.metric_label||plan.metric_column,plan.metric_label?null:"identifier");
  summaryRow(summary,"聚合",plan.metric_function,"function");
  const dimensions=Object.values(plan.dimension_labels||{}).join("、");
  summaryRow(summary,"维度",dimensions||((plan.dimensions||[]).join("、")),dimensions?null:"identifier");
  summaryRow(summary,"方案来源",plan.planner_source,"source");
  summaryRow(summary,"数据来源",provenance.database,"database");
  planningBody.append(summary);if(structured.explanation?.length)planningBody.append(el("p","heads",structured.explanation.join("；")));
  const schemaHost=el("div","schema-mount");planningBody.append(schemaHost);renderSchemaDiagram(schemaHost,plan,links);
  planning.append(planningBody);audit.append(planning);
  if(structured.sql){const sql=el("section","astep"),content=el("div","bd");sql.append(el("h4","","③ SQL 与执行结果"));
    content.append(el("pre","sqlbox",`${structured.sql}\n\n-- 参数 ${JSON.stringify(structured.parameters||[],null,2)}`));
    if(provenance.query_hash){const hash=el("p","schema-caption");hash.append(document.createTextNode("查询哈希："),auditCodeValue(provenance.query_hash,"identifier"));content.append(hash);}sql.append(content);audit.append(sql);}
  body.append(audit);
}
function renderTable(host,structured){
  const browser=window.ResultBrowser?.render(structured,{api:API,onFocus:detail=>host.closest(".assistant")?.querySelector(".query-journey")?.dispatchEvent(new CustomEvent("query-result-focus",{detail}))});
  if(browser){host.append(browser);return;}
  const columns=structured.columns||[],rows=structured.rows||[];if(!columns.length)return;
  if(!rows.length){host.append(el("p","empty-state","查询完成，当前条件下没有数据行。"));return;}
  const wrap=el("div","audit-table-wrap"),table=el("table","data"),head=el("thead"),tr=el("tr");
  columns.forEach(c=>tr.append(el("th","",c)));head.append(tr);table.append(head);
  const tbody=el("tbody");rows.forEach((row,index)=>{const line=el("tr");columns.forEach(c=>{
    const cell=el("td"),value=el("button","query-result-cell",row[c]==null?"—":row[c]);value.type="button";value.title=`查看结果列 ${c} 的字段来源`;
    value.onclick=()=>host.closest(".assistant")?.querySelector(".query-journey")?.dispatchEvent(new CustomEvent("query-result-focus",{detail:{row:index,column:c}}));cell.append(value);line.append(cell);
  });tbody.append(line);});
  table.append(tbody);wrap.append(table);host.append(wrap);
}
function normalizeOmniResponse(data){
  if(!data.route||!data.result)return data;
  const result=data.result;
  const sqlSummary=data.route==='sql'&&result.status==='ok'?
    result.rows?.length?`查询返回 ${result.rows.length} 行结果，具体数据如下。`:"查询完成，当前条件下没有找到数据。":null;
  return {...data,omni_response:true,answer:result.answer||result.clarification||sqlSummary||result.explanation?.join("；")||"本次未返回文字说明。",
    structured:["sql","comparison"].includes(data.route)?result:{},document_evidence:result.citations||[],
    visual_source_proof:result.visual_source_proof,native_row_proof:result.native_row_proof,native_total_proof:result.native_total_proof,answer_mode:result.answer_mode,
    answer_span_result:result.answer_span_result};
}
function sourcePageViewer(host,item,part,nativeSelection,totalSelection,financialSelection){
  const metadata=item.metadata||{},did=metadata.document_id||item.document_id,page=metadata.page_no;
  const sourceSha=metadata.source_sha256;
  if(typeof did!=="string"||!Number.isInteger(page)||page<1||!/^[a-f0-9]{64}$/.test(sourceSha||""))return;
  const details=el("details","source-page-viewer"),summary=el("summary","",`查看第 ${page} 页与引用位置`);
  const info=el("p","schema-caption"),stage=el("div","source-page-stage"),image=el("img");
  image.alt=`${item.title||"资料"} · 第 ${page} 页`;image.hidden=true;stage.append(image);details.append(summary,info,stage);host.append(details);
  let controller=null,blobUrl=null,revision=0;
  function release(){controller?.abort();controller=null;revision++;image.hidden=true;image.removeAttribute("src");stage.querySelector("svg")?.remove();if(blobUrl){URL.revokeObjectURL(blobUrl);blobUrl=null;}}
  const onToggle=async()=>{
    if(!details.open){release();return;}
    release();controller=new AbortController();const signal=controller.signal,current=revision;
    info.textContent="正在读取并核对原页…";
    try{
      const base=new URL(API,location.href),path=`/api/v1/knowledge/documents/${encodeURIComponent(did)}`;
      const response=await fetch(new URL(path+"/visual-evidence",base),{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({page_no:page,expected_source_sha256:sourceSha}),signal});
      const manifest=await json(response);
      if(manifest.document_id!==did||manifest.page_no!==page||manifest.source_sha256!==sourceSha||!/^[a-f0-9]{64}$/.test(manifest.render_sha256||""))throw Error("页图来源与回答引用不一致，请重新查询。");
      const uri=new URL(manifest.png_uri,base);
      if(uri.origin!==base.origin||uri.pathname!==path+`/pages/${page}/visual.png`||uri.searchParams.get("source_sha256")!==sourceSha||uri.searchParams.get("render_sha256")!==manifest.render_sha256)throw Error("页图地址未绑定本次引用。");
      const png=await fetch(uri,{signal});if(!png.ok){await json(png);throw Error("原页读取失败。");}
      if(!png.headers.get("Content-Type")?.startsWith("image/png"))throw Error("原页格式不正确。");
      const blob=await png.blob();if(blob.size>12*1024*1024)throw Error("页图超过显示预算。");
      const hash=await crypto.subtle.digest("SHA-256",await blob.arrayBuffer());
      const sha=Array.from(new Uint8Array(hash),v=>v.toString(16).padStart(2,"0")).join("");
      if(sha!==manifest.render_sha256)throw Error("页图内容核对失败。");
      if(signal.aborted||current!==revision||!details.open)return;
      blobUrl=URL.createObjectURL(blob);image.src=blobUrl;image.hidden=false;
      const nativeBoxes=metadata.native_row&&nativeSelection?window.NativeRowOverlay?.model(metadata,manifest,nativeSelection):
        metadata.native_total_annotation&&totalSelection?window.NativeRowOverlay?.totalModel(metadata,manifest,totalSelection):
        metadata.fact?.value_kind==='native_grouped_financial_cell_literal'&&financialSelection?
          window.NativeRowOverlay?.factModel(metadata,manifest,financialSelection):null;
      const box=part?.bbox_normalized,matched=Boolean(nativeBoxes?.length)||(part?.page_no===page&&part.source_sha256===sourceSha&&part.render_sha256===sha);
      const valid=Array.isArray(box)&&box.length===4&&box.every(v=>typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=1)&&box[0]<box[2]&&box[1]<box[3];
      if(matched&&(valid||nativeBoxes?.length)){
        const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
        svg.setAttribute("viewBox","0 0 1 1");svg.setAttribute("preserveAspectRatio","none");svg.setAttribute("aria-label","本次引用的原页位置");svg.setAttribute("role","img");
        for(const item of nativeBoxes||[{bbox_normalized:box,role:'value'}]){
          const bounds=item.bbox_normalized,shape=document.createElementNS(svg.namespaceURI,"rect");
          Object.entries({x:bounds[0],y:bounds[1],width:bounds[2]-bounds[0],height:bounds[3]-bounds[1],fill:item.role==='subject'?"rgba(70,130,220,.12)":"rgba(255,199,0,.18)",stroke:item.role==='subject'?"#4c83cd":"#d49a00","stroke-width":.003}).forEach(([key,value])=>shape.setAttribute(key,String(value)));
          if(item.label){const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=`${item.label}: ${item.text}`;shape.append(title);}
          svg.append(shape);
        }stage.append(svg);
      }
      info.textContent=`第 ${page} 页 · 原文件 ${sourceSha.slice(0,16)}… · ${nativeBoxes?.length?"实体与查询字段已定位；原件坐标及页面映射已核对。":matched&&valid?"引用位置已高亮；位置由独立视觉模型复核。":"原页内容已核对，本引用未提供匹配的位置标注。"}`;
    }catch(error){if(!signal.aborted&&current===revision)info.textContent=error.message||"原页读取失败。";}
  };
  details.addEventListener("toggle",onToggle);
  sourceViewerCleanups.set(details,()=>{release();details.removeEventListener("toggle",onToggle);sourceViewerCleanups.delete(details);});
}
function renderDocumentEvidence(host,data){
  const items=data.document_evidence||[];if(!items.length)return;
  const docs=el("div","docs");
  if(data.answer_mode==="visual_source_model_reviewed")docs.append(el("p","schema-caption","原页读取 · 独立视觉模型复核；下面展示返回的原文片段，可展开原页核对数值与单位。"));
  if(data.answer_mode==="native_row_comparison_model_reviewed")docs.append(el("p","schema-caption","原页表格比较 · 样本编号与单位已核对，数值关系由服务器计算；下方保留两页原始记录。"));
  if(data.answer_mode==="native_row_selection_model_reviewed")docs.append(el("p","schema-caption","原页字段筛选 · 已遍历本次候选表格内匹配记录，并独立复核输出字段；可展开原页查看对应位置。"));
  if(data.answer_mode==="source_multi_span_model_reviewed")docs.append(el("p","schema-caption","回答由多段原文组成，各子问与所选资料中的相关项目已分别复核。可展开来源核对；本次范围不包含未检索的页面。"));
  items.forEach(item=>{
    const row=el("article","doc"),metadata=item.metadata||{},did=metadata.document_id||item.document_id,page=metadata.page_no;
    row.append(el("div","ttl",item.title||"资料片段"),el("div","meta",Number.isInteger(page)?`第 ${page} 页`:"原文引用"),el("div","snip source-quote",item.snippet||""));
    if(typeof did==="string"&&item.source_uri===`/api/v1/knowledge/documents/${encodeURIComponent(did)}/original`){
      const link=el("a","source-original","查看原文件");link.href=new URL(`/api/v1/knowledge/documents/${encodeURIComponent(did)}/original`,new URL(API,location.href)).href;link.target="_blank";link.rel="noopener noreferrer";row.append(link);
    }
    const candidate=data.visual_source_proof?.parts?.[item.citation_id-1];
    const part=candidate?.quote===item.snippet?candidate:null;
    const financialSelection=items.filter(c=>c.metadata?.document_id===did&&c.metadata?.page_no===page&&c.metadata?.source_sha256===metadata.source_sha256).map(c=>c.metadata?.fact).filter(Boolean);
    sourcePageViewer(row,item,part,data.native_row_proof?.selection,data.native_total_proof?.selected_annotations,financialSelection);docs.append(row);
  });host.append(docs);
}
function renderResult(view,data,originalQuestion){
  data=normalizeOmniResponse(data);
  latestContext=window.ConversationContext.nextContext(latestContext,data);independentNext=false;updateContextComposer();
  document.querySelectorAll(".clarify button, .clarify input, .comparison-controls button").forEach(control=>control.disabled=true);
  view.live?.finish(data);
  view.wait.remove();view.title.textContent=data.status==="clarification"?"需要补充条件":`已完成 · ${data.latency_ms??"—"} ms`;view.turn.status=data.status||"完成";renderTurns();
  if(data.structured?.sql&&liveSchema.tables.length){
    view.live.record({id:'client:database.schema',tool:'database.schema',status:'success',executed:true,
      summary:'查看当前页面已加载的数据库结构，核对本次 SQL 使用的表与字段。',
      input:{source:liveSchema.source},
      output:{source:liveSchema.source,origin:'当前页面已加载的结构信息；本步未重新请求数据库',tables:liveSchema.tables}});
    if(window.SchemaSvg){
      const schemaDetail=el('details','agent-inspector'),schemaLabel=el('summary','','查看完整数据库字段 SVG');
      const plan=data.structured.plan||{};
      schemaDetail.append(schemaLabel,window.SchemaSvg.render(liveSchema,plan,data.structured.provenance?.field_links||plan.links||[]));
      view.live.attach('database.schema',schemaDetail);
    }
  }
  const inspector=el("details","agent-inspector"),inspectorBody=el("div"),inspectorSummary=el("summary");
  inspectorSummary.innerHTML='<svg class="agent-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6M4 12c0 4 16 4 16 0"/></svg>';
  inspectorSummary.append(document.createTextNode(data.route==='comparison'?"查看比较依据与 SQL":"查看字段 SVG、SQL 与数据关系"));
  renderAudit(inspectorBody,data);
  if(inspectorBody.childNodes.length){
    inspector.append(inspectorSummary,inspectorBody);
    if(!view.live.attach('nl2sql',inspector))view.body.append(inspector);
  }
  const answer=el("div","answer"),structured=data.structured||{};answer.append(el("p","",data.answer||"后端未返回文字说明"));
  if(data.route==='tasks'){
    view.title.textContent="待补问题";view.turn.status="已查看";
    renderTurns();
    const tasks=el("ul","context-changes pending-task-list"),catalogSession=sessionId;
    for(const item of window.ConversationContext.catalogItems(data)){
      const row=el("li"),controls=el("div","query-reference");row.style.borderBottom="1px solid var(--line)";
      row.append(el("b","",item.question),el("p","clarify-help",item.clarification));
      if(item.expiresAt)controls.append(el("small","",`保留至 ${new Date(item.expiresAt*1000).toLocaleString()}`));
      if(item.resumeQuestion){
        const button=el("button","context-mode-action","恢复这个问题");button.type="button";
        button.onclick=()=>{if(busy||catalogSession!==sessionId)return;independentNext=false;$("useContext").checked=true;ask(item.resumeQuestion);};controls.append(button);
      }else controls.append(el("small","","当前无法恢复，请重新确认完整问题。"));
      row.append(controls);tasks.append(row);
    }
    answer.append(tasks);clearSourceViewers(view.answer);view.answer.replaceChildren(answer);$("thread").scrollTop=$("thread").scrollHeight;return;
  }
  const context=window.ConversationContext.describe(data),scope=el("details","conversation-scope");
  scope.setAttribute("aria-label","本轮上下文与查询条件");
  scope.append(el("summary","","本轮使用的条件与来源"));
  scope.append(el("b","",context.label),el("p","",context.effective),el("small","",context.note));
  if(context.actual&&context.actual!==context.effective)scope.append(el("p","context-request",`本次输入：${context.actual}`));
  if(context.changes.length){
    const changes=el("ul","context-changes");changes.setAttribute("aria-label","本次修改的条件");
    for(const change of context.changes){
      const row=el("li");row.append(el("span","",change.label+'：'),el("del","",change.before),el("span","context-arrow"," → "),el("strong","",change.after));
      if(change.field)row.title=change.field;changes.append(row);
    }scope.append(changes);
  }
  if(context.base&&context.base!==context.effective){const source=el("details");source.append(el("summary","","查看引用的问题"),el("p","",context.base));scope.append(source);}
  answer.prepend(scope);
  const referencePrompt=window.ConversationContext.queryReferencePrompt(data)||window.ConversationContext.documentReferencePrompt(data);
  if(referencePrompt){
    const referenceSession=sessionId,reference=el("div","query-reference");
    const isDocument=data.route==='document';
    reference.append(el("small","",`${isDocument?'文档问题':'查询'}编号：${isDocument?data.document_reference_id:data.query_reference_id}`));
    const button=el("button","context-mode-action",isDocument?"继续问这份文档":"引用这次查询");button.type="button";
    button.onclick=()=>{if(busy||referenceSession!==sessionId)return;independentNext=false;$("useContext").checked=true;
      $("q").value=referencePrompt;updateContextComposer();$("q").focus();};
    reference.append(button,el("small","",isDocument?"填写后续问题；会重新核对原文版本并限定这份文档。":"填写编号后的修改条件；仅在本会话保留的历史内有效。"));scope.append(reference);
  }
  const pendingReference=window.ConversationContext.pendingReferencePrompt(data);
  if(pendingReference){
    const referenceSession=sessionId,reference=el("div","query-reference");
    reference.append(el("small","",`待补编号：${data.query_reference_id}`));
    const button=el("button","context-mode-action","恢复这轮待补条件");button.type="button";
    button.onclick=()=>{if(busy||referenceSession!==sessionId)return;independentNext=false;$("useContext").checked=true;ask(pendingReference);};
    reference.append(button,el("small","","编号对应这一轮的条件；默认保留24小时，每会话最多64条。完成后关联编号关闭，清空会话后不可恢复。"));scope.append(reference);
  }
  if(structured.status==="ok")renderTable(answer,structured);
  [...(structured.notices||[]),...(data.warnings||[])].forEach(w=>answer.append(el("p","warn",w)));
  if(structured.status==="clarification"){
    const options=el("div","clarify"),pendingQuestion=window.ConversationContext.clarificationQuestion(data,originalQuestion),pendingSession=sessionId;
    options.setAttribute("aria-label","补充查询条件");
    const guide=window.ConversationContext.clarificationGuide(data);
    if(guide){
      const description=el("div","clarify-description");
      description.append(el("b","",`请补充${guide.missing}`),el("p","",guide.message));answer.append(description);
    }
    for(const action of window.ConversationContext.comparisonActions(data)){
      const button=el("button","opt",action.label);button.type="button";
      button.onclick=()=>{if(!busy&&pendingSession===sessionId)ask(action.question);};options.append(button);
    }
    const confirm=(option,time)=>{if(busy||pendingSession!==sessionId)return;return clarify(pendingQuestion,structured.clarification_code,option,true,time);};
    (structured.clarification_options||[]).forEach((option,index)=>{
      const b=el("button","opt",window.ConversationContext.optionLabel(option,index));b.type="button";b.onclick=()=>{
        if(/^选择对话来源编号q_[a-f0-9]{32}$/.test(option.question||'')){
          if(!busy&&pendingSession===sessionId)ask(option.question);return;
        }
        if(!window.ConversationContext.timeOption(option)){confirm(option);return;}
        options.querySelector("form")?.remove();
        const editor=el("form","clarify-time"),input=el("input"),submit=el("button","opt","确认时间");
        input.type="text";input.required=true;input.maxLength=32;input.setAttribute("aria-label","查询年份或月份");
        input.placeholder=option.value==="month"?"例如 2025-03":"例如 2025";submit.type="submit";
        editor.append(input,submit);options.append(editor);input.focus();
        editor.onsubmit=event=>{event.preventDefault();confirm(option,input.value.trim());};
      };options.append(b);
    });
    if(data.route==='sql'&&(structured.clarification_options||[]).length)
      answer.append(el('p','clarify-help','也可以输入“选第一个”或“选第二个”；选择年份或月份后仍需填写具体时间。'));
    if(guide?.canExplain){
      const explain=el("button","opt","解释一下这些选项");explain.type="button";
      explain.onclick=()=>{if(!busy&&pendingSession===sessionId)ask("选项有什么区别");};options.append(explain);
      answer.append(el("p","clarify-help",data.route==='comparison'
        ?'先确认要修改的查询及所需条件；可以随时取消本次修改，返回原比较结果。'
        :'补充前也可以修改条件：输入“时间改成2024年”“增加字段筛选为取值”或“删除字段筛选”。字段和取值以当前数据库为准；多项修改不能确认时，原条件会保留。'));
    }
    if(guide?.canCancel&&!window.ConversationContext.comparisonActions(data).some(action=>action.question==='取消修改')){
      const cancel=el("button","opt","取消本次修改");cancel.type="button";
      cancel.onclick=()=>{if(!busy&&pendingSession===sessionId)ask("取消修改");};options.append(cancel);
    }
    answer.append(options,el("p","clarify-help",guide?.next||"请选择上面的选项，或在输入框补充具体条件。补充后将继续当前问题。"));
  }
  renderDocumentEvidence(answer,data);
  if(data.structured?.status==='ok'&&data.structured?.sql&&window.QueryResultViz){
    const visual=window.QueryResultViz.render(data.structured);
    if(visual){
      const detail=el('details','agent-inspector'),label=el('summary','','查看可视化');detail.append(label,visual);
      view.live.record({id:'client:visualization',tool:'visualization.build',status:'success',executed:true,
        summary:'根据本次返回的数据构建可视化。',input:{columns:data.structured.columns,row_count:data.structured.rows?.length||0},
        output:{renderer:'本地可视化组件',data_scope:'本次查询预览',row_count:data.structured.rows?.length||0}});
      view.live.attach('visualization.build',detail);
    }
  }
  clearSourceViewers(view.answer);view.answer.replaceChildren(answer);$("thread").scrollTop=$("thread").scrollHeight;
}
function parseSse(block){let type="message";const lines=[];block.split("\n").forEach(line=>{if(line.startsWith("event:"))type=line.slice(6).trim();if(line.startsWith("data:"))lines.push(line.slice(5).trimStart());});return lines.length?{type,data:JSON.parse(lines.join("\n"))}:null;}
async function streamQuery(question,useContext,completeResults,view,signal){
  const response=await fetch(API+"/api/v1/omni/query/stream",{method:"POST",headers:{"Content-Type":"application/json",Accept:"text/event-stream"},body:JSON.stringify({question,session_id:sessionId,reset_context:!useContext,complete_results:completeResults}),signal});
  if(!response.ok){await json(response);throw Error("查询失败");}
  if(!response.body)throw Error("当前连接未提供执行事件，请切换非流式模式重试。");
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="",result=null;
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done}).replace(/\r\n/g,"\n");let boundary;
    while((boundary=buffer.indexOf("\n\n"))>=0){const event=parseSse(buffer.slice(0,boundary));buffer=buffer.slice(boundary+2);if(!event)continue;
      if(event.type==="trace"){view.live?.update(event.data);}if(event.type==="done")result=event.data;if(event.type==="error")throw Error(detailText(event.data.detail));}
    if(done)break;
  }
  if(!result)throw Error("流式连接未返回完整结果");return result;
}
async function run(question,request){
  if(busy)return;busy=true;$("send").disabled=true;
  document.querySelectorAll(".clarify button, .clarify input, .comparison-controls button, .pending-task-list button").forEach(control=>control.disabled=true);
  updateContextComposer();const view=beginTurn(question),controller=new AbortController();activeController=controller;
  try{const data=await request(view,controller.signal);if(!controller.signal.aborted)renderResult(view,data,question);}
  catch(error){if(!controller.signal.aborted){view.live?.fail(error.message||"请求失败");view.wait.remove();view.title.textContent="查询失败";view.turn.status="失败";view.answer.append(el("div","error-line",error.message||"请求失败"));renderTurns();}}
  finally{if(activeController===controller){activeController=null;busy=false;$("send").disabled=false;updateContextComposer();}}
}
function ask(question){const text=question.trim();if(!text)return;const useContext=$("useContext").checked&&!independentNext,completeResults=$("completeResults").checked;
  return run(text,(view,signal)=>STREAM?streamQuery(text,useContext,completeResults,view,signal):post("/api/v1/omni/query",{question:text,session_id:sessionId,reset_context:!useContext,complete_results:completeResults},signal));}
function clarify(question,code,option,omni=true,time){return run(time?`${option.label||option.value}：${time}`:option.label||option.value,async(_view,signal)=>{
  return post("/api/v1/omni/clarify",{original_question:question,clarification_code:code,selected_value:option.value,selected_label:option.label,selected_time:time,session_id:sessionId,complete_results:$("completeResults").checked},signal);
});}
function setTab(doc){
  const docs=activeCapability.endpoint==="omni";
  [["tabAsk",!doc&&!docs],["tabDocs",!doc&&docs],["tabDoc",doc]].forEach(([id,selected])=>{
    $(id).classList.toggle("on",selected);$(id).setAttribute("aria-pressed",String(selected));
  });
  $("thread").style.display=doc?"none":"";$("askComposer").style.display=doc?"none":"";$("view-doc").classList.toggle("on",doc);
}
async function analyzeDocument(){
  const output=$("docRes");output.textContent="正在分析文档…";
  try{const text=$("docText").value,data=await post("/api/v1/documents/analyze",{document_id:"lattice-input",text,pages:[{page_no:1,text,ocr_confidence:Number($("sigConf").value),rotation_degrees:Number($("sigRot").value),skew_degrees:Number($("sigSkew").value),blur_score:Number($("sigBlur").value)}]});
    output.replaceChildren();output.append(el("h3","","文档分析"),el("p","heads",`质量 ${data.quality_score} · 复杂度 ${data.complexity_score}`));
    [["目录",data.headings],["公式",data.formulas],["问题",data.issues]].forEach(([label,items])=>{output.append(el("h4","heads",label));
      if(!items?.length)output.append(el("p","empty-state","本次未返回"));else items.forEach(item=>{
        const description=label==="公式"?`${item.label||"公式"} = ${item.source_expression||"未识别"}（${item.status||"未计算"}${item.error?`：${item.error}`:""}）`:
          label==="目录"?item.text||item.title||"未命名标题":item.message||item.code||JSON.stringify(item);
        output.append(el("p","",description));
      });});
  }catch(error){output.replaceChildren(el("div","error-line",error.message||"分析失败"));}
}
$("form").addEventListener("submit",e=>{e.preventDefault();const question=$("q").value.trim();if(!question||busy)return;$("q").value="";$("q").style.height="auto";ask(question);});
$("q").addEventListener("input",()=>{$("q").style.height="auto";$("q").style.height=`${Math.min(160,$("q").scrollHeight)}px`;});
$("q").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("form").requestSubmit();}});
function switchConversation(next){activeController?.abort();activeController=null;busy=false;$("send").disabled=false;latestContext=null;independentNext=false;$("useContext").checked=true;updateContextComposer();clearSourceViewers();clearToolTimelines();sessionId=next;turns=[];$("feed").replaceChildren($("empty"));$("empty").style.display="";renderTurns();setTab(false);conversationSessionPicker?.refresh();}
function newChat(){try{switchConversation(conversationSessions.start());}catch(error){$("q").setCustomValidity(error.message);$("q").reportValidity();$("q").setCustomValidity("");}}
$("useContext").addEventListener("change",()=>{independentNext=false;updateContextComposer();});
$("newChat").onclick=newChat;$("newChatTop").onclick=newChat;
const conversationSessionHost=el("div","session-picker");$("newChat").insertAdjacentElement("afterend",conversationSessionHost);
conversationSessionPicker=window.ConversationSessions.mount(conversationSessions,conversationSessionHost,switchConversation);
window.addEventListener("pageshow",()=>{const current=conversationSessions.current();if(current!==sessionId)switchConversation(current);else conversationSessionPicker.refresh();});
$("sixDemo").onclick=async()=>{setTab(false);for(const q of ["2025年华东地区的销售额","那华南呢","看看订单数","换成华北","换成2024年","看看销量"])await ask(q);};
$("theme").onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==="dark"?"light":"dark";};
$("tabAsk").onclick=()=>selectCapability(lastAskCapability);
$("tabDocs").onclick=()=>selectCapability(capabilities.find(capability=>capability.endpoint==="omni"));
$("tabDoc").onclick=()=>setTab(true);$("docRun").onclick=analyzeDocument;
selectCapability(activeCapability);schemaLoadPromise=loadMeta();
