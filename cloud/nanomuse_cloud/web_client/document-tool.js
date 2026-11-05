/* A Word document made on the relay: the model writes the text, this hands back a link. */
export const documentTools=[{type:'function',function:{name:'make_document',description:'Create a Word document (.docx) and give the person a download link. Use it whenever they ask for a document, letter, plan, report, contract, resume, list or table saved as a file. Write the whole text yourself in content: plain text with light markdown (# heading, ## subheading, - bullet, 1. numbers) is enough. This works in the browser; it does not need the computer app.',parameters:{type:'object',properties:{title:{type:'string',description:'The document title, also used as the file name'},content:{type:'string',description:'The full document text, not a summary and not instructions to the user'}},required:['title','content'],additionalProperties:false}}}];
export async function performDocument(calls,{key,valid}){
 if(calls.length!==1||calls[0]?.function?.name!=='make_document'||!calls[0].id)throw new Error('Unsupported document request');
 const args=JSON.parse(calls[0].function.arguments||'{}');
 if(!args||typeof args.title!=='string'||typeof args.content!=='string'||!args.content.trim()||Object.keys(args).some(k=>!['title','content'].includes(k)))throw new Error('Invalid document request');
 if(!valid())throw new Error('The account or conversation changed');
 const response=await fetch('/v1/documents',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify({title:args.title,content:args.content})});
 const data=await response.json().catch(()=>({}));
 if(!response.ok||!data.media_url)throw Object.assign(new Error(data?.error?.message||'document_failed'),{code:data?.error?.code});
 if(!valid())throw new Error('The account or conversation changed');
 const file={name:String(data.name||'document.docx'),mime:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',size:Number(data.size||0),url:String(data.media_url)};
 return {role:'tool',tool_call_id:calls[0].id,content:JSON.stringify({created:true,name:file.name,url:file.url}),file};
}
