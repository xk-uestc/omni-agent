const API = new URLSearchParams(location.search).get("api") || window.ICT8_API_BASE || (location.protocol === "file:" ? "http://127.0.0.1:8030" : location.origin);
const STREAM = new URLSearchParams(location.search).get("stream") !== "false";
const $ = (id) => document.getElementById(id);
const stageNames = {intent:"理解问题与上下文",structured_query:"规划并执行只读 SQL",document_retrieval:"检索文档依据",evidence_fusion:"融合结果与证据",clarification:"等待澄清"};
const capabilities = [
  {name:"基础问数",group:"问数",hint:"指标、筛选与聚合",questions:["2025年华东地区的销售额是多少","2025年各地区销售额排名","2025年华南地区的订单数"]},
  {name:"多轮追问",group:"问数",hint:"沿用或覆盖上一轮口径",questions:["2025年华东地区的销售额","那华南呢","换成2024年"]},
  {name:"比较分析",group:"分析",hint:"同比、环比与占比",questions:["2025年各地区销售额占比","2025年华东地区销售额同比","2025年3月销售额环比"]},
  {name:"跨源依据",group:"分析",hint:"结构化结果与文档证据",questions:["2025年华东地区的销售额政策","销售额的统计口径是什么"]},
  {name:"澄清补全",group:"分析",hint:"口径不足时由后端提示",questions:["增长率是多少","各地区排名","2025年销售额同比"]},
];
const chevron = '<svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 9 6 6 6-6"/></svg>';
let sessionId = sessionStorage.getItem("ict8_lattice_session") || crypto.randomUUID();
let activeController = null, busy = false, activeCapability = capabilities[0], turns = [];
let liveSchema = { tables: [], source: null };
let schemaLoadPromise = Promise.resolve();
sessionStorage.setItem("ict8_lattice_session", sessionId);

function el(tag, cls, value) { const n=document.createElement(tag); if(cls)n.className=cls; if(value!==undefined)n.textContent=String(value); return n; }
function detailText(value) { return typeof value==="string"?value:value?.message||value?.code||"请求失败"; }
async function json(response) { const data=await response.json().catch(()=>({})); if(!response.ok)throw Error(detailText(data.detail)); return data; }
function post(path, body, signal) { return fetch(API+path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),signal}).then(json); }

