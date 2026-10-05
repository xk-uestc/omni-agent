const test=require('node:test'),assert=require('node:assert/strict'),excel=require('./excel-preview.js');
test('zero false and null remain distinct',()=>{
  assert.equal(excel.valueLabel({raw_value:0}),'0');assert.equal(excel.valueLabel({raw_value:false}),'false');assert.equal(excel.valueLabel(null),'空');
});
test('stale formula cache is labelled and never displayed as the answer',()=>{
  assert.equal(excel.valueLabel({formula:'=A1*2',cached_value:999}),'=A1*2（公式未重算；缓存未核验）');
});
test('merged numeric anchor is reference not copied result',()=>{
  assert.match(excel.valueLabel({value_status:'merged_covered',merged_anchor:'B2',merged_anchor_value:200}),/不重复计数/);
});
test('error and identifier formatting stay visible',()=>{
  assert.match(excel.valueLabel({raw_value:'#REF!',error:'#REF!'}),/Excel错误/);
  assert.equal(excel.valueLabel({raw_value:123,display_value:'000123'}),'000123');
  assert.equal(excel.valueLabel({raw_value:.125,number_format:'0.00%'}),'12.5%');
});
test('same-row parallel tables stay separate and chunk parts are deduplicated',()=>{
  const layouts=['a','b'].map(table_id=>({table_id,headers:['地区','金额'],header_decision:'selected'}));
  const chunk=(id,row)=>({content_type:'row',row_start:row,metadata:{table_id:id,values:[{raw_value:'华东',coordinate:'A2'},{raw_value:7,coordinate:'B2'}]}});
  const result=excel.tables({stats:{table_layouts:layouts},chunks:[chunk('a',2),chunk('a',2),chunk('b',2)]});
  assert.equal(result.length,2);assert.equal(result[0].rows.length,1);assert.equal(result[1].rows.length,1);assert.equal(result[0].rows[0].cells[1].coordinate,'B2');
});
test('preview truncation is disclosed and source data is untouched',()=>{
  const payload={stats:{table_layouts:[{table_id:'a',headers:['a','b','c'],header_decision:'preserved_ambiguous'}]},chunks:[1,2,3].map(row=>({content_type:'row',row_start:row,metadata:{table_id:'a',values:[1,2,3]}}))};
  const result=excel.tables(payload,{rowLimit:1,columnLimit:2})[0];
  assert.equal(result.hiddenPreviewRows,2);assert.equal(result.hiddenPreviewColumns,1);assert.equal(result.needsConfirmation,true);assert.equal(payload.chunks.length,3);
});
test('explicit no-header and hierarchical selection roundtrip',()=>{
  assert.deepEqual(excel.selections([{sheet_name:'预算!2025',range:'D6:F10',header_rows:'0'}]),[{sheet_name:'预算!2025',range:'D6:F10',header_rows:0}]);
  assert.equal(excel.selections([{sheet_name:'数据',range:'A1:C10',header_rows:'3'}])[0].header_rows,3);
});
test('missing or invalid selection cannot become implicit no-header',()=>{
  for(const header_rows of ['',-1,9,'abc',1.5])assert.throws(()=>excel.selections([{sheet_name:'数据',range:'A1:B3',header_rows}]));
  assert.throws(()=>excel.selections([]));assert.throws(()=>excel.selections([{sheet_name:'数据',range:'not-a-range',header_rows:1}]));
});
