const API = new URLSearchParams(location.search).get("api") || window.ICT8_API_BASE || (location.protocol === "file:" ? "http://127.0.0.1:8030" : location.origin);
const STREAM = new URLSearchParams(location.search).get("stream") !== "false";
const $ = (id) => document.getElementById(id);
const examples = [
  {question:"2025年各地区销售额排名",hint:"查询业务数据"},
  {question:"销售额的统计口径是什么",hint:"查阅资料依据"},
  {question:"先查询2025年销售额排名第一的地区，再检索该地区的销售经验",hint:"结合数据与资料"},
  {question:"2025年华东地区销售额同比",hint:"分析变化"},
];
const questionDomains = [
  ['采购与供应商','purchase_orders',['2025年各地区采购金额','2025年已入库的采购数量','2025年各供应商采购金额排名']],
  ['物流与配送','shipment_records',['2025年各承运商运费','2025年平均运输天数','2025年各物流状态发货单数']],
  ['回款与费用','payment_receipts',['2025年华东回款金额','2025年各支付方式回款金额','2025年各费用类别费用金额']],
  ['人力与薪酬','payroll_records',['各部门员工数','2025年各部门薪酬总额','2025年华南加班时长']],
  ['网站运营','web_traffic_daily',['2025年各流量来源网站访问量','2025年各设备类型网页浏览量','2025年华东网站转化率']],
];
const conversationScope=window.ConversationSessions.scopeFor(API,location.href);
const conversationSessions=window.ConversationSessions.create({scope:conversationScope,
  legacyKeys:conversationScope===location.origin?[{key:"ict8_lattice_session",label:"原问数会话"},{key:"ict8.omni-session",label:"原文档会话"}]:[]});
