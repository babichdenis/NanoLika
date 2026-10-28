import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readReply} from '../nanomuse_cloud/web_client/stream.js';
import {fundsView} from '../nanomuse_cloud/web_client/funds.js';
const error={code:'insufficient_funds',message:'Not enough',required_rub:10.26,balance_rub:5,currency:'RUB',topup_url:'https://relay.example/app'};
test('HTTP priced refusal retains fields for the browser card',async()=>{
 const response=new Response(JSON.stringify({error}),{status:402,headers:{'content-type':'application/json'}});
 let deltas=0;
 await assert.rejects(()=>readReply(response,()=>deltas++),e=>{
  assert.equal(e.status,402);assert.deepEqual(e.details,error);
  const card=fundsView(e,'ru');assert.match(card.message,/10,26/);assert.match(card.message,/5,00/);assert.equal(card.topupUrl,error.topup_url);return true;
 });
 assert.equal(deltas,0);
});
test('a priced refusal inside SSE retains the same fields and never retries',async()=>{
 const response=new Response('data: '+JSON.stringify({error})+'\n\n',{headers:{'content-type':'text/event-stream'}});
 await assert.rejects(()=>readReply(response,()=>assert.fail('not a successful generation')),e=>{assert.deepEqual(e.details,error);assert.match(fundsView(e,'en').message,/10\.26/);return true;});
});
