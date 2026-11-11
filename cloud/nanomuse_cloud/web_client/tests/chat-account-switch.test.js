import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {ChatSync} from '../chat-sync.js';

test('a late catalogue for account A cannot delete account B chats',async()=>{
 Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
 Object.defineProperty(globalThis,'navigator',{value:{onLine:true},configurable:true});
 globalThis.document={visibilityState:'hidden'};
 const store=new Map();globalThis.localStorage={getItem:k=>store.get(k)||null,setItem:(k,v)=>store.set(k,v)};
 let session={key:'key-a',owner:'a'},chats=[{key:'a:y',owner:'a',syncCid:'y',shared:true,messages:[],updated:1000}];
 let entered,release;const started=new Promise(r=>entered=r),gate=new Promise(r=>release=r),removed=[];
 globalThis.fetch=async()=>new Response(JSON.stringify({complete:true,account_id:'a',conversations:[{cid:'y',title:'a',updated_at:1}],deleted:[{cid:'x'}]}),{status:200,headers:{'Content-Type':'application/json'}});
 const sync=new ChatSync({session:()=>session,getChats:()=>chats,setChats:r=>{chats=r},current:()=>null,save:async chat=>{if(chat.key==='a:y'){entered();await gate;}},remove:async key=>removed.push(key),onChange:()=>{}});
 const task=sync.refresh();await started;session={key:'key-b',owner:'b'};chats=[{key:'b:x',owner:'b',syncCid:'x',shared:true,messages:[],updated:1000}];release();await task;
 assert.deepEqual(removed,[]);assert.equal(chats[0].key,'b:x');
});
