const {test}=require('node:test');
test('rejected atomic edit keeps the successful composer without advertising a missing option',()=>{
  const {nextContext,clarificationGuide,describe,composerState,optionLabel}=require('./conversation-context.js');
  const previous={status:'ok',route:'sql',effective_question:'2025年华东销售额',structured:{status:'ok'}};
  const rejected={status:'clarification',route:'sql',context_resolution:{mode:'executed_sql_edit_rejected',context_preserved:true},structured:{status:'clarification'}};
  assert.equal(nextContext(previous,rejected),previous);
  assert.equal(clarificationGuide(rejected),null);
  assert.match(describe(rejected).note,/整条修改未执行/);
  assert.equal(composerState(nextContext(previous,rejected)).title,'可继续追问');
  assert.equal(optionLabel({label:'按年趋势',value:'yearly_trend'},1),'2. 按年趋势');
  assert.equal(nextContext(previous,{...rejected,context_reset:true}),null);
});
test('comparison help is available for a retained role or operand, never a generic comparison',()=>{
  const {clarificationGuide}=require('./conversation-context.js');
  const role={route:'comparison',status:'clarification',structured:{clarification_code:'comparison_edit_role_required'}};
  assert.equal(clarificationGuide(role).missing,'修改对象');
  assert.equal(clarificationGuide(role).canExplain,true);
  assert.equal(clarificationGuide(role).canCancel,true);
  const query={route:'comparison',status:'clarification',structured:{clarification_code:'missing_time_grain',comparison_pending_target:{role:'基准查询'}}};
  assert.equal(clarificationGuide(query).canExplain,true);
  query.structured.comparison_batch_progress={completed_count:1};
  assert.equal(clarificationGuide(query).canExplain,false);
  assert.equal(clarificationGuide({...role,structured:{clarification_code:'comparison_no_pairs'}}).canExplain,false);
});
test('task catalog keeps current composer context and reset clears it',()=>{
  const {nextContext}=require('./conversation-context.js');
  const previous={route:'sql',status:'clarification',effective_question:'2025年华东销售额趋势'};
  assert.equal(nextContext(previous,{route:'tasks'}),previous);
  assert.equal(nextContext(previous,{route:'tasks',context_reset:true}),null);
  const result={route:'sql',status:'ok'};
  assert.equal(nextContext(previous,result),result);
});
test('catalog recovery needs a usable stable reference; no ordinal or guessed identifier',()=>{
  const {catalogItems}=require('./conversation-context.js');
  const items=catalogItems({route:'tasks',status:'ok',pending_tasks:[
    {question:'A',available:true,query_reference_id:'q_'+'a'.repeat(32),expires_at:100},
    {question:'B',available:false,query_reference_id:'q_'+'b'.repeat(32)},
    {question:'C',available:true,query_reference_id:'2',expires_at:Infinity},null]});
  assert.equal(items.length,3);
  assert.equal(items[0].resumeQuestion,'继续待补查询编号q_'+'a'.repeat(32));
  assert.equal(items[1].resumeQuestion,'');assert.equal(items[2].resumeQuestion,'');
  assert.equal(items[2].expiresAt,null);
  assert.deepEqual(catalogItems({route:'sql',status:'ok',pending_tasks:[]}),[]);
});
test('predicate addition and removal display explicit changes; unverified empties stay hidden',()=>{
  const data={context_resolution:{mode:'server_verified_pending_sql_edit',replacements:[
    {slot:'sales_orders.channel',from:'',to:'线上',operation:'add'},
    {slot:'sales_orders.region',from:'华东',to:'',operation:'remove'},
    {slot:'sales_orders.channel',from:'线上',to:'线上',operation:'retain'},
    {slot:'sales_orders.region',from:'',to:'华东'}]}};
  assert.deepEqual(changes(data),[
    {label:'筛选条件',field:'sales_orders.channel',before:'未设置',after:'线上'},
    {label:'筛选条件',field:'sales_orders.region',before:'华东',after:'已移除'}]);
});
const assert=require('node:assert/strict');
const {describe,changes,clarificationGuide,clarificationQuestion,composerState,timeOption,comparisonActions,comparisonEditPrompt,comparisonReferenceLabel,referencePrompt}=require('./conversation-context.js');

