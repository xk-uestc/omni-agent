const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const zlib=require('node:zlib');
const {summarize,render,markdown}=require('./agent-results.js');
const SHA='a'.repeat(64);
const source={citation_id:1,title:'原始资料',snippet:'实际原文',source_uri:'/api/v1/knowledge/documents/d/original',metadata:{document_id:'d',source_sha256:SHA,page_no:3,source_locator:'page:3'}};
function fusion(){return {status:'ok',route:'fusion',result:{status:'ok',answer:'计算值为 0。[1]',source_validation:{status:'verified',documents:{d:SHA}},citations:[source],
  execution_plan:[{id:'f',tool:'document_formula'},{id:'s',tool:'sql'},{id:'c',tool:'calculate',args:{formula:{ref:'f',path:[]}}}],edges:[{from:'f',to:'c'},{from:'s',to:'c'}],
  trace:[{task_id:'f',tool:'document_formula',status:'complete'},{task_id:'s',tool:'sql',status:'complete'},{task_id:'c',tool:'calculate',status:'complete',dependencies:['f','s']}],
  results:{f:{label:'业务计算',expression:'a*b'},s:{status:'ok',sql:'SELECT amount FROM fact',rows:[{amount:0}]},c:{status:'ok',expression:'a*b',value:0,result_unit:'CNY',unit_validation:'validated',parameter_semantics_validation:{status:'verified'},parameters:{a:{value:0,unit:'CNY',source_uri:'sql://receipt',locator:'rows/0/amount'},b:{value:3,unit:'ratio',source_uri:'sql://rate',locator:'rows/0/rate'}}}}}};}

test('completed terminal calculation preserves zero and returned units',()=>{
  const model=summarize(fusion());
  assert.equal(model.metrics.length,1);assert.deepEqual(model.metrics[0],{label:'业务计算',value:0,unit:'元',unitUnknown:false,kind:'calculate'});
  assert.deepEqual(model.calculations[0].parameters.map(p=>p.value),[0,3]);
  assert.equal(model.calculations[0].unitVerified,true);assert.equal(model.sources[0].versionVerified,true);
});
test('plans without execution receipts cannot create completed metrics or tool steps',()=>{
  const data={status:'clarification',route:'fusion',result:{status:'clarification',execution_plan:[{id:'x',tool:'calculate'}],results:{},trace:[]}};
  const model=summarize(data);assert.deepEqual(model.metrics,[]);assert.deepEqual(model.steps,[]);
  data.status='ok';data.result.status='ok';data.result.results={x:{value:34,expression:'a+b'}};
  assert.equal(summarize(data).metrics.length,0);
});
test('incomplete tasks retain intermediate evidence without presenting a final success',()=>{
  const data=fusion();data.status='incomplete';data.result.status='incomplete';data.result.error_code='parameter_binding_failed';
  const model=summarize(data);assert.equal(model.ok,false);assert.equal(model.metrics.length,0);assert.equal(model.sources.length,1);assert.equal(model.steps.length,3);
});
test('native cell preserves raw fractions and does not manufacture a unit',()=>{
  const data={status:'ok',route:'document',result:{status:'ok',answer:'5/6',answer_mode:'native_table_literal',table_fact:{value:5/6,raw_value:'5/6',unit:'unknown',matched_conditions:{等级:'甲类'}}}};
  const model=summarize(data);assert.equal(model.metrics[0].value,'5/6');assert.equal(model.metrics[0].unit,'');assert.equal(model.metrics[0].unitUnknown,true);
  assert.equal(model.metrics[0].label,'甲类');
});
test('source binding is distinguished from an explicit source version verification receipt',()=>{
  const data=fusion();delete data.result.source_validation;
  const model=summarize(data);assert.equal(model.sources[0].versionBound,true);assert.equal(model.sources[0].versionVerified,false);
  data.result.source_validation={status:'verified',documents:{d:'b'.repeat(64)}};
  assert.equal(summarize(data).sources[0].versionVerified,false);
});
test('duplicate evidence from normalized and raw payloads is deduplicated without mutating input',()=>{
  const data=fusion();data.document_evidence=[source];const before=structuredClone(data);
  assert.equal(summarize(data).sources.length,1);assert.deepEqual(data,before);
});
test('repeated citation content keeps every original reference number',()=>dom(()=>{
  const data=fusion();data.result.citations.push({...source,citation_id:2});data.result.answer='同一出处支持 [1] 和 [2]。';
  const model=summarize(data);assert.equal(model.sources.length,1);assert.deepEqual(model.sources[0].citationIds,[1,2]);
  const card=render(data);assert.equal(descendants(card,n=>n.className==='agent-citation-ref').length,2);
  assert.equal(descendants(card,n=>n.className==='agent-source-card').length,1);
}));
test('clarification, insufficient evidence, service failure and source change have distinct guidance',()=>{
  assert.equal(summarize({status:'clarification',result:{status:'clarification',clarification_code:'missing_time_range'}}).guidance.kind,'clarification');
  const evidence=summarize({status:'insufficient_evidence',result:{status:'insufficient_evidence',answer:'没有足够的文档证据。'}});
  assert.equal(evidence.guidance.kind,'evidence');assert.match(evidence.guidance.next,/上传/);assert.doesNotMatch(evidence.title,/确认|授权|审批/);
  const provider=summarize({status:'incomplete',failure_trace:[{code:'model_provider_failed',retryable:true}]});
  assert.equal(provider.guidance.kind,'service');assert.equal(provider.guidance.retryable,true);
  assert.equal(summarize({status:'incomplete',error_code:'evidence_revision_changed'}).guidance.kind,'source');
});
test('generic unclassified clarification with retrieval-only completeness is an evidence gap',()=>{
  const model=summarize({status:'clarification',result:{status:'clarification',answer_completeness:'retrieval_only',clarification_code:'unclassified_clarification'}});
  assert.equal(model.guidance.kind,'evidence');assert.doesNotMatch(model.title,/确认/);
});
test('nested failed result cannot inherit outer ok status',()=>{
  const data=fusion();data.result.status='failed';assert.equal(summarize(data).ok,false);assert.equal(summarize(data).metrics.length,0);
});
test('SQL rows and prose are not heuristically reduced to an invented scalar or unit',()=>{
  const model=summarize({status:'ok',route:'sql',result:{status:'ok',sql:'SELECT value FROM fact',columns:['value'],rows:[{value:10.26}]}});
  assert.equal(model.metrics.length,0);assert.equal(model.answer,'已查询到 1 行结果。');
});

