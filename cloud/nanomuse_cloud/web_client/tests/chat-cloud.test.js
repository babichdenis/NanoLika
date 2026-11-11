import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../chat-sync.js',import.meta.url),'utf8');
const {ChatSync}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const {locales}=await import('../i18n.js');

function reply(body,status=200,headers={}){return new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json',...headers}});}
function row(mid,seq,text,role='user'){return {mid,cid:'cid-1',seq,role,text,created_at:seq,deleted:false,truncated:false,attachments:[]};}

/* A relay that answers only what a test plans for, and remembers every request it saw. */
function relay(plan){
 const calls=[],removed=[],kept=new Map(),session={key:'key-1',owner:'owner'};let chats=[];
 globalThis.localStorage={getItem:key=>kept.has(key)?kept.get(key):null,setItem:(key,value)=>{kept.set(key,String(value));},removeItem:key=>{kept.delete(key);}};
 globalThis.document={visibilityState:'visible'};
 globalThis.location={origin:'https://relay.test'};
 Object.defineProperty(globalThis,'navigator',{value:{onLine:true},configurable:true,writable:true});
 globalThis.fetch=async (path,options={})=>{
  const method=options.method||'GET';let body=null;try{body=options.body?JSON.parse(options.body):null;}catch{}calls.push({path,method,headers:options.headers||{},body});
  if(method==='DELETE')return new Response(null,{status:204});
  if(path.startsWith('/v1/sync/catalog')){const tag=options.headers?.['If-None-Match'];if(plan.catalogETag&&tag===plan.catalogETag)return new Response(null,{status:304,headers:{ETag:plan.catalogETag}});return reply(plan.catalog,200,plan.catalogETag?{ETag:plan.catalogETag}:{});}
  if(path.includes('/messages'))return reply({account_id:'owner',more:false,has_older:false,cursor:0,through_seq:0,messages:[],...(plan.messages?plan.messages(path):{})});
  if(path==='/v1/sync/changes'){const result=(plan.push&&plan.push(body,calls.filter(c=>c.path==='/v1/sync/changes').length-1))||{ok:true,rejected:[]};return result instanceof Response?result:reply(result);}
  if(path.includes('/read'))return reply({seen:0});
  throw new Error('unexpected request '+method+' '+path);
 };
 const makeSync=()=>new ChatSync({session:()=>session,getChats:()=>chats,setChats:rows=>{chats=rows;},current:plan.current||(()=>null),save:async chat=>{kept.set(chat.key,structuredClone(chat));},remove:async key=>{removed.push(key);},onChange:plan.onChange||(()=>{}),onSyncError:plan.onSyncError,onSyncRecovered:plan.onSyncRecovered});
 const sync=makeSync();
 return {calls,removed,deletes:()=>calls.filter(c=>c.method==='DELETE'),sync,makeSync,setChats:rows=>{chats=rows;},getChats:()=>chats,saved:key=>structuredClone(kept.get(key))};
}
function chat(extra){return {key:'owner:cid-1',owner:'owner',title:'Длинная беседа',messages:[],syncCid:'cid-1',shared:true,updated:1000,...extra};}

test('taking a chat off the cloud refuses when the relay holds text this browser does not get',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Длинная беседа',device:'phone',updated_at:10,unread:0,message_count:3}],deleted:[]},messages:()=>({messages:[row('m1',1,'первое'),row('m2',2,'второе')],has_older:false})});
 const one=chat();
 await assert.rejects(r.sync.unsync(one),error=>error.code==='incomplete_local_text');
 assert.equal(r.deletes().length,0,'the server copy must stay');
 assert.equal(one.syncCid,'cid-1');
 assert.notEqual(one.local,true);
 assert.equal(r.removed.length,0);
});