test('history count never proves that filters were inherited',()=>{
  const state=describe({question:'华南销售额',context_turns:8,context_resolution:{mode:'independent'}});
  assert.equal(state.label,'本轮查询条件');
  assert.match(state.note,/不代表/);
});
test('show verified effective scope and explicit topic reference',()=>{
  const state=describe({effective_question:'2025年华南销售额',context_resolution:{
    mode:'server_verified_sql_followup',context_reference:{question:'2025年华东销售额'}}});
  assert.equal(state.effective,'2025年华南销售额');
  assert.equal(state.base,'2025年华东销售额');
  assert.match(state.note,/返回/);
});
test('unconfirmed context is visibly pending rather than a completed query',()=>{
  const state=describe({status:'clarification',context_resolution:{requires_clarification:true}});
  assert.equal(state.label,'等待补充条件');
  assert.match(state.note,/暂不能确认/);
});
test('a second clarification uses accumulated effective question, not clicked label',()=>{
  const state={effective_question:'销售额趋势 2025年',structured:{rewritten_question:'销售额趋势'}};
  assert.equal(clarificationQuestion(state,'年份'),'销售额趋势 2025年');
  assert.equal(clarificationQuestion({structured:{rewritten_question:'2025年华东销售额'}},'销售额'),'2025年华东销售额');
});
test('time selections have an editor; a physical field is an immediate choice',()=>{
  for(const value of ['year','month','time_range'])assert.equal(timeOption({value}),true);
  assert.equal(timeOption({value:'sales_orders.sales_amount'}),false);
});

test('comparison controls preserve the actual baseline and alignment',()=>{
  const data={route:'comparison',status:'ok',structured:{comparison_evidence:{baseline:'较晚查询',alignment:'month_of_year'}}};
  assert.deepEqual(comparisonActions(data),[{label:'以较早查询为基准',question:'把基准换成较早结果'}]);
  data.structured.comparison_evidence={baseline:'较早查询',alignment:'exact_group_keys',can_align_months:true};
  assert.equal(comparisonActions(data)[0].question,'把基准换成较晚结果');
  assert.equal(comparisonActions(data)[1].question,'按月份对齐');
  assert.deepEqual(comparisonActions({...data,status:'clarification'}),[]);
  assert.deepEqual(comparisonActions({route:'sql',status:'ok'}),[]);
  assert.equal(comparisonActions({route:'comparison',status:'clarification',structured:{comparison_actions:[{label:'按月份对齐后比较',question:'按月份对齐'}]}})[0].question,'按月份对齐');
});

test('comparison label describes saved evidence, not inherited SQL filters',()=>{
  const state=describe({route:'comparison',status:'ok'});
  assert.equal(state.label,'两次查询结果比较');
  assert.match(state.note,/已保存结果/);
});

test('edit prompts name the selected role rather than infer a chronological operand',()=>{
  assert.equal(comparisonEditPrompt('基准值'),'把基准值改成');
  assert.equal(comparisonEditPrompt('比较值'),'把比较值改成');
  assert.equal(comparisonEditPrompt('other'),'');
});

test('pending operand explains its role and keeps the accumulated question visible',()=>{
  const data={route:'comparison',status:'clarification',effective_question:'销售额趋势 2025年',
    structured:{comparison_pending_target:{role:'比较查询'}}};
  const state=describe(data);
  assert.equal(state.effective,'销售额趋势 2025年');
  assert.match(state.note,/补全比较查询/);
  assert.match(state.note,/另一侧结果保持不变/);
});

