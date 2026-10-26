import {chatModel,chatModels} from './catalog.js?v=20261026-models';
export function enterSends(event){return event.key==='Enter'&&!event.shiftKey&&!event.ctrlKey&&!event.altKey&&!event.metaKey&&!event.isComposing&&event.keyCode!==229&&!event.repeat;}
export function installComposerControls({form,message,select,refresh,status,session,api,words,storage=localStorage}){
 let rows=[],selected='',owner='',pending=null,busy=false,generation=0;
 const key=()=> 'nl.web.model.'+owner;
 const paint=()=>{select.disabled=busy||!rows.length;refresh.disabled=busy||!!pending;};
 message.addEventListener('keydown',event=>{if(enterSends(event)){event.preventDefault();form.requestSubmit();}});
 select.addEventListener('change',()=>{if(busy||!rows.some(row=>row.id===select.value))return;selected=select.value;try{storage.setItem(key(),selected);}catch{}});
 async function load(){
  const identity=session();if(!identity?.owner||!identity.key)return '';
  if(owner!==identity.owner){owner=identity.owner;rows=[];selected='';generation++;pending=null;}
  if(pending)return pending;
  const version=++generation;status.textContent=words.loading;select.disabled=true;
  const task=(async()=>{try{
   const data=await api('/v1/models');if(session()?.key!==identity.key||session()?.owner!==identity.owner||generation!==version)return '';
   rows=chatModels(data);let saved='';try{saved=storage.getItem(key())||'';}catch{}
   selected=rows.some(row=>row.id===(selected||saved))?(selected||saved):(chatModel(data)||'');
   if(!rows.some(row=>row.id===selected))selected=rows[0]?.id||'';
   select.replaceChildren();for(const row of rows){const option=document.createElement('option');option.value=row.id;option.textContent=row.name;select.append(option);}select.value=selected;
   status.textContent=rows.length?'':words.empty;return selected;
  }catch(error){if(generation===version){status.textContent=words.error;}throw error;}finally{if(generation===version){pending=null;paint();}}})();
  pending=task;paint();return task;
 }
 refresh.addEventListener('click',()=>{load().catch(()=>{});});
 return {load,setBusy(value){busy=value;paint();},async model(){const identity=session();if(owner!==identity?.owner||!selected)await load();if(!selected)throw new Error(words.empty);return selected;}};
}
