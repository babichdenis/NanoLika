import {modelDetails} from './model-detail.js?v=20261029-fav';
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

/** Те же крупные провайдеры, что и в кабинете: мелкие уходят в «Другие». */
const OTHER_VENDOR = 'Другие';
const MAJOR_VENDORS = new Set([
  'OpenAI', 'Anthropic', 'Google', 'DeepSeek', 'Qwen', 'Mistral', 'Meta', 'xAI', 'Z.ai',
  'Moonshot', 'ByteDance', 'MiniMax', 'Sber', 'Yandex', 'Tencent', 'NVIDIA',
]);
const VENDOR_ALIASES = {
  openai: 'OpenAI', anthropic: 'Anthropic', google: 'Google', deepseek: 'DeepSeek', qwen: 'Qwen',
  alibaba: 'Qwen', mistral: 'Mistral', meta: 'Meta', xai: 'xAI', zai: 'Z.ai', moonshot: 'Moonshot',
  moonshotai: 'Moonshot', bytedance: 'ByteDance', minimax: 'MiniMax', sber: 'Sber', gigachat: 'Sber',
  yandex: 'Yandex', tencent: 'Tencent', nvidia: 'NVIDIA', amazon: 'Amazon', cohere: 'Cohere',
  perplexity: 'Perplexity', suno: 'Suno', xiaomi: 'Xiaomi', reka: 'Reka', rekaai: 'Reka',
  microsoft: 'Microsoft', ibmgranite: 'IBM', arceeai: 'Arcee AI', prismml: 'Prism ML',
  thinkingmachines: 'Thinking Machines', thedrummer: 'TheDrummer', sao10k: 'Sao10K',
  inclusionai: 'InclusionAI', upstage: 'Upstage', stepfun: 'StepFun', aion: 'Aion',
};
function vendorName(raw) {
  const cleaned = String(raw || '').trim().replace(/^~+/, '');
  if (!cleaned) return '';
  return VENDOR_ALIASES[cleaned.toLowerCase().replace(/[\s._-]+/g, '')] || cleaned;
}
function vendorBucket(raw) {
  const name = vendorName(raw);
  return MAJOR_VENDORS.has(name) ? name : OTHER_VENDOR;
}
const CODE_WORDS = ['codex', 'coder', 'codestral', 'code', 'kimi-k2', 'sonnet', 'opus', 'devstral', 'qwen3-coder'];
function isCoding(card) {
  return CODE_WORDS.some((word) => `${card.name} ${card.id} ${card.vendor}`.toLowerCase().includes(word));
}
const SECTION_LABELS = [
  ['__code', 'Кодинг'], ['chat', 'Переписка'], ['image', 'Картинки'], ['video', 'Видео'],
  ['speech', 'Озвучка'], ['transcribe', 'Распознавание'], ['music', 'Музыка'],
];
function sectionKeyOf(card) {
  return card.kind === 'audio' ? (card.audioKind || 'audio') : card.kind;
}

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
    audioKind: facts.audio_kind || facts.audio?.kind || '',
    locked: facts.plan_locked === true,
  };
}

/** Все модели каталога, приведённые к карточкам, без скрытых и дублей. */
export function modelCards(catalogue) {
  const seen = new Set();
  const cards = [];
  const rows = Array.isArray(catalogue?.models) ? catalogue.models : Array.isArray(catalogue?.data) ? catalogue.data : [];
  for (const row of rows) {
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
    const key = vendorBucket(card.vendor);
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((a, b) => (a[0] === OTHER_VENDOR) - (b[0] === OTHER_VENDOR) || b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, count]) => ({ name, count }));
}

/** Счётчики для ряда фильтров: как в кабинете, с «Выбранными» и «Кодингом». */
export function filterCounts(cards, { favourites = new Set(), selected = new Set() } = {}) {
  const counts = new Map();
  for (const card of cards) {
    const key = sectionKeyOf(card);
    counts.set(key, (counts.get(key) || 0) + 1);
    if (isCoding(card)) counts.set('__code', (counts.get('__code') || 0) + 1);
  }
  const items = [
    ['__fav', '★ Избранное', cards.filter((card) => favourites.has(card.id)).length],
    ['__mine', 'Выбранные', cards.filter((card) => selected.has(card.id)).length],
    ...SECTION_LABELS.map(([key, label]) => [key, label, counts.get(key) || 0]),
  ];
  return items.filter(([, , count]) => count > 0);
}

/** Счётчики по типам — на них живут кнопки фильтра. */
export function kindCounts(cards) {
  const counts = new Map();
  for (const card of cards) counts.set(card.kind, (counts.get(card.kind) || 0) + 1);
  return KIND_ORDER.filter((k) => counts.has(k)).map((k) => ({ kind: k, count: counts.get(k) }));
}

