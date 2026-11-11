/* Shared metadata everywhere; bodies and read marks belong to the selected visible chat. */
export class ChatSync {
 constructor({session,getChats,setChats,current,save,remove,onChange,onSyncError,onSyncRecovered}){Object.assign(this,{session,getChats,setChats,current,save,remove,onChange,onSyncError,onSyncRecovered});this.queue=Promise.resolve();this.selected='';this.cursors=new Map();this.known=new Set();this.failures=new Map();this.uploadTimer=null;this.uploadRetry=0;this.account='';let channel='web';try{channel=localStorage.getItem('nl.web.channel')||'web';}catch{}
  if(!['telegram','max','web'].includes(channel))channel='web';
  this.channel=channel;
  const prefix=channel==='telegram'?'tg':channel==='max'?'max':'web';
  this.deviceName=channel==='telegram'?'Telegram':channel==='max'?'MAX':'Web';
  const deviceKey='nl.web.chat-client.'+prefix;
  let id;try{id=localStorage.getItem(deviceKey);if(!id&&prefix==='web')id=localStorage.getItem('nl.web.chat-client');if(!id){id=prefix+'-'+crypto.randomUUID();localStorage.setItem(deviceKey,id);}}catch{id=prefix+'-'+crypto.randomUUID();}this.device=id;this.catalogETag='';this.catalogRows=[];this.started=false;this.socket=null;this.socketOpen=false;this.reconnectTimer=null;this.wakeTimer=null;this.pollTimer=null;this.retry=0;}
 run(fn){const task=this.queue.then(fn);this.queue=task.catch(()=>{});return task;}
 async request(path,options={},session){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);try{const r=await fetch(path,{...options,signal:controller.signal,cache:'no-store',headers:{...(options.headers||{}),Authorization:'Bearer '+session.key,'Content-Type':'application/json'}});if(r.headers.get('etag'))this.catalogETag=r.headers.get('etag');if(r.status===304||r.status===204)return null;const data=await r.json();if(!r.ok||data?.ok===false){const e=new Error(data?.error?.message||'Chat sync unavailable');e.status=r.status;e.code=data?.error?.code||data?.reason;throw e;}return data;}finally{clearTimeout(timer);}}
 valid(session){const now=this.session();return now?.key===session?.key&&now?.owner===session?.owner;}
 /* A chat with nothing to say is not a conversation yet: the first message publishes it. */
 async publish(chat){return this.run(async()=>{const session=this.session();try{await this.upload(chat,session);this.clearFailure(chat,session);}catch(error){this.noteFailure(chat,error,session);throw error;}});}
 /* Failed uploads remain in the saved chat, with the same message IDs. Retry even
    when the hub socket is open and no one else sends an event. */
 noteFailure(chat,error,session){if(!chat?.key||!this.valid(session)||chat.owner!==session.owner)return;const code=String(error?.code||'sync_unavailable');const previous=this.failures.get(chat.key);this.failures.set(chat.key,code);if(previous!==code)this.onSyncError?.(error,chat);this.scheduleUploadRetry();}
 clearFailure(chat,session){if(!this.valid(session)||!this.failures.delete(chat.key))return;if(!this.failures.size){clearTimeout(this.uploadTimer);this.uploadTimer=null;this.uploadRetry=0;this.onSyncRecovered?.();}}
 scheduleUploadRetry(){if(!this.started||this.uploadTimer||!this.failures.size)return;const delay=Math.min(300000,5000*Math.pow(2,Math.min(this.uploadRetry++,6)));this.uploadTimer=setTimeout(()=>{this.uploadTimer=null;this.refresh().catch(()=>{}).finally(()=>this.scheduleUploadRetry());},delay);}
 mainKey(owner){return 'nl.web.main.'+owner;}
 loadMain(session){try{return localStorage.getItem(this.mainKey(session.owner))||'';}catch{return '';}}
 saveMain(session,cid){try{localStorage.setItem(this.mainKey(session.owner),cid);}catch{}}
 async upload(chat,session,{force=false,adopted=false}={}){
  if(chat.deleted||chat.local||!session?.key||chat.owner!==session.owner||!this.valid(session))return;
  /* An interrupted answer still holds what the model said before the stream broke:
   publish it too, otherwise the other devices see the question and never the reply. */
  const pending=chat.messages.filter(m=>!m.shared&&(m.content||(Array.isArray(m.files)&&m.files.length)));
  /* The chat's own model goes with its conversation, not with the browser: the same
     chat answers with the same model in the app, in Telegram and here. */
  const model=String(chat.model||''), modelDirty=model!==String(chat.sharedModel||'');
  /* The thinking level rides with the model: a chat answers the same way in every client. */
  const reasoning=String(chat.reasoning||''), reasoningDirty=reasoning!==String(chat.sharedReasoning||'');
  /* The folder the chat works in rides with its conversation too (0.27): a web chat
     says Web, a chat carried from another computer keeps that computer's workspace. */
  const folder=String(chat.folder||'').slice(0,120), folderDirty=folder!==String(chat.sharedFolder||'');
  if(!pending.length&&!modelDirty&&!reasoningDirty&&!folderDirty&&!chat.syncCid&&!force)return;
  const fresh=!chat.syncCid;
  if(!chat.syncCid){chat.syncCid=crypto.randomUUID();chat.device=this.device;chat.syncPendingCreate=true;}
  else{
   /* Never reopen an archived or deleted conversation on a retry. A locally created
      ID may legitimately be absent if its first request failed before reaching relay. */
   const priorTag=this.catalogETag;let catalog;
   try{catalog=await this.request('/v1/sync/catalog?'+new URLSearchParams({client:this.device,device:this.device}),{},session);}finally{this.catalogETag=priorTag;}
   if(!this.valid(session))return;
   if(catalog?.complete!==true||(catalog.account_id&&catalog.account_id!==session.owner))throw new Error('The chat list is not available');
   const meta=(catalog.conversations||[]).find(row=>row.cid===chat.syncCid);
   const deleted=(catalog.deleted||[]).some(row=>row.cid===chat.syncCid);
   if(deleted||(!meta&&!chat.syncPendingCreate)){const error=new Error('The cloud conversation was deleted');error.code='conversation_deleted';throw error;}
   if(!meta&&chat.syncCreateUncertain){const error=new Error('The original creation could not be verified');error.code='creation_unknown';throw error;}
   if(meta?.archived){const error=new Error('The conversation is archived');error.code='conversation_archived';throw error;}
  }
  for(const m of chat.messages){if(!m.mid)m.mid=crypto.randomUUID();}
  // The first chat this browser puts in the cloud is the account's main chat (S9):
  // desktop and phones with "main only" see it. Later chats here stay side chats.
  let mainCid=this.loadMain(session);
  // Only a chat born here can become the account's main: promoting an old side chat
  // would leave its already-shared text under the old id. An existing chat stays side.
  if(!mainCid&&fresh){mainCid=chat.syncCid;this.saveMain(session,mainCid);}
  const isMain=chat.syncCid===mainCid;
  await this.save(chat);
  for(let offset=0;offset<Math.max(1,pending.length);offset+=200){
   if(!this.valid(session))return;
   /* Файлы остаются в браузере, но в тексте для других устройств и мессенджеров
      честно сказано, что было вложено: иначе там видно только подпись. */
   const syncedText=m=>{const names=(m.files||[]).map(file=>file.name).filter(Boolean);return names.length?`${m.content||''}${m.content?'\n\n':''}[вложение: ${names.join(', ')}]`:(m.content||'')};
   const batch=pending.slice(offset,offset+200),body={device:this.device,conversations:[{cid:chat.syncCid,kind:isMain?'main':'side',title:adopted?'':chat.title,created_at:Math.floor((chat.created||chat.updated)/1000),updated_at:Math.floor(chat.updated/1000),...(fresh?{archived:chat.archived===true}:{}),...(modelDirty?{model}:{}),...(reasoningDirty?{reasoning}:{}),...(folderDirty?{folder}:{})}],messages:batch.map(m=>({mid:m.mid,cid:chat.syncCid,role:m.role,text:syncedText(m),created_at:Math.floor(m.at/1000),attachments:(m.files||[]).map(file=>({name:file.name,mime:file.mime,size:file.size,url:file.url||''}))}))};
   if(chat.syncPendingCreate){chat.syncCreateUncertain=true;await this.save(chat);}
   let result;
   try{result=await this.request('/v1/sync/changes',{method:'POST',body:JSON.stringify(body)},session);}
   catch(error){/* A 409 message_limit is an atomic rejection: no conversation was created. */
    if(chat.syncPendingCreate&&error?.status===409&&error?.code==='message_limit'){chat.syncCreateUncertain=false;await this.save(chat);}throw error;}
   const clash=(result.rejected||[]).find(r=>r.reason==='main_exists'&&r.cid_main&&r.cid===chat.syncCid);
   if(clash&&!adopted){
    // the account already has a main chat (desktop/phone): this browser joins it under
    // the id the relay named instead of minting a second one; the batch goes again
    chat.syncCid=String(clash.cid_main);this.saveMain(session,chat.syncCid);
    for(const m of batch)m.shared=false;
    await this.save(chat);
    return this.upload(chat,session,{force:true,adopted:true});
   }
   if(result.rejected?.length){
    /* A timeout can hide a successful write. `known_mid` alone is not proof: it can
       refer to different content or a tombstone. Verify the actual remote text. */
    const known=result.rejected.every(r=>r.reason==='known_mid'&&batch.some(m=>m.mid===r.mid));
    if(!known||!await this.verifyKnown(chat,batch,result.rejected,syncedText,session)){const error=new Error('Some chat messages could not be synced');error.code=result.rejected[0]?.reason||'sync_unavailable';throw error;}
   }
   for(const m of batch)m.shared=true;chat.shared=true;delete chat.syncPendingCreate;delete chat.syncCreateUncertain;if(modelDirty)chat.sharedModel=model;if(reasoningDirty)chat.sharedReasoning=reasoning;if(folderDirty)chat.sharedFolder=folder;await this.save(chat);
  }
 }
 async verifyKnown(chat,batch,rejected,syncedText,session){
  const needed=new Map(rejected.map(row=>[row.mid,batch.find(m=>m.mid===row.mid)]));
  /* Files and truncated text need a stronger receipt than the old relay provides. */
  if([...needed.values()].some(m=>!m||(m.files||[]).length||new TextEncoder().encode(syncedText(m)).length>16384))return false;
  let before=0;
  for(let round=0;round<70&&needed.size;round++){
   const page=await this.request('/v1/sync/conversations/'+encodeURIComponent(chat.syncCid)+'/messages'+(before?'?before='+before+'&limit=300':'?after=0&limit=300'),{},session);
   if(!this.valid(session)||(page.account_id&&page.account_id!==session.owner))return false;
   for(const remote of page.messages||[]){const local=needed.get(remote.mid);if(!local)continue;if(remote.deleted||remote.cid!==chat.syncCid||remote.role!==local.role||remote.truncated||remote.text!==syncedText(local))return false;needed.delete(remote.mid);}
   if(!page.has_older||!page.messages?.length)break;
   const oldest=Math.min(...page.messages.map(m=>Number(m.seq)||0));if(!oldest||oldest===before)break;before=oldest;
  }
  return needed.size===0;
 }
 /* The relay's replies go into the local copy once: an update of a message already kept does not change it. */
 merge(chat,rows){const byId=new Map(chat.messages.filter(m=>m.mid).map(m=>[m.mid,m]));let changed=false;
  for(const m of rows||[]){
   if(m.deleted){const before=chat.messages.length;chat.messages=chat.messages.filter(x=>x.mid!==m.mid);changed=changed||before!==chat.messages.length;continue;}
   if(byId.has(m.mid)&&m.usage&&JSON.stringify(byId.get(m.mid).usage)!==JSON.stringify(m.usage)){byId.get(m.mid).usage=m.usage;changed=true;}
   if(!byId.has(m.mid)){const files=Array.isArray(m.attachments)?m.attachments.filter(a=>a&&(a.name||a.url)).map(a=>({name:a.name||'',mime:a.mime||'',size:a.size||0,url:a.url||''})):[];const picture=files.find(a=>/^image\//i.test(a.mime||'')&&a.url);const local={mid:m.mid,role:m.role,content:m.text,at:m.created_at*1000,shared:true,truncated:m.truncated,...(m.usage?{usage:m.usage}:{}),...(files.length?{files}:{}),...(picture?{image:picture.url}:{})};chat.messages.push(local);byId.set(m.mid,local);changed=true;}
  }
  chat.messages.sort((a,b)=>a.at-b.at);return changed;}
 async refresh(){if(this.refreshTask)return this.refreshTask;const task=this.run(async()=>{
  const session=this.session();if(!session?.key||!session.owner||!navigator.onLine)return;
  if(this.account!==session.owner){this.account=session.owner;this.catalogETag='';this.cursors.clear();this.known.clear();this.selected='';this.failures.clear();clearTimeout(this.uploadTimer);this.uploadTimer=null;this.uploadRetry=0;}
  for(const chat of [...this.getChats()])if(!chat.local&&(!chat.shared||chat.messages.some(m=>!m.shared&&(m.content||(Array.isArray(m.files)&&m.files.length))))){try{await this.upload(chat,session);this.clearFailure(chat,session);}catch(error){this.noteFailure(chat,error,session);}}
  const result=await this.request('/v1/sync/catalog?'+new URLSearchParams({client:this.device,device:this.device}),{headers:this.catalogETag?{'If-None-Match':this.catalogETag}:{}},session);
  if(!this.valid(session))return;
  if(result===null){/* 304: the list is what this tab already has; the open chat may still have new text */const open=this.current();if(open?.syncCid&&document.visibilityState!=='hidden'){const changed=await this.pull(open,session);if(this.valid(session))this.onChange({messages:changed});}return;}
  if(result.complete!==true||(result.account_id&&result.account_id!==session.owner))return;
  this.catalogRows=result.conversations||[];
  const existing=new Map(this.getChats().filter(c=>c.owner===session.owner).map(c=>[c.syncCid,c]));const rows=[];const ids=new Set();
  for(const meta of result.conversations){if(!this.valid(session))return;ids.add(meta.cid);let chat=existing.get(meta.cid);if(!chat)chat={key:session.owner+':'+meta.cid,syncCid:meta.cid,owner:session.owner,messages:[],shared:true};chat.title=meta.title;chat.archived=meta.archived===true;chat.device=meta.device||'';chat.updated=Math.max(chat.updated||0,meta.updated_at*1000);chat.unread=meta.unread||0;/* the model another device chose for this chat */const model=typeof meta.model==='string'?meta.model:'';if(model){chat.model=model;}chat.sharedModel=model;const reasoning=typeof meta.reasoning==='string'?meta.reasoning:'';if('reasoning' in meta){chat.reasoning=reasoning;chat.sharedReasoning=reasoning;}if('folder' in meta){chat.folder=String(meta.folder||'');chat.sharedFolder=chat.folder;}rows.push(chat);await this.save(chat);}
  const deleted=new Set((result.deleted||[]).map(c=>c.cid));
  for(const chat of this.getChats()){if(!this.valid(session))return;if(chat.owner!==session.owner)continue;if(!ids.has(chat.syncCid)){if(deleted.has(chat.syncCid)){if(chat.messages.some(m=>!m.shared&&(m.content||(Array.isArray(m.files)&&m.files.length)))){await this.markLocal(chat);if(!this.valid(session))return;this.failures.delete(chat.key);rows.push(chat);const error=new Error('The cloud conversation was deleted; unsent messages remain here');error.code='conversation_deleted';this.onSyncError?.(error,chat);}else{chat.deleted=true;await this.remove(chat.key);}}else rows.push(chat);}}
  if(!this.valid(session))return;
  const beforeKey=this.current()?.key;this.known=ids;this.setChats(rows.sort((a,b)=>b.updated-a.updated||a.key.localeCompare(b.key)));let messagesChanged=!!beforeKey&&!this.current();
  const current=this.current();if(current?.syncCid&&ids.has(current.syncCid)&&document.visibilityState!=='hidden')messagesChanged=(await this.pull(current,session))||messagesChanged;
  if(this.valid(session))this.onChange({messages:messagesChanged});
 });this.refreshTask=task.finally(()=>{this.refreshTask=null;});return this.refreshTask;}
 /* A permanent hub socket replaces the three-second catalogue poll (S8): another device
    says it pushed, and only then does this tab ask. A quiet relay costs nothing. */
 start(){if(this.started)return;this.started=true;this.connect();this.scheduleUploadRetry();this.pollTimer=setInterval(()=>{if(this.socketOpen)return;const session=this.session();if(session?.key&&navigator.onLine&&document.visibilityState!=='hidden')this.refresh().catch(()=>{});},30000);}
 stop(){this.started=false;clearInterval(this.pollTimer);clearTimeout(this.reconnectTimer);clearTimeout(this.wakeTimer);clearTimeout(this.uploadTimer);this.pollTimer=this.reconnectTimer=this.wakeTimer=this.uploadTimer=null;try{this.socket?.close();}catch{}this.socket=null;this.socketOpen=false;}
 connect(){
  if(!this.started)return;
  const session=this.session();if(!session?.key||!session.owner){this.scheduleReconnect(15000);return;}
  let socket;try{socket=new WebSocket(new URL('/v1/hub',location.origin).href.replace(/^http/,'ws'));}catch{this.scheduleReconnect(30000);return;}
  this.socket=socket;
  socket.onopen=()=>{if(this.socket!==socket)return;this.socketOpen=true;this.retry=0;socket.send(JSON.stringify({type:'hello',key:session.key,device:{id:this.device,name:this.deviceName,kind:'web',actions:[]}}));this.refresh().catch(()=>{});};
  socket.onmessage=event=>{let frame;try{frame=JSON.parse(event.data);}catch{return;}if(frame.type==='sync'){this.wake();}else if(frame.type==='working'){this.onChange({working:frame});}else if(frame.type==='error'&&['bad_key','signed_out'].includes(frame.code)){/* the key is gone: stop asking */this.stop();}};
  socket.onerror=()=>{};
  socket.onclose=()=>{if(this.socket===socket)this.socketOpen=false;this.scheduleReconnect();};
 }
 scheduleReconnect(delay){if(!this.started)return;clearTimeout(this.reconnectTimer);const wait=delay??Math.min(30000,1000*Math.pow(2,Math.min(this.retry++,5)));this.reconnectTimer=setTimeout(()=>this.connect(),wait);}
 /* A sync frame: one refresh behind a short quiet window, however many frames arrive. */
 wake(){if(this.wakeTimer)return;this.wakeTimer=setTimeout(()=>{this.wakeTimer=null;this.refresh().catch(()=>{});},150);}
 /* Only a chat with a conversation id has anything to ask the relay about. */
 async open(chat){return this.run(async()=>{const session=this.session();if(!session?.key||chat.owner!==session.owner)return;if(chat.local||!chat.syncCid)return;this.selected=chat.syncCid;const changed=await this.pull(chat,session);if(this.valid(session))this.onChange({messages:changed});});}
 async pull(chat,session){
  const cid=chat.syncCid;let changed=false;
  let before=Number(chat.historyBefore||0);
  let after=this.cursors.get(cid)||0;
  let mode=before?'before':after?'after':'newest';
  for(let round=0;round<50;round++){
   const query=mode==='before'?'?before='+before+'&limit=300':'?after='+(mode==='newest'?0:after)+'&limit=300';
   const page=await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/messages'+query,{},session);
   if(!this.valid(session)||(page.account_id&&page.account_id!==session.owner)||this.current()?.key!==chat.key||document.visibilityState==='hidden')return;
   changed=this.merge(chat,page.messages)||changed;
   if(mode==='before'){
    const oldest=Math.min(...(page.messages||[]).map(m=>Number(m.seq)||0));
    if(page.has_older&&oldest>0&&oldest<before){before=oldest;chat.historyBefore=oldest;this.cursors.set(cid,page.through_seq);await this.save(chat);continue;}
    delete chat.historyBefore;
    mode='after';after=page.through_seq||after;
    await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/read',{method:'POST',body:JSON.stringify({client:this.device,through_seq:page.through_seq})},session);chat.unread=0;await this.save(chat);
    continue;
   }
   if(mode==='newest'){
    this.cursors.set(cid,page.through_seq);await this.save(chat);
    const oldest=Math.min(...(page.messages||[]).map(m=>Number(m.seq)||0));
    if(page.has_older&&oldest>0){before=oldest;chat.historyBefore=oldest;mode='before';await this.save(chat);continue;}
    await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/read',{method:'POST',body:JSON.stringify({client:this.device,through_seq:page.through_seq})},session);chat.unread=0;await this.save(chat);break;
   }
   this.cursors.set(cid,page.cursor);await this.save(chat);
   if(!page.more){await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/read',{method:'POST',body:JSON.stringify({client:this.device,through_seq:page.through_seq})},session);chat.unread=0;await this.save(chat);break;}
   if(page.cursor<=after)break;after=page.cursor;
  }
  return changed;
 }
 /* Every message the relay keeps for this chat, older pages included, written into the local copy. */
 async collect(chat,session){
  const cid=chat.syncCid,seen=new Set();if(!cid)return seen;
  let before=0;
  for(let round=0;round<200;round++){
   const page=await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/messages'+(before?'?before='+before:'?after=0'),{},session);
   if(!this.valid(session))throw new Error('The account changed');
   this.merge(chat,page.messages);
   for(const m of page.messages||[])if(!m.deleted)seen.add(m.mid);
   await this.save(chat);
   if(!page.has_older||!page.messages?.length)break;
   const oldest=Math.min(...page.messages.map(m=>Number(m.seq)||0));
   if(!oldest||oldest===before)break;
   before=oldest;
  }
  return seen;
 }
 async catalog(session){const result=await this.request('/v1/sync/catalog?'+new URLSearchParams({client:this.device,device:this.device}),{},session);if(!this.valid(session)||result?.complete!==true||(result.account_id&&result.account_id!==session.owner))throw new Error('The chat list is not available');return result.conversations||[];}
 async markLocal(chat){delete chat.syncCid;delete chat.syncPendingCreate;delete chat.syncCreateUncertain;chat.local=true;chat.shared=false;chat.unread=0;for(const m of chat.messages)m.shared=false;await this.save(chat);}
 /** Take the cloud off a chat and keep this browser's copy: the server copy goes for every device. Refuses when this browser does not hold the whole text, so no message is lost. */
 async unsync(chat){return this.run(async()=>{
  const session=this.session(),cid=chat.syncCid;
  if(!cid){await this.markLocal(chat);return;}
  if(!session?.key||session.owner!==chat.owner)throw new Error('Sign in again');
  const meta=(await this.catalog(session)).find(c=>c.cid===cid);
  if(!meta){await this.markLocal(chat);return;}
  const expected=Number(meta.message_count)||0;
  let seen;
  try{seen=await this.collect(chat,session);}
  catch(error){if(['chat_missing','no_conversation','conversation_deleted'].includes(error?.code)){await this.markLocal(chat);return;}throw error;}
  if(seen.size<expected){const error=new Error('This browser does not hold the whole chat');error.code='incomplete_local_text';throw error;}
  await this.request('/v1/sync/conversations/'+encodeURIComponent(cid),{method:'DELETE'},session);
  this.known.delete(cid);
  await this.markLocal(chat);
 });}
 /** Put the cloud back on a local chat: a fresh conversation id (the old one is a tombstone) and its text goes up again. */
 async sync(chat){return this.run(async()=>{
  const session=this.session();if(!session?.key||chat.owner!==session.owner)throw new Error('Sign in again');
  delete chat.syncCid;chat.local=false;chat.shared=false;for(const m of chat.messages)m.shared=false;
  await this.save(chat);
  await this.upload(chat,session,{force:true});
 });}
 async setArchived(chat,archived){return this.run(async()=>{
  const session=this.session();if(!session?.key||session.owner!==chat.owner)throw new Error('Sign in again');
  if(chat.syncCid)await this.request('/v1/sync/conversations/'+encodeURIComponent(chat.syncCid)+'/archive',{method:'POST',body:JSON.stringify({archived:!!archived})},session);
  chat.archived=!!archived;await this.save(chat);this.onChange({archive:true});return chat;
 });}
 async delete(chat){return this.run(async()=>{const session=this.session();if(chat.syncCid&&session?.owner===chat.owner){if(this.loadMain(session)===chat.syncCid)this.saveMain(session,'');await this.request('/v1/sync/conversations/'+encodeURIComponent(chat.syncCid),{method:'DELETE'},session);}await this.remove(chat.key);this.known.delete(chat.syncCid);this.clearFailure(chat,session);});}
}