test('the whole text is downloaded before the server copy goes, and it stays in this browser',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Длинная беседа',device:'phone',updated_at:10,unread:0,message_count:2}],deleted:[]},messages:()=>({messages:[row('m1',1,'первое'),row('m2',2,'второе','assistant')],has_older:false})});
 const one=chat();
 await r.sync.unsync(one);
 assert.equal(r.deletes().length,1);
 assert.equal(one.syncCid,undefined,'no conversation id means the chat no longer travels');
 assert.equal(one.local,true);
 assert.deepEqual(one.messages.map(m=>m.content),['первое','второе']);
 assert.equal(r.removed.length,0,'the local row stays');
});

test('older pages come down too, so a long chat is not cut at the last page',async()=>{
 const pages={};
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Long',device:'phone',updated_at:10,unread:0,message_count:4}],deleted:[]},
  messages:path=>path.includes('before=')?(pages.older=true,{messages:[row('m1',1,'один'),row('m2',2,'два')],has_older:false}):(pages.newest=true,{messages:[row('m3',3,'три'),row('m4',4,'четыре')],has_older:true})});
 const one=chat();
 await r.sync.unsync(one);
 assert.equal(pages.newest,true);
 assert.equal(pages.older,true);
 assert.ok(r.calls.some(c=>c.path.includes('before=3')));
 assert.equal(one.messages.length,4);
 assert.equal(r.deletes().length,1);
});

test('opening a long chat pulls older pages, not only the newest',async()=>{
 const pages={};
 let one=chat();
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Long',device:'phone',updated_at:10,unread:0,message_count:4}],deleted:[]},
  current:()=>one,
  messages:path=>path.includes('before=')?(pages.older=true,{messages:[row('m1',1,'one'),row('m2',2,'two')],has_older:false,cursor:4,through_seq:4}):(pages.newest=true,{messages:[row('m3',3,'three'),row('m4',4,'four')],has_older:true,cursor:4,through_seq:4})});
 await r.sync.open(one);
 assert.equal(pages.newest,true);
 assert.equal(pages.older,true);
 assert.ok(r.calls.some(c=>c.path.includes('before=3')));
 assert.equal(one.messages.length,4);
});

test('a chat that came from another device leaves the cloud and keeps its text here',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Из мессенджера',device:'telegram',updated_at:10,unread:0,message_count:1}],deleted:[]},messages:()=>({messages:[row('m1',1,'привет')],has_older:false})});
 const one=chat({title:'Из мессенджера',device:'telegram'});
 await r.sync.unsync(one);
 assert.equal(r.deletes().length,1);
 assert.equal(r.removed.length,0);
 assert.equal(one.title,'Из мессенджера');
 assert.equal(one.messages[0].content,'привет');
 assert.equal(one.local,true);
});

test('a local chat and a chat without a conversation id never ask the relay',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[]}});
 const local=chat({local:true,shared:false,syncCid:undefined,messages:[{mid:'a',role:'user',content:'привет',at:1,shared:false}]});
 await r.sync.open(local);
 assert.equal(r.calls.length,0);
 const fresh={key:'owner:fresh',owner:'owner',title:'Новая беседа',messages:[],updated:1000};
 await r.sync.open(fresh);
 assert.equal(r.calls.length,0,'no chat_missing banner for a chat that was never in the cloud');
});

test('an empty chat is not published until its first message',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[]}});
 const fresh={key:'owner:fresh',owner:'owner',title:'Новая беседа',messages:[],updated:1000};
 await r.sync.publish(fresh);
 assert.equal(fresh.syncCid,undefined);
 assert.equal(r.calls.filter(c=>c.method==='POST').length,0);
 await r.sync.refresh();
 assert.equal(r.calls.filter(c=>c.method==='POST').length,0,'the three second refresh does not publish it either');
});

test('the first message publishes the chat with its text',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[]}});
 const fresh={key:'owner:fresh',owner:'owner',title:'Привет',messages:[{role:'user',content:'Привет',at:2000}],updated:2000};
 await r.sync.publish(fresh);
 const post=r.calls.find(c=>c.path==='/v1/sync/changes');
 assert.ok(post,'the chat goes up with its first message');
 assert.ok(fresh.syncCid);
 assert.equal(fresh.messages[0].shared,true);
});