let sessionId = conversationSessions.current(),conversationSessionPicker=null;
let activeController = null, busy = false, turns = [];
let pendingImages = [], imageReads = 0, imageReadBytes = 0;
let latestContext = null, independentNext = false;
let demoRunning = false;
function updateContextComposer(){
  const host=$("contextComposer"),independent=independentNext||!$("useContext").checked;
  const state=window.ConversationContext.composerState(latestContext,independent);
  host.hidden=!latestContext&&!independent;host.replaceChildren();
  host.append(el("b","",state.title),el("span","context-composer-detail",state.detail));
  const action=el("button","context-mode-action",independent?"恢复追问":"独立提问");action.type="button";action.disabled=busy;
  action.onclick=()=>{if(busy)return;independentNext=!independent;$("useContext").checked=true;updateContextComposer();$("q").focus();};
  host.append(action);$("q").placeholder=latestContext?state.placeholder:"直接提问：查数据、找依据，或结合两者分析";
  const catalog=el("button","context-mode-action","查看待补问题");catalog.type="button";catalog.disabled=busy;
  catalog.onclick=()=>{if(busy)return;independentNext=false;$("useContext").checked=true;ask("查看待补问题");};host.append(catalog);
}
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
function syncComposerControls(){
  const send=$("send"),picker=$("imagePicker");
  if(send)send.disabled=busy||imageReads>0||(!$('q').value.trim()&&!pendingImages.length);
  if(picker)picker.disabled=busy||pendingImages.length+imageReads>=3;
}
function setImageStatus(message){const status=$("imageStatus");status.textContent=message||"";status.hidden=!message;}
function renderImagePreviews(){
  const host=$("imagePreviews");host.replaceChildren();host.hidden=!pendingImages.length;
  pendingImages.forEach((image,index)=>{
    const thumb=el("div","composer-image-thumb"),preview=el("img");preview.src=image.dataUrl;preview.alt=image.name||"待发送图片";
    const remove=el("button","composer-image-remove","×");remove.type="button";remove.title="移除图片";remove.setAttribute("aria-label",`移除图片 ${index+1}`);
    remove.onclick=()=>{pendingImages.splice(index,1);renderImagePreviews();syncComposerControls();};
    thumb.append(preview,remove);host.append(thumb);
  });
  syncComposerControls();
}
async function imageDataUrl(file){
  const allowed=["image/png","image/jpeg","image/webp"];
  if(!allowed.includes(file.type))throw Error("仅支持 PNG、JPEG 或 WebP 图片。");
  if(!file.size||file.size>6*1024*1024)throw Error("每张图片不能超过 6 MB。");
  if(window.createImageBitmap){
    const bitmap=await createImageBitmap(file);
    const pixels=bitmap.width*bitmap.height;bitmap.close();
    if(!pixels||pixels>16000000)throw Error("图片像素数不能超过 1600 万。");
  }
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));
    reader.onerror=()=>reject(Error("图片读取失败。"));reader.readAsDataURL(file);
  });
}
async function addImageFiles(files){
  const remaining=Math.max(0,3-pendingImages.length-imageReads),selected=Array.from(files).slice(0,remaining);
  if(!remaining||selected.length<files.length)setImageStatus("最多添加 3 张图片。");else setImageStatus("");
  if(!selected.length)return;
  let byteBudget=Math.max(0,12*1024*1024-pendingImages.reduce((sum,image)=>sum+image.size,0)-imageReadBytes);
  const accepted=[];
  for(const file of selected){if(file.size>byteBudget){setImageStatus("本轮图片总大小不能超过 12 MB。");continue;}accepted.push(file);byteBudget-=file.size;}
  imageReads+=accepted.length;imageReadBytes+=accepted.reduce((sum,file)=>sum+file.size,0);syncComposerControls();
  for(const file of accepted){
    try{pendingImages.push({name:file.name||"图片",size:file.size,dataUrl:await imageDataUrl(file)});setImageStatus("");}
    catch(error){setImageStatus(error.message||"图片无法读取。");}
    finally{imageReads--;imageReadBytes-=file.size;renderImagePreviews();}
  }
}
let ragPreviewController=null,ragPreviewBlobUrl=null;
let knowledgeDocuments=[],knowledgeCatalogPromise=Promise.resolve([]);
function closeRagPreview(){
  const panel=document.querySelector(".rag-preview-popover");
  if(!panel)return;
  ragPreviewController?.abort();ragPreviewController=null;panel.remove();
  if(ragPreviewBlobUrl){URL.revokeObjectURL(ragPreviewBlobUrl);ragPreviewBlobUrl=null;}
  document.removeEventListener("pointerdown",closeRagPreviewOutside,true);
  document.removeEventListener("keydown",closeRagPreviewKey);
  window.removeEventListener("resize",positionRagPreviewOnResize);
  document.querySelector(".sidebar")?.removeEventListener("scroll",closeRagPreview);
  document.querySelectorAll(".rag-file-open[aria-expanded='true'],.data-source-item[aria-expanded='true']").forEach(button=>button.setAttribute("aria-expanded","false"));
}
function closeRagPreviewOutside(event){if(!event.target.closest(".rag-preview-popover"))closeRagPreview();}
function closeRagPreviewKey(event){if(event.key==="Escape")closeRagPreview();}
function positionRagPreview(panel,anchor){
  const sidebar=$("ragSources").closest(".sidebar"),sidebarRect=sidebar.getBoundingClientRect();
  const width=Math.min(760,window.innerWidth-32),left=Math.min(Math.max(16,sidebarRect.right+16),Math.max(16,window.innerWidth-width-16));
  panel.classList.add("is-expanded");panel.style.width=`${width}px`;panel.style.left=`${left}px`;
  panel.style.top=`${Math.max(16,(window.innerHeight-panel.offsetHeight)/2)}px`;
}
function positionRagPreviewOnResize(){const panel=document.querySelector(".rag-preview-popover");if(panel)positionRagPreview(panel);}
async function openRagPreview(doc,{kind,format,originalUrl,trigger}){
  closeRagPreview();
  const controller=new AbortController();ragPreviewController=controller;
  const panel=el("section","rag-preview-popover");panel.setAttribute("role","dialog");panel.setAttribute("aria-modal","false");panel.setAttribute("aria-label",`资料预览：${doc.title||doc.document_id}`);
  const header=el("div","rag-preview-head"),heading=el("div","rag-preview-heading");
  heading.append(el("strong","rag-preview-title",doc.title||doc.document_id),el("span","rag-preview-type",`${format} · ${Number(doc.chunk_count)||0} 个片段`));
  const close=el("button","rag-preview-close");close.type="button";close.setAttribute("aria-label","关闭资料预览");close.title="关闭预览";close.textContent="×";close.onclick=closeRagPreview;
  header.append(heading,close);
  const content=el("div","rag-preview-content");content.append(el("p","rag-preview-loading","正在载入预览…"));
  const footer=el("div","rag-preview-footer"),ask=el("button","rag-preview-ask","根据这份资料提问");ask.type="button";
  ask.onclick=()=>{$("q").value=`根据《${doc.title||doc.document_id}》，`;$("q").dispatchEvent(new Event("input"));$("q").focus();closeRagPreview();};
  const original=el("a","rag-preview-original","打开原文件");original.href=originalUrl;original.target="_blank";original.rel="noopener noreferrer";
  footer.append(ask,original);panel.append(header,content,footer);
  $("ragSources").closest(".sidebar").append(panel);trigger.setAttribute("aria-expanded","true");positionRagPreview(panel,trigger);
  document.addEventListener("pointerdown",closeRagPreviewOutside,true);document.addEventListener("keydown",closeRagPreviewKey);
  $("ragSources").closest(".sidebar").addEventListener("scroll",closeRagPreview,{once:true});
  window.addEventListener("resize",positionRagPreviewOnResize);
  if(kind==="pdf"){
    try{
      if(!/^[a-f0-9]{64}$/.test(doc.sha256||""))throw Error("资料来源校验信息缺失，暂时无法生成 PDF 预览。");
      const page=await fetch(new URL(`/api/v1/knowledge/documents/${encodeURIComponent(doc.document_id)}/visual-evidence`,new URL(API,location.href)),{
        method:"POST",headers:{"Content-Type":"application/json"},signal:controller.signal,
        body:JSON.stringify({page_no:1,expected_source_sha256:doc.sha256})
      }).then(json);
      if(controller.signal.aborted||!panel.isConnected)return;
      if(page.document_id!==doc.document_id||page.page_no!==1||page.source_sha256!==doc.sha256||!/^[a-f0-9]{64}$/.test(page.render_sha256||""))throw Error("PDF 页面与原件校验信息不一致。");
      const apiBase=new URL(API,location.href),imageUrl=new URL(page.png_uri,apiBase);
      const expectedPath=`/api/v1/knowledge/documents/${encodeURIComponent(doc.document_id)}/pages/1/visual.png`;
      if(imageUrl.origin!==apiBase.origin||imageUrl.pathname!==expectedPath||imageUrl.searchParams.get("source_sha256")!==doc.sha256||imageUrl.searchParams.get("render_sha256")!==page.render_sha256)throw Error("PDF 页面地址未通过来源校验。");
      const response=await fetch(imageUrl.href,{signal:controller.signal});
      if(!response.ok)throw Error(`PDF 页面加载失败（HTTP ${response.status}）。`);
      if(!response.headers.get("Content-Type")?.startsWith("image/png"))throw Error("服务未返回有效的 PDF 页面图。");
      const blob=await response.blob();
      if(blob.size>12*1024*1024)throw Error("PDF 页面图超过预览大小限制。");
      const digest=await crypto.subtle.digest("SHA-256",await blob.arrayBuffer());
      const renderSha=[...new Uint8Array(digest)].map(value=>value.toString(16).padStart(2,"0")).join("");
      if(renderSha!==page.render_sha256)throw Error("PDF 页面图校验失败，请重新打开预览。");
      if(controller.signal.aborted||!panel.isConnected)return;
      const locator=el("span","rag-preview-locator",`第 1 页 · 共 ${Number(page.page_count)||1} 页`),image=el("img","rag-preview-image");
      image.alt=`${doc.title||doc.document_id} · PDF 第 1 页`;image.onload=()=>positionRagPreview(panel,trigger);
      ragPreviewBlobUrl=URL.createObjectURL(blob);image.src=ragPreviewBlobUrl;content.replaceChildren(locator,image);positionRagPreview(panel,trigger);return;
    }catch(error){if(!controller.signal.aborted&&panel.isConnected)content.replaceChildren(el("p","rag-preview-empty",`PDF 预览加载失败：${error.message}`));return;}
  }
  if(kind==="image"){
    const image=el("img","rag-preview-image");image.alt=doc.title||doc.document_id;image.src=originalUrl;
    image.onerror=()=>content.replaceChildren(el("p","rag-preview-empty","暂时无法显示此图片，请打开原文件查看。"));content.replaceChildren(image);positionRagPreview(panel,trigger);return;
  }
  try{
    const data=await fetch(API+`/api/v1/knowledge/documents/${encodeURIComponent(doc.document_id)}`,{signal:controller.signal}).then(json);
    if(controller.signal.aborted||!panel.isConnected)return;
    const chunks=(data.chunks||[]).filter(chunk=>chunk.text?.trim()&&chunk.content_type!=="image").slice(0,6);
    if(!chunks.length){content.replaceChildren(el("p","rag-preview-empty","该资料暂无可展示的解析文本。"));return;}
    content.replaceChildren();
    chunks.forEach((chunk,index)=>{
      const article=el("article","rag-preview-chunk"),locator=chunk.page_no?`第 ${chunk.page_no} 页`:chunk.sheet_name?`${chunk.sheet_name}${chunk.row_start?` · 第 ${chunk.row_start}-${chunk.row_end||chunk.row_start} 行`:""}`:chunk.title_path?.join(" · ")||`片段 ${index+1}`;
      article.append(el("span","rag-preview-locator",locator),el("p","rag-preview-text",chunk.text));content.append(article);
    });
    if(data.chunks?.length>chunks.length)content.append(el("p","rag-preview-more",`预览前 ${chunks.length} 段，共 ${data.chunks.length} 段`));
    positionRagPreview(panel,trigger);
  }catch(error){if(!controller.signal.aborted&&panel.isConnected)content.replaceChildren(el("p","rag-preview-empty",`预览加载失败：${error.message}`));}
}

