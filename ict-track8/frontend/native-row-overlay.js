(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;if(root)root.NativeRowOverlay=api;})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const hex=/^[a-f0-9]{64}$/;
  function matrix(value){if(!Array.isArray(value)||value.length!==6||!value.every(Number.isFinite)||Math.abs(value[0]*value[3]-value[1]*value[2])<1e-9)throw Error('原页坐标映射不可用。');return value;}
  function point([x,y],m){return [m[0]*x+m[2]*y+m[4],m[1]*x+m[3]*y+m[5]];}
  function model(metadata,manifest,selection){
    const row=metadata.native_row,source=metadata.source_sha256;
    const indices=Array.isArray(selection?.column_indices)?selection.column_indices:[selection?.column_index];
    if(!hex.test(source||'')||manifest.source_sha256!==source||manifest.document_id!==metadata.document_id||manifest.page_no!==metadata.page_no
      ||row?.source_sha256!==source||row.document_id!==metadata.document_id||row.page_no!==metadata.page_no
      ||!indices.length||indices.length>6||new Set(indices).size!==indices.length
      ||indices.some(index=>!Number.isInteger(index)||index<0||index>=row.fields?.length))throw Error('原页与字段来源不匹配。');
    const size=manifest.size_px;if(!Array.isArray(size)||size.length!==2||!size.every(v=>Number.isInteger(v)&&v>0))throw Error('原页尺寸不可用。');
    const rotate=matrix(manifest.mappings?.fitz_unrotated_to_display),raster=matrix(manifest.mappings?.display_to_asset_px);
    return [...new Set([0,...indices])].map(i=>{
      const field=row.fields[i],box=field.bbox_pt;
      if(!Array.isArray(box)||box.length!==4||!box.every(Number.isFinite)||box[0]>=box[2]||box[1]>=box[3])throw Error('字段坐标不可用。');
      const points=[[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]].map(p=>point(point(p,rotate),raster));
      const normalized=[Math.min(...points.map(p=>p[0]))/size[0],Math.min(...points.map(p=>p[1]))/size[1],Math.max(...points.map(p=>p[0]))/size[0],Math.max(...points.map(p=>p[1]))/size[1]];
      if(normalized.some(v=>v<0||v>1))throw Error('字段超出本次原页显示范围。');
      return {bbox_normalized:normalized,label:field.header,text:field.text,role:i===0&&indices.some(index=>index!==0)?'subject':'value'};
    });
  }
  function totalModel(metadata,manifest,selected){
    const annotation=metadata.native_total_annotation;
    const approved=Array.isArray(selected)?selected.filter(item=>item.annotation_id===annotation?.annotation_id):[];
    if(!annotation||approved.length!==1||JSON.stringify(approved[0])!==JSON.stringify(annotation)
      ||annotation.source_sha256!==metadata.source_sha256||annotation.page_no!==metadata.page_no
      ||!annotation.annotation_id.startsWith(metadata.document_id+':')
      ||metadata.locator?.coordinate_system!=='fitz_unrotated_pt'
      ||typeof annotation.label!=='string'||typeof annotation.raw_value!=='string')throw Error('总额标注与本次原件选择不一致。');
    return model({...metadata,native_row:{document_id:metadata.document_id,page_no:metadata.page_no,
      source_sha256:metadata.source_sha256,fields:[
        {bbox_pt:annotation.label_bbox_pt,header:'总额标签',text:annotation.label},
        {bbox_pt:annotation.amount_bbox_pt,header:'原件金额',text:annotation.raw_value}]}},manifest,{column_index:1});
  }
  return {model,totalModel};
});