test('verified followup shows only reported condition changes, not guessed retained slots',()=>{
  const data={effective_question:'2025年华南销售额',context_resolution:{mode:'server_verified_sql_followup',actual_question:'那华南呢',
    base_scope_question:'2025年华东销售额',replacements:[{slot:'sales_orders.region',from:'华东',to:'华南'}]}};
  const state=describe(data);
  assert.equal(state.actual,'那华南呢');
  assert.deepEqual(state.changes,[{label:'筛选条件',field:'sales_orders.region',before:'华东',after:'华南'}]);
  assert.equal(state.effective,'2025年华南销售额');
  assert.deepEqual(changes({...data,context_resolution:{...data.context_resolution,mode:'independent'}}),[]);
});

test('group changes remain readable and malformed evidence does not fabricate a diff',()=>{
  const data={context_resolution:{mode:'server_verified_sql_followup',replacements:[
    {slot:'group_dimension',from:[],to:['region']},null,{slot:'time',from:{bad:true},to:'2025'},
    {slot:'metric',from:'销售额',to:'销售额'}]}};
  assert.deepEqual(changes(data),[{label:'分组方式',field:'',before:'无分组',after:'region'}]);
  assert.deepEqual(changes({context_resolution:{mode:'verified_scope',replacements:{}}}),[]);
});

test('clarification guide distinguishes missing time from missing grain and operand edits',()=>{
  const data={status:'clarification',structured:{clarification_code:'missing_time_range',plan:{clarification:'请指定年份'}}};
  assert.equal(clarificationGuide(data).missing,'时间范围');
  assert.equal(clarificationGuide(data).message,'请指定年份');
  assert.equal(clarificationGuide(data).canCancel,false);
  data.structured.clarification_code='missing_time_grain';
  data.structured.comparison_pending_target={role:'比较查询'};
  assert.equal(clarificationGuide(data).missing,'时间粒度');
  assert.equal(clarificationGuide(data).canCancel,true);
  assert.match(clarificationGuide(data).next,/另一侧保持不变/);
  assert.equal(clarificationGuide({status:'ok'}),null);
});

test('comparison source positions are labeled as the selection window, not absolute chat ordinals',()=>{
  assert.match(comparisonReferenceLabel({history_reference:{history_index:2}}),/历史窗口第 3 轮/);
  assert.match(comparisonReferenceLabel({history_reference:{history_index:2}}),/不是整个会话/);
  assert.equal(comparisonReferenceLabel({}), '');
  for(const history_index of [-1,NaN,'3'])assert.equal(comparisonReferenceLabel({history_reference:{history_index}}),'');
});

test('dual edit progress never claims that staged queries have already changed the published pair',()=>{
  const data={status:'clarification',route:'comparison',structured:{clarification_code:'comparison_no_pairs',
    comparison_batch_progress:{completed_count:2,awaiting_role:'分组对齐'}}};
  assert.match(describe(data).note,/2\/2 项已暂存/);
  assert.match(describe(data).note,/尚未更新/);
  assert.equal(clarificationGuide(data).missing,'分组对齐方式');
  assert.equal(clarificationGuide(data).canCancel,true);
  assert.match(clarificationGuide(data).next,/完成前原比较不会更新/);
});

test('composer keeps accumulated pending scope and gives the next missing condition',()=>{
  const data={status:'clarification',effective_question:'2025年华东销售额趋势',structured:{clarification_code:'missing_time_grain',clarification_options:[{value:'monthly_trend',label:'按月趋势'},{value:'yearly_trend',label:'按年趋势'}]}};
  assert.equal(composerState(data).detail,'2025年华东销售额趋势');
  assert.equal(composerState(data).title,'待补充：时间粒度');
  assert.match(composerState(data).placeholder,/按月/);
  assert.equal(composerState(data,true).title,'下一条独立提问');
  assert.doesNotMatch(composerState(data,true).detail,/华东/);
  assert.equal(composerState({...data,status:'ok',structured:{status:'ok'}}).title,'可继续追问');
});

test('composer suggests only actually offered labels and never invents quarterly support',()=>{
  const data={status:'clarification',structured:{clarification_code:'missing_time_grain',
    clarification_options:[{value:'monthly_trend',label:'按月趋势'}]}};
  assert.match(composerState(data).placeholder,/按月趋势/);
  assert.doesNotMatch(composerState(data).placeholder,/季度|按年/);
  assert.doesNotMatch(composerState({...data,structured:{clarification_code:'missing_time_grain'}}).placeholder,/按月|按年|季度/);
});

