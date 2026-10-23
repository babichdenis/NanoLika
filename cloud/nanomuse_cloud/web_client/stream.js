export async function readReply(response,onDelta){
 if(!response.ok){let data;try{data=await response.json();}catch{}const e=new Error(data?.error?.message||'request_failed');e.code=data?.error?.code;e.status=response.status;throw e;}
 if(!response.body)throw new Error('empty_response');
 const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',answer='',finished=false;
 const consume=event=>{const lines=event.split('\n').filter(l=>l.startsWith('data:')).map(l=>l.slice(5).trimStart());if(!lines.length)return;const raw=lines.join('\n').trim();if(raw==='[DONE]'){finished=true;return;}if(!raw)return;const data=JSON.parse(raw);if(data.error){const e=new Error(data.error.message||'request_failed');e.code=data.error.code;throw e;}const part=data.choices?.[0]?.delta?.content;if(typeof part==='string'){answer+=part;onDelta(answer);}if(data.choices?.[0]?.finish_reason)finished=true;};
 try{while(true){const {done,value}=await reader.read();buffer+=(done?decoder.decode():decoder.decode(value,{stream:true}));buffer=buffer.replace(/\r\n/g,'\n');let split;while((split=buffer.indexOf('\n\n'))>=0){consume(buffer.slice(0,split));buffer=buffer.slice(split+2);}if(done){if(buffer.trim())consume(buffer);break;}}if(!finished)throw new Error('incomplete_response');return answer;}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
}
