/* Shared metadata everywhere; bodies and read marks belong to the selected visible chat. */
export class ChatSync {
 constructor({session,getChats,setChats,current,save,remove,onChange}){Object.assign(this,{session,getChats,setChats,current,save,remove,onChange});this.queue=Promise.resolve();this.selected='';this.cursors=new Map();this.known=new Set();this.account='';let id;try{id=localStorage.getItem('nl.web.chat-client');if(!id){id='web-'+crypto.randomUUID();localStorage.setItem('nl.web.chat-client',id);}}catch{id='web-'+crypto.randomUUID();}this.device=id;this.catalogETag='';this.catalogRows=[];this.started=false;this.socket=null;this.socketOpen=false;this.reconnectTimer=null;this.wakeTimer=null;this.pollTimer=null;this.retry=0;}
 run(fn){const task=this.queue.then(fn);this.queue=task.catch(()=>{});return task;}
 async request(path,options={},session){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);try{const r=await fetch(path,{...options,signal:controller.signal,cache:'no-store',headers:{...(options.headers||{}),Authorization:'Bearer '+session.key,'Content-Type':'application/json'}});if(r.headers.get('etag'))this.catalogETag=r.headers.get('etag');if(r.status===304||r.status===204)return null;const data=await r.json();if(!r.ok||data?.ok===false){const e=new Error(data?.error?.message||'Chat sync unavailable');e.status=r.status;e.code=data?.error?.code||data?.reason;throw e;}return data;}finally{clearTimeout(timer);}}
 valid(session){const now=this.session();return now?.key===session?.key&&now?.owner===session?.owner;}
 /* A chat with nothing to say is not a conversation yet: the first message publishes it. */
 async publish(chat){return this.run(()=>this.upload(chat,this.session()));}
 mainKey(owner){return 'nl.web.main.'+owner;}
 loadMain(session){try{return localStorage.getItem(this.mainKey(session.owner))||'';}catch{return '';}}
 saveMain(session,cid){try{localStorage.setItem(this.mainKey(session.owner),cid);}catch{}}
 async upload(chat,session,{force=false,adopted=false}={}){
  if(chat.deleted||chat.local||!session?.key||chat.owner!==session.owner||!this.valid(session))return;
  const pending=chat.messages.filter(m=>!m.shared&&m.content&&(m.role==='user'||!m.interrupted));
  if(!pending.length&&!chat.syncCid&&!force)return;
  const fresh=!chat.syncCid;
  if(!chat.syncCid){chat.syncCid=crypto.randomUUID();chat.device=this.device;}for(const m of chat.messages){if(!m.mid)m.mid=crypto.randomUUID();}
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
   const batch=pending.slice(offset,offset+200),body={device:this.device,conversations:[{cid:chat.syncCid,kind:isMain?'main':'side',title:adopted?'':chat.title,created_at:Math.floor((chat.created||chat.updated)/1000),updated_at:Math.floor(chat.updated/1000),archived:chat.archived===true}],messages:batch.map(m=>({mid:m.mid,cid:chat.syncCid,role:m.role,text:m.content,created_at:Math.floor(m.at/1000),attachments:[]}))};
   const result=await this.request('/v1/sync/changes',{method:'POST',body:JSON.stringify(body)},session);
   const clash=(result.rejected||[]).find(r=>r.reason==='main_exists'&&r.cid_main&&r.cid===chat.syncCid);
   if(clash&&!adopted){
    // the account already has a main chat (desktop/phone): this browser joins it under
    // the id the relay named instead of minting a second one; the batch goes again
    chat.syncCid=String(clash.cid_main);this.saveMain(session,chat.syncCid);
    for(const m of batch)m.shared=false;
    await this.save(chat);
    return this.upload(chat,session,{force:true,adopted:true});
   }
   if(result.rejected?.length)throw new Error('Some chat messages could not be synced');
   for(const m of batch)m.shared=true;chat.shared=true;await this.save(chat);
  }
 }
 /* The relay's replies go into the local copy once: an update of a message already kept does not change it. */
 merge(chat,rows){const byId=new Map(chat.messages.filter(m=>m.mid).map(m=>[m.mid,m]));let changed=false;
  for(const m of rows||[]){
   if(m.deleted){const before=chat.messages.length;chat.messages=chat.messages.filter(x=>x.mid!==m.mid);changed=changed||before!==chat.messages.length;continue;}
   if(!byId.has(m.mid)){const local={mid:m.mid,role:m.role,content:m.text,at:m.created_at*1000,shared:true,truncated:m.truncated};chat.messages.push(local);byId.set(m.mid,local);changed=true;}
  }
  chat.messages.sort((a,b)=>a.at-b.at);return changed;}
 async refresh(){if(this.refreshTask)return this.refreshTask;const task=this.run(async()=>{
  const session=this.session();if(!session?.key||!session.owner||!navigator.onLine)return;
  if(this.account!==session.owner){this.account=session.owner;this.cursors.clear();this.known.clear();this.selected='';}
  for(const chat of [...this.getChats()])if(!chat.local&&(!chat.shared||chat.messages.some(m=>!m.shared&&m.content&&(m.role==='user'||!m.interrupted))))await this.upload(chat,session);
  const result=await this.request('/v1/sync/catalog?'+new URLSearchParams({client:this.device,device:this.device}),{headers:this.catalogETag?{'If-None-Match':this.catalogETag}:{}},session);
  if(!this.valid(session))return;
  if(result===null){/* 304: the list is what this tab already has; the open chat may still have new text */const open=this.current();if(open?.syncCid&&document.visibilityState!=='hidden'){const changed=await this.pull(open,session);if(this.valid(session))this.onChange({messages:changed});}return;}
  if(result.complete!==true||(result.account_id&&result.account_id!==session.owner))return;
  this.catalogRows=result.conversations||[];
  const existing=new Map(this.getChats().map(c=>[c.syncCid,c]));const rows=[];const ids=new Set();
  for(const meta of result.conversations){ids.add(meta.cid);let chat=existing.get(meta.cid);if(!chat)chat={key:session.owner+':'+meta.cid,syncCid:meta.cid,owner:session.owner,messages:[],shared:true};chat.title=meta.title;chat.archived=meta.archived===true;chat.device=meta.device||'';chat.updated=Math.max(chat.updated||0,meta.updated_at*1000);chat.unread=meta.unread||0;rows.push(chat);await this.save(chat);}
  const deleted=new Set((result.deleted||[]).map(c=>c.cid));
  for(const chat of this.getChats())if(!ids.has(chat.syncCid)){if(deleted.has(chat.syncCid)){chat.deleted=true;await this.remove(chat.key);}else rows.push(chat);}
  if(!this.valid(session))return;
  const beforeKey=this.current()?.key;this.known=ids;this.setChats(rows.sort((a,b)=>b.updated-a.updated||a.key.localeCompare(b.key)));let messagesChanged=!!beforeKey&&!this.current();
  const current=this.current();if(current?.syncCid&&ids.has(current.syncCid)&&document.visibilityState!=='hidden')messagesChanged=(await this.pull(current,session))||messagesChanged;
  if(this.valid(session))this.onChange({messages:messagesChanged});
 });this.refreshTask=task.finally(()=>{this.refreshTask=null;});return this.refreshTask;}
 /* A permanent hub socket replaces the three-second catalogue poll (S8): another device
    says it pushed, and only then does this tab ask. A quiet relay costs nothing. */
 start(){if(this.started)return;this.started=true;this.connect();this.pollTimer=setInterval(()=>{if(this.socketOpen)return;const session=this.session();if(session?.key&&navigator.onLine&&document.visibilityState!=='hidden')this.refresh().catch(()=>{});},30000);}
 stop(){this.started=false;clearInterval(this.pollTimer);clearTimeout(this.reconnectTimer);clearTimeout(this.wakeTimer);this.pollTimer=this.reconnectTimer=this.wakeTimer=null;try{this.socket?.close();}catch{}this.socket=null;this.socketOpen=false;}
 connect(){
  if(!this.started)return;
  const session=this.session();if(!session?.key||!session.owner){this.scheduleReconnect(15000);return;}
  let socket;try{socket=new WebSocket(new URL('/v1/hub',location.origin).href.replace(/^http/,'ws'));}catch{this.scheduleReconnect(30000);return;}
  this.socket=socket;
  socket.onopen=()=>{if(this.socket!==socket)return;this.socketOpen=true;this.retry=0;socket.send(JSON.stringify({type:'hello',key:session.key,device:{id:this.device,name:'nanoLika Web',kind:'web',actions:[]}}));this.refresh().catch(()=>{});};
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
  for(let round=0;round<50;round++){
   const page=await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/messages?after='+(this.cursors.get(cid)||0),{},session);
   if(!this.valid(session)||(page.account_id&&page.account_id!==session.owner)||this.current()?.key!==chat.key||document.visibilityState==='hidden')return;
   changed=this.merge(chat,page.messages)||changed;
   this.cursors.set(cid,page.cursor);await this.save(chat);
   if(!page.more){await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/read',{method:'POST',body:JSON.stringify({client:this.device,through_seq:page.through_seq})},session);chat.unread=0;await this.save(chat);break;}
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
 async markLocal(chat){delete chat.syncCid;chat.local=true;chat.shared=false;chat.unread=0;for(const m of chat.messages)m.shared=false;await this.save(chat);}
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
  const session=this.session();if(!session?.key||chat.owner!==session.owner)return;
  delete chat.syncCid;chat.local=false;chat.shared=false;for(const m of chat.messages)m.shared=false;
  await this.save(chat);
  await this.upload(chat,session,{force:true});
 });}
 async setArchived(chat,archived){return this.run(async()=>{
  const session=this.session();if(!session?.key||session.owner!==chat.owner)throw new Error('Sign in again');
  if(chat.syncCid)await this.request('/v1/sync/conversations/'+encodeURIComponent(chat.syncCid)+'/archive',{method:'POST',body:JSON.stringify({archived:!!archived})},session);
  chat.archived=!!archived;await this.save(chat);this.onChange({archive:true});return chat;
 });}
 async delete(chat){return this.run(async()=>{const session=this.session();if(chat.syncCid&&session?.owner===chat.owner){if(this.loadMain(session)===chat.syncCid)this.saveMain(session,'');await this.request('/v1/sync/conversations/'+encodeURIComponent(chat.syncCid),{method:'DELETE'},session);}await this.remove(chat.key);this.known.delete(chat.syncCid);});}
}
