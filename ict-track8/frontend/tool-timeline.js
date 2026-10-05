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
    for(const event of [...(data.trace||[]),...(data.result?.trace||[])])rows=reduceEvents(rows,event);
    const structured=data.structured||(['sql','comparison'].includes(data.route)?data.result:null)||{};
    const executed=structured.status==='ok'&&typeof structured.sql==='string'&&structured.sql.trim()
      &&structured.result_state!=='unexecuted'
      &&!['not_executed','unexecuted','rejected'].includes(structured.provenance?.execution_status);
    if(executed){
      if(!rows.some(row=>row.tool==='nl2sql'))rows=reduceEvents(rows,{id:'response:nl2sql',tool:'nl2sql',status:'success',
        summary:'已返回本次 SQL 与独立绑定参数。',input:{question:data.effective_question||data.question},
        output:{sql:structured.sql,parameters:structured.parameters||[]},executed:true});
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
  function invocationVerb(status){
    return {running:'正在调用',success:'已调用',error:'调用失败',attention:'调用待确认',stopped:'调用已停止',reported:'调用记录'}[status]||'调用记录';
  }
  function create(options={}){
    const React=window.React,ReactDOM=window.ReactDOM;
    if(!React||!ReactDOM)throw Error('工具时间线组件未加载');
    const h=React.createElement,root=document.createElement('div');root.className='agent-timeline';
    root.setAttribute('aria-label','工具调用与执行反馈');
    const mount=ReactDOM.createRoot(root);let rows=[],waiting=true,finished=false;
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
    function Code({value,language}){
      const source=typeof value==='string'?value:JSON.stringify(value,null,2);
      const rendered=source.slice(0,16000),tokens=window.LatticeSyntaxHighlight?.tokenize(rendered,language)||[{kind:'plain',text:rendered}];
      return h('pre',{className:'agent-tool-code','data-language':language,'data-query-visual':'true'},
        h('code',null,tokens.map((token,i)=>h('span',{key:i,className:`tok-${token.kind}`},token.text))),
        source.length>16000?h('span',{className:'agent-output-limit'},'\n… 已折叠超过 16,000 字符的展示内容'):null);
    }
    function Tool({item}){
      const [tab,setTab]=React.useState('output');
      const sql=item.input?.sql||item.output?.sql;
      const output=item.tool==='nl2sql'&&item.output?.sql?
        item.output.sql+'\n\n-- 参数\n'+JSON.stringify(item.output.parameters||[],null,2):item.output;
      return h('div',{className:'agent-tool-row','data-tool':item.tool,'data-status':item.status},
        item.summary?h('p',{className:'agent-action-note'},h(Icon,{name:'thought'}),h('span',null,item.summary)):null,
        h('details',{className:'agent-tool-details'},
          h('summary',{className:'agent-tool-summary'},h(Icon,{name:item.icon}),
            h('span',{className:'agent-tool-verb'},invocationVerb(item.status)),
            h('span',{className:'agent-tool-name'},item.title),
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
            item.status==='running'&&tab==='output'?h('p',{className:'agent-tool-wait'},'等待工具返回…'):
              h(Code,{value:tab==='input'?item.input:output,language:tab==='output'&&item.tool==='nl2sql'?'sql':'json'}),
            attachments.has(item.id)?h(Attachment,{node:attachments.get(item.id)}):null,
            h('footer',{className:`agent-tool-footer state-${item.status}`},
              Number.isFinite(item.latency_ms)?h('span',null,`${(item.latency_ms/1000).toFixed(2)} s`):h('span'),
              h('span',{className:'agent-tool-receipt'},item.status==='running'?h('i',{className:'agent-spinner'}):
                h(Icon,{name:item.status==='success'?'check':item.status==='error'?'close':'chevron'}),STATUS[item.status])))));
    }
    function Timeline(){return h(React.Fragment,null,
      rows.map(item=>item.kind==='tool'?h(Tool,{key:item.id,item}):h('p',{key:item.id,className:'agent-commentary',
        'data-status':item.status},h(Icon,{name:'thought'}),h('span',null,item.summary))),
      waiting?h('div',{className:'agent-request-wait',role:'status'},h('i',{className:'agent-spinner'}),'正在处理你的请求…'):null);
    }
    function draw(){mount.render(h(Timeline));}
    function update(event){if(finished)return;rows=reduceEvents(rows,event);waiting=!rows.some(item=>item.status==='running');draw();}
    function finish(data){waiting=false;for(const item of fromResult(data)){
      if(item.id==='response:nl2sql'&&rows.some(row=>row.tool==='nl2sql'))continue;
      if(item.id==='response:database.read'&&rows.some(row=>['sql','database.read','structured_query'].includes(row.tool)))continue;
      if(item.stage==='intent_planning'&&rows.some(row=>['intent.plan','context.resolve'].includes(row.tool)))continue;
      const index=rows.findIndex(row=>row.id===item.id);if(index<0)rows.push(item);else rows[index]={...rows[index],...item};
    }
      rows=rows.map(row=>row.status==='running'?{...row,status:'stopped',summary:row.summary||'未收到此工具的完成记录。'}:row);
      finished=true;draw();}
    function record(event){rows=reduceEvents(rows,event);draw();}
    function attach(tool,node){
      const item=rows.find(row=>row.tool===tool);
      if(!item)return false;
      attachments.set(item.id,node);draw();return true;
    }
    function fail(message='请求未完成'){waiting=false;finished=true;
      rows=rows.map(item=>item.status==='running'?{...item,status:'stopped'}:item);
      rows.push({id:'request:failed',kind:'commentary',status:'error',summary:message});draw();}
    draw();return {root,update,finish,record,attach,fail,dispose:()=>{mount.unmount();attachments.clear();}};
  }
  return {normalizeEvent,reduceEvents,fromResult,invocationVerb,create};
});
