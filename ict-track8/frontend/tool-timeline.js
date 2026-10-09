(function(root,factory){
  const api=factory();
  if(typeof module==='object'&&module.exports)module.exports=api;
  if(root)root.ToolTimeline=api;
})(typeof window!=='undefined'?window:globalThis,function(){
  'use strict';
  const TOOLS={
    'intent.plan':['理解问题','thought'], 'context.resolve':['核对对话上下文','thought'], 'knowledge.answer':['文档问答','file'],
    nl2sql:['NL2SQL · 生成 SQL','code'], sql:['查询数据库','database'], 'database.read':['查询数据库','database'],
    'database.schema':['查看数据库结构','database'], 'database.write':['修改数据库内容','edit'],
    search:['检索文档','search'], 'knowledge.search':['检索文档','search'], document_retrieval:['检索文档','search'],
    search_fact:['查找原文事实','search'], document_fact:['读取文档依据','file'], document_cell:['读取表格单元格','database'],
    document_formula:['读取原文公式','file'], calculate:['计算结果','calculator'], calculator:['计算结果','calculator'],
    compare:['比较结果','chart'], policy_select:['核对适用条款','file'],
    'visualization.build':['构建可视化','chart'], 'fusion.execute':['执行跨源任务','layers'],
    'document.table.read':['读取原页表格','database'], 'document.compare':['比较原页数据','calculator'],
    'document.amount.read':['读取原件金额','file'],
    'document.read':['读取原文','file'], 'document.answer':['整理文档答案','file'],
    'document.table.filter':['筛选原页字段','search'],
    structured_query:['查询数据库','database']
  };
  const NARRATION={intent_planning:'已识别本次问题，接下来按所选数据来源处理。',
    intent:'正在确认问题及已有对话中的条件。',pending_scope_edit:'正在确认修改后的查询条件。',
    relational_scope_edit:'核对跨表查询条件，并重新执行只读查询。',scope_edit:'核对修改后的查询条件。',
    pending_task_resume:'恢复已保存的问题与条件。',conversation_comparison:'使用已有查询结果进行比较。',
    pending_task_catalog:'读取当前会话中待补充的问题。',evidence_fusion:'根据查询结果和文档依据整理回答。'};
  function normalizedStatus(event){
    const value=String(event.status||'').toLowerCase();
    if(/^(skipped|skip|not_executed|unexecuted)$/.test(value))return 'skipped';
    if(/^(running|in_progress|started|processing)$/.test(value))return 'running';
    if(/^(error|failed|failure|blocked)$/.test(value)||event.error_code||event.error)return 'error';
    if(/^(clarification|incomplete|insufficient_evidence|degraded|partial|rejected|attention)$/.test(value))return 'attention';
    if(/^(cancelled|canceled|stopped)$/.test(value))return 'stopped';
    if(event.executed===false)return 'skipped';
    if(/^(success|complete|completed|ok|executed)$/.test(value))return 'success';
    return 'reported';
  }
  function normalizeEvent(event){
    if(!event||typeof event!=='object'||Array.isArray(event))return null;
    const tool=event.tool||(['structured_query','document_retrieval'].includes(event.stage)?event.stage:null);
    const stage=event.stage||tool||'event';
    let status=normalizedStatus(event);
    if(tool==='database.write'&&!(event.executed===true&&status==='success')){
      if(status==='success'||status==='reported')status='attention';
    }
    const label=TOOLS[tool]||[event.title||'工具调用','terminal'];
    const kind=tool&&!['context.resolve','query.complete'].includes(tool)?'tool':'commentary';
    let summary=event.summary||event.message||NARRATION[stage]||
      (kind==='tool'?'':event.source?'正在处理本次问题。':'');
    if(summary==='调用完成'||!summary){
      if(tool==='context.resolve')summary=status==='running'?'正在核对当前问题与已有对话中的条件。':'已核对本轮问题与对话上下文。';
      else if(tool==='intent.plan')summary=status==='running'?'正在选择本次问题需要的数据和工具。':'已选择本次问题需要的数据来源与工具。';
      else if(tool==='nl2sql')summary=status==='running'?'将业务问题转换为 SQL，并执行本次查询。':
        status==='success'?`已生成 SQL，查询返回 ${event.output?.row_count??'所请求的'} 行数据。`:'本次查询还需要补充条件。';
      else if(tool==='knowledge.answer')summary=status==='running'?'查找相关原文，核对来源后回答。':'已返回本次文档处理结果。';
      else if(tool==='knowledge.search')summary='检索本次问题相关的资料。';
    }
    if(!tool&&!summary)return null;
    return {id:String(event.call_id||event.id||event.task_id&&`${event.trace_id||'task'}:${event.task_id}`||`${kind}:${stage}`),
      tool:tool||null,stage,title:label[0],icon:label[1],kind,status,summary,
      input:event.input||event.args||{},output:event.output||event.result||
        (status!=='running'?event:{}),latency_ms:event.latency_ms,
      executed:event.executed,language:tool==='nl2sql'||tool==='sql'||tool==='database.read'?'sql':'json'};
  }
  function reduceEvents(rows,event){
    const item=normalizeEvent(event);if(!item||item.status==='skipped')return rows;
    const index=rows.findIndex(row=>row.id===item.id);
    if(index<0)return [...rows,item];
    const old=rows[index];
    // A late start cannot erase a final receipt. A final receipt may update a running row.
    if(old.status!=='running'&&item.status==='running')return rows;
    const copy=rows.slice();copy[index]={...old,...item,
      input:Object.keys(item.input||{}).length?item.input:old.input,
      summary:item.summary||old.summary};return copy;
  }
  function fromResult(data){
    let rows=[];
    if(data.routing?.label)rows=reduceEvents(rows,{id:'response:routing',stage:'routing',status:'reported',
      summary:`${data.routing.label} · ${data.routing.reason||''}`});
    if(data.result?.provenance?.execution_status==='reused_verified_result')rows=reduceEvents(rows,
      {id:'response:context-result',stage:'context_result',status:'reported',
       summary:'已从上一轮核验结果中读取所问数据。'});
    for(const event of [...(data.trace||[]),...(data.result?.trace||[])])rows=reduceEvents(rows,event);
    const structured=data.structured||(['sql','comparison'].includes(data.route)?data.result:null)||{};
    const executed=structured.status==='ok'&&typeof structured.sql==='string'&&structured.sql.trim()
      &&structured.result_state!=='unexecuted'
      &&!['not_executed','unexecuted','rejected'].includes(structured.provenance?.execution_status);
    if(executed){
      if(!rows.some(row=>row.tool==='nl2sql'))rows=reduceEvents(rows,{id:'response:nl2sql',tool:'nl2sql',status:'success',
        summary:'已返回本次 SQL 与独立绑定参数。',input:{question:data.effective_question||data.question},
        output:{sql:structured.sql,parameters:structured.parameters||[],plan:structured.plan||{},source_tables:structured.source_tables||{}},executed:true});
      if(!rows.some(row=>['sql','database.read','structured_query'].includes(row.tool)))rows=reduceEvents(rows,
        {id:'response:database.read',tool:'database.read',status:'success',executed:true,
         summary:`查询返回 ${(structured.rows||[]).length} 行预览数据。`,input:{sql:structured.sql,parameters:structured.parameters||[]},
         output:{columns:structured.columns||[],rows:structured.rows||[],result_state:structured.result_state||'preview',
           provenance:structured.provenance||{}}});
    }
    return rows;
  }
  const ICONS={
    terminal:'M4 4h16v16H4z M8 9l3 3-3 3 M13 15h3',
    database:'M4 6c0-4 16-4 16 0s-16 4-16 0v12c0 4 16 4 16 0V6 M4 12c0 4 16 4 16 0',
    code:'M8 6l-6 6 6 6 M16 6l6 6-6 6 M14 3l-4 18',
    search:'M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    file:'M5 3h9l5 5v13H5z M14 3v6h5 M8 13h8 M8 17h6',
    chart:'M4 3v18h17 M8 16v-4 M13 16V8 M18 16V5',
    edit:'M15 4l5 5 M4 20l4-1L21 6l-4-4L4 15z',
    calculator:'M5 2h14v20H5z M8 6h8 M8 11h1 M12 11h1 M16 11h1 M8 16h1 M12 16h1 M16 16h1',
    layers:'M12 2l10 6-10 6L2 8z M2 13l10 6 10-6 M2 18l10 6 10-6',
    thought:'M8 18h8 M9 21h6 M8 15a7 7 0 1 1 8 0v3H8z',
    check:'M5 12l4 4L19 6',close:'M6 6l12 12 M6 18L18 6',chevron:'M9 5l7 7-7 7'
  };
  const STATUS={running:'运行中',success:'完成',error:'失败',attention:'需要确认',stopped:'已停止',reported:'处理记录'};
  const RAG_STEPS=[
    ['question_analysis','问题解析'],
    ['context_resolution','多轮上下文与部件指代消解'],
    ['source_binding','知识库与资料范围绑定'],
    ['bm25_retrieval','BM25 稀疏召回'],
    ['dense_retrieval','Dense 向量召回'],
    ['hybrid_ranking','RRF 融合与 Rerank 重排'],
    ['evidence_selection','资料范围约束与关联证据裁剪'],
    ['generation_evidence','证据限额与模型输入'],
    ['answer_generation','答案生成与来源核验'],
    ['completion','处理完成']
  ];
  const RAG_STATUS={pending:'等待',running:'进行中',success:'完成',error:'失败',attention:'需补充',skipped:'未执行',stopped:'已停止'};
  const RAG_FIELDS={question:'原始问题',query:'检索问题',retrieval_query:'检索问题',query_rewrite:'查询改写',
    retrieval_context_attached:'附加检索上下文',
    mode:'处理方式',actual_question:'本轮问题',effective_question:'处理后问题',source_question:'引用的问题',
    context_turns:'对话轮数',document_id:'资料编号',page_no:'页码',scope:'范围',exact_identifiers:'精确标识',
    document_count:'资料数量',eligible_chunk_count:'可检索片段',candidate_limit:'候选上限',candidate_pool_limit:'候选池上限',
    selection_limit:'入选上限',candidate_count:'候选数',
    question_parts:'问题拆分导航词',part_candidate_count:'拆分候选数',part_selected_count:'拆分入选数',
    dense_status:'Dense 状态',embedding_model:'向量模型',rrf_executed:'执行 RRF',ranking_method:'排序方式',
    selected_count:'入选证据数',selection_method:'证据选择方式',evidence_count:'回答证据数',user_image_count:'用户图片数',
    image_count:'关联图片数',status:'结果状态',answer_mode:'回答方式',citation_count:'引用数量'};
  const RAG_TRACE_STAGES=new Set(['document_retrieval','evidence_selection','generation_evidence','grounded_generation']);
  function formatRagDuration(milliseconds){
    return milliseconds>=1000?`${(milliseconds/1000).toFixed(1)} s`:`${Math.round(milliseconds)} ms`;
  }
  function invocationVerb(status){
    return {running:'正在调用',success:'已调用',error:'调用失败',attention:'调用待确认',stopped:'调用已停止',reported:'调用记录'}[status]||'调用记录';
  }
  function invocationPreview(item){
    const input=item.input||{},output=item.output||{};
    const value=input.sql||output.sql||input.question||input.query||input.document_id||input.source||
      (item.tool==='visualization.build'&&Array.isArray(input.columns)?input.columns.join(' · '):'');
    return typeof value==='string'?value.replace(/\s+/g,' ').trim().slice(0,180):'';
  }
  function create(options={}){
    const React=window.React,ReactDOM=window.ReactDOM;
    if(!React||!ReactDOM)throw Error('工具时间线组件未加载');
    const h=React.createElement,root=document.createElement('div');root.className='agent-timeline';
    root.setAttribute('aria-label','工具调用与执行反馈');
    const mount=ReactDOM.createRoot(root);let rows=[],waiting=true,finished=false,finalData=null,ragStarted=false;
    const requestStartedAt=performance.now();
    const ragProgress=new Map(RAG_STEPS.map(([key])=>[key,{status:'pending',input:{},output:{}}]));
    const attachments=new Map();
    // Legacy SVG explorers retain their event handlers inside a React-owned tool receipt.
    function Attachment({node}){
      const host=React.useRef(null);
      React.useLayoutEffect(()=>{host.current.append(node);return ()=>node.remove();},[node]);
      return h('div',{ref:host,className:'agent-tool-attachment'});
    }
    function Icon({name,className=''}){return h('svg',{className:`agent-icon ${className}`,viewBox:'0 0 24 24',fill:'none',
      stroke:'currentColor',strokeWidth:1.6,strokeLinecap:'round',strokeLinejoin:'round','aria-hidden':true},
      h('path',{d:ICONS[name]||ICONS.terminal}));}
    const Code=React.memo(function Code({value,language}){
      const source=typeof value==='string'?value:JSON.stringify(value,null,2);
      const rendered=source.slice(0,16000),tokens=window.LatticeSyntaxHighlight?.tokenize(rendered,language)||[{kind:'plain',text:rendered}];
      return h('pre',{className:'agent-tool-code','data-language':language,'data-query-visual':'true'},
        h('code',null,tokens.map((token,i)=>h('span',{key:i,className:`tok-${token.kind}`},token.text))),
        source.length>16000?h('span',{className:'agent-output-limit'},'\n… 已折叠超过 16,000 字符的展示内容'):null);
    });
    const Tool=React.memo(function Tool({item,attachment}){
      const [tab,setTab]=React.useState('output');
      const [opened,setOpened]=React.useState(false);
      const sql=item.input?.sql||item.output?.sql;
      const preview=invocationPreview(item);
      const output=item.tool==='nl2sql'&&item.output?.sql?
        item.output.sql+'\n\n-- 参数\n'+JSON.stringify(item.output.parameters||[],null,2):item.output;
      const resultContext=finalData?.structured||finalData?.result||{};
      const plan=item.output?.plan||item.input?.plan||resultContext.plan||{};
      const sourceTables=item.output?.source_tables||item.input?.source_tables||resultContext.source_tables||{};
      const schema=options.getSchema?.()||options.schema||{};
      const displayOutput=item.tool==='nl2sql'&&item.output?.sql
        ? (window.QueryJourney?.formatSqlWithComments(item.output.sql,plan,schema,sourceTables)||item.output.sql)+'\n\n-- 参数\n'+JSON.stringify(item.output.parameters||[],null,2)
        :output;
      return h('div',{className:'agent-tool-row','data-tool':item.tool,'data-status':item.status},
        item.summary?h('p',{className:'agent-action-note'},h(Icon,{name:item.icon}),h('span',null,item.summary)):null,
        h('details',{className:'agent-tool-details',onToggle:event=>setOpened(event.currentTarget.open)},
          h('summary',{className:'agent-tool-summary'},h(Icon,{name:item.icon}),
            h('span',{className:'agent-tool-verb'},invocationVerb(item.status)),
            h('span',{className:'agent-tool-name'},item.title),
            preview?h('code',{className:'agent-tool-preview',title:preview},preview):null,
            h('span',{className:`agent-tool-state state-${item.status}`},item.status==='running'?h('i',{className:'agent-spinner'}):
              h(Icon,{name:item.status==='success'?'check':item.status==='error'?'close':'chevron'}),STATUS[item.status]),
            h(Icon,{name:'chevron',className:'agent-disclosure'})),
          h('div',{className:'agent-tool-panel'},
            h('div',{className:'agent-tool-toolbar'},h('span',{className:'agent-tool-language',title:item.tool},sql?'SQL':item.tool),
              h('div',{className:'agent-tool-tabs',role:'tablist','aria-label':'调用详情'},
                ['input','output'].map(value=>h('button',{key:value,type:'button',role:'tab','aria-selected':tab===value,
                  onClick:()=>setTab(value)},value==='input'?'输入':'反馈'))),
              h('button',{type:'button',className:'agent-tool-copy',onClick:async(event)=>{
                const button=event.currentTarget,value=tab==='input'?item.input:output;try{await navigator.clipboard.writeText(typeof value==='string'?value:JSON.stringify(value,null,2));
                  button.textContent='已复制';}catch{button.textContent='请选中复制';}}},'复制')),
              !opened?null:item.status==='running'&&tab==='output'?h('p',{className:'agent-tool-wait'},'等待工具返回…'):
              h(Code,{value:tab==='input'?item.input:displayOutput,language:tab==='output'&&item.tool==='nl2sql'?'sql':'json'}),
            attachment?h(Attachment,{node:attachment}):null,
            h('footer',{className:`agent-tool-footer state-${item.status}`},
              Number.isFinite(item.latency_ms)?h('span',null,`${(item.latency_ms/1000).toFixed(2)} s`):h('span'),
              h('span',{className:'agent-tool-receipt'},item.status==='running'?h('i',{className:'agent-spinner'}):
                h(Icon,{name:item.status==='success'?'check':item.status==='error'?'close':'chevron'}),STATUS[item.status])))));
    });
    function ragValue(value){
      if(value===null||value===undefined||value==='')return '—';
      if(typeof value==='boolean')return value?'是':'否';
      if(Array.isArray(value))return value.map(ragValue).join('、')||'无';
      if(typeof value==='object')return Object.entries(value).map(([key,item])=>`${RAG_FIELDS[key]||key}：${ragValue(item)}`).join('；');
      return String(value);
    }
    function RagFields({data}){
      if(!data||typeof data!=='object')return null;
      const source=Object.entries(data).filter(([key])=>!['selected_evidence','evidence'].includes(key));
      const evidence=data.selected_evidence||data.evidence||[];
      return h(React.Fragment,null,
        source.length?h('dl',{className:'agent-rag-fields'},source.map(([key,value])=>h(React.Fragment,{key},
          h('dt',null,RAG_FIELDS[key]||key),h('dd',null,ragValue(value))))):null,
        Array.isArray(evidence)&&evidence.length?h('ul',{className:'agent-rag-evidence'},evidence.map((item,index)=>{
          const locator=[item.source_locator,item.page_no?`第 ${item.page_no} 页`:null].filter(Boolean).join(' · ');
          return h('li',{key:item.chunk_id||index},h('strong',null,item.title||item.chunk_id||`证据 ${index+1}`),
            locator?h('small',null,locator):null,
            item.snippet?h('p',null,item.snippet):null,
            item.image_count?h('small',null,`关联图片 ${item.image_count} 张`):null);
        })):null);
    }
    function RagProcess(){
      const steps=RAG_STEPS.map(([key,title])=>({key,title,...ragProgress.get(key)}));
      const completed=steps.filter(step=>['success','error','attention','skipped','stopped'].includes(step.status)).length;
      const current=steps.find(step=>step.status==='running');
      const percent=Math.round(completed/steps.length*100);
      return h('section',{className:'agent-rag-process','aria-label':'RAG 可审计处理过程'},
        h('header',{className:'agent-rag-head'},h('div',null,
          h('strong',null,'知识库召回'),
          h('p',null,'问题解析、混合检索、证据筛选与答案生成。')),
          h('span',{className:'agent-rag-progress-label'},`${percent}% · ${completed}/${steps.length} · ${((performance.now()-requestStartedAt)/1000).toFixed(1)} s`)),
        h('div',{className:'agent-rag-progress','role':'progressbar','aria-valuemin':0,'aria-valuemax':100,'aria-valuenow':percent},
          h('i',{style:{width:`${percent}%`}})),
        current?h('p',{className:'agent-rag-current','role':'status'},current.summary||`正在${current.title}…`):null,
        h('ol',{className:'agent-rag-steps'},steps.map((step,index)=>{
          const status=step.status||'pending',open=status==='running'||(!current&&step.key==='generation_evidence'&&status==='success');
          const duration=Number.isFinite(step.latency_ms)?formatRagDuration(step.latency_ms):'';
          return h('li',{key:step.key,'data-state':status},
            h('span',{className:'agent-rag-marker','aria-hidden':true},status==='running'?h('i',{className:'agent-spinner'}):
              status==='success'?h(Icon,{name:'check'}):`${String(index+1).padStart(2,'0')}`),
            h('details',{open},h('summary',null,h('strong',null,step.title),
              h('span',{className:'agent-rag-step-state'},RAG_STATUS[status]||'等待'),
              duration?h('time',null,duration):null),
              step.summary?h('p',{className:'agent-rag-step-summary'},step.summary):null,
              Object.keys(step.input||{}).length?h('div',{className:'agent-rag-data'},
                h('b',null,'输入'),h(RagFields,{data:step.input})):null,
              Object.keys(step.output||{}).length?h('div',{className:'agent-rag-data'},
                h('b',null,'处理结果'),h(RagFields,{data:step.output})):null));
        })));
    }
    function Timeline(){return h(React.Fragment,null,
      ragStarted?h(RagProcess,{key:'rag-process'}):null,
      rows.filter(item=>!(ragStarted&&(item.tool==='knowledge.answer'||item.tool==='knowledge.search'||
        item.tool==='document_retrieval'||RAG_TRACE_STAGES.has(item.stage)))).map(item=>item.kind==='tool'?h(Tool,{key:item.id,item,attachment:attachments.get(item.id)}):h('p',{key:item.id,className:'agent-commentary',
        'data-status':item.status},h(Icon,{name:'thought'}),h('span',null,item.summary))),
      waiting?h('div',{className:'agent-request-wait',role:'status'},h('i',{className:'agent-spinner'}),'正在处理你的请求…'):null);
    }
    function draw(){mount.render(h(Timeline));}
    function update(event){if(finished)return;
      if(event&&typeof event.rag_step==='string'&&ragProgress.has(event.rag_step)){
        ragStarted=true;const old=ragProgress.get(event.rag_step),status=normalizedStatus(event);
        ragProgress.set(event.rag_step,{...old,status,summary:event.summary||old.summary||'',
          input:event.input&&Object.keys(event.input).length?event.input:old.input,
          output:event.output&&Object.keys(event.output).length?event.output:old.output,
          latency_ms:event.latency_ms,executed:event.executed});
        if(event.rag_step==='completion'&&status!=='running')for(const [key,value] of ragProgress){
          if(value.status==='pending')ragProgress.set(key,{...value,status:'skipped',summary:'本轮处理路径未执行此阶段。'});
        }
      }else rows=reduceEvents(rows,event);
      waiting=!rows.some(item=>item.status==='running')&&![...ragProgress.values()].some(item=>item.status==='running');draw();}
    function finish(data){waiting=false;finalData=data;for(const item of fromResult(data)){
      if(item.id==='response:nl2sql'&&rows.some(row=>row.tool==='nl2sql'))continue;
      if(item.id==='response:database.read'&&rows.some(row=>['sql','database.read','structured_query'].includes(row.tool)))continue;
      if(item.stage==='intent_planning'&&rows.some(row=>['intent.plan','context.resolve'].includes(row.tool)))continue;
      const index=rows.findIndex(row=>row.id===item.id);if(index<0)rows.push(item);else rows[index]={...rows[index],...item};
    }
      rows=rows.map(row=>row.status==='running'?{...row,status:'stopped',summary:row.summary||'未收到此工具的完成记录。'}:row);
      for(const [key,value] of ragProgress)if(value.status==='running'||value.status==='pending')ragProgress.set(key,{...value,
        status:value.status==='running'?'stopped':'skipped',summary:value.summary||'请求结束前未执行此阶段。'});
      finished=true;draw();}
    function record(event){rows=reduceEvents(rows,event);draw();}
    function attach(tool,node){
      const item=rows.find(row=>row.tool===tool);
      if(!item)return false;
      attachments.set(item.id,node);draw();return true;
    }
    function fail(message='请求未完成'){waiting=false;finished=true;
      rows=rows.map(item=>item.status==='running'?{...item,status:'stopped'}:item);
      for(const [key,value] of ragProgress)if(value.status==='running'||value.status==='pending')ragProgress.set(key,{...value,
        status:value.status==='running'?'error':'skipped',summary:value.summary||'请求未执行此阶段。'});
      rows.push({id:'request:failed',kind:'commentary',status:'error',summary:message});draw();}
    draw();return {root,update,finish,record,attach,fail,dispose:()=>{mount.unmount();attachments.clear();}};
  }
  return {normalizeEvent,reduceEvents,fromResult,invocationVerb,invocationPreview,create};
});
