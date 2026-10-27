import {modelDetails} from './model-detail.js?v=20261027-model-info';
/**
 * Панель выбора модели: провайдеры слева, карточки справа, фильтры по типу и поиск.
 *
 * Зачем отдельная панель вместо <select>: в списке из четырёхсот строк нельзя ни понять,
 * что модель умеет, ни найти рисующую — типы терялись в общем потоке. Здесь у каждой модели
 * карточка с типом и описанием, слева отсев по производителю, сверху поиск и ряд типов.
 *
 * Данные — тот же `/v1/models`, что и раньше: панель ничего не запрашивает сверх него.
 */

/** Что показать для типа: подпись и значок. */
const KIND_LABELS = {
  chat: { label: 'Текст', glyph: 'T' },
  image: { label: 'Картинка', glyph: '▣' },
  video: { label: 'Видео', glyph: '▶' },
  audio: { label: 'Аудио', glyph: '♪' },
  embedding: { label: 'Векторы', glyph: '#' },
  rerank: { label: 'Ранжирование', glyph: '≋' },
};

const KIND_ORDER = ['chat', 'image', 'video', 'audio', 'embedding', 'rerank'];

/** Строка каталога → карточка: тип, производитель, описание. */
function cardOf(row) {
  const facts = row.nanomuse || row;
  const kind =
    (typeof facts.kind === 'string' && facts.kind) ||
    (Array.isArray(facts.for) && facts.for.includes('chat') ? 'chat' : 'chat');
  return {
    id: row.id,
    name: row.name || facts.name || row.id,
    kind,
    vendor: facts.vendor || '',
    provider: facts.provider || '',
    desc: facts.description || facts.desc || '',
    free: facts.free === true || row.id?.endsWith(':free') === true,
    price: facts.commerce ? (kind==='chat'?((facts.commerce.effective_rub||{}).input||0)+((facts.commerce.effective_rub||{}).output||0)*.512:(facts.commerce.effective_rub||{})[kind==='image'?'image':kind==='video'?'second':facts.commerce.unit==='speech'?'speech':'minute']??null) : null,
    row,
    context: Number(facts.context_length) || 0,
    recommended: facts.recommended === true,
  };
}

/** Все модели каталога, приведённые к карточкам, без скрытых и дублей. */
export function modelCards(catalogue) {
  const seen = new Set();
  const cards = [];
  for (const row of catalogue.data || []) {
    const facts = row.nanomuse || row;
    if (!row.id || seen.has(row.id) || facts.listed === false || facts.hidden === true) continue;
    seen.add(row.id);
    cards.push(cardOf(row));
  }
  return cards;
}

/** Производители для левой колонки: имя и сколько моделей. */
export function vendorRows(cards) {
  const counts = new Map();
  for (const card of cards) {
    const key = card.vendor || 'другие';
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, count]) => ({ name, count }));
}

/** Счётчики по типам — на них живут кнопки фильтра. */
export function kindCounts(cards) {
  const counts = new Map();
  for (const card of cards) counts.set(card.kind, (counts.get(card.kind) || 0) + 1);
  return KIND_ORDER.filter((k) => counts.has(k)).map((k) => ({ kind: k, count: counts.get(k) }));
}

/** Отбор: тип, производитель, строка поиска. Пустой тип или вендор значит «все». */
export function filterCards(cards, { kind = '', vendor = '', query = '', favourites = null } = {}) {
  const q = String(query || '').trim().toLowerCase();
  return cards.filter((card) => {
    if (kind === '__fav') {
      if (!favourites?.has(card.id)) return false;
    } else if (kind && card.kind !== kind) return false;
    if (vendor && (card.vendor || 'другие') !== vendor) return false;
    if (!q) return true;
    return (
      card.name.toLowerCase().includes(q) ||
      card.id.toLowerCase().includes(q) ||
      (card.desc || '').toLowerCase().includes(q)
    );
  });
}

