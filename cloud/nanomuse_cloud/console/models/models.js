import {modelDetails} from '/web/model-detail.js?v=20261026-catalogue';
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
      intro: 'Выберите, какие модели будут в приложении, и какая из них отвечает по умолчанию. Цены со скидкой вашего тарифа, возможности и предварительный расчёт доступны у каждой модели.',
      search: 'Быстрый поиск моделей', all: 'Все модели', mine: 'Выбранные',
      chat: 'Переписка', code: 'Кодинг', image: 'Картинки', video: 'Видео', sound: 'Звук', music: 'Музыка',
      showMore: (n, total) => `Показать ещё ${n} из ${total}`, showAll: 'Показать все',
      speech: 'Озвучка', transcribe: 'Распознавание',
      loading: 'Загружаем список моделей…', error: 'Не удалось получить список моделей. Попробуйте ещё раз.',
      login: 'Войдите в nanoLika, чтобы увидеть свои модели.', expired: 'Вход больше не действует. Войдите заново.',
      vendor: (v) => `Провайдер ${v}`, more: 'Подробнее', less: 'Скрыть',
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
      footer: 'Цены продажи поступают из прайса nanoLika. Действующая скидка учтена. Расчёт не запускает генерацию.',
      back: '← В кабинет', voice: (v) => `голос: ${v}`, id: 'у провайдера', takes: (l) => `принимает: ${l}`,
      mod: { text: 'текст', image: 'картинки', audio: 'звук', video: 'видео' },
    },
    en: {
      title: 'Models', eyebrow: 'nanoLika · Your account',
      intro: 'Pick what the app should offer and which model answers by default. Your discounted retail prices, model capabilities and estimates are shown before generation.',
      search: 'Search models', all: 'All models', mine: 'My picks',
      chat: 'Chat', code: 'Coding', image: 'Pictures', video: 'Clips', sound: 'Sound', music: 'Music',
      showMore: (n, total) => `Show ${n} more of ${total}`, showAll: 'Show all',
      speech: 'Speech', transcribe: 'Transcription',
      loading: 'Loading the model list…', error: 'Could not load the model list. Please try again.',
      login: 'Sign in to nanoLika to see your models.', expired: 'Your sign-in expired. Please sign in again.',
      vendor: (v) => `Provider ${v}`, more: 'More', less: 'Hide',
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
      footer: 'Retail prices come from nanoLika. Your active discount is included. Estimates do not start a generation.',
      back: '← Account', voice: (v) => `voice: ${v}`, id: 'upstream', takes: (l) => `takes: ${l}`,
      mod: { text: 'text', image: 'pictures', audio: 'sound', video: 'video' },
    },
    zh: {
      title: '模型', eyebrow: 'nanoLika · 账户',
      intro: '选择应用里要提供哪些模型，以及哪一个默认回答。单次生成的费用与供应商成本直接写在各自一行。',
      search: '快速搜索模型', all: '全部模型', mine: '已选',
      chat: '对话', code: '编程', image: '图片', video: '视频', sound: '语音', music: '音乐',
      showMore: (n, total) => `再显示 ${n} 个，共 ${total} 个`, showAll: '显示全部',
      speech: '语音合成', transcribe: '语音识别',
      loading: '正在读取模型列表…', error: '无法读取模型列表，请重试。',
      login: '请登录 nanoLika 查看模型。', expired: '登录已失效，请重新登录。',
      vendor: (v) => `供应商 ${v}`, more: '详情', less: '收起',
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
      footer: '设置保存在服务器上，对所有设备生效。令牌是额度的内部计量；成本一栏只为透明，卢布价目稍后提供。',
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

  /** Полка ведётся по видам, поэтому у звука и музыки один вид: «ключ полки». */
  const SHELF_KINDS = ['chat', 'image', 'video', 'audio'];

  let loaded = null; // что сказал сервер в прошлый раз
  let draft = null; // что человек выбрал сейчас
  let contextMin=0;let favouriteWrites=Promise.resolve();
  let section = 'all'; // какой раздел открыт
  let needle = ''; // что в поиске
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

  /** Что покупают за токены и сколько это стоит у провайдера — словами, без выдуманных цен. */
  function priceBlock(model) {
    const commerce=model.commerce||{},rates=commerce.effective_rub||{};
    const labels={input:T.perMillionIn,output:T.perMillionOut,image:T.perPicture,second:T.perSecond,speech:T.perSpeech,minute:T.perMinute};
    const fields=model.kind==='chat'?['input','output']:model.kind==='image'?['image']:model.kind==='video'?['second']:commerce.unit==='speech'?['speech']:['minute'];
    return {lines:fields.filter(k=>Number(rates[k])>0).map(k=>[labels[k],new Intl.NumberFormat(document.documentElement.lang,{maximumFractionDigits:4}).format(rates[k])+' ₽']),note:commerce.discount_pct?'−'+commerce.discount_pct+'%':''};
  }

  function details(model) {
    const rows = [];
    if (model.upstream) rows.push([T.id, model.upstream]);
    const takes = (model.in || []).map((m) => T.mod[m] || m).filter(Boolean);
    if (takes.length) rows.push(['', T.takes(takes.join(', '))]);
    const wrap = h('div', undefined, 'mm-more');
    for (const [label, value] of rows) {
      const line = h('div', undefined, 'mm-more-row');
      if (label) line.append(h('span', label, 'mm-more-label'));
      line.append(h('code', value, 'mm-more-value'));
      wrap.append(line);
    }
    return wrap;
  }

  function row(model) {
    const kind = model.kind;
    const on = draft.enabled[kind].has(model.id);
    const usable = model.available !== false;
    const line = h('div', undefined, `mm-row${usable ? '' : ' mm-row-off'}${on ? ' mm-row-on' : ''}`);

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
    const title = h('label', model.name, 'mm-name');
    title.htmlFor = box.id;
    if (model.default_on === false && usable) title.append(h('em', T.manual, 'tag'));
    if (!usable) title.append(h('em', T.soon, 'tag tag-wait'));
    names.append(title);
    const meta = [];
    if (model.vendor) meta.push(T.vendor(model.vendor));
    if (model.context_length) meta.push(`Контекст ${num(model.context_length)} токенов`);
    if (model.max_output) meta.push(`Ответ до ${num(model.max_output)} токенов`);
    if (meta.length) names.append(h('div', meta.join(' · '), 'mm-vendor'));

    const more = h('button', T.more, 'mm-link');
    more.type = 'button';
    more.onclick = () => {
      const opened = names.querySelector('.mm-more');
      if (opened) {
        opened.remove();
        more.textContent = T.more;
      } else {
        names.append(details(model));
        more.textContent = T.less;
      }
    };
    names.append(more);

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

    const calc=document.createElement('details');calc.className='mm-calculator';const caption=h('summary','Параметры и расчёт');calc.append(caption);let calculated=false;calc.ontoggle=()=>{if(calc.open&&!calculated){calculated=true;calc.append(modelDetails(model,{api:async(path,options={})=>{const response=await fetch(path,{...options,cache:'no-store',headers:{Authorization:'Bearer '+token(),'Content-Type':'application/json'}});if(!response.ok)throw new Error('quote');return response.json();}}));}};
    const star=h('button',(loaded.shelf?.favourites||[]).includes(model.id)?'★':'☆','chip');star.type='button';star.setAttribute('aria-label','В избранное: '+model.name);star.setAttribute('aria-pressed',String((loaded.shelf?.favourites||[]).includes(model.id)));star.onclick=()=>{const old=loaded.shelf?.favourites||[],next=old.includes(model.id)?old.filter(id=>id!==model.id):[...old,model.id];loaded.shelf.favourites=next;draw();favouriteWrites=favouriteWrites.catch(()=>{}).then(async()=>{const response=await fetch('/v1/me/models',{method:'PUT',headers:{Authorization:'Bearer '+token(),'Content-Type':'application/json'},body:JSON.stringify({enabled:{},favourites:next})});if(!response.ok)throw new Error('favourite');}).catch(()=>{el('status').textContent='Избранное не сохранилось. Обновите страницу.';});};
    line.append(box, names, prices,star);line.append(calc);
    if (usable) line.append(pick);
    return line;
  }

  function visible() {
    const all = loaded.models || [];
    const wanted = needle.trim().toLowerCase();
    const spec = SECTIONS.find((s) => s.key === section);
    return all.filter((model) => {
      const hay = `${model.name} ${model.vendor || ''} ${model.id} ${model.upstream || ''}`.toLowerCase();
      if (wanted && !hay.includes(wanted)) return false;
      if(contextMin && Number(model.context_length||0)<contextMin)return false;
      if(section==='favourites')return (loaded.shelf?.favourites||[]).includes(model.id);
      if (section === 'mine') return SHELF_KINDS.some((k) => draft.enabled[k].has(model.id));
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
      const head = h('button', undefined, `kind-title${collapsedKinds.has(spec.key) ? ' kind-title-folded' : ''}`);
      head.type = 'button';
      head.setAttribute('aria-expanded', String(!collapsedKinds.has(spec.key)));
      head.onclick = () => {
        if (collapsedKinds.has(spec.key)) collapsedKinds.delete(spec.key);
        else collapsedKinds.add(spec.key);
        draw();
      };
      // когда открыт «Кодинг», раздел называется кодингом, а не перепиской
      const title = section === 'code' ? T.code : T[spec.title];
      head.append(h('span', `${collapsedKinds.has(spec.key) ? '＋' : '−'} ${title}`), h('span', T.found(rows.length), 'kind-count'));
      block.append(head);
      if (collapsedKinds.has(spec.key)) {
        holder.append(block);
        continue;
      }
      // включённые по умолчанию — первыми, дальше по имени: дорогое не прячется, но и не лезет вперёд
      const sorted = [...rows].sort(
        (a, b) => Number(b.default_on !== false) - Number(a.default_on !== false) || a.name.localeCompare(b.name),
      );
      const shown = shownCount[spec.key] || PAGE;
      for (const model of sorted.slice(0, shown)) block.append(row(model));
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
      ['favourites', '★ Избранное', (loaded.shelf?.favourites||[]).length],
      ['mine', T.mine, all.filter((m) => SHELF_KINDS.some((k) => draft.enabled[k].has(m.id))).length],
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
        shownCount = {};
        draw();
      };
      holder.append(chip);
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
      if(!el('context-filter')){const label=h('label','Контекст ');const select=h('select');select.id='context-filter';select.setAttribute('aria-label','Контекст');for(const n of [0,128000,200000,1000000]){const option=h('option',n?(n/1000)+'K+':'Любой');option.value=n;select.append(option);}select.onchange=()=>{contextMin=Number(select.value);draw();};label.append(select);document.querySelector('.search-row').append(label);const refresh=h('button','Обновить цены','chip');refresh.type='button';refresh.onclick=load;document.querySelector('.search-row').append(refresh);}

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
