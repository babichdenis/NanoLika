/* nanoLika web console — the models page.

   A chooser, not a table: one search field, one row of kinds, and every model of the relay
   in a row of its own, with the maker, the price of one use and a quiet "more" that opens
   the model's own name and limits. A model is a thing a person picks, so each row carries
   its tick and its "default" mark in place, and nothing is hidden because it is dear. */
(() => {
  'use strict';

  const words = {
    ru: {
      title: 'Модели', eyebrow: 'nanoLika · Ваш кабинет',
      intro: 'Выберите, какие модели будут в приложении, и какая из них отвечает по умолчанию. Цены со скидкой вашего тарифа и возможности видны у каждой модели.',
      search: 'Быстрый поиск моделей', all: 'Все модели', mine: 'Выбранные', favouritesShort: '★ Избранное',
      sort: 'Сортировка', sortRecommended: 'По рекомендации', sortPrice: 'Сначала дешевле', sortContext: 'Сначала большой контекст', sortName: 'По названию',
      allVendors: 'Все производители', otherVendors: 'Другие', vendorFilter: 'Производитель',
      planNote: (n) => `На старших тарифах доступно ещё ${n}.`,
      planTitle: 'Тариф и пополнение', planPill: 'Лика+', planHeader: 'Тариф', planIntro: 'Пополнение остаётся на балансе. Когда оплаченный остаток достигает порога тарифа, тариф включается на 30 дней.',
      planAmount: 'Своя сумма, ₽', planHint: 'Можно пополнить на любую сумму; тариф выберете на странице оплаты.', planGo: 'Перейти к тарифам и пополнению',
      planLoading: 'Загружаем тарифы…', planUnavailable: 'Тарифы сейчас недоступны. Откройте кабинет, чтобы пополнить баланс.',
      discount: 'Скидка', days: 'дней', close: 'Закрыть',
      chat: 'Переписка', code: 'Кодинг', image: 'Картинки', video: 'Видео', sound: 'Звук', music: 'Музыка',
      showMore: (n, total) => `Показать ещё ${n} из ${total}`, showAll: 'Показать все',
      speech: 'Озвучка', transcribe: 'Распознавание', capability: 'Умеет', drawImage: 'рисует', editImage: 'правит картинки', drawAndEdit: 'рисует и правит', textToVideo: 'текст → видео', firstFrame: 'первый кадр', textAndFrame: 'текст и кадр',
      loading: 'Загружаем список моделей…', error: 'Не удалось получить список моделей. Попробуйте ещё раз.',
      login: 'Войдите в nanoLika, чтобы увидеть свои модели.', expired: 'Вход больше не действует. Войдите заново.',
      vendor: (v) => `Производитель ${v}`, more: 'Подробнее', less: 'Скрыть',
      perMillionIn: 'Запрос / 1М', perMillionOut: 'Ответ / 1М',
      perPicture: '1 картинка', perSecond: '1 секунда', perClip: (s) => `клип ${s} с`,
      perSpeech: '1000 знаков', perMinute: '1 минута записи', perTrack: '1 песня',
      tokensNote: (n) => `${n} токенов`,
      defaultMark: 'по умолчанию', makeDefault: 'Сделать основной',
      manual: 'включается вручную', soon: 'ещё не подключена',
      save: 'Сохранить', saving: 'Сохраняем…', saved: 'Сохранено',
      reset: 'Вернуть как было', dirty: 'Есть несохранённые изменения', unchanged: 'Ничего не изменено',
      chatCannotEmpty: 'Переписка не может остаться пустой: хотя бы одна модель должна быть отмечена.',
      nothing: 'Ничего не нашлось.', found: (n) => `моделей: ${n}`,
      footer: 'Цены продажи поступают из прайса nanoLika и загружаются при открытии страницы. Действующая скидка учтена.',
      back: '← В кабинет', voice: (v) => `голос: ${v}`, id: 'у провайдера', takes: (l) => `принимает: ${l}`,
      mod: { text: 'текст', image: 'картинки', audio: 'звук', video: 'видео' },
    },
    en: {
      title: 'Models', eyebrow: 'nanoLika · Your account',
      intro: 'Pick what the app should offer and which model answers by default. Your discounted retail prices and model capabilities are shown on every row.',
      search: 'Search models', all: 'All models', mine: 'My picks', favouritesShort: '★ Favourites',
      sort: 'Sort', sortRecommended: 'Recommended', sortPrice: 'Lowest price', sortContext: 'Largest context', sortName: 'Name',
      allVendors: 'All makers', otherVendors: 'Other', vendorFilter: 'Maker',
      planNote: (n) => `${n} models open on higher plans.`,
      planTitle: 'Plan and top-up', planPill: 'Lika+', planHeader: 'Plan', planIntro: 'Top-ups stay on your balance. When the paid balance reaches a plan threshold, the plan starts for 30 days.',
      planAmount: 'Custom amount, ₽', planHint: 'Top up any amount; choose the plan on the payment page.', planGo: 'Open plans and top-up',
      planLoading: 'Loading plans…', planUnavailable: 'Plans are unavailable right now. Open your account to top up.',
      discount: 'Discount', days: 'days', close: 'Close',
      chat: 'Chat', code: 'Coding', image: 'Pictures', video: 'Clips', sound: 'Sound', music: 'Music',
      showMore: (n, total) => `Show ${n} more of ${total}`, showAll: 'Show all',
      speech: 'Speech', transcribe: 'Transcription', capability: 'Does', drawImage: 'draws', editImage: 'edits pictures', drawAndEdit: 'draws and edits', textToVideo: 'text to video', firstFrame: 'first frame', textAndFrame: 'text and frame',
      loading: 'Loading the model list…', error: 'Could not load the model list. Please try again.',
      login: 'Sign in to nanoLika to see your models.', expired: 'Your sign-in expired. Please sign in again.',
      vendor: (v) => `Maker ${v}`, more: 'More', less: 'Hide',
      perMillionIn: 'Request / 1M', perMillionOut: 'Reply / 1M',
      perPicture: '1 picture', perSecond: '1 second', perClip: (s) => `a ${s}s clip`,
      perSpeech: '1000 characters', perMinute: '1 minute of recording', perTrack: '1 song',
      tokensNote: (n) => `${n} tokens`,
      defaultMark: 'default', makeDefault: 'Make this the default',
      manual: 'switched on by hand', soon: 'not connected yet',
      save: 'Save', saving: 'Saving…', saved: 'Saved',
      reset: 'Undo changes', dirty: 'Unsaved changes', unchanged: 'Nothing changed',
      chatCannotEmpty: 'Chat cannot be left empty: at least one model stays ticked.',
      nothing: 'Nothing found.', found: (n) => `${n} models`,
      footer: 'Retail prices come from nanoLika and are loaded when the page opens. Your active discount is included.',
      back: '← Account', voice: (v) => `voice: ${v}`, id: 'upstream', takes: (l) => `takes: ${l}`,
      mod: { text: 'text', image: 'pictures', audio: 'sound', video: 'video' },
    },
    zh: {
      title: '模型', eyebrow: 'nanoLika · 账户',
      intro: '选择应用里要提供哪些模型，以及哪一个默认回答。每行显示套餐折扣价和模型能力。',
      search: '快速搜索模型', all: '全部模型', mine: '已选', favouritesShort: '★ 收藏',
      sort: '排序', sortRecommended: '推荐', sortPrice: '价格从低到高', sortContext: '上下文从大到小', sortName: '名称',
      allVendors: '全部制造商', otherVendors: '其他', vendorFilter: '制造商',
      planNote: (n) => `${n} 个模型需要更高套餐。`,
      planTitle: '套餐与充值', planPill: 'Lika+', planHeader: '套餐', planIntro: '充值金额留在余额中；付费余额达到套餐门槛后，套餐开通 30 天。',
      planAmount: '自定义金额，₽', planHint: '可以充值任意金额；在支付页面选择套餐。', planGo: '前往套餐与充值',
      planLoading: '正在读取套餐…', planUnavailable: '暂时无法读取套餐，请到账户页面充值。',
      discount: '折扣', days: '天', close: '关闭',
      chat: '对话', code: '编程', image: '图片', video: '视频', sound: '语音', music: '音乐',
      showMore: (n, total) => `再显示 ${n} 个，共 ${total} 个`, showAll: '显示全部',
      speech: '语音合成', transcribe: '语音识别', capability: '能力', drawImage: '生成图片', editImage: '编辑图片', drawAndEdit: '生成与编辑', textToVideo: '文生视频', firstFrame: '首帧', textAndFrame: '文本与首帧',
      loading: '正在读取模型列表…', error: '无法读取模型列表，请重试。',
      login: '请登录 nanoLika 查看模型。', expired: '登录已失效，请重新登录。',
      vendor: (v) => `制造商 ${v}`, more: '详情', less: '收起',
      perMillionIn: '输入 / 百万', perMillionOut: '输出 / 百万',
      perPicture: '1 张图片', perSecond: '1 秒', perClip: (s) => `${s} 秒视频`,
      perSpeech: '1000 字', perMinute: '1 分钟录音', perTrack: '1 首',
      tokensNote: (n) => `${n} 令牌`,
      defaultMark: '默认', makeDefault: '设为默认',
      manual: '需手动开启', soon: '尚未接入',
      save: '保存', saving: '正在保存…', saved: '已保存',
      reset: '撤销修改', dirty: '有未保存的修改', unchanged: '没有修改',
      chatCannotEmpty: '对话不能为空，至少保留一个模型。',
      nothing: '没有找到。', found: (n) => `共 ${n} 个模型`,
      footer: '价格来自 nanoLika 价目表，页面打开时载入；已计入当前套餐折扣。',
      back: '← 账户', voice: (v) => `音色：${v}`, id: '上游名称', takes: (l) => `可接收：${l}`,
      mod: { text: '文字', image: '图片', audio: '语音', video: '视频' },
    },
  };

  const q = new URLSearchParams(location.search);
  const web = q.get('source') === 'web';
  let storedLang = 'ru';
  try {
    storedLang = localStorage.getItem('nm.lang') || 'ru';
  } catch (_) {
    /* storage may be unavailable */
  }
  const lang = q.get('lang') || storedLang;
  const T = words[lang] || words.ru;
  document.documentElement.lang = words[lang] ? lang : 'ru';
  document.title = `${T.title} · nanoLika`;

  const el = (id) => document.getElementById(id);
  const h = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  };
  const num = (n, digits = 0) =>
    new Intl.NumberFormat(document.documentElement.lang, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(n || 0);

  // каталог хранит себестоимость в юанях; 12 ₽ за юань — курс, по которому считались замеры
  const RUB_PER_YUAN = 12;

  el('title').textContent = T.title;
  el('eyebrow').textContent = T.eyebrow;
  el('intro').textContent = T.intro;
  el('footer').textContent = T.footer;
  el('back').textContent = T.back;
  el('back').href = web ? '/web/account/' : '../';
  el('save').textContent = T.save;
  el('reset').textContent = T.reset;
  el('search').placeholder = T.search;

  /** Разделы страницы: переписка, картинки, видео, звук (речь и слух отдельно), музыка. */
  const SECTIONS = [
    { key: 'chat', kind: 'chat', title: 'chat' },
    { key: 'image', kind: 'image', title: 'image' },
    { key: 'video', kind: 'video', title: 'video' },
    { key: 'speech', kind: 'audio', sub: 'speech', title: 'speech' },
    { key: 'transcribe', kind: 'audio', sub: 'transcribe', title: 'transcribe' },
    { key: 'music', kind: 'audio', sub: 'music', title: 'music' },
  ];
  // модель, которую берут для кода: имя говорит об этом прямо
  const CODE_WORDS = ['codex', 'coder', 'codestral', 'code', 'kimi-k2', 'sonnet', 'opus', 'devstral', 'qwen3-coder'];
  const isCoding = (model) => CODE_WORDS.some((w) => `${model.name} ${model.upstream || ''} ${model.id}`.toLowerCase().includes(w));

  const OTHER_BUCKET = '__other__';
  // Крупные провайдеры остаются отдельными пунктами фильтра; остальные собираются в «Другие».
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
    const key = cleaned.toLowerCase().replace(/[\s._-]+/g, '');
    return VENDOR_ALIASES[key] || cleaned;
  }

  function vendorBucket(raw) {
    const name = vendorName(raw);
    return MAJOR_VENDORS.has(name) ? name : OTHER_BUCKET;
  }

  function upgradeHref(plan, amount) {
    const base = web ? '/web/account/' : '../';
    const params = new URLSearchParams({ pay: '1' });
    if (plan) params.set('plan', plan);
    if (amount) params.set('amount', amount);
    return `${base}?${params.toString()}`;
  }

  /** Полка ведётся по видам, поэтому у звука и музыки один вид: «ключ полки». */
  const SHELF_KINDS = ['chat', 'image', 'video', 'audio'];
  const SECTION_KEYS = new Set(['all', 'favourites', 'mine', 'code', ...SECTIONS.map((spec) => spec.key)]);
  const readPref = (key, fallback) => {
    try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; }
  };
  const writePref = (key, value) => {
    try { localStorage.setItem(key, value); } catch (_) { /* storage may be unavailable */ }
  };

  let loaded = null; // что сказал сервер в прошлый раз
  let draft = null; // что человек выбрал сейчас
  let contextMin=0;let favouriteWrites=Promise.resolve();
  let section = readPref('nm.models.section', 'all'); // какой раздел открыт
  if (!SECTION_KEYS.has(section)) section = 'all';
  let needle = ''; // что в поиске
  let vendorFilter = readPref('nm.models.vendor', ''); // один провайдер или все
  let sortMode = readPref('nm.models.sort', 'recommended'); // как расставлены строки
  let planChoice = ''; // выбранный тариф в черновике окна
  if (!['recommended', 'price', 'context', 'name'].includes(sortMode)) sortMode = 'recommended';
  let shownCount = {}; // сколько строк раздела уже показано
  let collapsedKinds = new Set(); // разделы можно свернуть, не меняя выбранные модели
  const PAGE = 40; // столько строк ждёт человека, остальное по кнопке: 384 модели разом браузер не любит
  let busy = false;

  function token() {
    try {
      if (web) return JSON.parse(localStorage.getItem('nl.web.session.v1') || 'null')?.key || '';
      return localStorage.getItem('nm.key') || '';
    } catch (_) {
      return '';
    }
  }

  function rub(cny) {
    if (!cny) return '';
    return '₽' + num(Number(cny) * RUB_PER_YUAN, 2);
  }

  /** Одна сопоставимая цена для сортировки внутри раздела: текст — вход плюс выход. */
  function priceValue(model) {
    const rates = (model.commerce || {}).effective_rub || {};
    const value = (key) => rates[key] === undefined || rates[key] === null ? Infinity : Number(rates[key]);
    if (model.kind === 'chat') return (Number(rates.input) || 0) + (Number(rates.output) || 0) * 0.512;
    if (model.kind === 'image') return value('image');
    if (model.kind === 'video') return value('second');
    if ((model.audio || {}).kind === 'speech') return value('speech');
    return value('minute');
  }

  /** Что покупают за токены и сколько это стоит у провайдера — словами, без выдуманных цен. */
  function priceBlock(model) {
    const commerce=model.commerce||{},rates=commerce.effective_rub||{};
    const labels={input:T.perMillionIn,output:T.perMillionOut,image:T.perPicture,second:T.perSecond,speech:T.perSpeech,minute:T.perMinute};
    const fields=model.kind==='chat'?['input','output']:model.kind==='image'?['image']:model.kind==='video'?['second']:commerce.unit==='speech'?['speech']:['minute'];
    return {lines:fields.filter(k=>rates[k]!==null&&rates[k]!==undefined&&Number.isFinite(Number(rates[k]))&&Number(rates[k])>=0).map(k=>[labels[k],new Intl.NumberFormat(document.documentElement.lang,{maximumFractionDigits:4}).format(rates[k])+' ₽']),note:commerce.discount_pct?'−'+commerce.discount_pct+'%':''};
  }

  function row(model, spec = { kind: model.kind }) {
    const kind = model.kind;
    const on = !model.plan_locked && draft.enabled[kind].has(model.id);
    const usable = model.available !== false && !model.plan_locked;
    const line = h('tr', undefined, `mm-row${usable ? '' : ' mm-row-off'}${on ? ' mm-row-on' : ''}`);

    const box = h('input');
    box.type = 'checkbox';
    box.className = 'mm-box';
    box.checked = on;
    box.disabled = !usable;
    box.id = `pick-${model.id}`;
    box.setAttribute('aria-label', model.name);
    box.onchange = () => {
      if (box.checked) draft.enabled[kind].add(model.id);
      else draft.enabled[kind].delete(model.id);
      if (!draft.enabled[kind].has(draft.default[kind] || '')) {
        const first = [...draft.enabled[kind]][0];
        if (first) draft.default[kind] = first;
      }
      draw();
    };

    const names = h('div', undefined, 'mm-names');
    const title = h('div', undefined, 'mm-name');
    const name = h('label', model.name, 'mm-model-name');
    name.htmlFor = box.id;
    title.append(name);
    if (model.vendor) title.append(h('span', ' · ' + vendorName(model.vendor), 'mm-vendor-inline'));
    if (model.default_on === false && usable) title.append(h('em', T.manual, 'tag'));
    if (!usable && !model.plan_locked) title.append(h('em', T.soon, 'tag tag-wait'));
    names.append(title);

    const prices = h('div', undefined, 'mm-prices');
    const block = priceBlock(model);
    for (const [label, value] of block.lines) {
      if (!value) continue;
      const one = h('div', undefined, 'mm-price');
      one.append(h('span', label, 'mm-price-label'), h('strong', value));
      prices.append(one);
    }
    if (block.note) prices.append(h('div', block.note, 'mm-price-note'));

    // «по умолчанию» — только у разделов, где такой выбор вообще есть
    const radio = h('input');
    radio.type = 'radio';
    radio.name = `def-${kind}`;
    radio.checked = draft.default[kind] === model.id;
    radio.disabled = !on || !usable;
    radio.onchange = () => {
      draft.default[kind] = model.id;
      draw();
    };
    const pick = h('label', undefined, `mm-def${radio.checked && usable ? ' mm-def-on' : ''}`);
    pick.title = T.makeDefault;
    pick.append(radio, h('span', radio.checked ? T.defaultMark : T.makeDefault));

    const star=h('button',(loaded.shelf?.favourites||[]).includes(model.id)?'★':'☆','chip');star.type='button';star.setAttribute('aria-label','В избранное: '+model.name);star.setAttribute('aria-pressed',String((loaded.shelf?.favourites||[]).includes(model.id)));star.onclick=()=>{const old=loaded.shelf?.favourites||[],next=old.includes(model.id)?old.filter(id=>id!==model.id):[...old,model.id];loaded.shelf.favourites=next;draw();favouriteWrites=favouriteWrites.catch(()=>{}).then(async()=>{const response=await fetch('/v1/me/models',{method:'PUT',headers:{Authorization:'Bearer '+token(),'Content-Type':'application/json'},body:JSON.stringify({enabled:{},favourites:next})});if(!response.ok)throw new Error('favourite');}).catch(()=>{el('status').textContent='Избранное не сохранилось. Обновите страницу.';});};
    const td=(...nodes)=>{const n=h('td');n.append(...nodes);return n;};
    const priceCell=(item,note=false)=>{const n=h('td',undefined,'numeric');n.append(h('strong',item?item[1]:'—'));if(item)n.append(h('small',item[0],'table-subtitle'));if(note&&block.note)n.append(h('small',block.note,'table-subtitle'));return n;};
    const audioKind=(model.audio||{}).kind;
    const mods=model.in||[], hasText=mods.includes('text'), hasImage=mods.includes('image');
    const capability=spec.kind==='image'
      ? (hasText&&hasImage?T.drawAndEdit:hasImage?T.editImage:T.drawImage)
      : spec.kind==='video'
        ? (hasText&&hasImage?T.textAndFrame:hasImage?T.firstFrame:T.textToVideo)
        : spec.kind==='audio'
          ? (audioKind==='speech'?T.speech:audioKind==='transcribe'?T.transcribe:audioKind==='music'?T.music:T.sound)
          : '';
    const priceCells=spec.kind==='chat'
      ? [priceCell(block.lines[0],true),priceCell(block.lines[1])]
      : [priceCell(block.lines[0],true),h('td',capability,'mm-capability')];
    const enabled=h('label',undefined,'table-toggle');enabled.append(box);box.setAttribute('role','switch');
    const plan=td();plan.className='mm-plan-cell';
    if (model.plan_locked) {
      const pill=h('a',T.planPill,'plan-pill');
      pill.href=upgradeHref();
      pill.onclick=(event)=>{event.preventDefault();openPlanDialog();};
      plan.append(pill);
    }
    line.append(td(names),...priceCells,td(enabled),td(...(usable?[pick]:[])),td(star),plan);
    return line;
  }

  function visible() {
    const all = loaded.models || [];
    const wanted = needle.trim().toLowerCase();
    const spec = SECTIONS.find((s) => s.key === section);
    return all.filter((model) => {
      const hay = `${model.name} ${model.vendor || ''} ${model.id} ${model.upstream || ''}`.toLowerCase();
      if (wanted && !hay.includes(wanted)) return false;
      if (vendorFilter === OTHER_BUCKET) { if (MAJOR_VENDORS.has(vendorName(model.vendor))) return false; }
      else if (vendorFilter && vendorBucket(model.vendor) !== vendorFilter) return false;
      if(contextMin && Number(model.context_length||0)<contextMin)return false;
      if(section==='favourites')return (loaded.shelf?.favourites||[]).includes(model.id);
      if (section === 'mine') return !model.plan_locked && SHELF_KINDS.some((k) => draft.enabled[k].has(model.id));
      if (section === 'code') return isCoding(model);
      if (!spec) return true;
      if (spec.sub) return model.kind === 'audio' && (model.audio || {}).kind === spec.sub;
      return model.kind === spec.kind;
    });
  }

  function drawSections(holder) {
    const list = visible();
    if (!list.length) {
      holder.append(h('p', T.nothing, 'muted'));
      return;
    }
    for (const spec of SECTIONS) {
      const rows = list.filter((model) =>
        spec.sub ? model.kind === 'audio' && (model.audio || {}).kind === spec.sub : model.kind === spec.kind,
      );
      if (!rows.length) continue;
      const block = h('section', undefined, 'kind');
      const head = h('h2', undefined, 'kind-title');
      // когда открыт «Кодинг», раздел называется кодингом, а не перепиской
      const title = section === 'code' ? T.code : T[spec.title];
      head.append(h('span', title), h('span', T.found(rows.length), 'kind-count'));
      block.append(head);
      // по умолчанию включённые первыми, дальше по имени: дорогое не прячется, но и не лезет вперёд
      const byName = (a, b) => a.name.localeCompare(b.name, document.documentElement.lang);
      const sorted = [...rows].sort((a, b) => {
        if (sortMode === 'price') return priceValue(a) - priceValue(b) || byName(a, b);
        if (sortMode === 'context') return Number(b.context_length || 0) - Number(a.context_length || 0) || byName(a, b);
        if (sortMode === 'name') return byName(a, b);
        return Number(b.default_on !== false) - Number(a.default_on !== false) || byName(a, b);
      });
      const shown = shownCount[spec.key] || PAGE;
      const wrap=h('div',undefined,'table-scroll');wrap.tabIndex=0;wrap.setAttribute('role','region');wrap.setAttribute('aria-label',title);const table=h('table',undefined,'compact-table model-settings-table'),thead=h('thead'),heading=h('tr'),tbody=h('tbody');
      const labels={ru:['Модель','Цена','Ответ','Включена','Основная','Избранное','Тариф'],en:['Model','Price','Output','Enabled','Default','Favourite','Plan'],zh:['模型','价格','输出','启用','默认','收藏','套餐']}[document.documentElement.lang]||['Model','Price','Output','Enabled','Default','Favourite','Plan'];
      if (spec.kind !== 'chat') labels[2] = T.capability;
      for(const label of labels){const th=h('th',label);th.scope='col';heading.append(th);}thead.append(heading);table.append(thead,tbody);wrap.append(table);block.append(wrap);
      const page = sorted.slice(0, shown);
      // Название провайдера стоит в строке модели: отдельные группы только удлиняли таблицу.
      for (const model of page) tbody.append(row(model, spec));
      if (sorted.length > shown) {
        const more = h('button', T.showMore(Math.min(PAGE, sorted.length - shown), sorted.length), 'chip mm-all');
        more.type = 'button';
        more.onclick = () => {
          shownCount[spec.key] = shown + PAGE;
          draw();
        };
        block.append(more);
      }
      if (spec.key === 'chat') block.append(h('p', T.chatCannotEmpty, 'fine'));
      holder.append(block);
    }
  }

  function drawChips() {
    const holder = el('kinds');
    holder.replaceChildren();
    const all = loaded.models || [];
    const ofKind = (spec) =>
      all.filter((m) => (spec.sub ? m.kind === 'audio' && (m.audio || {}).kind === spec.sub : m.kind === spec.kind)).length;
    const chips = [
      ['all', T.all, all.length],
      ['favourites', T.favouritesShort, (loaded.shelf?.favourites||[]).length],
      ['mine', T.mine, all.filter((m) => !m.plan_locked && SHELF_KINDS.some((k) => draft.enabled[k].has(m.id))).length],
      ['code', T.code, all.filter(isCoding).length],
      ...SECTIONS.map((spec) => [spec.key, T[spec.title], ofKind(spec)]),
    ];
    for (const [key, label, count] of chips) {
      if (!count) continue;
      const chip = h('button', undefined, `chip${section === key ? ' chip-on' : ''}`);
      chip.type = 'button';
      chip.append(h('span', label), h('span', num(count), 'chip-count'));
      chip.onclick = () => {
        section = key;
        writePref('nm.models.section', section);
        shownCount = {};
        draw();
      };
      holder.append(chip);
    }
    const locked = all.filter((m) => m.plan_locked).length;
    if (locked) {
      const note = h('p', T.planNote(locked), 'fine plan-note');
      const link = h('a', T.planPill, 'plan-pill');
      link.href = upgradeHref();
      link.onclick = (event) => { event.preventDefault(); openPlanDialog(); };
      note.append(link);
      holder.append(note);
    }
  }

  function snapshot(shelf) {
    const out = {};
    for (const kind of SHELF_KINDS) {
      out[kind] = {
        enabled: [...((shelf.enabled && shelf.enabled[kind]) || [])].sort(),
        default: (shelf.default && shelf.default[kind]) || '',
      };
    }
    return out;
  }

  function fromServer(shelf) {
    const state = { enabled: {}, default: {} };
    for (const kind of SHELF_KINDS) {
      state.enabled[kind] = new Set((shelf.enabled && shelf.enabled[kind]) || []);
      state.default[kind] = (shelf.default && shelf.default[kind]) || '';
    }
    return state;
  }

  function changed() {
    return JSON.stringify(snapshot(draft)) !== JSON.stringify(snapshot(loaded.shelf || {}));
  }

  function draw() {
    if (!draft) return;
    drawChips();
    const holder = el('list');
    holder.replaceChildren();
    drawSections(holder);
    const isChanged = changed();
    el('save').disabled = busy || !isChanged;
    el('save').parentElement.classList.toggle('has-changes',isChanged);
    el('reset').hidden = !isChanged;
    el('status').textContent = busy ? T.saving : isChanged ? T.dirty : T.unchanged;
    el('count').textContent = T.found(visible().length);
  }

  const money = (cents) => new Intl.NumberFormat(document.documentElement.lang, { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(Number(cents) / 100);

  /** Черновик окна тарифа: карточки тарифов и переход в кабинет, где проходит оплата. */
  async function loadPlanConfig(holder, status, go, updateGo, amount) {
    try {
      const response = await fetch('/v1/payments/config', { headers: { Authorization: 'Bearer ' + token() }, cache: 'no-store' });
      if (!response.ok) throw new Error('config');
      const config = await response.json();
      const ids = ['lika', 'plus', 'lika-plus', 'team'];
      holder.replaceChildren();
      let any = false;
      for (const id of ids) {
        const plan = config?.plans?.[id];
        if (!plan || !Number.isSafeInteger(plan.amount) || plan.amount < 1) continue;
        any = true;
        const card = h('button', undefined, 'plan-card');
        card.type = 'button';
        card.append(
          h('strong', plan.name || id, 'plan-name'),
          h('span', money(plan.amount), 'plan-price'),
          h('span', `${T.discount} ${plan.discount}% · ${plan.days || 30} ${T.days}`, 'plan-meta'),
        );
        card.onclick = () => {
          planChoice = id;
          amount.value = '';
          for (const other of holder.querySelectorAll('.plan-card')) other.classList.toggle('plan-card-on', other === card);
          updateGo();
        };
        holder.append(card);
      }
      status.textContent = any ? '' : T.planUnavailable;
      if (!any) updateGo();
    } catch (_) {
      status.textContent = T.planUnavailable;
    }
  }

  function buildPlanDialog() {
    const dialog = el('plan-dialog');
    if (!dialog || dialog.dataset.built) return;
    dialog.dataset.built = '1';
    const close = h('button', '×', 'plan-close');
    close.type = 'button';
    close.setAttribute('aria-label', T.close);
    close.onclick = () => dialog.close();
    const title = h('h2', T.planTitle);
    title.id = 'plan-title';
    const plans = h('div', undefined, 'plan-options');
    const status = h('p', T.planLoading, 'fine');
    status.id = 'plan-status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const label = h('label', T.planAmount + ' ');
    const amount = h('input');
    amount.type = 'text';
    amount.inputMode = 'decimal';
    amount.placeholder = '1000';
    amount.id = 'plan-amount';
    amount.autocomplete = 'off';
    label.append(amount);
    const go = h('a', T.planGo, 'btn');
    const updateGo = () => { go.href = upgradeHref(planChoice, planChoice ? '' : amount.value.trim()); };
    amount.oninput = () => {
      if (amount.value.trim()) {
        planChoice = '';
        for (const card of plans.querySelectorAll('.plan-card')) card.classList.remove('plan-card-on');
      }
      updateGo();
    };
    updateGo();
    dialog.append(close, title, h('p', T.planIntro, 'fine'), plans, status, label, h('p', T.planHint, 'fine'), go);
    dialog.setAttribute('aria-labelledby', 'plan-title');
    dialog.addEventListener('close', () => document.body.classList.remove('plan-open'));
    dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
    loadPlanConfig(plans, status, go, updateGo, amount);
  }

  function openPlanDialog() {
    const dialog = el('plan-dialog');
    if (!dialog) return;
    buildPlanDialog();
    if (!dialog.open) dialog.showModal();
    document.body.classList.add('plan-open');
  }

  async function load() {
    const key = token();
    if (!key) {
      el('status').textContent = T.login;
      return;
    }
    el('status').textContent = T.loading;
    try {
      const response = await fetch('/v1/me/models', {
        headers: { Authorization: `Bearer ${key}` },
        cache: 'no-store',
      });
      if (response.status === 401) {
        el('status').textContent = T.expired;
        return;
      }
      if (!response.ok) throw new Error('request');
      loaded = await response.json();
      if(!el('context-filter')){const label=h('label','Контекст ');const select=h('select');select.id='context-filter';select.setAttribute('aria-label','Контекст');for(const n of [0,128000,200000,1000000]){const option=h('option',n?(n/1000)+'K+':'Любой');option.value=n;select.append(option);}select.onchange=()=>{contextMin=Number(select.value);draw();};label.append(select);document.querySelector('.search-row').append(label);}
      if(!el('vendor-filter')){
        const label=h('label',T.vendorFilter+' ');
        const select=h('select');select.id='vendor-filter';select.setAttribute('aria-label',T.vendorFilter);
        select.onchange=()=>{vendorFilter=select.value;writePref('nm.models.vendor',vendorFilter);draw();};
        label.append(select);document.querySelector('.search-row').append(label);
      }
      const vendorSelect=el('vendor-filter');
      const buckets=[...new Set((loaded.models||[]).map(m=>vendorBucket(m.vendor)))];
      const vendors=buckets.filter(v=>v!==OTHER_BUCKET).sort((a,b)=>a.localeCompare(b,document.documentElement.lang));
      const hasOther=buckets.includes(OTHER_BUCKET);
      vendorSelect.replaceChildren();
      const anyVendor=h('option',T.allVendors);anyVendor.value='';vendorSelect.append(anyVendor);
      for(const v of vendors){const option=h('option',v);option.value=v;vendorSelect.append(option);}
      if(hasOther){const other=h('option',T.otherVendors);other.value=OTHER_BUCKET;vendorSelect.append(other);}
      const known=[...vendors,...(hasOther?[OTHER_BUCKET]:[])];
      vendorSelect.value=known.includes(vendorFilter)?vendorFilter:'';
      vendorFilter=vendorSelect.value;
      if(!el('sort-filter')){
        const label=h('label',T.sort+' ');
        const select=h('select');select.id='sort-filter';select.setAttribute('aria-label',T.sort);
        for(const [value,title] of [['recommended',T.sortRecommended],['price',T.sortPrice],['context',T.sortContext],['name',T.sortName]]){const option=h('option',title);option.value=value;select.append(option);}
        select.value=sortMode;
        select.onchange=()=>{sortMode=select.value;writePref('nm.models.sort',sortMode);draw();};
        label.append(select);document.querySelector('.search-row').append(label);
      }

      draft = fromServer(loaded.shelf || {});
      draw();
    } catch (_) {
      el('status').textContent = T.error;
    }
  }

  async function save() {
    if (busy || !draft) return;
    const key = token();
    if (!key) {
      el('status').textContent = T.login;
      return;
    }
    busy = true;
    draw();
    const body = { enabled: {}, default: {} };
    for (const kind of SHELF_KINDS) {
      body.enabled[kind] = [...draft.enabled[kind]];
      if (draft.default[kind]) body.default[kind] = draft.default[kind];
    }
    try {
      const response = await fetch('/v1/me/models', {
        method: 'PUT',
        headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        busy = false;
        draw();
        el('status').textContent = (data.error && data.error.message) || T.error;
        return;
      }
      loaded = data.shelf ? { ...loaded, shelf: data.shelf } : loaded;
      draft = fromServer(loaded.shelf || {});
      busy = false;
      draw();
      el('status').textContent = T.saved;
    } catch (_) {
      el('status').textContent = T.error;
      busy = false;
      draw();
    }
  }

  el('search').oninput = () => {
    needle = el('search').value;
    shownCount = {};
    if (draft) draw();
  };
  el('save').onclick = save;
  el('reset').onclick = () => {
    if (!loaded) return;
    draft = fromServer(loaded.shelf || {});
    draw();
  };

  load();
})();
