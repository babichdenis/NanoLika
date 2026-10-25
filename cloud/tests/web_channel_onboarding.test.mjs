import {test} from 'node:test';
import assert from 'node:assert/strict';
const {isChannelLinked,BOT_LINKS,validateStartLink}=await import(new URL('../nanomuse_cloud/web_client/channel-onboarding.js',import.meta.url));
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

test('start links accept only the selected bot and channel-bound token',()=>{
 const token='nl_TG'+'A'.repeat(30);
 const valid={channel:'telegram',url:BOT_LINKS.telegram+'?start='+token};
 assert.equal(validateStartLink(valid,'telegram'),valid.url);
 assert.throws(()=>validateStartLink({...valid,url:'https://other.example/?start='+token},'telegram'));
 assert.throws(()=>validateStartLink({...valid,channel:'max'},'max'));
 assert.throws(()=>validateStartLink({...valid,url:valid.url+'&next=other'},'telegram'));
});
