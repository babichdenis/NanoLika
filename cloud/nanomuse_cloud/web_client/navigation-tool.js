/* Only this account conversation can be opened; model-supplied ids cannot redirect it. */
export const navigationTools=[{type:'function',function:{name:'conversation_open',description:'Open this same existing shared conversation in Telegram, MAX or the desktop app. This does not create a new chat. Desktop succeeds only after the destination UI confirms visibility. Use this for explicit navigation requests; do not research servers or simulate clicks.',parameters:{type:'object',properties:{channel:{type:'string',enum:['telegram','max','desktop']},device:{type:'string',description:'Optional destination device id if the user specified one'}},required:['channel'],additionalProperties:false}}}];
export async function performNavigation(calls,{cid,open,valid}){
 if(calls.length!==1||calls[0]?.function?.name!=='conversation_open'||!calls[0].id)throw new Error('Unsupported navigation request');
 const args=JSON.parse(calls[0].function.arguments||'{}');
 if(!args||typeof args!=='object'||!['telegram','max','desktop'].includes(args.channel)||Object.keys(args).some(k=>!['channel','device'].includes(k))||(args.device!==undefined&&typeof args.device!=='string'))throw new Error('Invalid navigation request');
 if(!valid())throw new Error('The account or conversation changed');
 const result=await open(cid,args.channel,args.device||'');
 if(!valid())throw new Error('The account or conversation changed');
 return {role:'tool',tool_call_id:calls[0].id,content:JSON.stringify(result)};
}