test('an explicit move to the cloud publishes even an empty chat, as the messenger buttons need a conversation id',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[]}});
 const fresh={key:'owner:fresh',owner:'owner',title:'Новая беседа',messages:[],updated:1000,local:true};
 await r.sync.sync(fresh);
 assert.ok(fresh.syncCid);
 assert.equal(fresh.local,false);
 assert.equal(r.calls.filter(c=>c.path==='/v1/sync/changes').length,1);
});

test('the new cloud words exist in every language and carry no en or em dash',()=>{
 const keys=['cloudIncomplete','leaveCloudConfirm','cloudOffHint'];
 const dash=/[\u2013\u2014]/;
 for(const [name,dict] of Object.entries(locales)){
  for(const key of keys){assert.equal(typeof dict[key],'string',name+' is missing '+key);assert.ok(dict[key].length>0);assert.ok(!dash.test(dict[key]),name+' '+key+' has a dash');}
 }
});


test('the catalogue carries its tag and a 304 leaves this tab\'s list alone',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'One',device:'phone',updated_at:10,unread:0,message_count:1}],deleted:[]},catalogETag:'"rev-1"'});
 await r.sync.refresh();
 const first=r.calls.filter(c=>c.path.startsWith('/v1/sync/catalog')).at(-1);
 assert.equal(first.headers['If-None-Match'],undefined,'the first ask has no tag yet');
 assert.equal(r.sync.catalogETag,'"rev-1"');
 const chats=[chat()]; // the rows a 304 must not clear
 r.sync.setChats(chats);
 await r.sync.refresh();
 const second=r.calls.filter(c=>c.path.startsWith('/v1/sync/catalog')).at(-1);
 assert.equal(second.headers['If-None-Match'],'"rev-1"');
 assert.deepEqual(r.sync.getChats(),chats);
});

test('a sync frame replaces the three-second poll',async()=>{
 const sockets=[];
 globalThis.WebSocket=class{constructor(url){this.url=url;this.sent=[];this.closed=false;sockets.push(this);}send(value){this.sent.push(JSON.parse(value));}close(){this.closed=true;}};
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[]}});
 try{
  r.sync.start();
  const socket=sockets.at(-1);
  assert.equal(socket.url,'wss://relay.test/v1/hub');
  socket.onopen();
  assert.equal(socket.sent[0].type,'hello');
  assert.equal(socket.sent[0].device.kind,'web');
  await r.sync.refresh(); // the hello's own refresh settles before the frame count
  const before=r.calls.filter(c=>c.path.startsWith('/v1/sync/catalog')).length;
  socket.onmessage({data:JSON.stringify({type:'sync',cursor:3})});
  socket.onmessage({data:JSON.stringify({type:'sync',cursor:4})});
  await new Promise(resolve=>setTimeout(resolve,220));
  const after=r.calls.filter(c=>c.path.startsWith('/v1/sync/catalog')).length;
  assert.equal(after,before+1,'two frames inside the quiet window make one refresh');
  r.sync.stop();
  assert.equal(socket.closed,true);
 } finally {r.sync.stop();delete globalThis.WebSocket;}
});


test('the first chat this browser syncs is the account main chat; later ones are side',async()=>{
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[]}});
 const first=chat({key:'owner:first',syncCid:undefined,shared:false,title:'First',messages:[{mid:'m1',role:'user',content:'hello',at:1000,shared:false}]});
 const second=chat({key:'owner:second',syncCid:undefined,shared:false,title:'Second',messages:[{mid:'m2',role:'user',content:'again',at:2000,shared:false}]});
 await r.sync.publish(first);
 await r.sync.publish(second);
 const pushes=r.calls.filter(c=>c.path==='/v1/sync/changes').map(c=>c.body.conversations[0]);
 assert.equal(pushes[0].kind,'main');
 assert.equal(pushes[1].kind,'side');
 assert.equal(localStorage.getItem('nl.web.main.owner'),first.syncCid);
});

