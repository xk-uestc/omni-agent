/* Pure XLSX preview model. Original coordinates and unverified formulas stay visible. */
(function(root){
  'use strict';
  function valueLabel(cell){
    if(cell===null||cell===undefined)return '空';
    if(typeof cell!=='object')return String(cell);
    if(cell.formula)return `${cell.formula}（公式未重算${cell.cached_value!==null&&cell.cached_value!==undefined?'；缓存未核验':''}）`;
    if(cell.error)return `${cell.error}（Excel错误）`;
    if(cell.value_status==='merged_covered')return `↳ ${cell.merged_anchor}：${cell.merged_anchor_value??'空'}（合并覆盖，不重复计数）`;
    if(cell.raw_value===null||cell.raw_value===undefined)return '空';
    if(cell.display_value!==undefined)return String(cell.display_value);
    if(typeof cell.raw_value==='number'&&String(cell.number_format||'').includes('%'))return `${Number((cell.raw_value*100).toPrecision(12))}%`;
    return String(cell.raw_value);
  }
  function tables(payload,{rowLimit=12,columnLimit=20}={}){
    const chunks=Array.isArray(payload?.chunks)?payload.chunks:[],layouts=payload?.stats?.table_layouts||[];
    return layouts.map(layout=>{
      const seen=new Set(),rows=[];
      for(const chunk of chunks){
        const meta=chunk.metadata||{};
        if(chunk.content_type!=='row'||meta.table_id!==layout.table_id||seen.has(chunk.row_start))continue;
        seen.add(chunk.row_start);rows.push({row:chunk.row_start,role:meta.row_role||'record',
          cells:(meta.values||[]).slice(0,columnLimit).map(value=>({label:valueLabel(value),coordinate:value?.coordinate||'',raw:value}))});
      }
      return {...layout,headers:layout.headers.slice(0,columnLimit),rows:rows.slice(0,rowLimit),
        hiddenPreviewRows:Math.max(0,rows.length-rowLimit),hiddenPreviewColumns:Math.max(0,layout.headers.length-columnLimit),
        needsConfirmation:layout.header_decision==='preserved_ambiguous'||layout.header_decision==='inherited_after_blank'};
    });
  }
  function selections(entries){
    if(!Array.isArray(entries)||!entries.length||entries.length>64)throw Error('请指定1至64个表区域');
    return entries.map(item=>{
      const count=Number(item.header_rows);
      if(!item.sheet_name||!Number.isInteger(count)||count<0||count>8||String(item.header_rows).trim()===''||
        !/^[A-Z]{1,3}[1-9]\d{0,6}:[A-Z]{1,3}[1-9]\d{0,6}$/.test(item.range))throw Error('请填写工作表、A1:B10格式区域与0至8层表头');
      return {sheet_name:item.sheet_name,range:item.range,header_rows:count};
    });
  }
  const api={valueLabel,tables,selections};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  if(root)root.ExcelPreview=api;
})(typeof window!=='undefined'?window:null);