/** Отбор: тип, производитель, строка поиска. Пустой тип или вендор значит «все». */
export function filterCards(cards, { kind = '', vendor = '', query = '', favourites = null, selected = null } = {}) {
  const q = String(query || '').trim().toLowerCase();
  return cards.filter((card) => {
    if (kind === '__fav') {
      if (!favourites?.has(card.id)) return false;
    } else if (kind === '__mine') {
      if (!selected?.has(card.id)) return false;
    } else if (kind === '__code') {
      if (!isCoding(card)) return false;
    } else if (kind === 'speech' || kind === 'transcribe' || kind === 'music') {
      if (card.kind !== 'audio' || card.audioKind !== kind) return false;
    } else if (kind && card.kind !== kind) return false;
    if (vendor && vendorBucket(card.vendor) !== vendor) return false;
    if (!q) return true;
    return (
      card.name.toLowerCase().includes(q) ||
      card.id.toLowerCase().includes(q) ||
      (card.desc || '').toLowerCase().includes(q)
    );
  });
}

/** Accessible catalogue shared by the chat picker and model settings. */
export function installModelSheet({onPick,api,session,onFavourites}){
 const sheet=document.createElement('div');sheet.className='model-sheet';sheet.dataset.open='0';
 sheet.innerHTML='<div class="model-sheet-panel" role="dialog" aria-modal="true" aria-label="Выбор модели"><aside class="model-vendors" id="ms-vendors"></aside><div class="model-body"><div class="model-searchline"><input id="ms-search" type="search" placeholder="Поиск модели" aria-label="Поиск модели"><button id="ms-close" type="button" class="quiet" aria-label="Закрыть">✕</button></div><div class="model-kindrow" id="ms-kinds"></div><div class="model-filterrow"><label>Контекст <select id="ms-context"><option value="0">Любой</option><option value="128000">128 тыс. и больше</option><option value="200000">200 тыс. и больше</option><option value="1000000">1 млн и больше</option></select></label><label>Порядок <select id="ms-sort"><option value="recommended">Рекомендованные</option><option value="price">По цене</option><option value="context">По контексту</option></select></label></div><div id="ms-status" role="status"></div><div class="model-cards" id="ms-cards"></div></div></div>';
 document.body.append(sheet);const el=id=>sheet.querySelector('#'+id);
 const state={cards:[],kind:'',vendor:'',query:'',picked:'',context:0,sort:'recommended',favourites:new Set(),selected:new Set()};
 let owner='',key='',version=0,previousFocus=null,queue=Promise.resolve();
 function paintKinds(){const box=el('ms-kinds');box.replaceChildren();const items=[['','Все модели',state.cards.length],...filterCounts(state.cards,{favourites:state.favourites,selected:state.selected})];if(!items.some(item=>item[0]===state.kind))state.kind='';for(const [kind,label,count] of items){const b=document.createElement('button');b.type='button';b.className='model-kind-btn';b.setAttribute('aria-pressed',String(kind===state.kind));const title=document.createElement('span');title.textContent=label;const badge=document.createElement('b');badge.textContent=String(count);b.append(title,badge);b.onclick=()=>{state.kind=kind;paint();};box.append(b);}}
 function paintVendors(){const box=el('ms-vendors');box.replaceChildren();for(const row of [{name:'',count:state.cards.length},...vendorRows(state.cards)]){const b=document.createElement('button');b.type='button';b.className='model-vendor-row';b.textContent=(row.name||'Все производители')+' · '+row.count;b.setAttribute('aria-pressed',String(state.vendor===row.name));b.onclick=()=>{state.vendor=row.name;paint();};box.append(b);}}
 function persist(){const identity=session(),ids=[...state.favourites];queue=queue.catch(()=>{}).then(async()=>{if(session()?.key!==identity?.key)return;try{await api('/v1/me/models',{method:'PUT',body:JSON.stringify({enabled:{},favourites:ids})});onFavourites?.();}catch{el('ms-status').textContent='Избранное не сохранилось. Обновите список и попробуйте ещё раз.';}});}
 function paintCards(){const box=el('ms-cards');box.replaceChildren();let rows=filterCards(state.cards,{...state,favourites:state.favourites,selected:state.selected}).filter(c=>!state.context||c.context>=state.context);
 rows.sort((a,b)=>state.sort==='context'?b.context-a.context||a.name.localeCompare(b.name):state.sort==='price'&&state.kind&&state.kind!=='__fav'?(a.price??Infinity)-(b.price??Infinity):Number(b.recommended)-Number(a.recommended)||a.name.localeCompare(b.name));
 if(!rows.length){const empty=document.createElement('p');empty.textContent='Ничего не нашлось. Измените поиск или фильтры.';box.append(empty);}
 const table=document.createElement('table');table.className='compact-table model-picker-table';const head=document.createElement('thead'),hr=document.createElement('tr'),body=document.createElement('tbody');for(const label of ['Модель','Тип','Вход / единица','Ответ','Избранное','']){const th=document.createElement('th');th.scope='col';th.textContent=label;hr.append(th);}head.append(hr);table.append(head,body);box.append(table);
 const h=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};const rub=n=>n===null||n===undefined?'—':new Intl.NumberFormat('ru-RU',{maximumFractionDigits:4}).format(n)+' ₽';
 for(const card of rows){const tr=h('tr');tr.dataset.id=card.id;tr.classList.toggle('is-selected',card.id===state.picked);const pick=h('button',undefined,'model-card-pick');pick.type='button';pick.setAttribute('aria-pressed',String(card.id===state.picked));pick.append(h('strong',card.name),h('small',card.id,'table-subtitle'));pick.title=card.desc;const name=h('td');name.append(pick);if(card.locked){const up=h('a','поднять тариф','model-lock');up.href='/web/account/?pay=1';name.append(up);}const facts=card.row.nanomuse||card.row,rates=facts.commerce?.effective_rub||{},unit=card.kind==='image'?'image':card.kind==='video'?'second':facts.commerce?.unit==='speech'?'speech':'minute';const first=card.kind==='chat'?rates.input:rates[unit];const price=h('td',card.free?'0 ₽':rub(first),'numeric'),output=h('td',card.kind==='chat'?(card.free?'0 ₽':rub(rates.output)):'—','numeric');
 price.append(h('small',card.kind==='chat'?'за 1 млн токенов':({image:'за изображение',second:'за секунду',speech:'за 1000 знаков',minute:'за минуту'}[unit]||''),'table-subtitle'));if(card.kind==='chat')output.append(h('small','за 1 млн токенов','table-subtitle'));const detailRow=h('tr',undefined,'model-detail-row'),detailCell=h('td');detailRow.hidden=true;detailCell.colSpan=6;detailRow.append(detailCell);let loaded=false;const expand=()=>{detailRow.hidden=!detailRow.hidden;if(!loaded){loaded=true;detailCell.append(modelDetails(card.row,{api}));}info.setAttribute('aria-expanded',String(!detailRow.hidden));};
 pick.disabled=card.locked;pick.setAttribute('aria-disabled',String(card.locked));pick.onclick=()=>{if(card.locked)return;if(card.kind!=='chat'){expand();return;}state.picked=card.id;onPick?.(card.id);close();};
 const star=h('button',state.favourites.has(card.id)?'★':'☆','model-card-star');star.type='button';star.setAttribute('aria-label','В избранное: '+card.name);star.setAttribute('aria-pressed',String(state.favourites.has(card.id)));star.onclick=()=>{state.favourites.has(card.id)?state.favourites.delete(card.id):state.favourites.add(card.id);persist();paintCards();};
 const info=h('button','Подробнее','model-row-info');info.type='button';info.setAttribute('aria-expanded','false');info.onclick=expand;const favourite=h('td'),detail=h('td');favourite.append(star);detail.append(info);const type=card.kind==='audio'?({speech:'Озвучка',transcribe:'Распознавание',music:'Музыка'}[card.audioKind]||'Аудио'):(KIND_LABELS[card.kind]?.label||card.kind);tr.append(name,h('td',type),price,output,favourite,detail);body.append(tr,detailRow);}}

 function paint(){paintKinds();paintVendors();paintCards();}
 async function load(){const identity=session();if(!identity?.key)return;const request=++version,requestedKey=identity.key;if(key!==identity.key){key=identity.key;owner=identity.owner;state.cards=[];state.favourites=new Set();state.selected=new Set();}el('ms-status').textContent='Загружаем модели и цены…';try{const data=await api('/v1/me/models');if(request!==version||session()?.key!==requestedKey)return;state.cards=modelCards(data);state.favourites=new Set(data.shelf?.favourites||[]);state.selected=new Set(Object.values(data.shelf?.enabled||{}).flat());el('ms-status').textContent='';paint();}catch{if(request===version)el('ms-status').textContent='Не удалось загрузить каталог. Проверьте связь и откройте список ещё раз.';}}
 function open(){previousFocus=document.activeElement;sheet.dataset.open='1';paint();el('ms-search').focus();void load();}
 function close(){version++;sheet.dataset.open='0';previousFocus?.focus();}
 el('ms-search').oninput=e=>{state.query=e.target.value;paintCards();};el('ms-context').onchange=e=>{state.context=Number(e.target.value);paintCards();};el('ms-sort').onchange=e=>{state.sort=e.target.value;paintCards();};el('ms-close').onclick=close;
 sheet.onclick=e=>{if(e.target===sheet)close();};sheet.onkeydown=e=>{if(e.key==='Escape'){e.preventDefault();close();}if(e.key==='Tab'){const focus=[...sheet.querySelectorAll('button,input,select,summary')].filter(n=>n.getClientRects().length&&!n.disabled),first=focus[0],last=focus.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}};
 return {open,close,setCards(cards){state.cards=cards;paint();},setPicked(id){state.picked=id;paintCards();},refresh:load};
}