function selectCapability(capability) {
  activeCapability=capability; $("capTitle").textContent=capability.name;
  const caps=$("caps"),plates=$("plates"),suggests=$("suggests"); caps.replaceChildren(); plates.replaceChildren(); suggests.replaceChildren();
  let group="";
  for(const c of capabilities) {
    if(c.group!==group){ group=c.group; caps.append(el("div","nav-label",group)); }
    const b=el("button","cap"+(c===capability?" on":"")); b.type="button";
    b.append(el("i","",c.group==="问数"?"SQL":"分析"),el("span","",c.name)); b.onclick=()=>selectCapability(c); caps.append(b);
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
  const assistant=el("div","turn assistant"),thought=el("div","thought open"),toggle=el("button","thought-head"),title=el("span","tlabel","正在查询");
  toggle.type="button";toggle.append(title);toggle.insertAdjacentHTML("beforeend",chevron);toggle.onclick=()=>thought.classList.toggle("open");
  const body=el("div","thought-body"),wait=el("div","dots");wait.innerHTML="<i></i><i></i><i></i>";body.append(wait);thought.append(toggle,body);
  const answer=el("div");assistant.append(thought,answer);$("feed").append(user,assistant);
  const turn={question,status:"进行中",element:user};turns.push(turn);renderTurns();$("thread").scrollTop=$("thread").scrollHeight;
  return {body,wait,title,answer,turn};
}
function appendTrace(body,item){
  const step=el("div","step"),button=el("button","step-title"),panel=el("div","step-panel"),tool=el("div","tool");
  button.type="button";button.append(el("span","lab",stageNames[item.stage]||item.stage||"执行阶段"));button.insertAdjacentHTML("beforeend",chevron);
  button.onclick=()=>step.classList.toggle("open");tool.append(el("div","tool-bar",`状态：${item.status||"已完成"}`),el("pre","out trace-json",JSON.stringify(item,null,2)));
  panel.append(tool);step.append(button,panel);body.insertBefore(step,body.querySelector(".dots"));
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
  const structured=data.structured||{},plan=structured.plan||{},provenance=structured.provenance||{},links=provenance.field_links||plan.links||[];
  const audit=el("div","audit"),intro=el("div","audit-intro");intro.append(el("b","","查询审计"),el("span","","来自本次后端响应"));audit.append(intro);
  const mapping=el("section","astep"),mappingBody=el("div","bd");mapping.append(el("h4","","① 问题改写与字段映射"));
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
  const planning=el("section","astep"),planningBody=el("div","bd"),summary=el("div","audit-summary");planning.append(el("h4","","② 查询计划与安全边界"));
  summaryRow(summary,"数据表",plan.table,"identifier");
  summaryRow(summary,"指标",plan.metric_label||plan.metric_column,plan.metric_label?null:"identifier");
  summaryRow(summary,"聚合",plan.metric_function,"function");
  const dimensions=Object.values(plan.dimension_labels||{}).join("、");
  summaryRow(summary,"维度",dimensions||((plan.dimensions||[]).join("、")),dimensions?null:"identifier");
  summaryRow(summary,"规划来源",plan.planner_source,"source");
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
  const columns=structured.columns||[],rows=structured.rows||[];if(!columns.length)return;
  if(!rows.length){host.append(el("p","empty-state","查询完成，当前条件下没有数据行。"));return;}
  const wrap=el("div","audit-table-wrap"),table=el("table","data"),head=el("thead"),tr=el("tr");
  columns.forEach(c=>tr.append(el("th","",c)));head.append(tr);table.append(head);
  const tbody=el("tbody");rows.forEach(row=>{const line=el("tr");columns.forEach(c=>line.append(el("td","",row[c]==null?"—":row[c])));tbody.append(line);});
  table.append(tbody);wrap.append(table);host.append(wrap);
}
function renderResult(view,data,originalQuestion){
  view.wait.remove();view.title.textContent=`已完成 · ${data.latency_ms??"—"} ms`;view.turn.status=data.status||"完成";renderTurns();
  if(!view.body.querySelector(".step")) (data.trace||[]).forEach(t=>appendTrace(view.body,t));renderAudit(view.body,data);
  const answer=el("div","answer"),structured=data.structured||{};answer.append(el("p","",data.answer||"后端未返回文字说明"));
  if(structured.status==="ok")renderTable(answer,structured);
  [...(structured.notices||[]),...(data.warnings||[])].forEach(w=>answer.append(el("p","warn",w)));
  if(structured.status==="clarification"){
    const options=el("div","clarify");(structured.clarification_options||[]).forEach(option=>{
      const b=el("button","opt",option.label||option.value);b.type="button";b.onclick=()=>clarify(originalQuestion,structured.clarification_code,option);options.append(b);
    });answer.append(options);
  }
  if(data.document_evidence?.length){const docs=el("div","docs");data.document_evidence.forEach(item=>{
    const card=el("article","doc");card.append(el("div","meta",`${item.source_uri||"文档"} · score ${item.score??"—"}`),el("div","ttl",item.title||"文档片段"),el("div","snip",item.snippet||""));docs.append(card);
  });answer.append(docs);}
  view.answer.replaceChildren(answer);$("thread").scrollTop=$("thread").scrollHeight;
}
function parseSse(block){let type="message";const lines=[];block.split("\n").forEach(line=>{if(line.startsWith("event:"))type=line.slice(6).trim();if(line.startsWith("data:"))lines.push(line.slice(5).trimStart());});return lines.length?{type,data:JSON.parse(lines.join("\n"))}:null;}
async function streamQuery(question,useContext,view,signal){
  const response=await fetch(API+"/api/v1/agent/query/stream",{method:"POST",headers:{"Content-Type":"application/json",Accept:"text/event-stream"},body:JSON.stringify({question,session_id:sessionId,use_context:useContext}),signal});
  if(!response.ok){await json(response);throw Error("查询失败");}
  if(!response.body)return post("/api/v1/agent/query",{question,session_id:sessionId,use_context:useContext},signal);
  const reader=response.body.getReader(),decoder=new TextDecoder();let buffer="",result=null;
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done}).replace(/\r\n/g,"\n");let boundary;
    while((boundary=buffer.indexOf("\n\n"))>=0){const event=parseSse(buffer.slice(0,boundary));buffer=buffer.slice(boundary+2);if(!event)continue;
      if(event.type==="trace")appendTrace(view.body,event.data);if(event.type==="done")result=event.data;if(event.type==="error")throw Error(detailText(event.data.detail));}
    if(done)break;
  }
  if(!result)throw Error("流式连接未返回完整结果");return result;
}
async function run(question,request){
  if(busy)return;busy=true;$("send").disabled=true;const view=beginTurn(question),controller=new AbortController();activeController=controller;
  try{const data=await request(view,controller.signal);if(!controller.signal.aborted)renderResult(view,data,question);}
  catch(error){if(!controller.signal.aborted){view.wait.remove();view.title.textContent="查询失败";view.turn.status="失败";view.answer.append(el("div","error-line",error.message||"请求失败"));renderTurns();}}
  finally{if(activeController===controller)activeController=null;busy=false;$("send").disabled=false;}
}
function ask(question){const text=question.trim();if(!text)return;const useContext=$("useContext").checked;
  return run(text,(view,signal)=>STREAM?streamQuery(text,useContext,view,signal):post("/api/v1/agent/query",{question:text,session_id:sessionId,use_context:useContext},signal));}
function clarify(question,code,option){return run(option.label||option.value,async(_view,signal)=>{
  const response=await post("/api/v1/nl2sql/clarify",{original_question:question,clarification_code:code,selected_value:option.value,selected_label:option.label,session_id:sessionId},signal);return response.result;
});}
function setTab(doc){$("tabAsk").classList.toggle("on",!doc);$("tabDoc").classList.toggle("on",doc);$("thread").style.display=doc?"none":"";$("askComposer").style.display=doc?"none":"";$("view-doc").classList.toggle("on",doc);}
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
function newChat(){activeController?.abort();sessionId=crypto.randomUUID();sessionStorage.setItem("ict8_lattice_session",sessionId);turns=[];$("feed").replaceChildren($("empty"));$("empty").style.display="";renderTurns();setTab(false);}
$("newChat").onclick=newChat;$("newChatTop").onclick=newChat;
$("sixDemo").onclick=async()=>{setTab(false);for(const q of ["2025年华东地区的销售额","那华南呢","看看订单数","换成华北","换成2024年","看看销量"])await ask(q);};
$("theme").onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==="dark"?"light":"dark";};
$("tabAsk").onclick=()=>setTab(false);$("tabDoc").onclick=()=>setTab(true);$("docRun").onclick=analyzeDocument;
selectCapability(activeCapability);schemaLoadPromise=loadMeta();
