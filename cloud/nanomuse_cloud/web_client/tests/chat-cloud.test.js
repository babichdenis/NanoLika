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
  const method=options.method||'GET';calls.push({path,method,headers:options.headers||{}});
  if(method==='DELETE')return new Response(null,{status:204});
  if(path.startsWith('/v1/sync/catalog')){const tag=options.headers?.['If-None-Match'];if(plan.catalogETag&&tag===plan.catalogETag)return new Response(null,{status:304,headers:{ETag:plan.catalogETag}});return reply(plan.catalog,200,plan.catalogETag?{ETag:plan.catalogETag}:{});}
  if(path.includes('/messages'))return reply({account_id:'owner',more:false,has_older:false,cursor:0,through_seq:0,messages:[],...(plan.messages?plan.messages(path):{})});
  if(path==='/v1/sync/changes')return reply({ok:true,rejected:[]});
  if(path.includes('/read'))return reply({seen:0});
  throw new Error('unexpected request '+method+' '+path);
 };
 const sync=new ChatSync({session:()=>session,getChats:()=>chats,setChats:rows=>{chats=rows;},current:plan.current||(()=>null),save:async chat=>{kept.set(chat.key,chat);},remove:async key=>{removed.push(key);},onChange:plan.onChange||(()=>{})});
 return {calls,removed,deletes:()=>calls.filter(c=>c.method==='DELETE'),sync};
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
