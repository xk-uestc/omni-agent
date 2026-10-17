const test=require('node:test'),assert=require('node:assert/strict');
const {model,totalModel,factModel,tableFactModel}=require('./native-row-overlay.js');
const sha='a'.repeat(64);
function fixture(){return {metadata:{document_id:'d',page_no:1,source_sha256:sha,native_row:{document_id:'d',page_no:1,source_sha256:sha,fields:[{bbox_pt:[10,20,30,40],header:'Subject',text:'A'},{bbox_pt:[50,20,60,40],header:'Result',text:'3 UG/L'}]}},manifest:{document_id:'d',page_no:1,source_sha256:sha,size_px:[200,100],mappings:{fitz_unrotated_to_display:[1,0,0,1,0,0],display_to_asset_px:[1,0,0,1,0,0]}},selection:{column_index:1}};}
test('subject and queried cell keep separate exact rectangles',()=>{const f=fixture(),boxes=model(f.metadata,f.manifest,f.selection);assert.deepEqual(boxes[1].bbox_normalized,[.25,.2,.3,.4]);assert.equal(boxes[1].role,'value');assert.equal(boxes[0].role,'subject');});
test('90-degree rotated original uses server matrix rather than guessed orientation',()=>{const f=fixture();f.manifest.size_px=[100,200];f.manifest.mappings.fitz_unrotated_to_display=[0,1,-1,0,100,0];assert.deepEqual(model(f.metadata,f.manifest,f.selection)[1].bbox_normalized,[.6,.25,.8,.3]);});
test('crop offsets and rendering scale both apply',()=>{const f=fixture();f.manifest.mappings.display_to_asset_px=[2,0,0,2,-10,-20];assert.deepEqual(model(f.metadata,f.manifest,f.selection)[1].bbox_normalized,[.45,.2,.55,.6]);});
test('wrong source page or document prevents misleading highlight',()=>{for(const key of ['source_sha256','document_id','page_no']){const f=fixture();f.manifest[key]='wrong';assert.throws(()=>model(f.metadata,f.manifest,f.selection));}});
test('invalid index matrix or field bounds are rejected',()=>{const f=fixture();assert.throws(()=>model(f.metadata,f.manifest,{column_index:true}));f.manifest.mappings.fitz_unrotated_to_display=[0,0,0,0,0,0];assert.throws(()=>model(f.metadata,f.manifest,f.selection));const g=fixture();g.metadata.native_row.fields[1].bbox_pt=[50,20,500,40];assert.throws(()=>model(g.metadata,g.manifest,g.selection));});
test('multiple projected fields remain individually located',()=>{const f=fixture();const boxes=model(f.metadata,f.manifest,{column_indices:[0,1]});assert.equal(boxes.length,2);assert.equal(boxes[0].role,'subject');assert.deepEqual(boxes[1].bbox_normalized,[.25,.2,.3,.4]);for(const indices of [[],[1,1],[true],[99]])assert.throws(()=>model(f.metadata,f.manifest,{column_indices:indices}));});
function totalFixture(){const f=fixture();delete f.metadata.native_row;const a={annotation_id:'d:amount-1',source_sha256:sha,page_no:1,label:'TOTAL FUNDS',raw_value:'$32.50m',label_bbox_pt:[10,20,30,40],amount_bbox_pt:[50,20,60,40]};f.metadata.native_total_annotation=a;f.metadata.locator={coordinate_system:'fitz_unrotated_pt'};f.selected=[structuredClone(a)];return f;}
test('isolated total locates both the original label and selected amount',()=>{const f=totalFixture();const boxes=totalModel(f.metadata,f.manifest,f.selected);assert.equal(boxes[0].role,'subject');assert.equal(boxes[1].text,'$32.50m');assert.deepEqual(boxes[1].bbox_normalized,[.25,.2,.3,.4]);});
test('total highlight refuses absent, duplicated or changed selected annotations',()=>{for(const change of [f=>f.selected=[],f=>f.selected.push({...f.selected[0]}),f=>f.selected[0].raw_value='$99m',f=>f.metadata.native_total_annotation.source_sha256='b'.repeat(64)]){const f=totalFixture();change(f);assert.throws(()=>totalModel(f.metadata,f.manifest,f.selected));}});
test('total source and coordinate frames must match the current rendered page',()=>{for(const change of [f=>f.manifest.page_no=2,f=>f.manifest.document_id='other',f=>f.metadata.locator.coordinate_system='display_pt',f=>f.manifest.source_sha256='b'.repeat(64)]){const f=totalFixture();change(f);assert.throws(()=>totalModel(f.metadata,f.manifest,f.selected));}});
test('total highlight follows actual rotation and rejects out of bounds geometry',()=>{const f=totalFixture();f.manifest.size_px=[100,200];f.manifest.mappings.fitz_unrotated_to_display=[0,1,-1,0,100,0];assert.deepEqual(totalModel(f.metadata,f.manifest,f.selected)[1].bbox_normalized,[.6,.25,.8,.3]);const g=totalFixture();g.metadata.native_total_annotation.amount_bbox_pt=[1,2,9999,40];g.selected=structuredClone([g.metadata.native_total_annotation]);assert.throws(()=>totalModel(g.metadata,g.manifest,g.selected));});
function financialFixture(){const f=fixture();delete f.metadata.native_row;f.metadata.fact={fact_id:'table:row:2:column:0',value_kind:'native_grouped_financial_cell_literal',source_sha256:sha,page_no:1,row_header:'Profit for the year',row_header_bbox_display_pt:[10,20,30,40],column_header_path:['Group','2008',"RM'000"],column_header_bboxes_display_pt:[[50,2,60,6],[50,7,60,11],[50,12,60,16]],raw_value:'(10,000)',bbox_display_pt:[50,20,60,40]};f.selected=[structuredClone(f.metadata.fact)];return f;}
test('financial highlight separates indicator, group, year, scale and original amount',()=>{const f=financialFixture();const boxes=factModel(f.metadata,f.manifest,f.selected);assert.equal(boxes.length,5);assert.equal(boxes[1].text,'Group');assert.equal(boxes[2].text,'2008');assert.equal(boxes[3].text,"RM'000");assert.equal(boxes[4].text,'(10,000)');assert.deepEqual(boxes[4].bbox_normalized,[.25,.2,.3,.4]);});
test('financial display coordinates never receive the PDF rotation twice',()=>{const f=financialFixture();f.manifest.mappings.fitz_unrotated_to_display=[0,1,-1,0,100,0];assert.deepEqual(factModel(f.metadata,f.manifest,f.selected)[4].bbox_normalized,[.25,.2,.3,.4]);});
test('financial highlight applies raster crop and scale',()=>{const f=financialFixture();f.manifest.mappings.display_to_asset_px=[2,0,0,2,0,0];assert.deepEqual(factModel(f.metadata,f.manifest,f.selected)[4].bbox_normalized,[.5,.4,.6,.8]);});
test('financial highlight rejects wrong source, page, selected value or header geometry',()=>{for(const mutate of [f=>f.selected=[],f=>f.selected.push({...f.selected[0]}),f=>f.selected[0].raw_value='100',f=>f.manifest.source_sha256='b'.repeat(64),f=>f.manifest.page_no=2,f=>f.manifest.document_id='other',f=>f.metadata.fact.column_header_bboxes_display_pt=[],f=>f.metadata.fact.bbox_display_pt=[50,20,9999,40]]){const f=financialFixture();mutate(f);assert.throws(()=>factModel(f.metadata,f.manifest,f.selected));}});