test('dimension clarification is described as a field choice, not generic missing conditions',()=>{
  assert.equal(clarificationGuide({status:'clarification',structured:{clarification_code:'ambiguous_dimension'}}).missing,'分组字段');
});

test('stable reference prompts use the full identifier and reject malformed input',()=>{
  const id='q_'+'a'.repeat(32);
  assert.equal(referencePrompt(id),`回到SQL查询编号${id}，`);
  assert.equal(referencePrompt('q_bad'), '');
  assert.match(comparisonReferenceLabel({history_reference:{turn_id:id,history_index:0}}),/查询编号/);
  assert.match(comparisonReferenceLabel({history_reference:{turn_id:id}}),/当前会话/);
});

test('retained clarification displays the recovery explanation without claiming execution',()=>{
  const data={status:'clarification',effective_question:'销售额趋势',
    context_resolution:{mode:'pending_sql_clarification_retained'},structured:{
      clarification_code:'missing_time_range',clarification:'月份应为1到12。原问题已保留。',
      plan:{clarification:'请选择年份'}}};
  assert.match(describe(data).note,/已保留/);
  assert.match(describe(data).note,/尚未执行/);
  assert.match(clarificationGuide(data).message,/1到12/);
  assert.equal(composerState(data).detail,'销售额趋势');
});

test('only successful executed SQL offers a history reference shortcut',()=>{
  const {queryReferencePrompt}=require('./conversation-context.js');
  const data={route:'sql',status:'ok',query_reference_id:'q_'+'a'.repeat(32),
    structured:{status:'ok',sql:'SELECT 1',result_state:'rows'}};
  assert.match(queryReferencePrompt(data),/回到SQL查询编号/);
  assert.equal(queryReferencePrompt({...data,status:'clarification'}),'');
  assert.equal(queryReferencePrompt({...data,structured:{status:'clarification',sql:null}}),'');
  assert.equal(queryReferencePrompt({...data,structured:{...data.structured,result_state:'partial_rows'}}),'');
  assert.equal(queryReferencePrompt({...data,route:'comparison'}),'');
  assert.equal(queryReferencePrompt({...data,structured:{...data.structured,sql:''}}),'');
});

test('option explanation is offered only for supported SQL clarification, not comparison or documents',()=>{
  const data={route:'sql',status:'clarification',structured:{clarification_code:'missing_time_grain'}};
  assert.equal(clarificationGuide(data).canExplain,true);
  assert.equal(clarificationGuide({...data,route:'comparison'}).canExplain,false);
  assert.equal(clarificationGuide({...data,route:'document'}).canExplain,false);
  assert.equal(clarificationGuide({...data,structured:{clarification_code:'unknown'}}).canExplain,false);
  assert.equal(clarificationGuide({...data,structured:{...data.structured,comparison_pending_target:{role:'基准值'}}}).canExplain,false);
});

test('pending edit shows confirmed changes while making clear no query was executed',()=>{
  const data={route:'sql',status:'clarification',effective_question:'2024年华南订单数趋势',structured:{clarification_code:'missing_time_grain'},
    context_resolution:{mode:'server_verified_pending_sql_edit',actual_question:'时间改成2024年，地区改成华南，指标改成订单数',
      base_scope_question:'2025年华东销售额趋势',replacements:[
        {slot:'time',from:'2025年',to:'2024年'},
        {slot:'sales_orders.region',from:'华东',to:'华南'},
        {slot:'metric',from:'销售额',to:'订单数'}]}};
  const state=describe(data);
  assert.equal(state.changes.length,3);
  assert.match(state.note,/补齐.*再执行/);
  assert.equal(state.effective,'2024年华南订单数趋势');
  assert.equal(composerState(data).title,'待补充：时间粒度');
  assert.match(describe({...data,status:'ok',structured:{status:'ok'}}).note,/实际采用/);
});

