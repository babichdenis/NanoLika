/* Shared metadata everywhere; bodies and read marks belong to the selected visible chat. */
export class ChatSync {
 constructor({session,getChats,setChats,current,save,remove,onChange}){Object.assign(this,{session,getChats,setChats,current,save,remove,onChange});this.queue=Promise.resolve();this.selected='';this.cursors=new Map();this.known=new Set();this.account='';let id;try{id=localStorage.getItem('nl.web.chat-client');if(!id){id='web-'+crypto.randomUUID();localStorage.setItem('nl.web.chat-client',id);}}catch{id='web-'+crypto.randomUUID();}this.device=id;}
 run(fn){const task=this.queue.then(fn);this.queue=task.catch(()=>{});return task;}
 async request(path,options,session){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);try{const r=await fetch(path,{...options,signal:controller.signal,cache:'no-store',headers:{Authorization:'Bearer '+session.key,'Content-Type':'application/json'}});const data=r.status===204?null:await r.json();if(!r.ok||data?.ok===false){const e=new Error(data?.error?.message||'Chat sync unavailable');e.status=r.status;e.code=data?.error?.code||data?.reason;throw e;}return data;}finally{clearTimeout(timer);}}
 valid(session){const now=this.session();return now?.key===session?.key&&now?.owner===session?.owner;}
 async publish(chat){return this.run(()=>this.upload(chat,this.session()));}
 async upload(chat,session){
  if(chat.deleted||chat.local||!session?.key||chat.owner!==session.owner||!this.valid(session))return;
  if(!chat.syncCid){chat.syncCid=crypto.randomUUID();chat.device=this.device;}for(const m of chat.messages){if(!m.mid)m.mid=crypto.randomUUID();}await this.save(chat);
  const pending=chat.messages.filter(m=>!m.shared&&m.content&&(m.role==='user'||!m.interrupted));
  for(let offset=0;offset<Math.max(1,pending.length);offset+=200){
   if(!this.valid(session))return;
   const batch=pending.slice(offset,offset+200),body={device:this.device,conversations:[{cid:chat.syncCid,kind:'side',title:chat.title,created_at:Math.floor((chat.created||chat.updated)/1000),updated_at:Math.floor(chat.updated/1000)}],messages:batch.map(m=>({mid:m.mid,cid:chat.syncCid,role:m.role,text:m.content,created_at:Math.floor(m.at/1000),attachments:[]}))};
   const result=await this.request('/v1/sync/changes',{method:'POST',body:JSON.stringify(body)},session);
   if(result.rejected?.length)throw new Error('Some chat messages could not be synced');
   for(const m of batch)m.shared=true;chat.shared=true;await this.save(chat);
  }
 }
 async refresh(){if(this.refreshTask)return this.refreshTask;const task=this.run(async()=>{
  const session=this.session();if(!session?.key||!session.owner||!navigator.onLine)return;
  if(this.account!==session.owner){this.account=session.owner;this.cursors.clear();this.known.clear();this.selected='';}
  for(const chat of [...this.getChats()])if(!chat.local&&(!chat.shared||chat.messages.some(m=>!m.shared&&m.content&&(m.role==='user'||!m.interrupted))))await this.upload(chat,session);
  const result=await this.request('/v1/sync/catalog?'+new URLSearchParams({client:this.device,device:this.device}),{},session);
  if(!this.valid(session)||result.complete!==true||(result.account_id&&result.account_id!==session.owner))return;
  const existing=new Map(this.getChats().map(c=>[c.syncCid,c]));const rows=[];const ids=new Set();
  for(const meta of result.conversations){ids.add(meta.cid);let chat=existing.get(meta.cid);if(!chat)chat={key:session.owner+':'+meta.cid,syncCid:meta.cid,owner:session.owner,messages:[],shared:true};chat.title=meta.title;chat.device=meta.device||'';chat.updated=Math.max(chat.updated||0,meta.updated_at*1000);chat.unread=meta.unread||0;rows.push(chat);await this.save(chat);}
  const deleted=new Set((result.deleted||[]).map(c=>c.cid));
  for(const chat of this.getChats())if(!ids.has(chat.syncCid)){if(deleted.has(chat.syncCid)){chat.deleted=true;await this.remove(chat.key);}else rows.push(chat);}
  if(!this.valid(session))return;
  const beforeKey=this.current()?.key;this.known=ids;this.setChats(rows.sort((a,b)=>b.updated-a.updated||a.key.localeCompare(b.key)));let messagesChanged=!!beforeKey&&!this.current();
  const current=this.current();if(current?.syncCid&&ids.has(current.syncCid)&&document.visibilityState!=='hidden')messagesChanged=(await this.pull(current,session))||messagesChanged;
  if(this.valid(session))this.onChange({messages:messagesChanged});
 });this.refreshTask=task.finally(()=>{this.refreshTask=null;});return this.refreshTask;}
 async open(chat){return this.run(async()=>{const session=this.session();if(!session?.key||chat.owner!==session.owner)return;if(!chat.syncCid&&!chat.local)await this.upload(chat,session);this.selected=chat.syncCid;const changed=await this.pull(chat,session);if(this.valid(session))this.onChange({messages:changed});});}
 async pull(chat,session){
  const cid=chat.syncCid;let changed=false;
  for(let round=0;round<50;round++){
   const page=await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/messages?after='+(this.cursors.get(cid)||0),{},session);
   if(!this.valid(session)||(page.account_id&&page.account_id!==session.owner)||this.current()?.key!==chat.key||document.visibilityState==='hidden')return;
   const byId=new Map(chat.messages.filter(m=>m.mid).map(m=>[m.mid,m]));
   for(const m of page.messages){if(m.deleted){const before=chat.messages.length;chat.messages=chat.messages.filter(x=>x.mid!==m.mid);changed ||=before!==chat.messages.length;continue;}if(!byId.has(m.mid)){const local={mid:m.mid,role:m.role,content:m.text,at:m.created_at*1000,shared:true,truncated:m.truncated};chat.messages.push(local);byId.set(m.mid,local);changed=true;}}
   chat.messages.sort((a,b)=>a.at-b.at);this.cursors.set(cid,page.cursor);await this.save(chat);
   if(!page.more){await this.request('/v1/sync/conversations/'+encodeURIComponent(cid)+'/read',{method:'POST',body:JSON.stringify({client:this.device,through_seq:page.through_seq})},session);chat.unread=0;await this.save(chat);break;}
  }
  return changed;
 }
 /** Take the cloud off a chat made here: its server copy goes, this browser keeps the text and the chat stops travelling. */
 async unsync(chat){return this.run(async()=>{
  const session=this.session(),cid=chat.syncCid;
  if(cid&&session?.key&&session.owner===chat.owner)await this.request('/v1/sync/conversations/'+encodeURIComponent(cid),{method:'DELETE'},session);
  if(cid)this.known.delete(cid);delete chat.syncCid;
  chat.local=true;chat.shared=false;for(const m of chat.messages)m.shared=false;
  await this.save(chat);
 });}
 /** Put the cloud back on a local chat: a fresh conversation id (the old one is a tombstone) and its text goes up again. */
 async sync(chat){return this.run(async()=>{
  const session=this.session();if(!session?.key||chat.owner!==session.owner)return;
  delete chat.syncCid;chat.local=false;chat.shared=false;for(const m of chat.messages)m.shared=false;
  await this.save(chat);
  await this.upload(chat,session);
 });}
 async delete(chat){return this.run(async()=>{const session=this.session();if(chat.syncCid&&session?.owner===chat.owner){await this.request('/v1/sync/conversations/'+encodeURIComponent(chat.syncCid),{method:'DELETE'},session);}await this.remove(chat.key);this.known.delete(chat.syncCid);});}
}
