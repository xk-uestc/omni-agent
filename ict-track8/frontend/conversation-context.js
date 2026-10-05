(function(root){
  function displayValue(value){
    if(typeof value==='string'||typeof value==='number')return String(value);
    if(Array.isArray(value)&&value.every(item=>typeof item==='string'))return value.join('、')||'无分组';
    return '';
  }
  function changes(data){
    const audit=data.context_resolution||{};
    if(!/^(server_verified_|verified_)/.test(audit.mode||''))return [];
    const labels={time:'时间范围',metric:'统计指标',group_dimension:'分组方式',time_grain:'时间粒度',clarification_choice:'补充条件'};
    return (Array.isArray(audit.replacements)?audit.replacements:[]).flatMap(item=>{
      if(!item||typeof item.slot!=='string')return [];
      const before=item.operation==='add'&&item.from===''?'未设置':
        ['time','time_grain','clarification_choice'].includes(item.slot)&&item.from===''?'未指定':displayValue(item.from),
        after=item.operation==='remove'&&item.to===''?'已移除':displayValue(item.to);
      if(!before||!after||before===after)return [];
      return [{label:labels[item.slot]||'筛选条件',field:labels[item.slot]?'':item.slot,before,after}];
    });
  }
  function clarificationGuide(data){
    if(data.context_resolution?.mode==='executed_sql_edit_rejected'&&data.context_resolution?.context_preserved)return null;
    const structured=data.structured||{};
    if(data.status!=='clarification'&&structured.status!=='clarification')return null;
    const names={dialogue_source_unavailable:'查询或文档来源',dialogue_document_changed:'文档版本',dialogue_document_title_not_unique:'文档名称',dialogue_reference_not_offered:'具体来源',dialogue_reference_ambiguous:'具体来源',dialogue_reference_out_of_range:'具体来源',missing_time_range:'时间范围',missing_time_grain:'时间粒度',missing_metric:'统计指标',ambiguous_metric:'统计字段',ambiguous_dimension:'分组字段',missing_group_dimension:'分组方式',comparison_no_pairs:'分组对齐方式',comparison_edit_role_required:'修改对象'};
    const pending=structured.comparison_pending_target;
    const batch=structured.comparison_batch_progress;
    return {missing:names[structured.clarification_code]||'查询条件',
      message:structured.clarification||structured.plan?.clarification||'请选择下方选项，或输入具体条件。',
      next:data.context_resolution?.mode==='dialogue_reference_clarification'?'请确认要继续的查询或文档；选择后将重新核对来源。':batch?`已完成并暂存 ${batch.completed_count}/2 项查询；补充后继续双侧修改，完成前原比较不会更新。`:pending?'补充后重新执行所选查询，再继续比较；另一侧保持不变。':'补充后继续当前问题，已确认的条件会保留。',
      canExplain:!batch&&((data.route==='sql'&&!pending||data.route==='comparison'&&pending)&&['missing_time_range','missing_comparison_period','missing_comparison_scope','missing_time_grain','missing_metric','ambiguous_metric','ambiguous_dimension','ambiguous_value','missing_analysis_dimension'].includes(structured.clarification_code)
        ||data.route==='comparison'&&structured.clarification_code==='comparison_edit_role_required'),
      canCancel:!!(pending||batch||data.route==='comparison'&&structured.clarification_code==='comparison_edit_role_required')};
  }
  function describe(data){
    const audit=data.context_resolution||{};
    const effective=data.effective_question||data.structured?.rewritten_question||data.question||"";
    const inherited=/^(server_verified_|verified_)/.test(audit.mode||"");
    const pending=data.status==="clarification"||data.structured?.status==="clarification";
    return {effective,actual:audit.actual_question||data.question||'',changes:changes(data),base:audit.base_scope_question||audit.context_reference?.question||audit.source_reference?.question||"",
      label:pending&&audit.mode==='server_verified_pending_task_resume'?"已恢复待补条件":pending?"等待补充条件":data.route==="comparison"?"两次查询结果比较":inherited?"已沿用并核对上轮条件":"本轮查询条件",
      note:audit.mode==='server_verified_dialogue_source'?'已确认具体文档及原文版本，本次重新检索这份文档；不会复用上轮答案。':
        audit.mode==='server_verified_relational_parameter_edit'?'保留原查询的关联、分组、排序和统计表达式，仅修改指定筛选参数并重新取数。':
        audit.mode==='server_verified_pending_task_resume'?'恢复的是指定轮次的历史条件，原问题尚未执行；修改后的条件以最新一轮为准。':
        audit.mode==='executed_sql_edit_rejected'&&audit.context_preserved?'整条修改未执行，上一条成功查询的条件仍保留；可以纠正修改后继续。':
        audit.mode==='server_verified_pending_sql_edit'&&pending?'指定条件已修改，其余条件保留；补齐下方条件后再执行查询。':
        audit.mode==='pending_sql_clarification_retained'?'原问题和已确认条件已保留，本轮尚未执行查询；可以继续补充或选择独立提问。':
        audit.requires_clarification?"上下文暂不能确认，请补充完整条件。":
        data.structured?.comparison_batch_progress?`双侧修改：${data.structured.comparison_batch_progress.completed_count}/2 项已暂存，正在补充${data.structured.comparison_batch_progress.awaiting_role}；原比较尚未更新。`:
        data.structured?.comparison_pending_target?`正在补全${data.structured.comparison_pending_target.role}的条件；另一侧结果保持不变。`:
        data.route==="comparison"?"使用所引用查询的已保存结果；具体来源见比较依据。":
        audit.context_reference?"已返回你明确指定的 SQL 查询。":
        inherited?"以下完整问题是本轮实际采用的条件。":"上下文轮数不代表已沿用查询条件。"};
  }
  function clarificationQuestion(data,fallback){
    return data.effective_question||data.structured?.rewritten_question||fallback;
  }
  function composerState(data,independent=false){
    if(independent)return {title:'下一条独立提问',detail:'发送时不沿用之前的查询条件。',placeholder:'输入一个完整的新问题'};
    const guide=data&&clarificationGuide(data);
    if(guide){
      const options=data.structured?.clarification_options||[];
      const labels=options.filter(option=>option&&typeof option.label==='string'&&!timeOption(option)).slice(0,3).map(option=>option.label);
      return {title:`待补充：${guide.missing}`,detail:clarificationQuestion(data,'当前问题'),
        placeholder:guide.missing==='时间范围'?'例如：2025年，或2025年3月':
          labels.length?`可输入：${labels.join(' / ')}`:'选择回答中的选项，或输入具体条件'};
    }
    return {title:'可继续追问',detail:'具体沿用条件以回答中的完整问题为准。',placeholder:'可说「那华南呢」，或「时间改成2024年，地区改成华南」'};
  }
  function timeOption(option){return ["year","month","time_range"].includes(option.value);}
  function comparisonActions(data){
    if(data.route!=="comparison")return [];
    if(data.status==="clarification")return data.structured?.comparison_actions||[];
    if(data.status!=="ok")return [];
    const evidence=data.structured?.comparison_evidence;
    if(!evidence)return [];
    const target=evidence.baseline==="较晚查询"?"较早":"较晚";
    const actions=[{label:`以${target}查询为基准`,question:`把基准换成${target}结果`}];
    if(evidence.can_align_months&&evidence.alignment!=="month_of_year")actions.push({label:"按月份对齐",question:"按月份对齐"});
    return actions;
  }
  function comparisonEditPrompt(role){return ['基准值','比较值'].includes(role)?`把${role}改成`:'';}
  function comparisonReferenceLabel(source){
    const ref=source?.history_reference;
    if(/^q_[a-f0-9]{32}$/.test(ref?.turn_id||''))return `查询编号：${ref.turn_id}；仅引用当前会话保留的历史。`;
    return ref&&Number.isInteger(ref.history_index)&&ref.history_index>=0
      ?`选取时的历史窗口第 ${ref.history_index+1} 轮；不是整个会话的绝对编号。`:'';
  }
  function referencePrompt(identifier){return /^q_[a-f0-9]{32}$/.test(identifier||'')?`回到SQL查询编号${identifier}，`:'';}
  function queryReferencePrompt(data){
    const result=data.structured||{};
    if(data.route!=='sql'||data.status!=='ok'||result.status!=='ok'||typeof result.sql!=='string'||!result.sql.trim()
      ||result.result_state==='partial_rows')return '';
    return referencePrompt(data.query_reference_id);
  }
  function documentReferencePrompt(data){
    return data.route==='document'&&data.status==='ok'&&/^q_[a-f0-9]{32}$/.test(data.document_reference_id||'')
      ?`回到文档查询编号${data.document_reference_id}，`:'';
  }
  function pendingReferencePrompt(data){
    const result=data.structured||{};
    if(data.route!=='sql'||data.status!=='clarification'||result.status!=='clarification'
      ||!result.clarification_code||result.sql||!/^q_[a-f0-9]{32}$/.test(data.query_reference_id||''))return '';
    return `继续待补查询编号${data.query_reference_id}`;
  }
  function optionLabel(option,index){return `${index+1}. ${option.label||option.value}`;}
  function nextContext(previous,data){return data.route==='tasks'||data.context_resolution?.mode==='executed_sql_edit_rejected'&&data.context_resolution?.context_preserved?(data.context_reset?null:previous):data;}
  function catalogItems(data){
    if(data.route!=='tasks'||data.status!=='ok'||!Array.isArray(data.pending_tasks))return [];
    return data.pending_tasks.slice(0,64).filter(item=>item&&typeof item.question==='string').map(item=>({
      question:item.question,clarification:typeof item.clarification==='string'?item.clarification:'请确认完整查询条件。',
      expiresAt:typeof item.expires_at==='number'&&Number.isFinite(item.expires_at)&&item.expires_at>0&&item.expires_at<8.64e12?item.expires_at:null,
      resumeQuestion:item.available===true&&/^q_[a-f0-9]{32}$/.test(item.query_reference_id||'')?`继续待补查询编号${item.query_reference_id}`:''
    }));
  }
  const api={describe,changes,clarificationGuide,clarificationQuestion,composerState,timeOption,comparisonActions,comparisonEditPrompt,comparisonReferenceLabel,referencePrompt,queryReferencePrompt,documentReferencePrompt,pendingReferencePrompt,nextContext,catalogItems,optionLabel};
  if(typeof module!=="undefined"&&module.exports)module.exports=api;
  root.ConversationContext=api;
})(typeof window!=="undefined"?window:globalThis);
