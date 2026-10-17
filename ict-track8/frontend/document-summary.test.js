const {test}=require('node:test'),assert=require('node:assert/strict');
const {summarize,friendlyError}=require('./document-summary.js');
test('PDF summary uses actual parser counts and never counts rejected formula candidates',()=>{
  const result=summarize({modality:'pdf',chunk_count:47,stats:{page_count:1,native_table_count:1,formula_count:1},analysis:{metrics:{formula_count:6},formulas:[{status:'rejected'}]}});
  assert.deepEqual(result.metrics,[{label:'页',value:1},{label:'原生表格',value:1},{label:'公式片段',value:1},{label:'索引片段',value:47}]);
  assert.equal(result.status,'已入库 · 可提问');
});
test('missing statistics are unknown, while an observed zero is shown as zero',()=>{
  const result=summarize({modality:'pdf',chunk_count:3,stats:{formula_count:0}});
  assert.deepEqual(result.metrics,[{label:'公式片段',value:0},{label:'索引片段',value:3}]);
});
test('successful OCR is not displayed as a failure or a confirmation request',()=>{
  const result=summarize({modality:'pdf',chunk_count:1,stats:{ocr_succeeded_pages:[1]},warnings:['page_1:ocr_status:ok','page_orientation_detected']});
  assert.equal(result.issues.length,0);assert.deepEqual(result.ocrPages,[1]);assert.equal(result.tone,'ready');
});
test('table budget and OCR failures provide specific recovery without claiming all parsing failed',()=>{
  const result=summarize({modality:'pdf',chunk_count:6,stats:{ocr_failed_pages:[8]},warnings:['native_table_ingest_page_budget_exceeded']});
  assert.equal(result.readable,true);assert.equal(result.tone,'warning');
  assert.ok(result.issues.some(x=>x.message.includes('第 8 页')));assert.ok(result.issues.some(x=>x.action.includes('文字仍可检索')));
});
test('empty corpus and network errors have actionable messages',()=>{
  assert.equal(summarize({chunk_count:0}).readable,false);
  assert.equal(summarize({modality:'image',chunk_count:1,stats:{content_type_breakdown:{image:1}},warnings:['ocr_returned_no_text']}).readable,false);
  assert.match(friendlyError(Error('Failed to fetch')),/连接/);
  assert.match(friendlyError(Error('PDF 已加密')),/导出/);
});
