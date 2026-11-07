import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../telegram-entry.js',import.meta.url),'utf8');
async function launch({fragment='',next='',sdkData='',status=200,storageBlocked=false,failSdk=false}={}) {
  const elements=Object.fromEntries(['status','retry','login'].map(k=>[k,{hidden:false,textContent:''}]));
  const requests=[], redirects=[], saved=[];
  const context={URLSearchParams,setTimeout,clearTimeout,window:{},document:{getElementById:k=>elements[k],createElement:()=>({}),head:{append(script){if(!failSdk)context.window.Telegram={WebApp:{initData:sdkData,ready(){},expand(){}}};queueMicrotask(()=>failSdk?script.onerror():script.onload());}}},history:{replaceState(){}},location:{hash:fragment,search:next,pathname:'/web/telegram.html',replace:url=>redirects.push(url)},localStorage:{setItem(k,v){if(storageBlocked)throw new Error('blocked');saved.push([k,v]);}},fetch:async(url,body)=>{requests.push([url,body]);return {ok:status===200,status,json:async()=>({api_key:'fixture-session',account:{id:'fixture-account'}})};}};
  vm.runInNewContext(source,context);
  await new Promise(resolve=>setImmediate(resolve));
  await new Promise(resolve=>setImmediate(resolve));
  return {elements,requests,redirects,saved};
}
test('waits for SDK before using its signed launch data',async()=>{
  const r=await launch({sdkData:'signed-sdk'});
  assert.equal(JSON.parse(r.requests[0][1].body).init_data,'signed-sdk');
  assert.deepEqual(r.redirects,['/web/?channel=telegram']);
});
test('fragment data works when SDK fails; server is still responsible for validation',async()=>{
  const r=await launch({fragment:'#tgWebAppData=signed-fragment',failSdk:true});
  assert.equal(JSON.parse(r.requests[0][1].body).init_data,'signed-fragment');
  assert.equal(r.requests[0][1].credentials,'same-origin');
});
test('storage restrictions show recovery instead of redirecting into a signed-out lite client',async()=>{
  const r=await launch({sdkData:'signed',storageBlocked:true});
  assert.equal(r.redirects.length,0); assert.equal(r.elements.retry.hidden,false); assert.match(r.elements.status.textContent,/сохранить/);
});
test('only the account destination is allowed',async()=>{
  assert.deepEqual((await launch({sdkData:'signed',next:'?next=account'})).redirects,['/web/account/?source=telegram']);
  assert.deepEqual((await launch({sdkData:'signed',next:'?next=https://evil.example'})).redirects,['/web/?channel=telegram']);
});
test('unverified ordinary browser launch never sends a Telegram user id',async()=>{
  const r=await launch({failSdk:true});
  assert.equal(r.requests.length,0);assert.equal(r.elements.login.hidden,false);
});
test('expired launch does not redirect or save credentials',async()=>{
  const r=await launch({sdkData:'expired',status:401});
  assert.equal(r.redirects.length,0);assert.equal(r.saved.length,0);
  assert.equal(r.elements.retry.hidden,true);assert.equal(r.elements.login.hidden,false);
});
