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

/**
 * Every model the relay offers, with its kind, for the picker's filters.
 *
 * The chat selector used to show chat models only, so a person looking for the model that
 * draws found nothing at all: image, video and audio models have `kind` of their own and no
 * `for: ["chat"]`. They are listed here too — the picker decides what to show, and a model
 * of another kind says so in one line instead of vanishing.
 */
export function allModels(catalogue){
 const seen=new Set();return (catalogue.data||[]).filter(row=>{
  const facts=row.nanomuse||row;
  if(!row.id||seen.has(row.id)||facts.listed===false||facts.hidden===true)return false;
  seen.add(row.id);return true;
 }).map(row=>{
  const facts=row.nanomuse||row;
  const kind=(typeof facts.kind==='string'&&facts.kind)||(Array.isArray(facts.for)&&facts.for.includes('chat')?'chat':'chat');
  return {id:row.id,name:row.name||facts.name||row.id,kind,
          forChat:Array.isArray(facts.for)?facts.for.includes('chat'):kind==='chat'};
 });
}

/** Модели по типу: chat, image, video, audio — так их и подписываем в фильтре. */
export function kindsOf(catalogue){
 const kinds=new Set(allModels(catalogue).map(m=>m.kind));
 return ['chat','image','video','audio'].filter(k=>kinds.has(k));
}
