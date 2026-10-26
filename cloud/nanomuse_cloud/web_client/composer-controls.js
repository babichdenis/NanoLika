import {allModels,chatModel,chatModels} from './catalog.js?v=20261026-models';
export function enterSends(event){return event.key==='Enter'&&!event.shiftKey&&!event.ctrlKey&&!event.altKey&&!event.metaKey&&!event.isComposing&&event.keyCode!==229&&!event.repeat;}
export function installComposerControls({form,message,select,refresh,status,session,api,words,storage=localStorage}){
 let rows=[],all=[],selected='',owner='',pending=null,busy=false,generation=0;
 let kind='chat',query='';
 const key=()=> 'nl.web.model.'+owner;
 const paint=()=>{select.disabled=busy||!rows.length;refresh.disabled=busy||!!pending;};
 /** Список под фильтры: по типу (chat/image/video/audio) и по куску названия. */
 const applyFilters=()=>{
  const q=query.trim().toLowerCase();
  rows=all.filter(m=>(kind==='all'||m.kind===kind)&&(!q||m.name.toLowerCase().includes(q)||m.id.toLowerCase().includes(q)));
  if(!rows.some(m=>m.id===selected))selected=rows[0]?.id||'';
  select.replaceChildren();
  for(const m of rows){const option=document.createElement('option');option.value=m.id;option.textContent=m.name;select.append(option);}
  if(selected)select.value=selected;
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
   all=allModels(data);
   const chat=chatModels(data);
   let saved='';try{saved=storage.getItem(key())||'';}catch{}
   selected=(chat.some(row=>row.id===(selected||saved))?(selected||saved):(chatModel(data)||''))||'';
   applyFilters();return selected;
  }catch(error){if(generation===version){status.textContent=words.error;}throw error;}finally{if(generation===version){pending=null;paint();}}})();
  pending=task;paint();return task;
 }
 refresh.addEventListener('click',()=>{load().catch(()=>{});});
 return {load,setBusy(value){busy=value;paint();},
  /** Фильтры списка: тип модели и кусок названия. */
  setKind(value){kind=value||'chat';applyFilters();},
  setQuery(value){query=value||'';applyFilters();},
  kinds(){const set=new Set(all.map(m=>m.kind));return ['chat','image','video','audio'].filter(k=>set.has(k));},
  async model(){const identity=session();if(owner!==identity?.owner||!selected)await load();if(!selected)throw new Error(words.empty);return selected;}};
}
