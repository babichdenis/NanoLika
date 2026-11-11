import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../app.js',import.meta.url),'utf8');
const between=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));
const implementation=[between('async function openCreateChat(', 'async function uploadCreated('),between('async function createVideo(', 'async function createFromPrompt('),between('async function newChat()', 'function renderConversationActions()'),between('async function drawPicture(', "$('composer').onsubmit=")].join('\n');

function fixture(){
 let calls=0;const input={value:'Keep my request',focus(){}};
 const ctx={session:{owner:'a',key:'key-a'},current:null,controller:null,sending:false,chatSync:{channel:'web'},createChoice:{video:{},audio:{}},createCatalogue:{},createModel:()=> 'model',crypto:{randomUUID:()=> 'new'},Date,console,Blob,FormData,
  $:()=>input,t:key=>key,closeSidebar(){},authenticated:()=>true,applyChatModel(){},persist:async()=>false,showSection(){},renderConversation(){},updateStatus(){},fetch:async()=>{calls++;throw Error('unexpected paid request');}};
 vm.createContext(ctx);vm.runInContext(implementation,ctx);
 return {ctx,input,calls:()=>calls};
}

test('failed new chat persistence leaves the composer text intact',async()=>{
 const f=fixture();assert.equal(await f.ctx.newChat(),false);assert.equal(f.input.value,'Keep my request');assert.equal(f.ctx.current,null);
});

for(const name of ['drawPicture','createVideo','createAudio'])test(`${name} does not launch a paid task when local save fails`,async()=>{
 const f=fixture();await f.ctx[name]('Keep my request');assert.equal(f.calls(),0);assert.equal(f.input.value,'Keep my request');
});

for(const name of ['drawPicture','createVideo','createAudio'])test(`${name} preserves text when saving into an existing chat fails`,async()=>{
 const f=fixture();f.ctx.current={owner:'a',key:'a:one',messages:[],title:'existing'};
 await f.ctx[name]('Keep my request');
 assert.equal(f.calls(),0);assert.equal(f.input.value,'Keep my request');assert.equal(f.ctx.current.messages.length,0);
});

for(const existing of [false,true])test(`ordinary send preserves text when ${existing?'message':'new chat'} storage fails`,async()=>{
 const f=fixture();Object.assign(f.ctx,{opening:false,createMode:null,pendingFiles:[],navigator:{onLine:true},notice(){},renderPending(){},looksLikeDrawing:()=>false,epoch:1});
 if(existing)f.ctx.current={owner:'a',key:'a:one',messages:[],title:'existing'};
 vm.runInContext(between("$('composer').onsubmit=", "$('stop').onclick="),f.ctx);
 await f.input.onsubmit({preventDefault(){}});
 assert.equal(f.input.value,'Keep my request');assert.equal(f.calls(),0);
 if(existing)assert.equal(f.ctx.current.messages.length,0);
});
