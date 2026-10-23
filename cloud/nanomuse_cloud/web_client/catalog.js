/** Read Cloud's public catalogue; implementation IDs never become UI labels. */
export function chatModel(catalogue){
 const facts=row=>row.nanomuse||row;
 const rows=(catalogue.data||[]).filter(row=>{const m=facts(row);return m.listed!==false&&(Array.isArray(m.for)?m.for.includes('chat'):m.kind?m.kind==='chat':true);});
 return (rows.find(row=>facts(row).recommended_for?.includes('chat'))||rows.find(row=>facts(row).recommended)||rows[0])?.id;
}