// A tiny DOM checks rendered structure and text safely without adding a runtime
// dependency to this static frontend. It intentionally rejects innerHTML.
class Node{
  constructor(tag='#text',value=''){this.tagName=tag.toUpperCase();this.childNodes=[];this.attributes={};this._text=String(value);this.className='';}
  set textContent(value){this._text=String(value);this.childNodes=[];}get textContent(){return this._text+this.childNodes.map(n=>n.textContent).join('');}
  set innerHTML(_){throw Error('Unsafe HTML rendering');}
  append(...nodes){this.childNodes.push(...nodes.map(n=>typeof n==='string'?new Node('#text',n):n));}
  setAttribute(key,value){this.attributes[key]=String(value);}
  scrollIntoView(){this.scrolled=true;}focus(){this.focused=true;}
}
function dom(callback){const previous=global.document;global.document={createElement:tag=>new Node(tag),createTextNode:value=>new Node('#text',value)};try{return callback();}finally{global.document=previous;}}
function descendants(node,predicate){return [node,...node.childNodes.flatMap(child=>descendants(child,()=>true))].filter(predicate);}

test('safe Markdown renders basic structure but never executes raw HTML, remote images or links',()=>dom(()=>{
  const node=markdown('# 标题\n\n**重点**与`代码`\n\n- 项目一\n- 项目二\n\n<script>alert(1)</script>\n![remote](https://example.com/image)\n[点击](javascript:alert(1))');
  assert.equal(descendants(node,n=>n.tagName==='H4').length,1);assert.equal(descendants(node,n=>n.tagName==='STRONG').length,1);
  assert.equal(descendants(node,n=>n.tagName==='LI').length,2);assert.equal(descendants(node,n=>['SCRIPT','IMG','A'].includes(n.tagName)).length,0);
  assert.match(node.textContent,/<script>alert\(1\)<\/script>/);
}));
test('citation buttons are created only for actual returned citation ids and focus their source',()=>dom(()=>{
  const data=fusion();data.result.answer='原文 [1]，未知标记 [99]。';const card=render(data);
  const buttons=descendants(card,n=>n.className==='agent-citation-ref');assert.equal(buttons.length,1);assert.equal(buttons[0].textContent,'[1]');
  buttons[0].onclick();assert.equal(descendants(card,n=>n.className==='agent-source-card')[0].focused,true);
  assert.match(card.textContent,/\[99\]/);
}));
test('integration callbacks preserve original source evidence and only render successful top-level SQL tables',()=>dom(()=>{
  const calls=[];render(fusion(),{renderEvidence:(host,data)=>calls.push(data.document_evidence[0]),renderTable:()=>assert.fail('fusion intermediate table must remain collapsed')});
  assert.equal(calls.length,1);assert.equal(calls[0],source);
  let tables=0;render({status:'ok',route:'sql',result:{status:'ok',sql:'SELECT 1',rows:[{n:1}]}},{renderTable:()=>tables++});assert.equal(tables,1);
  render({status:'clarification',route:'sql',result:{status:'clarification',sql:null,rows:[]}},{renderTable:()=>tables++});assert.equal(tables,1);
}));
test('per-source render callback retains all evidence for multi-cell page overlays',()=>dom(()=>{
  const data=fusion();data.result.citations.push({...source,citation_id:2,snippet:'另一单元格',metadata:{...source.metadata,source_locator:'page:3/cell:2'}});
  const calls=[];render(data,{renderEvidence:(_,payload)=>calls.push(payload)});
  assert.equal(calls.length,2);assert.equal(calls[0].document_evidence.length,1);assert.equal(calls[0].document_evidence_context.length,2);
  assert.equal(calls[0].document_evidence_context[1],data.result.citations[1]);
}));
test('formula basis is collapsed by default and contains actual expression, inputs and returned zero',()=>dom(()=>{
  const card=render(fusion()),details=descendants(card,n=>n.tagName==='DETAILS');assert.ok(details.length);
  assert.ok(details.every(n=>!n.open));assert.match(card.textContent,/a\*b/);assert.match(card.textContent,/计算返回：0 元/);assert.match(card.textContent,/参数对应关系已核对/);
}));
test('clarification callbacks forward the returned option unchanged without fabricating a request',()=>dom(()=>{
  const option={label:'按季度',value:'quarter'},calls=[];
  const card=render({status:'clarification',question:'查询趋势',result:{status:'clarification',clarification_options:[option]}},{onClarify:event=>calls.push(event)});
  const button=descendants(card,n=>n.tagName==='BUTTON')[0];button.onclick();assert.equal(calls[0].option,option);assert.equal(calls[0].question,'查询趋势');
}));
test('saved live API evidence yields the real calculation and unknown-unit cell',()=>{
  const root=path.resolve(__dirname,'../../docs/foundation/local_run_20261012');
  const formulaPath=path.join(root,'model-formula-question.json'),nativePath=path.join(root,'http-acceptance.json.gz');
  if(!fs.existsSync(formulaPath)||!fs.existsSync(nativePath))return;
  const formula=summarize(JSON.parse(fs.readFileSync(formulaPath,'utf8')));
  assert.equal(formula.metrics[0].value,10);assert.equal(formula.calculations[0].parameters.length,2);assert.equal(formula.sources[0].versionVerified,true);
  const receipt=JSON.parse(zlib.gunzipSync(fs.readFileSync(nativePath))).records.find(row=>row.id==='native-table-query');
  const native=summarize(receipt.response);assert.equal(native.metrics[0].value,'10.26');assert.equal(native.metrics[0].unitUnknown,true);
});
test('scope conflicts offer an explicit scope-clear action without submitting a question',()=>dom(()=>{
  const calls=[];const card=render({status:'clarification',question:'继续之前的查询',result:{status:'clarification',clarification_code:'selected_source_reference_conflict'}},{onClarify:event=>calls.push(event)});
  assert.match(card.textContent,/当前文件与问题引用不一致/);
  const button=descendants(card,n=>n.tagName==='BUTTON')[0];assert.equal(button.textContent,'取消文件限定，继续提问');
  assert.equal(calls.length,0);button.onclick();assert.equal(calls[0].kind,'clear_scope');
}));
test('formula sources bind the exact original route when the tool omits document_id',()=>{
  const data={status:'ok',route:'fusion',result:{status:'ok',results:{f:{expression:'销售额/订单数',source_uri:'/api/v1/knowledge/documents/formula-1/original',sha256:SHA,locator:'body:paragraph:5'}},trace:[{task_id:'f',tool:'document_formula',status:'complete'}],source_validation:{status:'verified',documents:{'formula-1':SHA}}}};
  const model=summarize(data);assert.equal(model.sources.length,1);assert.equal(model.sources[0].documentId,'formula-1');assert.equal(model.sources[0].snippet,'销售额/订单数');assert.equal(model.sources[0].versionVerified,true);
  data.result.results.f.document_id='different';assert.equal(summarize(data).sources.length,0);
});
