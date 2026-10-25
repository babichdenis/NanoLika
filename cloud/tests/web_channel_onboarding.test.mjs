import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../nanomuse_cloud/web_client/channel-onboarding.js',import.meta.url),'utf8');
const {isChannelLinked,BOT_LINKS}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
test('an empty or unavailable channel list never implies a connection',()=>{
 for(const value of [null,undefined,[],{}])assert.equal(isChannelLinked(value,'telegram'),false);
});
test('Telegram and MAX are checked separately on the current account',()=>{
 const rows=[{channel:'telegram'}];
 assert.equal(isChannelLinked(rows,'telegram'),true);
 assert.equal(isChannelLinked(rows,'max'),false);
});
test('bot links are fixed HTTPS destinations, not account keys',()=>{
 for(const value of Object.values(BOT_LINKS)){const url=new URL(value);assert.equal(url.protocol,'https:');assert.equal(url.search,'');}
});