/** Accessible catalogue shared by the chat picker and model settings. */
export function installModelSheet({onPick,api,session}){
 const sheet=document.createElement('div');sheet.className='model-sheet';sheet.dataset.open='0';
 sheet.innerHTML='<div class="model-sheet-panel" role="dialog" aria-modal="true" aria-label="Выбор модели"><aside class="model-vendors" id="ms-vendors"></aside><div class="model-body"><div class="model-searchline"><input id="ms-search" type="search" placeholder="Поиск модели" aria-label="Поиск модели"><button id="ms-close" type="button" class="quiet" aria-label="Закрыть">✕</button></div><div class="model-kindrow" id="ms-kinds"></div><div class="model-filterrow"><label>Контекст <select id="ms-context"><option value="0">Любой</option><option value="128000">128 тыс. и больше</option><option value="200000">200 тыс. и больше</option><option value="1000000">1 млн и больше</option></select></label><label>Порядок <select id="ms-sort"><option value="recommended">Рекомендованные</option><option value="price">По цене</option><option value="context">По контексту</option></select></label><button type="button" id="ms-reload" class="quiet">Обновить цены</button></div><div id="ms-status" role="status"></div><div class="model-cards" id="ms-cards"></div></div></div>';
 document.body.append(sheet);const el=id=>sheet.querySelector('#'+id);
 const state={cards:[],kind:'chat',vendor:'',query:'',picked:'',context:0,sort:'recommended',favourites:new Set()};
 let owner='',key='',version=0,previousFocus=null,queue=Promise.resolve();
 function paintKinds(){const box=el('ms-kinds');box.replaceChildren();const items=[['','Все',state.cards.length],['__fav','★ Избранное',state.favourites.size],...kindCounts(state.cards).map(item=>[item.kind,KIND_LABELS[item.kind]?.label||item.kind,item.count])];for(const [kind,label,count] of items){const b=document.createElement('button');b.type='button';b.className='model-kind-btn';b.setAttribute('aria-pressed',String(kind===state.kind));const title=document.createElement('span');title.textContent=label;const badge=document.createElement('b');badge.textContent=String(count);b.append(title,badge);b.onclick=()=>{state.kind=kind;paint();};box.append(b);}}
 function paintVendors(){const box=el('ms-vendors');box.replaceChildren();for(const row of [{name:'',count:state.cards.length},...vendorRows(state.cards)]){const b=document.createElement('button');b.type='button';b.className='model-vendor-row';b.textContent=(row.name||'Все производители')+' · '+row.count;b.setAttribute('aria-pressed',String(state.vendor===row.name));b.onclick=()=>{state.vendor=row.name;paint();};box.append(b);}}
 function persist(){const identity=session(),ids=[...state.favourites];queue=queue.catch(()=>{}).then(async()=>{if(session()?.key!==identity?.key)return;try{await api('/v1/me/models',{method:'PUT',body:JSON.stringify({enabled:{},favourites:ids})});}catch{el('ms-status').textContent='Избранное не сохранилось. Обновите список и попробуйте ещё раз.';}});}
 function paintCards(){const box=el('ms-cards');box.replaceChildren();let rows=filterCards(state.cards,{...state,favourites:state.favourites}).filter(c=>!state.context||c.context>=state.context);
 rows.sort((a,b)=>state.sort==='context'?b.context-a.context||a.name.localeCompare(b.name):state.sort==='price'&&state.kind&&state.kind!=='__fav'?(a.price??Infinity)-(b.price??Infinity):Number(b.recommended)-Number(a.recommended)||a.name.localeCompare(b.name));
 if(!rows.length){const empty=document.createElement('p');empty.textContent='Ничего не нашлось. Измените поиск или фильтры.';box.append(empty);}
 for(const card of rows){const article=document.createElement('article');article.className='model-card';article.dataset.id=card.id;const pick=document.createElement('button');pick.type='button';pick.className='model-card-pick';pick.setAttribute('aria-pressed',String(card.id===state.picked));const title=document.createElement('strong');title.textContent=card.name;if(card.free){const badge=document.createElement('span');badge.className='model-card-free';badge.textContent='free';title.append(' ',badge);}const desc=document.createElement('span');desc.className='model-card-desc';desc.textContent=card.desc||`${KIND_LABELS[card.kind]?.label||card.kind}${card.context?' · контекст '+card.context.toLocaleString('ru-RU'):''}`;pick.append(title,desc);const rates=card.row.nanomuse?.commerce?.effective_rub||{},price=document.createElement('span');price.className='model-card-price';const rub=n=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(n)+' ₽';price.textContent=card.free?'Бесплатная модель':card.kind==='chat'&&rates.input!=null?`${rub(rates.input)} вход · ${rub(rates.output)} ответ / 1 млн токенов`:rates.image!=null&&card.kind==='image'?rub(rates.image)+' / картинка':rates.second!=null&&card.kind==='video'?rub(rates.second)+' / секунда':'';pick.append(price);pick.onclick=()=>{if(card.kind!=='chat'){details.open=true;return;}state.picked=card.id;onPick?.(card.id);close();};
 const star=document.createElement('button');star.type='button';star.className='model-card-star';star.textContent=state.favourites.has(card.id)?'★':'☆';star.setAttribute('aria-label','В избранное: '+card.name);star.setAttribute('aria-pressed',String(state.favourites.has(card.id)));star.onclick=()=>{state.favourites.has(card.id)?state.favourites.delete(card.id):state.favourites.add(card.id);persist();paintCards();};
 const details=document.createElement('details');details.className='model-card-details';const summary=document.createElement('summary');summary.textContent=card.kind==='chat'?'Цены и расчёт':'Параметры и расчёт';details.append(summary);let loaded=false;details.ontoggle=()=>{if(details.open&&!loaded){loaded=true;details.append(modelDetails(card.row,{api}));}};article.append(pick,star,details);box.append(article);}}
 function paint(){paintKinds();paintVendors();paintCards();}
 async function load(){const identity=session();if(!identity?.key)return;const request=++version,requestedKey=identity.key;if(key!==identity.key){key=identity.key;owner=identity.owner;state.cards=[];state.favourites=new Set();}el('ms-status').textContent='Загружаем модели и цены…';try{const [data,shelf]=await Promise.all([api('/v1/models'),api('/v1/me/models')]);if(request!==version||session()?.key!==requestedKey)return;state.cards=modelCards(data);state.favourites=new Set(shelf.shelf?.favourites||[]);el('ms-status').textContent='';paint();}catch{if(request===version)el('ms-status').textContent='Не удалось загрузить каталог. Нажмите «Обновить цены».';}}
 function open(){previousFocus=document.activeElement;sheet.dataset.open='1';paint();el('ms-search').focus();void load();}
 function close(){version++;sheet.dataset.open='0';previousFocus?.focus();}
 el('ms-search').oninput=e=>{state.query=e.target.value;paintCards();};el('ms-context').onchange=e=>{state.context=Number(e.target.value);paintCards();};el('ms-sort').onchange=e=>{state.sort=e.target.value;paintCards();};el('ms-close').onclick=close;el('ms-reload').onclick=load;
 sheet.onclick=e=>{if(e.target===sheet)close();};sheet.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();close();}if(e.key==='Tab'){const focus=[...sheet.querySelectorAll('button,input,select,summary')].filter(n=>n.getClientRects().length&&!n.disabled),first=focus[0],last=focus.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}};
 return {open,close,setCards(cards){state.cards=cards;paint();},setPicked(id){state.picked=id;paintCards();},refresh:load};
}
