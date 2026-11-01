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
function priceOf(facts, kind){
 const rates=(facts.commerce&&facts.commerce.rates_rub)||{};
 const one=(value,unit)=>Number(value)>0?{value:Number(value),unit}:null;
 if(kind==='image')return one(rates.image,'изображение');
 if(kind==='video')return one(rates.second,'секунда');
 if(kind==='audio')return one(rates.speech,'1000 знаков')||one(rates.minute,'минута');
 return one(rates.input,'М. ток')||one(rates.output,'М. ток');
}
/** Цена выхода отдельно: у текста она своя, и без неё подсказка обещает половину счёта. */
function priceOutOf(facts, kind){
 const rates=(facts.commerce&&facts.commerce.rates_rub)||{};
 const out=Number(rates.output);
 return kind==='chat'&&out>0?{value:out,unit:'М. ток'}:null;
}
export function allModels(catalogue){
 const seen=new Set();return (catalogue.data||[]).filter(row=>{
  const facts=row.nanomuse||row;
  if(!row.id||seen.has(row.id)||facts.listed===false||facts.hidden===true)return false;
  seen.add(row.id);return true;
 }).map(row=>{
  const facts=row.nanomuse||row;
  const kind=(typeof facts.kind==='string'&&facts.kind)||(Array.isArray(facts.for)&&facts.for.includes('chat')?'chat':'chat');
  return {id:row.id,name:row.name||facts.name||row.id,kind,
          vendor:facts.vendor||row.vendor||'',
          context:Number(facts.context_length||row.context_length||0),
          price:priceOf(facts,kind),
          priceOut:priceOutOf(facts,kind),
          locked:Boolean(row.plan_locked||facts.plan_locked||row.locked||facts.locked),
          vision:row.architecture?.input_modalities?.includes?.('image')===true||facts.vision===true||row.input_modalities?.includes?.('image')===true,
          forChat:Array.isArray(facts.for)?facts.for.includes('chat'):kind==='chat'};
 });
}

/** Модели по типу: chat, image, video, audio — так их и подписываем в фильтре. */
export function kindsOf(catalogue){
 const kinds=new Set(allModels(catalogue).map(m=>m.kind));
 return ['chat','image','video','audio'].filter(k=>kinds.has(k));
}
