import {allModels,chatModel,chatModels} from './catalog.js?v=20261026-models';
export function enterSends(event){return event.key==='Enter'&&!event.shiftKey&&!event.ctrlKey&&!event.altKey&&!event.metaKey&&!event.isComposing&&event.keyCode!==229&&!event.repeat;}
export function installComposerControls({form,message,select,refresh,status,session,api,words,storage=localStorage}){
 let rows=[],all=[],allChat=[],allVisible=[],favSet=new Set(),enabledSet=new Set(),selected='',owner='',pending=null,busy=false,generation=0;
 let kind='chat',query='';
 const key=()=> 'nl.web.model.'+owner;
 const paint=()=>{select.disabled=busy||!rows.length;refresh.disabled=busy||!!pending;};
 // ── выбор модели: поиск и категории ───────────────────────────────
 const pickerStyle=document.createElement('style');pickerStyle.textContent=`
.mp-wrap{position:relative;display:flex;align-items:center;min-width:0}
.mp-trigger{appearance:none;border:1px solid var(--line,#d8d1c1);background:var(--surface,#fffdf8);color:var(--ink,#38372f);border-radius:8px;padding:7px 30px 7px 10px;font:inherit;font-size:13px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;text-align:left;position:relative}
.mp-trigger::after{content:"▾";position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:11px;opacity:.6}
.mp-panel{position:fixed;inset:auto 0 0 0;margin:0 auto;width:min(560px,100vw);max-height:min(72vh,560px);overflow:auto;background:var(--surface,#fffdf8);color:var(--ink,#38372f);border:0;border-top:1px solid var(--line,#d8d1c1);border-radius:16px 16px 0 0;box-shadow:0 -18px 40px rgba(40,35,20,.18);padding:12px 12px calc(12px + env(safe-area-inset-bottom))}
.mp-panel::backdrop{background:rgba(40,35,20,.38)}
.mp-panel[open]{display:block}
.mp-panel[hidden]{display:none}
.mp-search{width:100%;box-sizing:border-box;margin:0 0 6px;padding:8px 10px;border:1px solid var(--line,#d8d1c1);border-radius:8px;background:var(--paper,#f7f1e6);color:var(--ink,#38372f);font:inherit;font-size:13px}
.mp-cat{margin:10px 10px 4px;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted,#66675b)}
.mp-empty{margin:0 10px 6px;font-size:12px;color:var(--muted,#66675b)}
.mp-list{display:block}
.mp-row{display:flex;align-items:center;gap:2px;width:100%}
.mp-panel .mp-item{display:flex;align-items:center;flex:1 1 auto;min-width:0;width:auto;text-align:left;border:0;background:none;color:var(--ink,#38372f);border-radius:7px;padding:7px 8px;font:inherit;font-size:13px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mp-panel .mp-item:hover{background:var(--fill,#e7eadc)}
.mp-panel .mp-item.on{background:var(--fill,#e7eadc);font-weight:600}
.mp-panel .mp-item:disabled{color:var(--muted,#66675b);opacity:.55;cursor:default}
.mp-star{flex:0 0 30px;width:30px;height:30px;display:grid;place-items:center;border:0;background:none;color:var(--action,#52694e); border-radius:7px;font-size:15px;line-height:1;cursor:pointer}
.mp-star:hover{background:var(--fill,#e7eadc)}
`;document.head.append(pickerStyle);
 const wrap=document.createElement('div');wrap.className='mp-wrap';
 select.parentNode.insertBefore(wrap,select);wrap.append(select);select.hidden=true;
 const trigger=document.createElement('button');trigger.type='button';trigger.className='mp-trigger';trigger.setAttribute('aria-haspopup','listbox');trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-label','Выбрать модель');
 const panel=document.createElement('dialog');panel.className='mp-panel';
 const search=document.createElement('input');search.type='search';search.className='mp-search';search.placeholder='Поиск модели';search.setAttribute('aria-label','Поиск модели');
 const list=document.createElement('div');list.className='mp-list';
 panel.append(search,list);document.body.append(panel);wrap.append(trigger);
 const CATS=[['chat','Текст'],['image','Картинки'],['video','Видео'],['audio','Аудио']];
 const catOf=m=>m.kind==='chat'?'chat':m.kind==='image'?'image':m.kind==='video'?'video':m.kind==='audio'?'audio':'other';
 const pickerModels=()=>{const chosen=allVisible.filter(m=>favSet.has(m.id)||enabledSet.has(m.id));return chosen.length?chosen:allChat;};
 function paintTrigger(){const m=allVisible.find(x=>x.id===selected)||allChat.find(x=>x.id===selected);trigger.textContent=m?m.name:'Модель';}
 function renderPicker(){const q=search.value.trim().toLowerCase();list.replaceChildren();
  const base=pickerModels();
  for(const [kind,label] of CATS){const items=base.filter(m=>catOf(m)===kind&&(!q||m.name.toLowerCase().includes(q)||m.id.toLowerCase().includes(q)));
   if(!items.length){if(!q){const h=document.createElement('p');h.className='mp-cat';h.textContent=label;const e=document.createElement('p');e.className='mp-empty';e.textContent='нет избранных';list.append(h,e);}continue;}
   const h=document.createElement('p');h.className='mp-cat';h.textContent=label;list.append(h);
   for(const m of items){
    const row=document.createElement('div');row.className='mp-row';
    const b=document.createElement('button');b.type='button';b.className='mp-item'+(m.id===selected?' on':'');b.textContent=m.name;b.disabled=!m.forChat;if(!m.forChat)b.title='Только для картинок, видео или аудио';b.onclick=()=>{if(!m.forChat)return;selected=m.id;select.value=m.id;select.dispatchEvent(new Event('change'));paintTrigger();closePicker();};
    const star=document.createElement('button');star.type='button';star.className='mp-star';star.textContent=favSet.has(m.id)?'★':'☆';star.title=favSet.has(m.id)?'Убрать из избранного':'В избранное';star.setAttribute('aria-label',star.title+': '+m.name);
    star.onclick=async(event)=>{event.stopPropagation();if(favSet.has(m.id))favSet.delete(m.id);else favSet.add(m.id);const ownerId=session()?.owner||'';try{localStorage.setItem('nl.web.favourites.'+ownerId,JSON.stringify([...favSet]));}catch{}renderPicker();const pref=allChat.filter(x=>favSet.has(x.id)||enabledSet.has(x.id));all=pref.length?pref:allChat;applyFilters();try{await api('/v1/me/models',{method:'PUT',body:JSON.stringify({enabled:{},favourites:[...favSet]})});}catch{}};
    row.append(b,star);list.append(row);
   }}}
 function openPicker(){search.value='';renderPicker();trigger.setAttribute('aria-expanded','true');panel.showModal();search.focus();}
 function closePicker(){trigger.setAttribute('aria-expanded','false');if(panel.open)panel.close();}
 trigger.onclick=()=>panel.open?closePicker():openPicker();
 search.oninput=renderPicker;
 panel.addEventListener('click',e=>{if(e.target===panel)closePicker();});
 panel.addEventListener('cancel',e=>{e.preventDefault();closePicker();});

 /** Список под фильтры: по типу (chat/image/video/audio) и по куску названия. */
 const applyFilters=()=>{
  const q=query.trim().toLowerCase();
  rows=all.filter(m=>(kind==='all'||m.kind===kind)&&(!q||m.name.toLowerCase().includes(q)||m.id.toLowerCase().includes(q)));
  if(selected&&!rows.some(m=>m.id===selected)&&!q){const current=allChat.find(m=>m.id===selected);if(current)rows=[current,...rows];}
  if(!rows.some(m=>m.id===selected))selected=rows[0]?.id||'';
  select.replaceChildren();
  for(const m of rows){const option=document.createElement('option');option.value=m.id;option.textContent=m.name;select.append(option);}
  if(selected)select.value=selected;
  paintTrigger();
  status.textContent=rows.length?'':words.empty;
  paint();
 };
 message.addEventListener('keydown',event=>{if(enterSends(event)){event.preventDefault();form.requestSubmit();}});
 select.addEventListener('change',()=>{if(busy||!rows.some(row=>row.id===select.value))return;selected=select.value;try{storage.setItem(key(),selected);}catch{}});
 async function load(){
  const identity=session();if(!identity?.owner||!identity.key)return '';
  if(owner!==identity.owner){owner=identity.owner;rows=[];selected='';generation++;pending=null;}
  if(pending)return pending;
  const version=++generation;status.textContent=words.loading;select.disabled=true;
  const task=(async()=>{try{
   const data=await api('/v1/models');if(session()?.key!==identity.key||session()?.owner!==identity.owner||generation!==version)return '';
   allVisible=allModels(data);allChat=allVisible.filter(m=>m.forChat);
   let shelf=null;try{shelf=await api('/v1/me/models');}catch{}
   if(session()?.key!==identity.key||session()?.owner!==identity.owner||generation!==version)return '';
   const favourites=new Set(shelf?.shelf?.favourites||[]);try{const raw=localStorage.getItem('nl.web.favourites.'+identity.owner);if(raw!==null){for(const id of JSON.parse(raw)||[])favourites.add(id);}}catch{}const enabledChat=new Set((shelf?.shelf?.enabled||{}).chat||[]);
   const preferred=allChat.filter(m=>favourites.has(m.id)||enabledChat.has(m.id));favSet=favourites;enabledSet=enabledChat;
   all=preferred.length?preferred:allChat;
   const chat=chatModels(data);
   let saved='';try{saved=storage.getItem(key())||'';}catch{}
   selected=(chat.some(row=>row.id===(selected||saved))?(selected||saved):(chatModel(data)||''))||'';
   applyFilters();return selected;
  }catch(error){if(generation===version){status.textContent=words.error;}throw error;}finally{if(generation===version){pending=null;paint();}}})();
  pending=task;paint();return task;
 }
 refresh.addEventListener('click',()=>{load().catch(()=>{});});
 return {load,selectModel(id){kind="chat";query="";selected=id;applyFilters();select.dispatchEvent(new Event("change"));},setBusy(value){busy=value;paint();},
  /** Фильтры списка: тип модели и кусок названия. */
  setKind(value){kind=value||'chat';applyFilters();},
  setQuery(value){query=value||'';applyFilters();},
  kinds(){const set=new Set(all.map(m=>m.kind));return ['chat','image','video','audio'].filter(k=>set.has(k));},
  async model(){const identity=session();if(owner!==identity?.owner||!selected)await load();if(!selected)throw new Error(words.empty);return selected;}};
}