function tableFixture(){
  const f=fixture();delete f.metadata.native_row;
  f.tableFact={document_id:'d',page_no:1,sha256:sha,source_uri:'/api/v1/knowledge/documents/d/original',
    locator:'page:1/element:native_e00001/row:2/column:Unit price',structured_element_id:'native_e00001',
    validation:'native_table_literal_cell_replayed',value:7.25,raw_value:'7.25',unit:'unknown',
    matched_conditions:{Grade:'Level B'},condition_bboxes_display_pt:{Grade:[10,20,30,40]},
    column_header_bbox_display_pt:[50,2,60,10],bbox_display_pt:[50,20,60,40]};
  Object.assign(f.metadata,{source_locator:f.tableFact.locator,bbox_display_pt:structuredClone(f.tableFact.bbox_display_pt),
    condition_bboxes_display_pt:structuredClone(f.tableFact.condition_bboxes_display_pt),
    column_header_bbox_display_pt:structuredClone(f.tableFact.column_header_bbox_display_pt)});
  f.manifest.coordinate_frame={origin:'top_left',unit:'pt',rotation_applied:true};return f;
}
test('replayed native table highlights literal conditions, exact column and returned cell',()=>{
  const f=tableFixture(),before=structuredClone(f),boxes=tableFactModel(f.metadata,f.manifest,f.tableFact);
  assert.deepEqual(boxes.map(b=>[b.label,b.text,b.role]),[
    ['Grade','Level B','subject'],['查询列','Unit price','subject'],['原文值','7.25','value']]);
  assert.deepEqual(boxes[2].bbox_normalized,[.25,.2,.3,.4]);assert.deepEqual(f,before);
});
test('native table display boxes apply scale and crop but never rotate a second time',()=>{
  const f=tableFixture();f.manifest.mappings.fitz_unrotated_to_display=[0,1,-1,0,100,0];
  assert.deepEqual(tableFactModel(f.metadata,f.manifest,f.tableFact)[2].bbox_normalized,[.25,.2,.3,.4]);
  f.manifest.mappings.display_to_asset_px=[2,0,0,2,-10,0];
  assert.deepEqual(tableFactModel(f.metadata,f.manifest,f.tableFact)[2].bbox_normalized,[.45,.4,.55,.8]);
});
test('native table overlay requires the exact replay validation and current source identity',()=>{
  for(const mutate of [f=>f.tableFact.validation='model_reviewed',f=>delete f.tableFact.validation,
    f=>f.tableFact.sha256='b'.repeat(64),f=>f.tableFact.document_id='other',f=>f.tableFact.page_no=2,
    f=>f.manifest.source_sha256='b'.repeat(64),f=>f.manifest.document_id='other',f=>f.manifest.page_no=2,
    f=>f.metadata.page_no=true,f=>f.tableFact.source_uri='/api/v1/knowledge/documents/other/original']){
    const f=tableFixture();mutate(f);assert.throws(()=>tableFactModel(f.metadata,f.manifest,f.tableFact));
  }
});
test('native table overlay refuses a different locator page, element, row or target column',()=>{
  for(const mutate of [f=>f.metadata.source_locator=f.metadata.source_locator.replace('/row:2/','/row:3/'),
    f=>f.tableFact.locator=f.tableFact.locator.replace('Unit price','Another column'),
    f=>f.tableFact.structured_element_id='native_e00002',f=>f.tableFact.locator='page:1',
    f=>{f.tableFact.locator=f.tableFact.locator.replace('page:1','page:2');f.metadata.source_locator=f.tableFact.locator;}]){
    const f=tableFixture();mutate(f);assert.throws(()=>tableFactModel(f.metadata,f.manifest,f.tableFact));
  }
});
test('native table citation geometry must exactly agree with replayed cell and conditions',()=>{
  for(const mutate of [f=>f.metadata.bbox_display_pt[0]=49,f=>f.metadata.column_header_bbox_display_pt[0]=49,
    f=>f.metadata.condition_bboxes_display_pt.Grade[0]=9,f=>delete f.tableFact.condition_bboxes_display_pt.Grade,
    f=>f.tableFact.matched_conditions.Other='A',f=>f.metadata.condition_bboxes_display_pt.Other=[1,2,3,4],
    f=>f.tableFact.matched_conditions.Grade=true,f=>f.tableFact.matched_conditions.Grade=Infinity,
    f=>f.tableFact.matched_conditions={},f=>f.tableFact.raw_value=7.25]){
    const f=tableFixture();mutate(f);assert.throws(()=>tableFactModel(f.metadata,f.manifest,f.tableFact));
  }
});
test('native table overlay fails closed for unsupported frames, invalid matrices and off-page cells',()=>{
  for(const mutate of [f=>delete f.manifest.coordinate_frame,f=>f.manifest.coordinate_frame.rotation_applied=false,
    f=>f.manifest.coordinate_frame.unit='px',f=>f.manifest.coordinate_frame.origin='bottom_left',
    f=>f.manifest.mappings.display_to_asset_px=[0,0,0,0,0,0],f=>f.manifest.size_px=[0,100],
    f=>{f.tableFact.bbox_display_pt=[50,20,9999,40];f.metadata.bbox_display_pt=[50,20,9999,40];},
    f=>{f.tableFact.bbox_display_pt=[50,20,50,40];f.metadata.bbox_display_pt=[50,20,50,40];},
    f=>f.manifest.mappings.display_to_asset_px=[1,0,0,1,0,-30]]){
    const f=tableFixture();mutate(f);assert.throws(()=>tableFactModel(f.metadata,f.manifest,f.tableFact));
  }
});
