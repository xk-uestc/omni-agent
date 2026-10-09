(function(root){
  'use strict';
  function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=String(text);return n;}
  async function request(options,path,body,signal){
    if(options.request)return options.request(path,body,signal);
    const response=await fetch(new URL(path,options.api||location.origin),{method:body?'POST':'GET',
      headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,signal});
    const value=await response.json();if(!response.ok)throw Error(typeof value.detail==='string'?value.detail:'来源读取失败。');return value;
  }
  function lazy(details,body,load){
    let controller=null,loaded=false;
    details.addEventListener('toggle',async()=>{
      if(!details.open){controller?.abort();controller=null;return;}
      if(loaded||controller)return;
      const current=new AbortController();controller=current;body.replaceChildren(el('p','source-status','正在核对来源并定位…'));
      try{const content=await load(current.signal);if(current.signal.aborted||!details.isConnected)return;body.replaceChildren(content);loaded=true;}
      catch(error){if(!current.signal.aborted)body.replaceChildren(el('p','source-error',error.message));}
      finally{if(controller===current)controller=null;}
    });
  }
  function locatorUrl(context){const id=crypto.randomUUID();sessionStorage.setItem('ict8-source-location:'+id,JSON.stringify(context));return new URL('./source-locator.html#id='+id,location.href).href;}
  function openLocatorPanel(title, meta, text, href, context){
    document.querySelector('.source-locator-panel')?.remove();
    const panel=el('aside','source-locator-panel');panel.setAttribute('role','dialog');panel.setAttribute('aria-label','来源定位');
    const head=el('div','source-locator-head'),close=el('button','source-locator-close','×');close.type='button';
    close.setAttribute('aria-label','关闭来源定位');close.onclick=()=>panel.remove();
    head.append(el('strong','',title),close);
    panel.append(head,el('p','source-location',meta),el('p','source-locator-label','定位到的原始片段'));
    const pre=el('pre','source-locator-text',text);panel.append(pre);
    if(context){const locate=el('button','source-locate',context.type==='document'?'在完整文档中定位':'在原始数据表中定位');locate.type='button';locate.onclick=()=>window.open(locatorUrl(context),'_blank');panel.append(locate);}
    if(href){const link=el('a','source-original','打开原文件');link.href=href;link.target='_blank';link.rel='noopener noreferrer';panel.append(link);}
    document.body.append(panel);close.focus();
  }
  function quote(text,quoted){
    const pre=el('pre','source-chunk-text');text=String(text||'');quoted=String(quoted||'');
    const chars=[],positions=[];for(let i=0;i<text.length;i++){
      for(const char of text[i].normalize('NFKC'))if(!/\s/.test(char)){chars.push(char);positions.push(i);}
    }
    const normalized=quoted.normalize('NFKC').replace(/\s/g,''),at=normalized?chars.join('').indexOf(normalized):-1;
    if(at<0){pre.textContent=text;return pre;}
    const start=positions[at],end=positions[at+normalized.length-1]+1;
    pre.append(document.createTextNode(text.slice(0,start)),el('mark','',text.slice(start,end)),document.createTextNode(text.slice(end)));return pre;
  }
  function loadVisible(body,load){
    body.replaceChildren(el('p','source-status','正在核对来源并读取原始数据…'));
    const controller=new AbortController();
    load(controller.signal).then(content=>{if(body.isConnected)body.replaceChildren(content);})
      .catch(error=>{if(body.isConnected)body.replaceChildren(el('p','source-error',error.message));});
  }
  function citation(hit,options={}){
    const m=hit.metadata||{},did=m.document_id,sha=m.source_sha256,cid=m.chunk_id;
    if(!did||!sha)return null;
    const details=el('details','source-evidence source-chunk-source');
    details.append(el('summary','',cid?`定位 RAG chunk · ${cid}`:`定位原文片段${m.page_no?' · 第 '+m.page_no+' 页':''}`));
    const body=el('div','source-evidence-body');details.append(body);
    lazy(details,body,async signal=>{
      const path='/api/v1/knowledge/documents/'+encodeURIComponent(did);
      const payload=cid?await request(options,path+'/chunks/'+encodeURIComponent(cid)+'?expected_source_sha256='+encodeURIComponent(sha),null,signal):
        await request(options,path+'/source-location',{expected_source_sha256:sha,quote:hit.snippet||'',...(m.page_no?{page_no:m.page_no}:{})},signal);
      if(payload.document_id!==did||payload.source_sha256!==sha)throw Error('资料版本与本次回答引用不一致。');
      const content=el('div');content.append(el('p','source-location',`资料：${hit.title||did} · ${did}`));
      const selected=new Set(payload.matched_chunk_ids||[payload.selected_chunk_id]);
      if(!payload.chunks?.length){
        content.append(el('p','source-status',`本条引用来自${m.page_no?'第 '+m.page_no+' 页的':'原文件的'}直接原文读取，未定位到对应入库 chunk。保留已引用的原文及位置。`),quote(hit.snippet,''));
      }
      for(const chunk of payload.chunks||[]){
        const active=selected.has(chunk.chunk_id),card=el('article','source-chunk'+(active?' is-selected':''));
        card.dataset.chunkId=chunk.chunk_id;card.append(el('b','',active?'回答引用的 chunk':'相邻上下文'));
        const position=[chunk.source_locator,chunk.page_no?'第 '+chunk.page_no+' 页':null,chunk.sheet_name,chunk.row_start?'行 '+chunk.row_start:null].filter(Boolean).join(' · ');
        const chunkText=String(chunk.text||'');
        card.append(el('p','source-location',position),el('code','source-id',chunk.chunk_id),quote(chunkText,active?hit.snippet:''));
        if(active){const locate=el('button','source-locate','查看定位片段');locate.type='button';locate.onclick=()=>openLocatorPanel(hit.title||did,`${position} · ${chunk.chunk_id}`,chunkText,new URL(path+'/original',options.api||location.origin).href);card.append(locate);}
        content.append(card);
      }
      const link=el('a','source-original','打开原文件');link.href=new URL(path+'/original',options.api||location.origin).href;link.target='_blank';link.rel='noopener noreferrer';content.append(link);
      return content;
    });
    const wrapper=el('div'),locateEntry=el('button','source-locate','查看片段并定位原文');locateEntry.type='button';
    locateEntry.onclick=()=>openLocatorPanel(hit.title||did,`${cid||'原文引用'}${m.page_no?' · 第 '+m.page_no+' 页':''}`,hit.snippet||'',new URL('/api/v1/knowledge/documents/'+encodeURIComponent(did)+'/original',options.api||location.origin).href,{type:'document',hit});
    wrapper.append(locateEntry,details);return wrapper;
  }
  function value(value){return value==null?'—':typeof value==='object'?JSON.stringify(value):String(value);}
  function compareValue(left,right){
    if(typeof left==='number'||typeof right==='number'){
      const a=Number(left),b=Number(right);return Number.isFinite(a)&&Number.isFinite(b)?(a===b?0:a<b?-1:1):null;
    }
    if(typeof left==='string'&&typeof right==='string')return left===right?0:left<right?-1:1;
    return left===right?0:null;
  }
  function matchesFilter(actual,filter){
    const expected=filter.value,op=String(filter.operator||'').toUpperCase();
    if(op==='RANGE'||op==='BETWEEN'){
      if(actual==null||!Array.isArray(expected)||expected.length!==2)return false;
      const low=compareValue(actual,expected[0]),high=compareValue(actual,expected[1]);
      return low!==null&&high!==null&&low>=0&&(op==='RANGE'?high<0:high<=0);
    }
    if(op==='IN'||op==='NOT IN'){
      if(actual==null||!Array.isArray(expected)||expected.some(item=>item==null))return false;
      const included=expected.some(item=>compareValue(actual,item)===0);
      return op==='IN'?included:!included;
    }
    if(op==='LIKE'){
      if(typeof actual!=='string'||typeof expected!=='string')return false;
      const asciiFold=text=>text.replace(/[A-Z]/g,char=>char.toLowerCase());
      const pattern=asciiFold(expected).split('').map(char=>char==='%'?'.*':char==='_'?'.':char.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('');
      return new RegExp('^'+pattern+'$').test(asciiFold(actual));
    }
    if(expected==null)return op==='='?actual==null:op==='!='&&actual!=null;
    if(actual==null)return false;
    const compared=compareValue(actual,expected);if(compared===null)return false;
    return op==='='?compared===0:op==='!='?compared!==0:op==='>'?compared>0:
      op==='>='?compared>=0:op==='<'?compared<0:op==='<='?compared<=0:false;
  }
  function metricSpecs(plan){return plan.metrics?.length?plan.metrics:[{table:plan.metric_table||plan.table,
    column:plan.metric_column,function:plan.metric_function,label:plan.metric_label,filters:[]}];}
  function isRowCountMetric(metric,plan,source,result){
    return metric.table===source.name&&String(metric.function||'').toUpperCase()==='COUNT'&&!plan.fan_out&&
      metricFeedsOutput(metric,plan,result);
  }
  function countIdentityColumns(result,source,schema){
    const plan=result.plan||{};
    if(!metricSpecs(plan).some(metric=>isRowCountMetric(metric,plan,source,result)))return [];
    return (schema?.columns||[]).filter(column=>column.primary_key&&typeof column.name==='string').map(column=>column.name);
  }
  function metricFeedsOutput(metric,plan,result){
    if((result.rows||[]).some(row=>Object.prototype.hasOwnProperty.call(row,metric.label)))return true;
    if(!metric.id)return false;
    const metrics=plan.metrics||[],derived=plan.derived_metrics||[];
    const required=new Set(plan.output_metrics?.length?plan.output_metrics:[...metrics,...derived].map(item=>item.id));
    const visit=(node,depth=0)=>{
      if(depth>8||!node||typeof node!=='object')return;
      if(Object.keys(node).length===1&&typeof node.ref==='string'){
        if(required.has(node.ref))return;
        required.add(node.ref);
        const dependency=derived.find(item=>item.id===node.ref);if(dependency)visit(dependency.expression,depth+1);
      }else{if(node.left)visit(node.left,depth+1);if(node.right)visit(node.right,depth+1);}
    };
    for(const item of derived)if(required.has(item.id))visit(item.expression);
    return required.has(metric.id);
  }
  function filterTable(filter,plan,metric){return filter.table||metric?.table||plan.table||plan.metric_table;}
  function canLocateRows(result,source){
    const plan=result.plan||{},tables=result.source_tables?.tables||[],metrics=metricSpecs(plan);
    if(tables.length!==1||tables[0].name!==source.name||plan.join_conditions?.length||plan.join_tables?.length||
        plan.fan_out||plan.comparison_mode&&plan.comparison_mode!=='none')return false;
    if((plan.filters||[]).some(filter=>filterTable(filter,plan)!==source.name))return false;
    if(metrics.some(metric=>metric.table!==source.name||(metric.filters||[]).some(filter=>filterTable(filter,plan,metric)!==source.name)))return false;
    return (plan.dimensions||[]).every(dimension=>(plan.dimension_tables?.[dimension]||plan.table||plan.metric_table)===source.name);
  }
  function nextMonthStart(value){
    const match=/^(\d{4})-(\d{2})$/.exec(String(value));if(!match)return null;
    const year=Number(match[1]),month=Number(match[2]);if(month<1||month>12)return null;
    return month===12?`${year+1}-01-01`:`${match[1]}-${String(month+1).padStart(2,'0')}-01`;
  }
  function sourceFilterCandidates(result,source){
    if(!canLocateRows(result,source))return [];
    const plan=result.plan||{},metrics=metricSpecs(plan),global=(plan.filters||[]).map(filter=>({...filter,table:filterTable(filter,plan)}));
    const dimensions=plan.dimensions||[],outputs=Array.isArray(result.rows)?result.rows:[];
    const groups=dimensions.length?outputs.slice(0,16).map(output=>{
      const filters=[];
      for(const dimension of dimensions){
        const label=plan.dimension_labels?.[dimension]||dimension;
        if(!Object.prototype.hasOwnProperty.call(output,label))return null;
        const value=output[label],transform=plan.dimension_transforms?.[dimension]||'raw';
        if(transform==='year'){
          const match=/^(\d{4})$/.exec(String(value));if(!match)return null;
          filters.push({table:source.name,column:dimension,operator:'RANGE',value:[`${match[1]}-01-01`,`${Number(match[1])+1}-01-01`]});
        }else if(transform==='month'){
          const next=nextMonthStart(value);if(!next)return null;
          filters.push({table:source.name,column:dimension,operator:'RANGE',value:[`${value}-01`,next]});
        }else filters.push({table:source.name,column:dimension,operator:'=',value});
      }
      return filters;
    }).filter(Boolean):[[]];
    const candidates=[];
    for(const group of groups)for(const metric of metrics.filter(item=>metricFeedsOutput(item,plan,result))){
      const filters=[...global,...(metric.filters||[]).map(filter=>({...filter,table:filterTable(filter,plan,metric)})),...group];
      const key=JSON.stringify(filters);if(!candidates.some(candidate=>JSON.stringify(candidate)===key))candidates.push(filters);
    }
    return candidates.slice(0,16);
  }
  async function locatePreviewOffset(options,result,source,manifest,signal){
    const candidates=sourceFilterCandidates(result,source);if(!candidates.length)return null;
    let first=null;
    for(const filters of candidates){
      const found=await request(options,'/api/v1/nl2sql/tables/'+encodeURIComponent(source.name)+'/locate',
        {expected_source_revision:manifest.source_revision,filters},signal);
      if(found.first_offset!=null){first=found.first_offset;break;}
    }
    return first;
  }
  function outputRowsForSourceRow(row,plan,result){
    const dimensions=plan.dimensions||[],outputs=Array.isArray(result.rows)?result.rows:[];
    if(!outputs.length)return [];
    if(!dimensions.length)return outputs;
    return outputs.filter(output=>dimensions.every(dimension=>{
      const label=plan.dimension_labels?.[dimension]||dimension;
      if(!Object.prototype.hasOwnProperty.call(output,label)||!Object.prototype.hasOwnProperty.call(row,dimension))return false;
      const transform=plan.dimension_transforms?.[dimension]||'raw',raw=row[dimension];let actual=raw;
      if(transform==='year')actual=typeof raw==='string'&&/^\d{4}-\d{2}-\d{2}/.test(raw)?raw.slice(0,4):null;
      else if(transform==='month')actual=typeof raw==='string'&&/^\d{4}-\d{2}-\d{2}/.test(raw)?raw.slice(0,7):null;
      return actual!=null&&compareValue(actual,output[label])===0;
    }));
  }
  function selectedRowFields(row,source,result,schema){
    const plan=result.plan||{};
    if(!canLocateRows(result,source))return new Set();
    const matches=(filters,metric)=>(filters||[]).every(filter=>
      filterTable(filter,plan,metric)===source.name&&Object.prototype.hasOwnProperty.call(row,filter.column)&&
      matchesFilter(row[filter.column],filter));
    if(!matches(plan.filters))return new Set();
    const outputRows=outputRowsForSourceRow(row,plan,result);if(!outputRows.length)return new Set();
    const metrics=metricSpecs(plan);
    const active=metrics.filter(metric=>metric.table===source.name&&metricFeedsOutput(metric,plan,result)&&matches(metric.filters,metric)&&
      (isRowCountMetric(metric,plan,source,result)||metric.column==='*'||row[metric.column]!=null));
    if(!active.length)return new Set();
    const selected=new Set((plan.filters||[]).map(filter=>filter.column));
    for(const metric of active){
      const contributes=outputRows.some(output=>{
        if(!['MIN','MAX'].includes(String(metric.function||'').toUpperCase()))return true;
        return Object.prototype.hasOwnProperty.call(output,metric.label)&&
          compareValue(row[metric.column],output[metric.label])===0;
      });
      if(contributes){
        if(isRowCountMetric(metric,plan,source,result))for(const column of countIdentityColumns(result,source,schema))selected.add(column);
        else if(metric.column!=='*')selected.add(metric.column);
      }
      for(const filter of metric.filters||[])selected.add(filter.column);
    }
    for(const dimension of plan.dimensions||[]){
      const dimensionTable=plan.dimension_tables?.[dimension]||plan.table;
      if(dimensionTable===source.name)selected.add(dimension);
    }
    const selectable=new Set([...(source.used_columns||[]),...countIdentityColumns(result,source,schema)]);
    return new Set([...selectable].filter(column=>selected.has(column)));
  }
  function sql(result,options={}){
    const manifest=result.source_tables;if(!manifest?.tables?.length)return null;
    const panel=el('section','source-evidence source-sql-source');panel.setAttribute('aria-label','回答的数据表来源');
    panel.append(el('h4','',`SQL 数据来源 · ${manifest.database}`));
    if(manifest.reused_result)panel.append(el('p','source-status','沿用上一轮已核验结果；下面展示原查询的数据表来源。'));
    for(const source of manifest.tables){
      const details=el('section','source-table-detail'),used=new Set(source.used_columns||[]);
      details.dataset.table=source.name;
      const locate=el('button','source-locate','查看数据来源并定位原表');locate.type='button';locate.onclick=()=>openLocatorPanel(source.name,manifest.database,JSON.stringify(result.plan?.filters||[],null,2),null,{type:'sql',result,source});details.append(locate);
      const body=el('div','source-evidence-body');details.append(body);
      loadVisible(body,async signal=>{
        if(!manifest.source_revision)throw Error('当前查询缺少来源版本，无法核对表预览。');
        const offset=await locatePreviewOffset(options,result,source,manifest,signal);
        const start=offset==null?0:Math.max(0,offset-2);
        const data=await request(options,'/api/v1/nl2sql/tables/'+encodeURIComponent(source.name)+'/preview?expected_source_revision='+manifest.source_revision+'&limit=10&offset='+start,null,signal);
        if(data.table!==source.name||data.source_revision!==manifest.source_revision)throw Error('数据表来源与本次查询不一致。');
        const content=el('div');
        if(offset!=null)content.append(el('p','source-status',`已定位到查询命中记录附近 · 原表第 ${offset+1} 条起 · 当前展示10条原始记录`));
        else if(canLocateRows(result,source))content.append(el('p','source-status','当前查询结果没有可定位的原始记录，未标记无关单元格。'));
        else content.append(el('p','source-status','当前查询包含跨表或跨期条件；原表字段照常展示，无法逐行核验的单元格不做高亮。'));
        const wrap=el('div','source-table-scroll'),table=el('table','source-data-table'),head=el('tr');
        const headerColumns=new Set([...(source.used_columns||[]),...countIdentityColumns(result,source,data.schema)]);
        data.columns.forEach(column=>{const cell=el('th',headerColumns.has(column)?'is-used-column':'',column);cell.scope='col';head.append(cell);});table.append(head);
        data.rows.forEach(row=>{const line=el('tr'),selectedFields=selectedRowFields(row,source,result,data.schema);line.tabIndex=0;line.title='点击查看这条原始记录及命中条件';line.onclick=()=>openLocatorPanel(source.name,`原始表记录 · ${source.name}`,JSON.stringify({row,matched_fields:[...selectedFields],filters:result.plan?.filters||[]},null,2));line.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();line.click();}};data.columns.forEach(column=>{
          const selectedCell=selectedFields.has(column);
          const cell=el('td',selectedCell?'is-filter-match':'',value(row[column]));
          if(selectedCell)cell.title='本单元格满足查询条件并参与结果计算';
          line.append(cell);
        });table.append(line);});wrap.append(table);content.append(wrap);return content;
      });panel.append(details);
    }
    const execution=el('section','source-query-detail');
    const formattedSql=window.QueryJourney?.formatSqlWithComments(manifest.sql,result.plan||{},options.schema||{},manifest)||manifest.sql;
    execution.append(el('h4','','来源 SQL 与参数'),el('pre','source-query-sql sqlbox',formattedSql),
      el('pre','source-query-parameters',JSON.stringify(manifest.parameters||[],null,2)));panel.append(execution);return panel;
  }
  document.addEventListener('keydown',event=>{if(event.key==='Escape')document.querySelector('.source-locator-panel')?.remove();});
  root.SourceEvidence={citation,sql,selectedRowFields,countIdentityColumns,canLocateRows,sourceFilterCandidates};
})(window);
