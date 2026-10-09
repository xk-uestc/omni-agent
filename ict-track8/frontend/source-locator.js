(async function(){
  const p=new URLSearchParams(location.hash.slice(1)),id=p.get('id'),stored=sessionStorage.getItem('ict8-source-location:'+id);
  const $=id=>document.getElementById(id),el=(tag,text)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;return n;};
  async function request(path,body){const response=await fetch(new URL(path,location.origin),{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'来源核验失败');return data;}
  function marked(text,quote){const pre=el('pre'),chars=[],positions=[];for(let i=0;i<text.length;i++)for(const c of text[i].normalize('NFKC'))if(!/[\s,，。.!！?？;；:：]/.test(c)){chars.push(c);positions.push(i);}const q=String(quote||'').normalize('NFKC').replace(/[\s,，。.!！?？;；:：]/g,''),at=q?chars.join('').indexOf(q):-1;if(at<0){pre.textContent=text;return pre;}const a=positions[at],b=positions[at+q.length-1]+1;pre.append(document.createTextNode(text.slice(0,a)),el('mark',text.slice(a,b)),document.createTextNode(text.slice(b)));return pre;}
  try{
    if(!stored)throw Error('定位信息已过期，请从回答中的来源重新打开。');
    const context=JSON.parse(stored);
    if(context.type==='document'){
      const {hit}=context,m=hit.metadata,path='/api/v1/knowledge/documents/'+encodeURIComponent(m.document_id);
      const doc=await request(path);if(doc.sha256!==m.source_sha256)throw Error('资料版本已变化，请重新查询。');
      let selected=new Set([m.chunk_id]);if(!m.chunk_id){const match=await request(path+'/source-location',{expected_source_sha256:m.source_sha256,quote:hit.snippet,...(m.page_no?{page_no:m.page_no}:{})});selected=new Set(match.matched_chunk_ids);}
      $('title').textContent=doc.title||hit.title;$('status').textContent='完整文档内容 · 自动定位本条引用；黄色为引用原文，绿色边框为对应片段。';
      const original=el('a','打开原文件');original.href=path+'/original';original.target='_blank';$('toolbar').append(original);
      let target=null;
      for(const chunk of doc.chunks||[]){const card=el('article'),active=selected.has(chunk.chunk_id);card.id='chunk-'+chunk.chunk_id;if(active){card.className='is-selected';target=target||card;}
        const label=[chunk.section,chunk.source_locator,chunk.page_no?'第 '+chunk.page_no+' 页':null].filter(Boolean).join(' · ')||chunk.chunk_id;
        card.append(el('h3',label),el('small',chunk.chunk_id),marked(String(chunk.text||''),active?hit.snippet:''));$('content').append(card);
        const button=el('button',(active?'● ':'')+label);button.onclick=()=>card.scrollIntoView({block:'center'});$('catalog').append(button);
      }
      if(!target)$('status').textContent='已显示完整文档；本条引用没有可核验的 chunk 对应位置，请打开原文件核对。';else target.scrollIntoView({block:'center'});
    }else{
      const {result,source}=context,manifest=result.source_tables;let offset=0;
      $('title').textContent=manifest.database+' / '+source.name;
      $('status').textContent='完整原表分页浏览 · 仅高亮满足查询条件并参与计算的具体单元格。';
      const script=el('script');script.src='./source-evidence.js?v=20261009-precise-source-cells-2';await new Promise((resolve,reject)=>{script.onload=resolve;script.onerror=reject;document.head.append(script);});
      const base='/api/v1/nl2sql/tables/'+encodeURIComponent(source.name),filters=result.plan?.filters||[];
      const candidates=window.SourceEvidence.sourceFilterCandidates(result,source);let firstOffset=null;
      for(const candidate of candidates){const located=await request(base+'/locate',{expected_source_revision:manifest.source_revision,filters:candidate});
        if(located.first_offset!=null){firstOffset=located.first_offset;break;}
      }
      if(firstOffset!=null)offset=Math.floor(firstOffset/50)*50;
      else if(candidates.length)$('status').textContent='当前查询分组没有可定位的原始记录；未猜测高亮位置。';
      else if(window.SourceEvidence.canLocateRows(result,source))$('status').textContent='查询结果没有可定位的原始记录；未猜测高亮位置。';
      else $('status').textContent='跨表或跨期来源无法由单表行精确证明；原表仍可分页浏览，未猜测高亮位置。';
      async function show(){const data=await request(base+'/preview?expected_source_revision='+manifest.source_revision+'&limit=50&offset='+offset);
        $('content').replaceChildren();const section=el('div'),wrap=el('div');wrap.className='source-table-scroll';const table=el('table');table.className='source-data-table';const head=el('tr');head.append(el('th','原表序号'));const usedColumns=new Set([...(source.used_columns||[]),...window.SourceEvidence.countIdentityColumns(result,source,data.schema)]);data.columns.forEach(c=>{const th=el('th',usedColumns.has(c)?'is-used-column':'',c);head.append(th);});table.append(head);
        let first=null;data.rows.forEach((row,i)=>{const tr=el('tr'),fields=window.SourceEvidence.selectedRowFields(row,source,result,data.schema);tr.append(el('td',offset+i+1));data.columns.forEach(c=>{const td=el('td',fields.has(c)?'is-filter-match':'',row[c]==null?'—':String(row[c]));if(fields.has(c)){td.title='本单元格满足查询条件并参与结果计算';first=first||tr;}tr.append(td);});table.append(tr);});wrap.append(table);section.append(wrap);$('content').append(section);first?.scrollIntoView({block:'center'});
        $('catalog').replaceChildren(el('p','本次查询条件'),el('pre',JSON.stringify(filters,null,2)),el('p',`原表第 ${offset+1}–${offset+data.rows.length} 条`));
        $('toolbar').replaceChildren();const prev=el('button','上一页'),next=el('button','下一页');prev.disabled=offset===0;next.disabled=data.rows.length<50;prev.onclick=()=>{offset=Math.max(0,offset-50);show().catch(fail);};next.onclick=()=>{offset+=50;show().catch(fail);};$('toolbar').append(prev,next);const sql=el('article');sql.append(el('h3','本次来源 SQL'),el('pre',manifest.sql),el('pre',JSON.stringify(manifest.parameters)));$('content').append(sql);
      }
      function fail(error){$('status').textContent=error.message;}
      await show();
    }
  }catch(error){$('status').textContent=error.message;}
})();
