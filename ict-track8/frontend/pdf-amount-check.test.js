const {test}=require('node:test'),assert=require('node:assert/strict');
const {describe}=require('./pdf-amount-check.js');
test('amount agreement is source agreement, never ground truth',()=>{
  const view=describe({text:'$2,000',amount_check:{status:'matched',ocr_text:'$2,000',native_text:'$2000'}});
  assert.match(view.label,/原文字/);assert.match(view.detail,/仍可能错误/);assert.equal(view.color,'#16803c');
});
test('conflict and ambiguity remain visible with original text',()=>{
  assert.equal(describe({text:'$200',amount_check:{status:'conflict'}}).color,'#c62828');
  const view=describe({text:'$2.000',amount_check:{status:'needs_review',native_text:'$2,000'}});
  assert.match(view.detail,/\$2\.000/);assert.equal(view.label,'金额需复核');
});
test('nonamount cells have no unverified monetary label',()=>{
  assert.equal(describe({text:'Personnel',amount_check:{status:'unavailable'}}),null);
  assert.equal(describe({text:'$1000',amount_check:{status:'unavailable'}}).label,'金额未独立核验');
  assert.equal(describe({amount_check:{status:'verified'}}),null);
});
