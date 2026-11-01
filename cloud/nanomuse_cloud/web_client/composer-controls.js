import {allModels,chatModel,chatModels} from './catalog.js?v=20261101-notip';
import {openModelPicker} from './model-picker.js?v=20261101-notip';
export function enterSends(event){return event.key==='Enter'&&!event.shiftKey&&!event.ctrlKey&&!event.altKey&&!event.metaKey&&!event.isComposing&&event.keyCode!==229&&!event.repeat;}
export function installComposerControls({form,message,select,refresh,status,session,api,words,onOtherPicked,onPick,storage=localStorage}){
 let rows=[],all=[],allChat=[],allVisible=[],favSet=new Set(),enabledSet=new Set(),enabledByKind={},selected='',chatModel='',owner='',pending=null,busy=false,generation=0;
 let kind='chat',query='';
 const key=()=> 'nl.web.model.'+owner;
 const paint=()=>{select.disabled=busy||!rows.length;refresh.disabled=busy||!!pending;};
 // ── выбор модели: общий компонент model-picker ────────────────────
 const wrap=document.createElement('div');wrap.className='mp-wrap';
 select.parentNode.insertBefore(wrap,select);wrap.append(select);select.hidden=true;
 const trigger=document.createElement('button');trigger.type='button';trigger.className='mp-trigger';trigger.setAttribute('aria-haspopup','dialog');trigger.setAttribute('aria-label','Выбрать модель');
 wrap.append(trigger);
 function paintTrigger(){const m=allVisible.find(x=>x.id===selected)||allChat.find(x=>x.id===selected);trigger.textContent=m?m.name:'Модель';}
 /* Картинка/видео/аудио из избранного: назначаем моделью для этого дела, текстовую
    модель чата не трогаем. Так выбор в пикере что-то делает, а не просто серый текст. */
 async function chooseOther(id,kind){
  if(!id||!kind)return;
  const list=[...(enabledByKind[kind]||[])];if(!list.includes(id))list.push(id);
  try{
   const data=await api('/v1/me/models',{method:'PUT',body:JSON.stringify({enabled:{[kind]:list},default:{[kind]:id}})});
   if(data?.shelf?.enabled&&typeof data.shelf.enabled==='object')enabledByKind=data.shelf.enabled;
   const row=allVisible.find(m=>m.id===id);
   onOtherPicked?.(kind,row?.name||id,true);
  }catch{onOtherPicked?.(kind,id,false);}
 }
 function chosenModels(){return allVisible.filter(m=>favSet.has(m.id)).sort((a,b)=>Number(b.forChat)-Number(a.forChat)||a.name.localeCompare(b.name));}
 async function saveFavourites(){try{const data=await api('/v1/me/models',{method:'PUT',body:JSON.stringify({enabled:{},favourites:[...favSet]})});const echoed=data?.shelf?.favourites;if(Array.isArray(echoed))favSet=new Set(echoed);}catch{}}
 trigger.onclick=async()=>{
  // Новые модели на сервере должны появляться в открытом списке без перезагрузки
  // страницы: перед открытием перечитываем каталог, старый список оставляем,
  // если сеть не ответила.
  try{await load();}catch{}
  openModelPicker({models:chosenModels(),favourites:favSet,selected,onPickOther:chooseOther,onManage:()=>document.getElementById('model-browse')?.click(),onPick:(id)=>{if(!allChat.some(m=>m.id===id))return;selected=id;select.value=id;select.dispatchEvent(new Event('change'));paintTrigger();},onToggleFavourite:(id,next)=>{next?favSet.add(id):favSet.delete(id);void saveFavourites();const pref=allChat.filter(m=>favSet.has(m.id)||enabledSet.has(m.id));all=pref.length?pref:allChat;applyFilters();}});
 };
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
 select.addEventListener('change',()=>{if(busy||!rows.some(row=>row.id===select.value))return;selected=select.value;chatModel=selected;try{storage.setItem(key(),selected);}catch{}onPick?.(selected);});
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
   const shelfModels=Array.isArray(shelf?.models)?shelf.models:[];
   const lockedIds=new Set(shelfModels.filter(m=>m.plan_locked).map(m=>m.id));
   if(shelfModels.length){const known=new Set(allVisible.map(m=>m.id));const extra=allModels({data:shelfModels}).filter(m=>!known.has(m.id));for(const m of extra)m.locked=lockedIds.has(m.id);allVisible=allVisible.concat(extra);for(const m of allVisible)if(lockedIds.has(m.id))m.locked=true;allChat=allVisible.filter(m=>m.forChat);}
   /* Один список избранного на весь аккаунт: только сервер, без локальных добавок.
      Раньше браузер подмешивал своё избранное из localStorage — отсюда разные списки
      в вебе, приложении и кабинете, и мусор возвращался на сервер при сохранении. */
   const favourites=new Set(shelf?.shelf?.favourites||[]);enabledByKind=(shelf?.shelf?.enabled&&typeof shelf.shelf.enabled==='object')?shelf.shelf.enabled:{};const enabledChat=new Set(enabledByKind.chat||[]);
   const preferred=allChat.filter(m=>favourites.has(m.id)||enabledChat.has(m.id));favSet=favourites;enabledSet=enabledChat;
   all=preferred.length?preferred:allChat;
   const chat=chatModels(data);
   let saved='';try{saved=storage.getItem(key())||'';}catch{}
   /* По умолчанию — модель по умолчанию из аккаунта, и только из избранного (или
      включённого на полке): список везде должен совпадать. */
   const shelfDefault=(shelf?.shelf?.default||{}).chat||'';
   const onShelf=id=>!favourites.size||favourites.has(id)||enabledChat.has(id);
   selected=[chatModel,selected,saved,shelfDefault].find(id=>id&&chat.some(row=>row.id===id)&&onShelf(id))||chatModel(data)||'';
   applyFilters();return selected;
  }catch(error){if(generation===version){status.textContent=words.error;}throw error;}finally{if(generation===version){pending=null;paint();}}})();
  pending=task;paint();return task;
 }
 refresh.addEventListener('click',()=>{load().catch(()=>{});});
 /**
  * Видит ли выбранная модель картинки: 'yes' | 'no' | 'unknown'.
  * По этому флагу веб-клиент не отправляет изображение текстовой модели:
  * релей отфильтровал бы всех поставщиков и ответил 503.
  */
 function modelVision(){const row=allVisible.find(m=>m.id===selected)||allChat.find(m=>m.id===selected);if(!row)return 'unknown';return row.vision===true?'yes':row.vision===false?'no':'unknown';}
 return {load,selectModel(id){kind="chat";query="";selected=id;applyFilters();select.dispatchEvent(new Event("change"));},setBusy(value){busy=value;paint();},
  /** Фильтры списка: тип модели и кусок названия. */
  setKind(value){kind=value||'chat';applyFilters();},
  setQuery(value){query=value||'';applyFilters();},
  kinds(){const set=new Set(all.map(m=>m.kind));return ['chat','image','video','audio'].filter(k=>set.has(k));},
  async model(){const identity=session();if(owner!==identity?.owner||!selected)await load();if(!selected)throw new Error(words.empty);return selected;},
  /* The model of the chat on screen ('' = nothing chosen there, follow the account
     default). Called on every chat switch: a picture model picked in one chat must not
     follow the person into the next one. */
  setChatModel(id){
   chatModel=String(id||'');
   if(!chatModel){paintTrigger();return;}
   const row=allChat.find(m=>m.id===chatModel)||allVisible.find(m=>m.id===chatModel);
   if(row){selected=chatModel;select.value=chatModel;paintTrigger();return;}
   /* The catalogue is not here yet (a chat opened before the first load, or a model
      this browser has not seen): ask again and settle on it when the rows arrive. */
   Promise.resolve().then(()=>load()).then(()=>{if(chatModel&&(allChat.some(m=>m.id===chatModel)||allVisible.some(m=>m.id===chatModel))){selected=chatModel;select.value=chatModel;paintTrigger();}}).catch(()=>{});
  },
  chatModel:()=>chatModel,
  modelVision};
}
