import {test} from 'node:test'
import assert from 'node:assert/strict'
import {build} from 'esbuild'
const output=await build({entryPoints:['src/client/link-state.ts'],bundle:true,write:false,format:'esm',platform:'node'})
const {linkKey,newLink}=await import('data:text/javascript;base64,'+Buffer.from(output.outputFiles[0].text).toString('base64'))

const telegram={channel:'telegram',identifier:'@denis',label:'tg ***8722',linkedAt:1792925233000}
const max={channel:'max',identifier:'@denis_max',label:'max ***0001',linkedAt:1793283999000}

test('a QR is done only when a chat appears that was not on the account before',()=>{
 const known=[linkKey(telegram)]
 assert.equal(newLink(known,[telegram],'telegram'),undefined,'the account’s own chat is not a new connection')
 assert.equal(newLink(known,[telegram],'max'),undefined,'another messenger’s chat does not stop this QR')
 assert.equal(newLink(known,[telegram,max],'max'),max)
})

test('a second chat of the same messenger still counts as the code’s result',()=>{
 const other={...telegram,identifier:'@second',linkedAt:1793283999000}
 assert.equal(newLink([linkKey(telegram)],[telegram,other],'telegram'),other)
})

test('the same chat after unlinking and linking again is new again',()=>{
 const known=[linkKey(telegram)]
 const again={...telegram,linkedAt:telegram.linkedAt+1000}
 assert.equal(newLink(known,[again],'telegram'),again)
})