function renderExamples() {
  const suggests=$("suggests");suggests.replaceChildren();
  for(const example of examples){
    const card=el("button","suggest");card.type="button";
    card.append(el("b","",example.question),el("span","",example.hint));
    card.onclick=()=>ask(example.question);suggests.append(card);
  }
}
function renderQuestionCatalog(tables=questionDomains.map(([,table])=>({name:table}))){
  const host=$("demoDomainCatalog");
  if(!host.dataset.ready){
    const widget=el("div","demo-domain-widget");
    const toggle=el("button","demo-domain-toggle");toggle.type="button";
    toggle.setAttribute("aria-controls","demoDomainPanel");toggle.setAttribute("aria-expanded","false");toggle.setAttribute("aria-label","打开问题推荐");
    toggle.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2L12 3Z" stroke-linejoin="round"/><path d="M19 3v4M21 5h-4M4 16v4M6 18H2" stroke-linecap="round"/></svg>';
    toggle.append(el("span","","问题推荐"));
    const panel=el("section","demo-domain-panel");panel.id="demoDomainPanel";panel.setAttribute("role","dialog");panel.setAttribute("aria-label","问题推荐");panel.hidden=true;
    const head=el("div","demo-domain-head"),close=el("button","demo-domain-close");head.append(el("b","","问题推荐"));
    close.type="button";close.setAttribute("aria-label","收起问题推荐");close.title="收起";
    close.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke-linecap="round"/></svg>';
    head.append(close);
    const featured=el("section","demo-domain-featured");featured.append(el("h3","","常用问题"));
    for(const example of examples){
      const button=el("button","demo-domain-question");button.type="button";
      button.append(el("span","",example.question),el("small","",example.hint));button.onclick=()=>{if(!busy)ask(example.question);};featured.append(button);
    }
    const groups=el("div","demo-domain-groups");panel.append(head,featured,groups);
    function setPanelOpen(open){panel.hidden=!open;toggle.setAttribute("aria-expanded",String(open));toggle.setAttribute("aria-label",open?"关闭问题推荐":"打开问题推荐");}
    toggle.onclick=()=>setPanelOpen(panel.hidden);close.onclick=()=>{setPanelOpen(false);toggle.focus();};
    panel.addEventListener("keydown",event=>{if(event.key==="Escape"){setPanelOpen(false);toggle.focus();}});
    document.addEventListener("pointerdown",event=>{if(!widget.contains(event.target))setPanelOpen(false);});
    widget.append(toggle,panel);host.replaceChildren(widget);host.dataset.ready="true";host._questionGroups=groups;
  }
  const groups=host._questionGroups;groups.replaceChildren();
  for(const [title,table,questions] of questionDomains){
    if(!tables.some(item=>item.name===table))continue;
    const group=el("details","demo-domain");if(!groups.querySelector(".demo-domain"))group.open=true;
    group.append(el("summary","",title));
    for(const question of questions){
      const button=el("button","demo-domain-question",question);button.type="button";
      button.onclick=()=>{if(busy)return;independentNext=true;ask(question);};group.append(button);
    }
    groups.append(group);
  }
}
async function loadKnowledgeCatalog(){
  const host=$("ragSources");host.replaceChildren(el("p","data-source-detail","正在读取资料…"));
  try{
    const data=await fetch(API+"/api/v1/knowledge/documents").then(json);
    host.replaceChildren();
    if(!data.documents?.length){host.append(el("p","data-source-detail","当前尚未接入资料"));return;}
    for(const doc of data.documents){
      const title=String(doc.title||doc.document_id),rawType=String(doc.modality||doc.filename?.split(".").pop()||"file").toLowerCase();
      const kind=({pdf:"pdf",xlsx:"excel",xls:"excel",docx:"word",doc:"word",md:"markdown",txt:"text",image:"image",png:"image",jpg:"image",jpeg:"image",webp:"image"})[rawType]||"file";
      const format=({pdf:"PDF",xlsx:"Excel",xls:"Excel",docx:"Word",doc:"Word",md:"Markdown",txt:"文本",image:"图片",png:"图片",jpg:"图片",jpeg:"图片",webp:"图片"})[rawType]||rawType.toUpperCase();
      const badge=({pdf:"PDF",excel:"XLSX",word:"WORD",markdown:"MD",text:"TXT",image:"IMG"})[kind]||format.slice(0,5);
      const item=el("article","rag-file-tile");
      const originalUrl=new URL(`/api/v1/knowledge/documents/${encodeURIComponent(doc.document_id)}/original`,new URL(API,location.href)).href;
      const original=el("button","rag-file-open");original.type="button";original.setAttribute("aria-haspopup","dialog");original.setAttribute("aria-expanded","false");
      original.onclick=()=>openRagPreview(doc,{kind,format,originalUrl,trigger:original});
      original.title=`预览资料：${title}`;
      const icon=el("span","rag-file-icon");icon.dataset.kind=kind;icon.dataset.badge=badge;icon.setAttribute("aria-hidden","true");
      icon.append(el("i","rag-file-page"));
      const name=el("span","rag-file-name",title);name.title=title;
      original.append(icon,name,el("span","rag-file-meta",`${format} · ${Number(doc.chunk_count)||0} 个片段`));
      const actions=el("div","rag-file-actions");
      const askButton=el("button","rag-file-ask","提问");askButton.type="button";askButton.title=`结合《${title}》提问`;askButton.setAttribute("aria-label",`结合《${title}》提问`);
      askButton.onclick=()=>{$("q").value=`根据《${title}》，`;
        $("q").dispatchEvent(new Event("input"));$("q").focus();};
      const originalAction=el("a","rag-file-original","原文件");originalAction.href=originalUrl;originalAction.target="_blank";originalAction.rel="noopener noreferrer";originalAction.title=`查看原文件：${title}`;
      actions.append(askButton,originalAction);item.append(original,actions);host.append(item);
    }
  }catch(error){host.replaceChildren(el("p","data-source-detail",`资料读取失败：${error.message}`));}
}
async function loadMeta() {
  try {
    const [health,schema,sources]=await Promise.all([
      fetch(API+"/health").then(json),fetch(API+"/api/v1/nl2sql/schema").then(json),
      fetch(API+"/api/v1/data-sources").then(json)]);
    liveSchema={tables:Array.isArray(schema.tables)?schema.tables:[],source:schema.source||null};
    $("apiHealth").dataset.state=health.ok?"ok":"error";$("healthText").textContent=health.ok?"API 已连接":"API 异常";
    const tables=schema.tables||[],box=$("schema");
    const recordCount=tables.reduce((sum,table)=>sum+(Number(table.row_count)||0),0);
    const tableNames={suppliers:'供应商',purchase_orders:'采购订单',shipment_records:'物流发货',payment_receipts:'回款记录',operating_expenses:'运营费用',employees:'员工档案',payroll_records:'薪酬记录',web_traffic_daily:'网站流量',sales_orders:'销售订单',customers:'客户',products:'产品',regions:'地区',sales_reps:'销售人员',inventory_snapshots:'库存快照',marketing_campaigns:'营销活动',sales_returns:'退货退款',sales_targets:'销售目标',support_tickets:'售后工单'};
    if(box){
      box.replaceChildren();
      box.append(el("b","",health.database||"数据库"),el("div","",`${tables.length} 张表 · ${recordCount.toLocaleString()} 条记录`));
      tables.forEach(t=>box.append(el("div","",`${tableNames[t.name]||t.name} · ${(t.row_count??0).toLocaleString()} 行`)));
    }
    const sourceList=$("dataSources");if(sourceList)sourceList.replaceChildren();
    function source(kind,name,detail,state="ok"){
      if(!sourceList)return;
      const item=el("article","data-source-item rag-file-tile");item.dataset.state=state;
      const icon=el("span","data-source-sql-icon");icon.setAttribute("aria-hidden","true");icon.append(el("span","data-source-sql-label","SQL"));
      const nameLabel=el("span","data-source-name",name);nameLabel.title=name;
      item.append(icon,nameLabel,el("span","data-source-kind",kind),el("span","data-source-detail rag-file-meta",detail));
      item.setAttribute("aria-label",`${state==="ok"?"已连接":"连接异常"}：${name}`);sourceList.append(item);
    }
    source("合成示例 · 问数数据库",health.database||"当前业务库",`${tables.length} 张表 · ${recordCount.toLocaleString()} 条记录`);
    const documents=sources.documents||{};
    source("文档资料库",documents.name||"knowledge.sqlite",
      `${Number(documents.document_count)||0} 份资料 · ${Number(documents.chunk_count)||0} 个检索片段`,
      documents.status==="connected"?"ok":"error");
  } catch(error) { liveSchema={tables:[],source:null};$("apiHealth").dataset.state="error";$("healthText").textContent="API 不可达";const box=$("schema");if(box)box.textContent=`无法读取当前数据源：${error.message||"请求失败"}`; }
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
function beginTurn(question,images=[]){
  $("empty").style.display="none";
  const user=el("div","turn user");user.append(el("div","bubble",question));
  if(images.length){const previews=el("div","user-image-attachments");images.forEach(image=>{
    const thumb=el("img");thumb.src=image.dataUrl;thumb.alt=image.name||"本轮上传图片";thumb.loading="lazy";previews.append(thumb);
  });user.append(previews);}
  const assistant=el("div","turn assistant"),body=el("div","agent-inspection-host"),title=el("span"),wait=el("span");
  const live=window.ToolTimeline.create({getSchema:()=>liveSchema});toolTimelineCleanups.add(live.dispose);
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
  const fusionSummary=data.route==='fusion'?(result.status==='ok'?'联合查询完成，下面展示数据结果与资料来源。':'联合查询尚未完成，请查看需要补充的条件。'):null;
  return {...data,omni_response:true,answer:result.answer||result.clarification||sqlSummary||fusionSummary||result.explanation?.join("；")||"本次未返回文字说明。",
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
    const excerpt=String(item.snippet||"").replace(/!\[([^\]]*)\]\([^)]+\)/g,"$1").trim();
    row.append(el("div","ttl",item.title||"资料片段"),el("div","meta",Number.isInteger(page)?`第 ${page} 页`:"原文引用"),el("div","snip source-quote",excerpt));
    const sourceImages=Array.isArray(metadata.raysource_images)?metadata.raysource_images:[];
    if(sourceImages.length){
      const gallery=el("div","doc-images");
      sourceImages.forEach(sourceImage=>{
        const imageId=String(sourceImage?.image_id||"");
        if(!/^Manual\d+_\d+$/.test(imageId))return;
        const uri=new URL(`/api/v1/knowledge/raysource-images/${imageId}`,new URL(API,location.href)).href;
        const figure=el("figure","doc-image"),link=el("a"),image=el("img");
        link.href=uri;link.target="_blank";link.rel="noopener noreferrer";link.title="打开手册原图";
        image.src=uri;image.alt=String(sourceImage.alt||"手册原图");image.loading="lazy";image.onerror=()=>figure.remove();
        link.append(image);figure.append(link,el("figcaption","",image.alt));gallery.append(figure);
      });
      if(gallery.childElementCount)row.append(gallery);
    }
    if(typeof did==="string"&&item.source_uri===`/api/v1/knowledge/documents/${encodeURIComponent(did)}/original`){
      const link=el("a","source-original","查看原文件");link.href=new URL(`/api/v1/knowledge/documents/${encodeURIComponent(did)}/original`,new URL(API,location.href)).href;link.target="_blank";link.rel="noopener noreferrer";row.append(link);
    }
    const candidate=data.visual_source_proof?.parts?.[item.citation_id-1];
    const part=candidate?.quote===item.snippet?candidate:null;
    const chunkSource=window.SourceEvidence?.citation(item,{api:API});if(chunkSource)row.append(chunkSource);
    const financialSelection=items.filter(c=>c.metadata?.document_id===did&&c.metadata?.page_no===page&&c.metadata?.source_sha256===metadata.source_sha256).map(c=>c.metadata?.fact).filter(Boolean);
    sourcePageViewer(row,item,part,data.native_row_proof?.selection,data.native_total_proof?.selected_annotations,financialSelection);docs.append(row);
  });host.append(docs);
}
function renderResultSchemas(host,result){
  if(!window.SchemaSvg||!result.source_tables?.tables?.length||!liveSchema.tables.length)return;
  const manifest=result.source_tables,plan=result.plan||{},links=result.provenance?.field_links||plan.links||[];
  const used=new Map(manifest.tables.map(table=>[table.name,new Set(table.used_columns||[])]));
  const originalModel=window.SchemaSvg.buildModel(liveSchema,plan,links),actualLinks=[];
  for(const [table,fields] of used){
    for(const column of fields){
      const roles=originalModel.nodes.get(table)?.columns.find(field=>field.name===column)?.roles||[];
      for(const role of roles.length?roles:['used'])actualLinks.push({table,column,role});
    }
  }
  const tables=liveSchema.tables.filter(table=>used.has(table.name)).map(table=>{
    const fields=used.get(table.name);
    return {...table,columns:(table.columns||[]).filter(column=>fields.has(column.name)),
      foreign_keys:(table.foreign_keys||[]).filter(key=>fields.has(key.from_column)&&used.get(key.table)?.has(key.to_column)),
      unique_keys:(table.unique_keys||[]).filter(key=>Array.isArray(key)&&key.every(column=>fields.has(column)))};
  });
  const grid=el('div','result-schema-grid');
  const section=el('section','result-schema-section result-schema-query');
  section.setAttribute('aria-label','本次结果涉及的数据库表与字段');
  if(tables.length)section.append(window.SchemaSvg.render({...liveSchema,tables},{},actualLinks,{minimal:true,hideCount:true,hideLegendNote:true}));
  else section.append(el('p','warn','当前结构目录中未找到本次执行的表，无法绘制。'));
  const full=el('section','result-schema-section result-schema-overview');
  full.setAttribute('aria-label','完整数据库总览，突出显示本次使用字段');
  full.append(window.SchemaSvg.render(liveSchema,{},actualLinks,{minimal:true,hideCount:true,hideLegendNote:true}));
  grid.append(section,full);host.append(grid);
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

  }
  const inspector=el("details","agent-inspector"),inspectorBody=el("div"),inspectorSummary=el("summary");
  inspectorSummary.innerHTML='<svg class="agent-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6M4 12c0 4 16 4 16 0"/></svg>';
  inspectorSummary.append(document.createTextNode(data.route==='comparison'?"查看比较依据与 SQL":"查看字段 SVG、SQL 与数据关系"));
  inspector.addEventListener('toggle',()=>{
    if(inspector.open&&!inspectorBody.childNodes.length)renderAudit(inspectorBody,data);
  });
  {
    inspector.append(inspectorSummary,inspectorBody);
    if(!view.live.attach('nl2sql',inspector))view.body.append(inspector);
  }
  const answer=el("div","answer"),structured=data.structured||{};
  if(data.routing?.label)answer.append(el('p','schema-caption',`${data.routing.label} · ${data.routing.reason||''}`));
  const userQuestion=String(originalQuestion||data.question||"").trim();
  const interpretedQuestion=String(data.effective_question||structured.rewritten_question||userQuestion).trim();
  if(interpretedQuestion){
    const questionLine=el("p","answer-question",`本次问题：${interpretedQuestion}`);
    if(userQuestion&&userQuestion!==interpretedQuestion)questionLine.append(el("small","answer-question-input",`本轮输入：${userQuestion}`));
    answer.append(questionLine);
  }
  answer.append(el("p","",data.answer||"后端未返回文字说明"));
  if(data.route==='fusion'){
    if(data.result.status==='ok'){
      for(const [id,step] of Object.entries(data.result.results||{})){
        const section=el('section','fusion-result');section.append(el('h4','',`联合查询 · ${id}`));
        if(Array.isArray(step.rows)){
          renderTable(section,step);
          if(step.sql){const audit=el('details');audit.append(el('summary','','查看本步 SQL'),el('pre','sqlbox',step.sql));section.append(audit);}
        }else if(Array.isArray(step.hits)){
          if(!step.hits.length)section.append(el('p','warn','当前检索没有找到相关资料。'));
          renderDocumentEvidence(section,{document_evidence:step.hits});
        }else if(step.value!=null){
          section.append(el('p','',`${step.value}${step.result_unit||step.unit?' '+(step.result_unit||step.unit):''}`));
          if(step.result_interpretation)section.append(el('p','',step.result_interpretation));
        }else if(step.left&&step.right){
          section.append(el('p','',`${step.left.value} ${step.operator||'↔'} ${step.right.value} · ${step.matched===true?'符合':step.matched===false?'不符合':step.status}`));
          if(typeof step.difference==='number'&&Number.isFinite(step.difference)){
            const unit=step.unit==='CNY'?'元':step.unit&&step.unit!=='unknown'?step.unit:'';
            section.append(el('p','',`差额（前者减后者）：${step.difference.toLocaleString('zh-CN',{maximumFractionDigits:2})}${unit?' '+unit:''}`));
          }
        }else section.append(el('pre','',JSON.stringify(step,null,2)));
        answer.append(section);
      }
    }else answer.append(el('p','warn',data.result.error||'联合查询未完成，中间结果可在工具详情中核对。'));
  }
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
  if(data.route==='fusion'){
    for(const [id,step] of Object.entries(data.result?.results||{})){
      if(step.source_tables?.tables?.length){const group=el('section','fusion-schema-result');group.append(el('h3','',`查询出处 · ${id}`));renderResultSchemas(group,step);answer.append(group);}
    }
  }else renderResultSchemas(answer,structured);
  if(data.route==='fusion'){
    for(const [id,step] of Object.entries(data.result?.results||{})){
      const source=window.SourceEvidence?.sql(step,{api:API,schema:liveSchema});
      if(source){source.prepend(el('h3','',`查询出处 · ${id}`));answer.append(source);}
    }
  }else{
    const source=window.SourceEvidence?.sql(structured,{api:API,schema:liveSchema});if(source)answer.append(source);
  }
  clearSourceViewers(view.answer);view.answer.replaceChildren(answer);$("thread").scrollTop=$("thread").scrollHeight;
}
function parseSse(block){let type="message";const lines=[];block.split("\n").forEach(line=>{if(line.startsWith("event:"))type=line.slice(6).trim();if(line.startsWith("data:"))lines.push(line.slice(5).trimStart());});return lines.length?{type,data:JSON.parse(lines.join("\n"))}:null;}
async function streamQuery(question,useContext,completeResults,view,signal,images=[]){
  const response=await fetch(API+"/api/v1/omni/query/stream",{method:"POST",headers:{"Content-Type":"application/json",Accept:"text/event-stream"},body:JSON.stringify({question,session_id:sessionId,reset_context:!useContext,complete_results:completeResults,images:images.map(image=>image.dataUrl)}),signal});
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
async function run(question,request,images=[]){
  if(busy)return;busy=true;$("send").disabled=true;
  syncComposerControls();
  $("sixDemo").disabled=true;
  document.querySelectorAll(".clarify button, .clarify input, .comparison-controls button, .pending-task-list button").forEach(control=>control.disabled=true);
  updateContextComposer();const view=beginTurn(question,images),controller=new AbortController();activeController=controller;
  try{const data=await request(view,controller.signal);if(!controller.signal.aborted){renderResult(view,data,question);return data;}}
  catch(error){if(!controller.signal.aborted){view.live?.fail(error.message||"请求失败");view.wait.remove();view.title.textContent="查询失败";view.turn.status="失败";view.answer.append(el("div","error-line",error.message||"请求失败"));renderTurns();}}
  finally{if(activeController===controller){activeController=null;busy=false;$("sixDemo").disabled=demoRunning;updateContextComposer();syncComposerControls();}}
}
function ask(question,images=[]){const text=question.trim();if(!text)return;const useContext=$("useContext").checked&&!independentNext,completeResults=$("completeResults").checked;
  return run(text,(view,signal)=>STREAM?streamQuery(text,useContext,completeResults,view,signal,images):post("/api/v1/omni/query",{question:text,session_id:sessionId,reset_context:!useContext,complete_results:completeResults,images:images.map(image=>image.dataUrl)},signal),images);}
function clarify(question,code,option,omni=true,time){return run(time?`${option.label||option.value}：${time}`:option.label||option.value,async(_view,signal)=>{
  return post("/api/v1/omni/clarify",{original_question:question,clarification_code:code,selected_value:option.value,selected_label:option.label,selected_time:time,session_id:sessionId,complete_results:$("completeResults").checked},signal);
});}
$("form").addEventListener("submit",e=>{e.preventDefault();if(busy||imageReads)return;const question=$("q").value.trim()||(pendingImages.length?"请结合图片和相关资料回答":"");if(!question)return;
  const images=pendingImages.map(image=>({...image}));pendingImages=[];renderImagePreviews();setImageStatus("");$("q").value="";$("q").style.height="auto";ask(question,images);});
$("q").addEventListener("input",()=>{$("q").style.height="auto";$("q").style.height=`${Math.min(160,$("q").scrollHeight)}px`;syncComposerControls();});
$("q").addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey){e.preventDefault();$("form").requestSubmit();}});
$("imagePicker").onclick=()=>$("imageFiles").click();
$("imageFiles").addEventListener("change",event=>{addImageFiles(event.target.files);event.target.value="";});
$("q").addEventListener("paste",event=>{
  const files=Array.from(event.clipboardData?.items||[]).filter(item=>item.kind==="file"&&item.type.startsWith("image/")).map(item=>item.getAsFile()).filter(Boolean);
  if(files.length){if(!event.clipboardData.getData("text/plain"))event.preventDefault();addImageFiles(files);}
});
$("form").addEventListener("dragover",event=>{if(Array.from(event.dataTransfer?.items||[]).some(item=>item.kind==="file"))event.preventDefault();});
$("form").addEventListener("drop",event=>{const files=Array.from(event.dataTransfer?.files||[]);if(files.length){event.preventDefault();addImageFiles(files);}});
function switchConversation(next){activeController?.abort();activeController=null;busy=false;latestContext=null;independentNext=false;$("useContext").checked=true;updateContextComposer();syncComposerControls();clearSourceViewers();clearToolTimelines();sessionId=next;turns=[];$("feed").replaceChildren($("empty"));$("empty").style.display="";renderTurns();conversationSessionPicker?.refresh();}
function newChat(){try{switchConversation(conversationSessions.start());}catch(error){$("q").setCustomValidity(error.message);$("q").reportValidity();$("q").setCustomValidity("");}}
$("useContext").addEventListener("change",()=>{independentNext=false;updateContextComposer();});
$("newChat").onclick=newChat;$("newChatTop").onclick=newChat;
const conversationSessionHost=el("div","session-picker");$("newChat").insertAdjacentElement("afterend",conversationSessionHost);
conversationSessionPicker=window.ConversationSessions.mount(conversationSessions,conversationSessionHost,switchConversation);
window.addEventListener("pageshow",()=>{const current=conversationSessions.current();if(current!==sessionId)switchConversation(current);else conversationSessionPicker.refresh();});
$("sixDemo").onclick=async()=>{
  if(busy||demoRunning)return;
  const button=$("sixDemo"),label=button.textContent;
  demoRunning=true;button.disabled=true;
  try{
    const previousSession=sessionId;newChat();if(sessionId===previousSession)return;
    const demoSession=sessionId,questions=["2025年华东地区的销售额","那华南呢","看看订单数","换成华北","换成2024年","看看销量"];
    for(const [index,question] of questions.entries()){
      if(sessionId!==demoSession)break;
      independentNext=false;$("useContext").checked=true;
      button.textContent=`演示中 · ${index+1}/6`;
      const data=await ask(question);
      if(sessionId!==demoSession||!data||data.status!=="ok")break;
    }
  }finally{demoRunning=false;button.textContent=label;button.disabled=busy;}
};
$("theme").onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==="dark"?"light":"dark";};
renderExamples();renderQuestionCatalog();schemaLoadPromise=loadMeta();loadKnowledgeCatalog();syncComposerControls();
