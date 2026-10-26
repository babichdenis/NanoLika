import {test} from 'node:test'
import assert from 'node:assert/strict'
import {build} from 'esbuild'
import {Relay} from '../lib/relay.js'
const output=await build({entryPoints:['src/client/channel-qr.ts'],bundle:true,write:false,format:'esm',platform:'node'})
const {connectionQR}=await import('data:text/javascript;base64,'+Buffer.from(output.outputFiles[0].text).toString('base64'))
test('desktop QR accepts only the selected bot and its one-use payload',()=>{
 const link={channel:'telegram',url:'https://t.me/nanolika_bot?start=nl_TG'+'A'.repeat(30),expiresAt:Date.now()+600000}
 const qr=connectionQR(link);assert.ok(qr.path.startsWith('M'));assert.ok(qr.size>30)
 for(const url of [link.url.replace('t.me','other.test'),link.url.replace('nl_TG','nl_MX'),link.url+'&extra=1',link.url+'#fragment'])assert.throws(()=>connectionQR({...link,url}))
})
test('desktop host sends the channel under the signed-in account and normalizes expiry',async()=>{
 const relay=new Relay('https://relay.example.test',async(url,options)=>{
  assert.equal(url,'https://relay.example.test/v1/link/start');assert.equal(options.method,'POST')
  assert.equal(new Headers(options.headers).get('authorization'),'Bearer fixture-key');assert.deepEqual(JSON.parse(options.body),{channel:'max'})
  return new Response(JSON.stringify({channel:'max',url:'https://max.ru/id420517592685_bot?start=nl_MX'+'B'.repeat(30),expires_at:1800000000}),{status:200})
 })
 const start=await relay.linkStart('fixture-key','max');assert.equal(start.expiresAt,1800000000000);assert.equal(start.channel,'max');assert.ok(!start.url.includes('fixture-key'))
})
