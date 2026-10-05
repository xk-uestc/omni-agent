(function(root){
  const hex=/^[a-f0-9]{64}$/;
  function model(metadata,preview,sourceSha){
    const geometry=preview.geometry;
    if(!hex.test(sourceSha||'')||metadata.source_image_sha256!==sourceSha
      ||!["image-affine-chain-v1","image-projective-chain-v1"].includes(geometry?.version)||geometry.source?.sha256!==sourceSha)
      throw Error("原图与坐标来源不匹配，未展示高亮。");
    const size=geometry.output?.size_px,matrix=geometry.source_to_output;
    if(!Array.isArray(size)||size.length!==2||!size.every(v=>Number.isInteger(v)&&v>0)
      ||!Array.isArray(matrix)||matrix.length!==(geometry.version==='image-projective-chain-v1'?9:6)||!matrix.every(Number.isFinite))throw Error("显示坐标不可用。");
    const determinant=matrix.length===9?
      matrix[0]*(matrix[4]*matrix[8]-matrix[5]*matrix[7])-matrix[1]*(matrix[3]*matrix[8]-matrix[5]*matrix[6])+matrix[2]*(matrix[3]*matrix[7]-matrix[4]*matrix[6]):
      matrix[0]*matrix[4]-matrix[1]*matrix[3];
    if(!Number.isFinite(determinant)||determinant===0)throw Error("显示坐标不可逆，未展示高亮。");
    const items=[];
    function add(location,kind,label,text){
      if(!location?.original_bbox_eligible||location.source_sha256!==sourceSha
        ||location.coordinate_scope!=="source_image_stored_pixel_edges")return;
      const polygon=location.polygon_px;
      if(!Array.isArray(polygon)||polygon.length!==4||!polygon.every(p=>Array.isArray(p)&&p.length===2&&p.every(Number.isFinite)))return;
      const weights=polygon.map(([x,y])=>matrix.length===9?matrix[6]*x+matrix[7]*y+matrix[8]:1);
      if(!weights.every(w=>w>1e-10)&&!weights.every(w=>w< -1e-10))return;
      const points=polygon.map(([x,y],index)=>[(matrix[0]*x+matrix[1]*y+matrix[2])/weights[index],(matrix[3]*x+matrix[4]*y+matrix[5])/weights[index]]);
      if(!points.every(p=>p[0]>=-1e-6&&p[0]<=size[0]+1e-6&&p[1]>=-1e-6&&p[1]<=size[1]+1e-6))return;
      items.push({kind,label,text:String(text||"未识别文字"),points});
    }
    if(metadata.original_pixel_mapping?.status==="mapped")
      (metadata.regions||[]).slice(0,2000).forEach((region,index)=>add(region.original_geometry,"text",`文字 ${index+1}`,region.text));
    const candidate=metadata.scanned_table_evidence;
    const tableSource=candidate?.original_pixel_mapping?.status==="mapped"?candidate:metadata;
    if(tableSource.original_pixel_mapping?.source?.sha256===sourceSha)
      (tableSource.scanned_grids?.tables||[]).slice(0,16).forEach((table,index)=>{
        let count=0;
        (table.cells||[]).forEach((row,r)=>row.forEach((cell,c)=>{
          if(++count>1000||cell.status==="covered_by_merged_cell")return;
          add(cell.original_geometry,"cell",`表 ${index+1} · 第 ${r+1} 行、第 ${c+1} 列${cell.rowspan>1||cell.colspan>1?` · 合并 ${cell.rowspan}×${cell.colspan}`:""}`,cell.text);
        }));
      });
    const borderless=metadata.borderless_table_evidence;
    const hasGrid=items.some(item=>item.kind==="cell");
    if(!hasGrid&&borderless?.original_pixel_mapping?.source?.sha256===sourceSha
      &&borderless.original_pixel_mapping.status==="mapped"&&borderless.table_layout?.status==="candidate"){
      let count=0;
      const regions=Array.isArray(borderless.table_layout.regional_candidates)&&borderless.table_layout.regional_candidates.length
        ?borderless.table_layout.regional_candidates.slice(0,16):[borderless.table_layout];
      regions.forEach((region,index)=>{
      const attempt=region.observation_provenance?.source_attempt;
      (region.rows||[]).slice(0,2000).forEach((row,r)=>row.forEach((cell,c)=>{
        if(++count>2000)return;
        add(cell.original_geometry,"cell",`无边框候选 · 区域 ${index+1} · 第 ${r+1} 行、观察 ${c+1}${Number.isInteger(attempt)?` · 识别尝试 ${attempt}`:""}${cell.observation_origin==="borderless_cell_crop_ocr"?" · 局部补识别":""}`,cell.text);
      }));
      (region.continuity_evidence||[]).slice(0,200).forEach(bridge=>{
        (bridge.observations||[]).slice(0,12).forEach(observation=>add(observation.original_geometry,
          "continuity","区域延续文字 · 尚未确认所属单元格",observation.text));
      });
      });
    }
    if(!hasGrid&&borderless?.original_pixel_mapping?.source?.sha256===sourceSha
      &&borderless.original_pixel_mapping.status==="mapped"&&borderless.table_layout?.status==="candidate"){
      (borderless.table_layout.sparse_observations||[]).slice(0,32).forEach((region,index)=>{
        if(region.is_table!==false||!["sparse_pairs_with_repeated_numeric_column","locally_reobserved_label_amount_pair","close_same_line_label_amount_pair"].includes(region.scope))return;
        const attempt=region.observation_provenance?.source_attempt;
        (region.rows||[]).slice(0,2).forEach((row,r)=>row.forEach((cell,c)=>{
          add(cell.original_geometry,"sparse",`独立条目 ${index+1} · 第 ${r+1} 行、观察 ${c+1}${Number.isInteger(attempt)?` · 识别尝试 ${attempt}`:''}${region.cross_attempt_supplement?' · 其他尝试保留条目':''} · 未确认表格归属`,cell.text);
        }));
      });
    }
    return {size,items,borderlessCandidate:!hasGrid&&!!borderless,
      refinedRegions:borderless?.region_selection?.replaced_regions||0,
      supplementedPairs:borderless?.region_selection?.supplemented_independent_pairs||0,
      localPairs:borderless?.table_layout?.scope==="local_observed_pairs_not_complete_table",
      separateFrames:[candidate,borderless].some(value=>value&&value.coordinate_frame?.sha256!==metadata.coordinate_frame?.sha256)};
  }
  async function digest(bytes){
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(x=>x.toString(16).padStart(2,"0")).join("");
  }
  async function render(metadata,preview,sourceSha){
    const bytes=Uint8Array.from(atob(preview.image_base64),c=>c.charCodeAt(0));
    if(await digest(bytes)!==preview.geometry?.output?.sha256)throw Error("原图预览内容校验失败。");
    const state=model(metadata,preview,sourceSha),host=document.createElement("section");host.className="scan-source-overlay";
    const toolbar=document.createElement("div"),info=document.createElement("p");toolbar.className="toolbar";
    info.setAttribute("aria-live","polite");info.textContent="点击高亮框查看对应文字或单元格。";
    const namespace="http://www.w3.org/2000/svg",svg=document.createElementNS(namespace,"svg");
    svg.setAttribute("viewBox",`0 0 ${state.size.join(" ")}`);svg.setAttribute("role","group");svg.setAttribute("aria-label","原图文字及表格位置");
    svg.style.cssText="display:block;width:100%;max-height:650px;margin:12px 0;background:white";
    const image=document.createElementNS(namespace,"image");image.setAttribute("width",state.size[0]);image.setAttribute("height",state.size[1]);
    image.setAttribute("href","data:image/png;base64,"+preview.image_base64);svg.append(image);
    const groups={text:document.createElementNS(namespace,"g"),cell:document.createElementNS(namespace,"g"),continuity:document.createElementNS(namespace,"g"),sparse:document.createElementNS(namespace,"g")};
    svg.append(groups.text,groups.cell,groups.continuity,groups.sparse);
    state.items.forEach(item=>{
      const shape=document.createElementNS(namespace,"polygon");shape.setAttribute("points",item.points.map(p=>p.join(",")).join(" "));
      shape.setAttribute("fill",item.kind==="cell"?"rgba(218,126,25,.08)":"rgba(38,117,191,.04)");
      shape.setAttribute("stroke",item.kind==="cell"?"#bc6b16":"#2675bf");shape.setAttribute("stroke-width","1.5");shape.setAttribute("vector-effect","non-scaling-stroke");
      if(item.kind==="continuity"){shape.setAttribute("stroke","#777");shape.setAttribute("stroke-dasharray","4 3");}
      if(item.kind==="sparse"){shape.setAttribute("stroke","#8862ad");shape.setAttribute("stroke-dasharray","3 2");}
      shape.setAttribute("tabindex","0");shape.setAttribute("role","button");shape.setAttribute("aria-label",`${item.label}：${item.text}`);
      const title=document.createElementNS(namespace,"title");title.textContent=`${item.label}：${item.text}`;shape.append(title);
      const focus=()=>{info.textContent=`${item.label} · OCR 观察值：${item.text}。请对照原图核对。`;};
      shape.addEventListener("click",focus);shape.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();focus();}});
      groups[item.kind].append(shape);
    });
    for(const [label,mode] of [["文字框","text"],["表格单元格","cell"],["同时显示","both"]]){
      const button=document.createElement("button");button.type="button";button.textContent=label;button.setAttribute("aria-pressed",String(mode==="both"));
      button.onclick=()=>{groups.text.style.display=mode==="cell"?"none":"";groups.cell.style.display=mode==="text"?"none":"";groups.continuity.style.display=mode==="text"?"none":"";groups.sparse.style.display=mode==="text"?"none":"";toolbar.querySelectorAll("button").forEach(b=>b.setAttribute("aria-pressed",String(b===button)));};toolbar.append(button);
    }
    const note=document.createElement("p");note.className="muted";
    note.textContent=`${state.items.filter(x=>x.kind==="text").length} 个文字框 · ${state.items.filter(x=>x.kind==="cell").length} 个${state.borderlessCandidate?"无边框候选观察框":"单元格"}。${state.localPairs?"仅识别局部标签与数值对，不能代表完整表格。":""}${state.separateFrames?"文字和表格来自不同尝试，均已映回同一原图。":""}这里只核对位置；OCR 数值与表头含义尚未独立验证。`;
    if(state.items.some(x=>x.kind==="continuity"))note.textContent+=" 灰色虚线仅表示区域延续文字，尚未分配到具体单元格。";
    if(state.items.some(x=>x.kind==="sparse"))note.textContent+=` 紫色虚线：${state.items.filter(x=>x.kind==="sparse").length} 个独立条目观察框，未确认表格归属。`;
    if(state.refinedRegions)note.textContent+=` ${state.refinedRegions} 个区域按相同金额与原图位置核对后，采用文字置信度更高的识别尝试；置信度不代表事实正确。`;
    if(state.supplementedPairs)note.textContent+=` 保留其他尝试中的 ${state.supplementedPairs} 个位置不重叠的独立条目，各自使用实际识别坐标；未拼接为完整表格。`;
    host.append(toolbar,note,svg,info);return host;
  }
  const api={model,digest,render};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  root.ScanSourceOverlay=api;
})(typeof window!=="undefined"?window:globalThis);
