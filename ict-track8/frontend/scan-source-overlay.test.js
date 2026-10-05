const {test}=require('node:test');
const assert=require('node:assert/strict');
const {model}=require('./scan-source-overlay.js');
const sha='a'.repeat(64);
const location={original_bbox_eligible:true,source_sha256:sha,coordinate_scope:'source_image_stored_pixel_edges',polygon_px:[[10,10],[20,10],[20,20],[10,20]]};
const preview={geometry:{version:'image-affine-chain-v1',source:{sha256:sha},output:{size_px:[30,40]},source_to_output:[0,-1,30,1,0,0]}};

test('projective highlight uses the homogeneous denominator for every original corner',()=>{
  const transformed={geometry:{version:'image-projective-chain-v1',source:{sha256:sha},
    output:{size_px:[30,40]},source_to_output:[1,0,0,0,1,0,.01,0,1]}};
  const points=model(metadata(),transformed,sha).items[0].points;
  for(const [index,[x,y]] of location.polygon_px.entries()){
    assert.ok(Math.abs(points[index][0]-x/(1+.01*x))<1e-10);
    assert.ok(Math.abs(points[index][1]-y/(1+.01*x))<1e-10);
  }
});

test('projective singularity or horizon crossing never produces a misleading highlight',()=>{
  const transformed={geometry:{version:'image-projective-chain-v1',source:{sha256:sha},
    output:{size_px:[30,40]},source_to_output:[1,0,0,0,1,0,1,0,-15]}};
  assert.equal(model(metadata(),transformed,sha).items.length,0);
  transformed.geometry.source_to_output=[0,0,1,0,0,1,0,0,1];
  assert.throws(()=>model(metadata(),transformed,sha),/不可逆/);
  transformed.geometry.source_to_output=[1,0,0,0,1,0];
  assert.throws(()=>model(metadata(),transformed,sha),/不可用/);
});
function metadata(){return {source_image_sha256:sha,coordinate_frame:{sha256:'b'.repeat(64)},original_pixel_mapping:{status:'mapped',source:{sha256:sha}},regions:[{text:'observed',original_geometry:location}]};}
test('stored EXIF coordinates align with the normalized displayed PNG',()=>{
  const state=model(metadata(),preview,sha);
  assert.deepEqual(state.items[0].points,[[20,10],[20,20],[10,20],[10,10]]);
});
test('independently selected corrected grid retains the original source binding',()=>{
  const data=metadata();data.scanned_table_evidence={coordinate_frame:{sha256:'c'.repeat(64)},original_pixel_mapping:{status:'mapped',source:{sha256:sha}},
    scanned_grids:{tables:[{cells:[[{text:'47000',rowspan:1,colspan:2,original_geometry:location},{status:'covered_by_merged_cell',original_geometry:location}]]}]}};
  const state=model(data,preview,sha);
  assert.equal(state.items.length,2);assert.equal(state.items[1].kind,'cell');assert.equal(state.separateFrames,true);
  assert.match(state.items[1].label,/合并 1×2/);
});
test('padding, wrong source and nonfinite polygon receive no highlight',()=>{
  const data=metadata();data.regions=[{original_geometry:{...location,original_bbox_eligible:false}},
    {original_geometry:{...location,source_sha256:'d'.repeat(64)}},
    {original_geometry:{...location,polygon_px:[[NaN,0],[2,0],[2,2],[0,2]]}}];
  assert.equal(model(data,preview,sha).items.length,0);
  assert.throws(()=>model(data,preview,'f'.repeat(64)),/来源不匹配/);
});

test('borderless observations keep candidate labels and use their own original frame',()=>{
  const data=metadata();data.borderless_table_evidence={coordinate_frame:{sha256:'c'.repeat(64)},
    original_pixel_mapping:{status:'mapped',source:{sha256:sha}},table_layout:{status:'candidate',rows:[[
      {text:'1',observation_origin:'borderless_cell_crop_ocr',original_geometry:location}]]}};
  const state=model(data,preview,sha);
  assert.equal(state.items[1].text,'1');assert.match(state.items[1].label,/候选/);
  assert.match(state.items[1].label,/局部补识别/);assert.equal(state.separateFrames,true);
  data.borderless_table_evidence.original_pixel_mapping.source.sha256='d'.repeat(64);
  assert.equal(model(data,preview,sha).items.length,1);
});