test('explicit added time is displayed as previously unspecified rather than silently hidden',()=>{
  const data={context_resolution:{mode:'server_verified_pending_sql_edit',replacements:[{slot:'time',from:'',to:'2024年'}]}};
  assert.deepEqual(changes(data),[{label:'时间范围',field:'',before:'未指定',after:'2024年'}]);
});

test('final offered grain is displayed together with atomic condition edits',()=>{
  const data={context_resolution:{mode:'server_verified_pending_sql_edit',replacements:[
    {slot:'time',from:'2025年',to:'2024年'},
    {slot:'time_grain',from:'',to:'按月趋势'}]}};
  assert.deepEqual(changes(data),[{label:'时间范围',field:'',before:'2025年',after:'2024年'},
    {label:'时间粒度',field:'',before:'未指定',after:'按月趋势'}]);
});

test('pending snapshot references are separate from successful SQL references',()=>{
  const {pendingReferencePrompt,queryReferencePrompt}=require('./conversation-context.js');
  const data={route:'sql',status:'clarification',query_reference_id:'q_'+'a'.repeat(32),
    structured:{status:'clarification',clarification_code:'missing_time_grain',sql:null}};
  assert.equal(pendingReferencePrompt(data),`继续待补查询编号${data.query_reference_id}`);
  assert.equal(queryReferencePrompt(data),'');
  assert.equal(pendingReferencePrompt({...data,route:'comparison'}),'');
  assert.equal(pendingReferencePrompt({...data,status:'ok',structured:{status:'ok',sql:'SELECT 1'}}),'');
  assert.equal(pendingReferencePrompt({...data,query_reference_id:'q_bad'}),'');
  assert.equal(pendingReferencePrompt({...data,structured:{...data.structured,sql:'SELECT 1'}}),'');
});

test('restored clarification describes a historical snapshot, not execution or the newest task state',()=>{
  const state=describe({route:'sql',status:'clarification',effective_question:'2025年华东销售额趋势',
    context_resolution:{mode:'server_verified_pending_task_resume'}});
  assert.equal(state.label,'已恢复待补条件');
  assert.match(state.note,/指定轮次的历史条件/);
  assert.match(state.note,/尚未执行/);
});

test('document references use their own successful identity and source-version explanation',()=>{
  const {documentReferencePrompt}=require('./conversation-context.js');
  const data={route:'document',status:'ok',document_reference_id:'q_'+'b'.repeat(32),
    context_resolution:{mode:'server_verified_dialogue_source',source_reference:{question:'保修期限是多少'}}};
  assert.equal(documentReferencePrompt(data),`回到文档查询编号${data.document_reference_id}，`);
  assert.equal(documentReferencePrompt({...data,status:'insufficient_evidence'}),'');
  assert.equal(documentReferencePrompt({...data,route:'sql'}),'');
  const presentation=describe(data);assert.match(presentation.note,/重新检索/);
  assert.equal(presentation.base,'保修期限是多少');
});

test('relational edit describes parameter replacement rather than regeneration of query structure',()=>{
  assert.match(describe({context_resolution:{mode:'server_verified_relational_parameter_edit'}}).note,/仅修改指定筛选参数/);
});

test('reference clarification asks for a source rather than advertising retained SQL conditions',()=>{
  const {clarificationGuide}=require('./conversation-context.js');
  const data={status:'clarification',context_resolution:{mode:'dialogue_reference_clarification'},
    structured:{status:'clarification',clarification_code:'dialogue_source_unavailable'}};
  const guide=clarificationGuide(data);assert.equal(guide.missing,'查询或文档来源');
  assert.match(guide.next,/重新核对来源/);assert.doesNotMatch(guide.next,/已确认的条件会保留/);
  assert.equal(guide.canExplain,false);
});

test('changed document reference is presented as a version issue rather than a missing field',()=>{
  const {clarificationGuide}=require('./conversation-context.js');
  assert.equal(clarificationGuide({status:'clarification',structured:{clarification_code:'dialogue_document_changed'}}).missing,'文档版本');
});