test('a web chat joins the main chat another device already owns',async()=>{
 let pushes=0;
 const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'main-cid',title:'Main',updated_at:1}],deleted:[]},push:(body)=>{
  pushes++;
  if(pushes===1)return {ok:true,accepted:0,rejected:[
   {cid:body.conversations[0].cid,reason:'main_exists',cid_main:'main-cid'},
   {mid:body.messages[0].mid,reason:'main_exists',cid_main:'main-cid'},
  ]};
  return {ok:true,accepted:2,rejected:[]};
 }});
 const one=chat({key:'owner:first',syncCid:undefined,shared:false,title:'Web title',messages:[{mid:'m1',role:'user',content:'hello',at:1000,shared:false}]});
 await r.sync.publish(one);
 assert.equal(one.syncCid,'main-cid');
 assert.equal(localStorage.getItem('nl.web.main.owner'),'main-cid');
 const sent=r.calls.filter(c=>c.path==='/v1/sync/changes').map(c=>c.body);
 assert.equal(sent.length,2);
 assert.equal(sent[1].conversations[0].cid,'main-cid');
 assert.equal(sent[1].conversations[0].kind,'main');
 assert.equal(sent[1].conversations[0].title,'','an adopted main keeps the title it had');
 assert.equal(one.messages[0].shared,true);
});

test('a full history keeps the saved message and retries after a browser restart even with a live socket',async()=>{
 let full=true,recovered=0;const errors=[];
 const catalog={complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Chat',updated_at:10}],deleted:[]};
 const r=relay({catalog,onSyncError:error=>errors.push(error.code),onSyncRecovered:()=>recovered++,push:()=>full?reply({error:{code:'message_limit',message:'full'}},409):{ok:true,rejected:[]}});
 const one=chat({messages:[{mid:'message-one',role:'user',content:'Сохранить текст',at:1000,shared:false}]});r.setChats([one]);
 r.sync.started=true;r.sync.socketOpen=true;
 await assert.rejects(r.sync.publish(one),error=>error.code==='message_limit');
 assert.equal(one.messages[0].shared,false);assert.equal(r.saved(one.key).messages[0].mid,'message-one');
 assert.equal(errors.at(-1),'message_limit');assert.ok(r.sync.uploadTimer,'retry is scheduled even while the socket is open');
 r.sync.stop();const saved=r.saved(one.key);r.setChats([saved]);full=false;
 const reloaded=r.makeSync();await reloaded.refresh();
 const sends=r.calls.filter(call=>call.path==='/v1/sync/changes');
 assert.equal(sends.length,2);assert.equal(sends[1].body.messages[0].mid,'message-one');
 assert.equal(r.saved(one.key).messages[0].shared,true);assert.equal(r.saved(one.key).messages[0].content,'Сохранить текст');
 assert.equal(recovered,0,'a fresh session has no stale warning to clear');
});

test('one blocked conversation does not stop other chats from syncing',async()=>{
 const errors=[];const catalog={complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'One',updated_at:2},{cid:'cid-2',title:'Two',updated_at:2}],deleted:[]};
 const r=relay({catalog,onSyncError:error=>errors.push(error.code),push:body=>body.conversations[0].cid==='cid-1'?reply({error:{code:'message_limit'}},409):{rejected:[]}});
 const a=chat({messages:[{mid:'a',role:'user',content:'waiting',at:1000,shared:false}]}),b=chat({key:'owner:cid-2',syncCid:'cid-2',messages:[{mid:'b',role:'user',content:'other chat',at:1000,shared:false}]});
 r.setChats([a,b]);await r.sync.refresh();
 assert.equal(a.messages[0].shared,false);assert.equal(b.messages[0].shared,true);assert.deepEqual(errors,['message_limit']);
});

test('pending text never reopens an archived chat on retry',async()=>{
 const errors=[];const r=relay({catalog:{complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'Archived',archived:true,updated_at:2}],deleted:[]},onSyncError:error=>errors.push(error.code)});
 const one=chat({messages:[{mid:'a',role:'user',content:'still here',at:1000,shared:false}]});r.setChats([one]);await r.sync.refresh();
 assert.equal(r.calls.filter(call=>call.path==='/v1/sync/changes').length,0);assert.equal(one.archived,true);
 assert.equal(one.messages[0].shared,false);assert.equal(r.saved(one.key).messages[0].content,'still here');assert.deepEqual(errors,['conversation_archived']);
});

