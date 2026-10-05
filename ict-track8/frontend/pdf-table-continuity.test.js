const {test}=require('node:test'),assert=require('node:assert/strict');
const {selections,appendLink}=require('./pdf-table-continuity.js');
test('first and following pages retain independent header choices',()=>{
  assert.deepEqual(selections(2,4,1,2,0),[{page_no:2,table_index:0,header_rows:2},{page_no:3,table_index:0,header_rows:0},{page_no:4,table_index:0,header_rows:0}]);
});
test('invalid ranges, fractions and header budgets are rejected',()=>{
  for(const values of [[1,5,1,1,1],[4,2,1,1,1],[0,1,1,1,1],[1,2,33,1,1],[1,2,1,6,1],[1,2.5,1,1,1]])assert.throws(()=>selections(...values));
});
test('explicit continuation appends exact identities and ignores duplicate click',()=>{
  const candidate={status:'needs_confirmation',from_page:1,from_table:0,to_page:2,to_table:0};
  const result=appendLink([],candidate);assert.equal(result.length,1);assert.equal(appendLink(result,candidate),result);
  assert.throws(()=>appendLink(result,{...candidate,to_table:1}));
  assert.throws(()=>appendLink([],{...candidate,status:'rejected'}));
});
