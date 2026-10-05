(function(root){
  const KEY='ict8.conversation.sessions.v1',valid=id=>typeof id==='string'&&/^[A-Za-z0-9._:-]{1,128}$/.test(id);
  function safeStorage(){try{return root.sessionStorage;}catch{return null;}}
  function scopeFor(base,href){const url=new URL(base,href);return url.origin+url.pathname.replace(/\/+$/,'');}
  function create({storage=safeStorage(),scope,legacyKeys=[],makeId=()=>root.crypto.randomUUID(),clock=()=>Date.now(),maxSessions=64}){
    let memory={version:1,scopes:{}},persistent=!!storage;
    function read(){
      if(!persistent)return memory;
      try{
        const raw=storage?.getItem(KEY);if(!raw)return memory;
        const parsed=JSON.parse(raw);
        if(parsed.version!==1||!parsed.scopes||typeof parsed.scopes!=='object')return memory;
        const clean={version:1,scopes:{}};
        for(const [name,value] of Object.entries(parsed.scopes).slice(0,8)){
          if(!/^https?:\/\//.test(name)||!value||!Array.isArray(value.sessions))continue;
          const seen=new Set(),sessions=value.sessions.filter(item=>item&&valid(item.id)&&!seen.has(item.id)&&seen.add(item.id)).slice(0,maxSessions)
            .map(item=>({id:item.id,label:typeof item.label==='string'?item.label.slice(0,80):'已保存会话',createdAt:Number.isFinite(item.createdAt)?item.createdAt:null}));
          clean.scopes[name]={active:sessions.some(item=>item.id===value.active)?value.active:null,sessions};
        }memory=clean;return memory;
      }catch{persistent=false;return memory;}
    }
    function save(state){memory=state;try{if(storage){storage.setItem(KEY,JSON.stringify(state));persistent=true;}}catch{persistent=false;}}
    function currentScope(state){
      if(!state.scopes[scope]){
        if(Object.keys(state.scopes).length>=8)throw Error('服务会话列表已满，请使用现有服务会话。');
        state.scopes[scope]={active:null,sessions:[]};
      }return state.scopes[scope];
    }
    function append(current,label){
      if(current.sessions.length>=maxSessions)throw Error('已保存会话达到上限，请选择现有会话。');
      const id=makeId();if(!valid(id)||current.sessions.some(item=>item.id===id))throw Error('无法生成唯一会话标识。');
      const createdAt=clock();current.sessions.push({id,label:label||`新对话 ${current.sessions.length+1} · ${new Date(createdAt).toLocaleString()}`,createdAt});current.active=id;return id;
    }
    const initial=read(),current=currentScope(initial);
    for(const {key,label} of legacyKeys){
      let id;try{id=storage?.getItem(key);}catch{persistent=false;}
      if(valid(id)&&!current.sessions.some(item=>item.id===id)&&current.sessions.length<maxSessions){
        current.sessions.push({id,label,createdAt:null});if(!current.active)current.active=id;
      }
    }
    if(!current.active)append(current,'当前对话');save(initial);
    function loadCurrent(){const state=read(),current=currentScope(state);if(!current.active){append(current,'当前对话');save(state);}return current;}
    return {
      current(){return loadCurrent().active;},
      list(){return loadCurrent().sessions.map(item=>({...item}));},
      start(){const state=read(),id=append(currentScope(state));save(state);return id;},
      activate(id){const state=read(),current=currentScope(state);if(!current.sessions.some(item=>item.id===id))throw Error('该会话不属于当前服务。');current.active=id;save(state);return id;},
      get persistent(){return persistent;}
    };
  }
  function mount(controller,host,onSelect){
    host.classList.add('conversation-session-picker');
    const label=document.createElement('label'),select=document.createElement('select'),note=document.createElement('small');
    label.textContent='当前会话';select.setAttribute('aria-label','问数与文档共用的当前会话');
    select.style.maxWidth='100%';select.style.width='100%';label.append(select);host.append(label,note);
    function refresh(){
      select.replaceChildren();for(const item of controller.list()){
        const option=document.createElement('option');option.value=item.id;option.textContent=item.label;select.append(option);
      }select.value=controller.current();note.textContent=controller.persistent?'问数与文档共用当前会话；切换保留旧记录。':'浏览器存储不可用，会话仅在当前页面保留。';note.style.display='block';
    }
    select.addEventListener('change',()=>{try{onSelect(controller.activate(select.value));refresh();}catch(error){note.textContent=error.message;}});
    refresh();return {refresh};
  }
  const api={create,mount,safeStorage,scopeFor};if(typeof module!=='undefined'&&module.exports)module.exports=api;root.ConversationSessions=api;
})(typeof window!=='undefined'?window:globalThis);
