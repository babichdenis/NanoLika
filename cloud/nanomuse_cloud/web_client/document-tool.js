/* Files made on the relay: Word, Excel and a ZIP archive, returned as download links. */
export const documentTools=[{type:'function',function:{name:'make_document',description:'Create a file and give the person a download link: a Word document (docx), an Excel spreadsheet (xlsx), or a ZIP archive (zip). Use it whenever they ask for a document, letter, plan, report, contract, resume, list, table or an archive of files. Write the whole content yourself. For docx: plain text with light markdown (# heading, ## subheading, - bullet, 1. numbers). For xlsx: rows separated by new lines, cells by a tab (or a JSON array of arrays). For zip: list the files in files, each with name and content. This works in the browser; it does not need the computer app.',parameters:{type:'object',properties:{title:{type:'string',description:'The file title, also used as the file name'},format:{type:'string',enum:['docx','xlsx','zip'],description:'docx for Word, xlsx for Excel, zip for an archive'},content:{type:'string',description:'The full text (docx) or the rows (xlsx); leave empty for zip'},files:{type:'array',description:'For zip only: the files inside the archive',items:{type:'object',properties:{name:{type:'string'},content:{type:'string'}},required:['name','content'],additionalProperties:false}}},required:['title','format'],additionalProperties:false}}}];
export async function performDocument(calls,{key,valid}){
 if(calls.length!==1||calls[0]?.function?.name!=='make_document'||!calls[0].id)throw new Error('Unsupported document request');
 const args=JSON.parse(calls[0].function.arguments||'{}');
 const format=String(args?.format||'').toLowerCase();
 if(!args||typeof args.title!=='string'||!['docx','xlsx','zip'].includes(format)||Object.keys(args).some(k=>!['title','format','content','files'].includes(k)))throw new Error('Invalid document request');
 if(format!=='zip'&&(typeof args.content!=='string'||!args.content.trim()))throw new Error('Invalid document request');
 if(format==='zip'&&(!Array.isArray(args.files)||!args.files.length))throw new Error('Invalid document request');
 if(!valid())throw new Error('The account or conversation changed');
 const response=await fetch('/v1/documents',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify({title:args.title,format,content:typeof args.content==='string'?args.content:'',files:Array.isArray(args.files)?args.files:[]})});
 const data=await response.json().catch(()=>({}));
 if(!response.ok||!data.media_url)throw Object.assign(new Error(data?.error?.message||'document_failed'),{code:data?.error?.code});
 if(!valid())throw new Error('The account or conversation changed');
 const file={name:String(data.name||'document'),mime:format==='xlsx'?'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':format==='zip'?'application/zip':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',size:Number(data.size||0),url:String(data.media_url)};
 return {role:'tool',tool_call_id:calls[0].id,content:JSON.stringify({created:true,name:file.name,url:file.url}),file};
}
