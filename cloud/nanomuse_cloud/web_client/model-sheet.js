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
    vendor: facts.vendor || facts.provider || '',
    provider: facts.provider || '',
    desc: facts.description || facts.desc || '',
    free: facts.free === true,
    price: facts.per_image || facts.per_clip || facts.per_speech || null,
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
export function filterCards(cards, { kind = '', vendor = '', query = '' } = {}) {
  const q = String(query || '').trim().toLowerCase();
  return cards.filter((card) => {
    if (kind && card.kind !== kind) return false;
    if (vendor && (card.vendor || 'другие') !== vendor) return false;
    if (!q) return true;
    return (
      card.name.toLowerCase().includes(q) ||
      card.id.toLowerCase().includes(q) ||
      (card.desc || '').toLowerCase().includes(q)
    );
  });
}

/**
 * Собрать панель в документе. `onPick(id)` зовут, когда человек выбрал модель.
 * Панель строит себя сама: элемент в разметке не нужен, крепится к body.
 */
export function installModelSheet({ onPick, api, session }) {
  const sheet = document.createElement('div');
  sheet.className = 'model-sheet';
  sheet.dataset.open = '0';
  sheet.innerHTML =
    '<div class="model-sheet-panel" role="dialog" aria-modal="true" aria-label="Выбор модели">' +
    '<div class="model-vendors" id="ms-vendors"></div>' +
    '<div class="model-body">' +
    '<div class="model-searchline"><input id="ms-search" type="search" placeholder="Поиск модели" autocomplete="off" spellcheck="false">' +
    '<button id="ms-close" type="button" class="quiet" aria-label="Закрыть">✕</button></div>' +
    '<div class="model-kindrow" id="ms-kinds"></div>' +
    '<div class="model-cards" id="ms-cards"></div>' +
    '</div></div>';
  document.body.append(sheet);

  const el = (id) => sheet.querySelector('#' + id);
  const state = { cards: [], kind: '', vendor: '', query: '', picked: '', favourites: new Set() };

  try {
    const saved = JSON.parse(localStorage.getItem('nl.web.favmodels') || '[]');
    if (Array.isArray(saved)) state.favourites = new Set(saved);
  } catch {
    /* испорченное хранилище — просто без избранного */
  }

  function saveFavourites() {
    try {
      localStorage.setItem('nl.web.favmodels', JSON.stringify([...state.favourites]));
    } catch {
      /* приватный режим: избранное живёт до перезагрузки */
    }
  }

  function paintKinds() {
    const box = el('ms-kinds');
    box.replaceChildren();
    const all = document.createElement('button');
    all.type = 'button';
    all.className = 'model-kind-btn';
    all.setAttribute('aria-pressed', String(state.kind === ''));
    all.innerHTML = 'Все типы <span class="n">' + state.cards.length + '</span>';
    all.onclick = () => {
      state.kind = '';
      paint();
    };
    box.append(all);
    for (const { kind, count } of kindCounts(state.cards)) {
      const meta = KIND_LABELS[kind] || { label: kind, glyph: '•' };
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'model-kind-btn';
      button.setAttribute('aria-pressed', String(state.kind === kind));
      button.textContent = meta.label + ' ';
      const n = document.createElement('span');
      n.className = 'n';
      n.textContent = String(count);
      button.append(n);
      button.onclick = () => {
        state.kind = state.kind === kind ? '' : kind;
        paint();
      };
      box.append(button);
    }
  }

  function paintVendors() {
    const box = el('ms-vendors');
    box.replaceChildren();
    const rows = [{ name: '', count: state.cards.length }, ...vendorRows(state.cards)];
    for (const row of rows) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'model-vendor-row';
      button.setAttribute('aria-pressed', String(state.vendor === row.name));
      const dot = document.createElement('span');
      dot.className = 'model-vendor-dot';
      dot.textContent = (row.name || 'Все').slice(0, 2).toUpperCase();
      const label = document.createElement('span');
      label.textContent = row.name || 'Все производители';
      const count = document.createElement('span');
      count.className = 'model-vendor-count';
      count.textContent = String(row.count);
      button.append(dot, label, count);
      button.onclick = () => {
        state.vendor = row.name;
        paint();
      };
      box.append(button);
    }
  }

  function paintCards() {
    const box = el('ms-cards');
    box.replaceChildren();
    const rows = filterCards(state.cards, state);
    if (!rows.length) {
      const empty = document.createElement('div');
      empty.className = 'model-empty';
      empty.textContent = 'Ничего не нашлось. Сбросьте фильтры или измените запрос.';
      box.append(empty);
      return;
    }
    for (const card of rows) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'model-card';
      button.setAttribute('aria-pressed', String(state.picked === card.id));
      button.dataset.id = card.id;

      const icon = document.createElement('span');
      icon.className = 'model-card-icon';
      icon.textContent = (KIND_LABELS[card.kind] || { glyph: '•' }).glyph;

      const middle = document.createElement('span');
      const title = document.createElement('span');
      title.className = 'model-card-title';
      title.textContent = card.name;
      if (card.free) {
        const badge = document.createElement('span');
        badge.className = 'model-card-badge';
        badge.textContent = 'free';
        title.append(badge);
      }
      middle.append(title);
      if (card.desc) {
        const desc = document.createElement('span');
        desc.className = 'model-card-desc';
        desc.textContent = card.desc;
        middle.append(desc);
      } else {
        const desc = document.createElement('span');
        desc.className = 'model-card-desc';
        desc.textContent = (KIND_LABELS[card.kind] || { label: card.kind }).label + ' · ' + card.id;
        middle.append(desc);
      }

      const star = document.createElement('button');
      star.type = 'button';
      star.className = 'model-card-star';
      star.textContent = '★';
      star.title = 'В избранное';
      star.setAttribute('aria-pressed', String(state.favourites.has(card.id)));
      star.onclick = (event) => {
        event.stopPropagation();
        if (state.favourites.has(card.id)) state.favourites.delete(card.id);
        else state.favourites.add(card.id);
        saveFavourites();
        paintCards();
      };

      button.append(icon, middle, star);
      button.onclick = () => {
        state.picked = card.id;
        onPick?.(card.id);
        close();
      };
      box.append(button);
    }
  }

  function paint() {
    paintKinds();
    paintVendors();
    paintCards();
  }

  function open() {
    sheet.dataset.open = '1';
    el('ms-search').focus();
    if (!state.cards.length) void load();
    paint();
  }

  function close() {
    sheet.dataset.open = '0';
  }

  let loading = null;
  async function load() {
    if (loading) return loading;
    loading = (async () => {
      try {
        const data = await api('/v1/models');
        state.cards = modelCards(data);
        paint();
      } catch {
        const box = el('ms-cards');
        box.replaceChildren();
        const empty = document.createElement('div');
        empty.className = 'model-empty';
        empty.textContent = 'Не удалось загрузить список моделей.';
        box.append(empty);
      } finally {
        loading = null;
      }
    })();
    return loading;
  }

  el('ms-search').addEventListener('input', (event) => {
    state.query = event.target.value;
    paintCards();
  });
  el('ms-close').onclick = close;
  sheet.addEventListener('click', (event) => {
    if (event.target === sheet) close();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && sheet.dataset.open === '1') close();
  });

  return {
    open,
    close,
    setCards(cards) {
      state.cards = cards;
      paint();
    },
    setPicked(id) {
      state.picked = id;
      paintCards();
    },
    refresh() {
      state.cards = [];
      return load();
    },
  };
}
