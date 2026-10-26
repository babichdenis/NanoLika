/** Read Cloud's public catalogue; implementation IDs never become UI labels. */
export function chatModel(catalogue){
 const facts=row=>row.nanomuse||row;
 const rows=(catalogue.data||[]).filter(row=>{const m=facts(row);return m.listed!==false&&(Array.isArray(m.for)?m.for.includes('chat'):m.kind?m.kind==='chat':true);});
 return (rows.find(row=>facts(row).recommended_for?.includes('chat'))||rows.find(row=>facts(row).recommended)||rows[0])?.id;
}

export function chatModels(catalogue){
 const seen=new Set();return (catalogue.data||[]).filter(row=>{
  const facts=row.nanomuse||row;
  if(!row.id||seen.has(row.id)||facts.listed===false||facts.hidden===true||!(Array.isArray(facts.for)?facts.for.includes('chat'):facts.kind?facts.kind==='chat':true))return false;
  seen.add(row.id);return true;
 }).map(row=>({id:row.id,name:row.name||row.nanomuse?.name||row.id}));
}
