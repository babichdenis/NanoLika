import test from 'node:test';
import assert from 'node:assert/strict';
import {chatModel} from '../catalog.js';
test('Cloud metadata determines the recommended chat lane, not list order',()=>{assert.equal(chatModel({data:[{id:'free',nanomuse:{kind:'chat',for:['chat'],recommended:false}},{id:'vision',nanomuse:{kind:'chat',for:['gui'],recommended:true}},{id:'primary',nanomuse:{kind:'chat',for:['chat','gui'],recommended_for:['chat','gui']}},{id:'hidden',nanomuse:{kind:'chat',for:['chat'],listed:false,recommended:true}}]}),'primary');});
test('unsupported or hidden model cannot be selected',()=>{assert.equal(chatModel({data:[{id:'image',nanomuse:{kind:'image',for:['image']}},{id:'hidden',nanomuse:{kind:'chat',listed:false,for:['chat']}}]}),undefined);});