test('remote deletion preserves an unsent message locally and never recreates its chat',async()=>{
 const errors=[];const r=relay({catalog:{complete:true,account_id:'owner',conversations:[],deleted:[{cid:'cid-1'}]},onSyncError:error=>errors.push(error.code)});
 const one=chat({messages:[{mid:'a',role:'user',content:'do not lose',at:1000,shared:false}]});r.setChats([one]);await r.sync.refresh();
 assert.equal(r.calls.filter(call=>call.path==='/v1/sync/changes').length,0);assert.equal(r.removed.length,0);
 assert.equal(one.local,true);assert.equal(one.syncCid,undefined);assert.equal(r.saved(one.key).messages[0].content,'do not lose');
 assert.ok(errors.includes('conversation_deleted'));
});

test('a lost acknowledgement is reconciled only against the same saved remote text',async()=>{
 const catalog={complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'One',updated_at:2}],deleted:[]};
 const r=relay({catalog,push:()=>({rejected:[{mid:'a',reason:'known_mid'}]}),messages:()=>({messages:[row('a',1,'same text')],has_older:false})});
 const one=chat({messages:[{mid:'a',role:'user',content:'same text',at:1000,shared:false}]});await r.sync.publish(one);
 assert.equal(one.messages[0].shared,true);
 const mismatch=chat({messages:[{mid:'a',role:'user',content:'different text',at:1000,shared:false}]});
 await assert.rejects(r.sync.publish(mismatch),error=>error.code==='known_mid');assert.equal(mismatch.messages[0].shared,false);
});

test('an existing chat update does not send an archive reset',async()=>{
 const catalog={complete:true,account_id:'owner',conversations:[{cid:'cid-1',title:'One',updated_at:2}],deleted:[]};
 const r=relay({catalog});const one=chat({messages:[{mid:'a',role:'user',content:'one',at:1000,shared:false}]});
 await r.sync.publish(one);const body=r.calls.find(call=>call.path==='/v1/sync/changes').body;
 assert.equal(Object.hasOwn(body.conversations[0],'archived'),false);
});

test('an uncertain first creation is not silently recreated after its server record disappears',async()=>{
 const catalog={complete:true,account_id:'owner',conversations:[],deleted:[]};
 const r=relay({catalog,push:()=>{throw new TypeError('connection lost');}});
 const one={key:'owner:new',owner:'owner',title:'New',messages:[{mid:'a',role:'user',content:'keep',at:1000,shared:false}],updated:1000};r.setChats([one]);
 await assert.rejects(r.sync.publish(one));assert.equal(r.saved(one.key).syncCreateUncertain,true);
 r.setChats([r.saved(one.key)]);const restarted=r.makeSync();await restarted.refresh();
 assert.equal(r.calls.filter(call=>call.path==='/v1/sync/changes').length,1);assert.equal(r.saved(one.key).messages[0].content,'keep');
});

test('an atomic history-limit refusal allows a newly created chat to retry',async()=>{
 let full=true;const catalog={complete:true,account_id:'owner',conversations:[],deleted:[]};
 const r=relay({catalog,push:()=>full?reply({error:{code:'message_limit'}},409):{rejected:[]}});
 const one={key:'owner:new',owner:'owner',title:'New',messages:[{mid:'a',role:'user',content:'keep',at:1000,shared:false}],updated:1000};r.setChats([one]);
 await assert.rejects(r.sync.publish(one),error=>error.code==='message_limit');assert.equal(r.saved(one.key).syncCreateUncertain,false);
 const saved=r.saved(one.key);r.setChats([saved]);full=false;await r.makeSync().refresh();
 assert.equal(r.calls.filter(call=>call.path==='/v1/sync/changes').length,2);assert.equal(r.saved(one.key).messages[0].shared,true);
});
