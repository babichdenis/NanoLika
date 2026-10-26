import {test} from 'node:test';import assert from 'node:assert/strict';
import {enterSends} from '../nanomuse_cloud/web_client/composer-controls.js';
import {chatModels} from '../nanomuse_cloud/web_client/catalog.js';
test('Enter sends while newline, IME, shortcuts and repeated keys stay editing',()=>{
 assert.equal(enterSends({key:'Enter'}),true);
 for(const props of [{shiftKey:true},{isComposing:true},{keyCode:229},{repeat:true},{ctrlKey:true},{altKey:true},{metaKey:true},{key:'a'}])assert.equal(enterSends({key:'Enter',...props}),false)
});
test('the web picker excludes hidden models, media and duplicate rows',()=>{
 const rows=chatModels({data:[{id:'one',name:'One',nanomuse:{kind:'chat'}},{id:'one'},{id:'hidden',nanomuse:{listed:false}},{id:'image',nanomuse:{kind:'image'}},{id:'hands',nanomuse:{for:['gui']}},{id:'two',nanomuse:{for:['chat','gui']}}]});assert.deepEqual(rows.map(row=>row.id),['one','two']);assert.equal(rows[0].name,'One')
});