test('layout continuity has a separate unassigned layer and rejects a foreign original frame',()=>{
  const data=metadata();data.borderless_table_evidence={original_pixel_mapping:{status:'mapped',source:{sha256:sha}},
    table_layout:{status:'candidate',scope:'local_observed_pairs_not_complete_table',rows:[],continuity_evidence:[{
      observations:[{text:'continued label',original_geometry:location},
                    {text:'foreign text',original_geometry:{...location,source_sha256:'d'.repeat(64)}}]}]}};
  const state=model(data,preview,sha);
  const items=state.items.filter(item=>item.kind==='continuity');
  assert.equal(items.length,1);assert.equal(items[0].text,'continued label');
  assert.match(items[0].label,/尚未确认所属单元格/);assert.equal(state.localPairs,true);
  assert.equal(state.items.filter(item=>item.kind==='cell').length,0);
});

test('sparse observations remain separate from table cells and reject foreign sources',()=>{
  const data=metadata();data.borderless_table_evidence={original_pixel_mapping:{status:'mapped',source:{sha256:sha}},
    table_layout:{status:'candidate',rows:[],sparse_observations:[{is_table:false,scope:'sparse_pairs_with_repeated_numeric_column',rows:[[
      {text:'Short item',original_geometry:location},{text:'0.10',original_geometry:location},
      {text:'foreign',original_geometry:{...location,source_sha256:'d'.repeat(64)}}]]}]}};
  const state=model(data,preview,sha);
  assert.equal(state.items.filter(item=>item.kind==='cell').length,0);
  assert.deepEqual(state.items.filter(item=>item.kind==='sparse').map(item=>item.text),['Short item','0.10']);
  assert.match(state.items.find(item=>item.kind==='sparse').label,/未确认表格归属/);
});

test('multiple local regions display without duplicating the compatibility primary rows',()=>{
  const data=metadata();data.borderless_table_evidence={original_pixel_mapping:{status:'mapped',source:{sha256:sha}},
    region_selection:{replaced_regions:1},table_layout:{status:'candidate',rows:[[{text:'primary duplicate',original_geometry:location}]],regional_candidates:[
      {observation_provenance:{source_attempt:1},rows:[[{text:'left amount',original_geometry:location}]]},
      {rows:[[{text:'right amount',original_geometry:location}]]}]}};
  const items=model(data,preview,sha).items.filter(item=>item.kind==='cell');
  assert.equal(items.length,2);assert.match(items[0].label,/区域 1/);assert.match(items[1].label,/区域 2/);
  assert.match(items[0].label,/识别尝试 1/);
  assert.equal(model(data,preview,sha).refinedRegions,1);
  assert.deepEqual(items.map(item=>item.text),['left amount','right amount']);
});

test('tight label and amount remain non-table observations with source-bound highlights',()=>{
  const data=metadata();data.borderless_table_evidence={original_pixel_mapping:{status:'mapped',source:{sha256:sha}},
    table_layout:{status:'candidate',rows:[],sparse_observations:[{is_table:false,scope:'close_same_line_label_amount_pair',
      rows:[[{text:'TOTAL FSA',original_geometry:location},{text:'$29.03m',original_geometry:location}]]}]}};
  const state=model(data,preview,sha);
  assert.deepEqual(state.items.filter(item=>item.kind==='sparse').map(item=>item.text),['TOTAL FSA','$29.03m']);
  assert.equal(state.items.filter(item=>item.kind==='cell').length,0);
  data.borderless_table_evidence.original_pixel_mapping.source.sha256='d'.repeat(64);
  assert.equal(model(data,preview,sha).items.filter(item=>item.kind==='sparse').length,0);
});

test('supplemental original observations display their own attempt and remain independent pairs',()=>{
  const data=metadata();data.borderless_table_evidence={original_pixel_mapping:{status:'mapped',source:{sha256:sha}},
    region_selection:{supplemented_independent_pairs:1},table_layout:{status:'candidate',rows:[],sparse_observations:[{
      scope:'locally_reobserved_label_amount_pair',is_table:false,cross_attempt_supplement:true,
      observation_provenance:{source_attempt:1},rows:[[{text:'Total',original_geometry:location},{text:'$22.56',original_geometry:location}]]}]}};
  const state=model(data,preview,sha);
  assert.equal(state.supplementedPairs,1);
  const items=state.items.filter(item=>item.kind==='sparse');
  assert.equal(items.length,2);
  assert.match(items[0].label,/识别尝试 1/);
  assert.match(items[0].label,/其他尝试保留条目/);
  assert.equal(state.items.filter(item=>item.kind==='cell').length,0);
});
