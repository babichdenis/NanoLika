// «Модели» в админке: одна модель — одна строка.
//
// Сверху список моделей меню: галочка видимости, тип, контекст, наша закупка,
// наша цена клиенту, лучшая цена у поставщика и число предложений.
// Ниже — связанная таблица: поставщики выбранной модели, у кого сколько стоит
// вход, выход и кэш, кто раздаёт сейчас и кого нельзя ставить в маршрут.
//
// Данные — GET /v1/admin/prices: меню релея, закупка, розница и предложения
// поставщиков (Bothub приходит из его API). Модуль монтируется в панель
// страницы «Модели»; состояние фильтров и выбранной модели живёт между
// перерисовками админки.

let ROOT = null, CTX = null, API = null;
let DATA = null;
let loading = false;
let error = "";

const state = {
  group: "all",
  visibility: "all", // all | visible | hidden
  provider: "",
  sellOnly: false,
  ours: false,   // только российские модели (GigaChat, Yandex, Alice…)
  sort: "name",
  dir: "asc",
  cols: {},   // фильтры колонок: ключ колонки → строка
  sel: "",
};

const GROUPS = [
  ["all", "Все"],
  ["text", "Текст"],
  ["code", "Код"],
  ["image", "Картинки"],
  ["video", "Видео"],
  ["audio", "Аудио"],
  ["embedding", "Эмбеддинги"],
  ["other", "Другое"],
];

const PROVIDER_LABELS = {
  polza: "Polza",
  routerai: "RouterAI",
  aitunnel: "AITUNNEL",
  proxyapi: "ProxyAPI",
  "proxyapi-openai": "ProxyAPI OpenAI",
  bothub: "Bothub",
  deepseek: "DeepSeek",
  openrouter: "OpenRouter",
  own: "Свой ключ",
};

const $ = (id) => (ROOT ? ROOT.querySelector("#" + id) : null);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

// ── формат ────────────────────────────────────────────────────────────────────

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function ctxText(value) {
  const n = Number(value) || 0;
  if (!n) return "—";
  if (n >= 1_000_000) return fmt(n / 1_000_000, 1) + "M";
  if (n >= 1000) return fmt(n / 1000, 0) + "K";
  return fmt(n, 0);
}

function providerLabel(name) {
  return PROVIDER_LABELS[name] || name;
}

function groupOf(model) {
  const kind = String(model.kind || "chat");
  if (kind === "chat" || kind === "responses" || kind === "completion") {
    const text = `${model.id || ""} ${model.name || ""}`.toLowerCase();
    return /(^|[\s/_-])(code|coder|coding|codex|codestral|starcoder)([\s/_-]|$)/.test(text) ? "code" : "text";
  }
  if (kind.startsWith("image")) return "image";
  if (kind.startsWith("video")) return "video";
  if (kind.startsWith("audio")) return "audio";
  if (kind === "embedding") return "embedding";
  return "other";
}

function groupLabel(id) {
  const found = GROUPS.find(([key]) => key === id);
  return found ? found[1] : "Другое";
}

/** Наша закупка: текст — за 1M токенов, медиа — за штуку/секунду/1000 знаков. */
function purchaseText(model) {
  const cost = model.cost || {};
  if (cost.rub_image != null) return `${fmt(cost.rub_image)} ₽ / картинка`;
  if (cost.rub_second != null) return `${fmt(cost.rub_second)} ₽ / секунда`;
  if (cost.rub_speech != null) return `${fmt(cost.rub_speech)} ₽ / 1000 знаков`;
  if (cost.rub_in != null || cost.rub_out != null) return `${fmt(cost.rub_in)} / ${fmt(cost.rub_out)}`;
  return "—";
}

/** Наша цена клиенту: у текста ставка за 1M, у медиа — подпись тарифа. */
function clientText(model) {
  const sell = model.sell || {};
  if (sell.rub_in || sell.rub_out) return `${fmt(sell.rub_in)} / ${fmt(sell.rub_out)}`;
  if (sell.hint) return sell.hint;
  return "—";
}

function offerEstimate(offer) {
  const input = Number(offer.input_rub || 0);
  const output = Number(offer.output_rub || 0);
  if (!input && !output) return null;
  return (input * 1000 + output * 512) / 1_000_000;
}

function offersOf(model) {
  return (model.routing && model.routing.offers) || [];
}

function bestOffer(offers) {
  let best = null;
  for (const offer of offers || []) {
    const price = offerEstimate(offer);
    if (price === null) continue;
    if (!best || price < offerEstimate(best)) best = offer;
  }
  return best;
}

/** Одна строка — одно название: закреплённая версия и её latest-алиас склеиваются. */
function groupKeyOf(model) {
  // дефисы и подчёркивания считаем пробелами: «GigaChat-3-Pro» и «GigaChat 3 Pro»
  // это одна модель, а не две строки
  const name = String(model.name || model.id).trim().toLowerCase().replace(/[-_]+/g, " ").replace(/\s+/g, " ");
  return `${name}|${groupOf(model)}`;
}

function modelRank(model) {
  if (model.default_on) return 0;
  if (model.recommended) return 1;
  if (model.listed) return 2;
  return 3;
}

function unionOffers(models) {
  const seen = new Set();
  const out = [];
  for (const model of models) {
    for (const offer of offersOf(model)) {
      // одна и та же цена у pinned-версии и latest-алиаса — одна строка
      const key = `${offer.provider}|${offer.input_rub}|${offer.output_rub}|${offer.cached_rub}`;
      if (seen.has(key)) continue;
      seen.add(key);
      offer._from = model.id;
      out.push(offer);
    }
  }
  return out;
}

function allGroups() {
  const map = new Map();
  for (const model of (DATA && DATA.models) || []) {
    const key = groupKeyOf(model);
    let group = map.get(key);
    if (!group) {
      group = { key, name: model.name || model.id, group: groupOf(model), models: [] };
      map.set(key, group);
    }
    group.models.push(model);
  }
  const groups = [...map.values()];
  for (const group of groups) {
    group.models.sort((a, b) => modelRank(a) - modelRank(b) || String(a.id).length - String(b.id).length);
    group.primary = group.models[0];
    group.listed = group.models.every((model) => Boolean(model.listed));
    group.offers = unionOffers(group.models);
  }
  return groups;
}

/** Наши российские модели: id начинается с вендора, а не с западного агрегатора. */
const RU_VENDOR = /^(sber|gigachat|yandex|alice|tbank|tinkoff|mts|vk|beeline)[-_]/i;

function isOurs(group) {
  return group.models.some((model) => RU_VENDOR.test(String(model.id || "")));
}

function hasSell(model) {
  const sell = model.sell || {};
  return Boolean(sell.rub_in || sell.rub_out || sell.hint);
}

// ── колонки, фильтры и сортировка ────────────────────────────────────────────

const COLUMNS = [
  { key: "listed", label: "Показывать", cls: "mt-pick", numeric: false },
  { key: "name", label: "Модель", cls: "mt-model", numeric: false },
  { key: "kind", label: "Тип", numeric: false },
  { key: "context", label: "Контекст", numeric: true },
  { key: "purchase", label: "Закупка", sub: "вход / выход", numeric: true },
  { key: "client", label: "Клиенту", sub: "вход / выход", numeric: true },
  { key: "markupIn", label: "Наценка вход", numeric: true },
  { key: "markupOut", label: "Наценка выход", numeric: true },
  { key: "best", label: "Дешевле всех", numeric: true },
  { key: "offers", label: "Предложений", numeric: true },
];

const SORT_KEYS = new Set(COLUMNS.map((column) => column.key));
const OLD_SORT = { cost: "purchase", client: "client", offers: "offers", name: "name" };

function parseNumber(text) {
  let value = String(text).trim().toLowerCase().replace(/\s+/g, "").replace(",", ".");
  let factor = 1;
  if (value.endsWith("k") || value.endsWith("к")) { factor = 1000; value = value.slice(0, -1); }
  else if (value.endsWith("m") || value.endsWith("м")) { factor = 1_000_000; value = value.slice(0, -1); }
  const number = Number(value);
  return Number.isFinite(number) ? number * factor : null;
}

function ratioText(value) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "—";
  return "×" + fmt(number, 2);
}

function columnText(group, key) {
  const model = group.primary;
  switch (key) {
    case "listed": return group.listed ? "Видна" : "Скрыта";
    case "name": return `${group.name} ${group.models.map((item) => item.id).join(" ")}`;
    case "kind": return groupLabel(group.group);
    case "context": return ctxText(model.context);
    case "purchase": return purchaseText(model);
    case "client": return clientText(model);
    case "markupIn": return ratioText(model.ratio?.in);
    case "markupOut": return ratioText(model.ratio?.out);
    case "best": {
      const best = bestOffer(group.offers);
      return best ? `${fmt(best.input_rub)} ₽ ${providerLabel(best.provider)}` : "—";
    }
    case "offers": return String(group.offers.length);
    default: return "";
  }
}

function columnNumber(group, key) {
  const model = group.primary;
  switch (key) {
    case "context": return Number(model.context) || null;
    case "purchase": {
      const cost = model.cost || {};
      return cost.rub_in ?? cost.rub_image ?? cost.rub_second ?? cost.rub_speech ?? null;
    }
    case "client": return Number(model.sell?.rub_in) || null;
    case "markupIn": return Number(model.ratio?.in) > 0 ? Number(model.ratio.in) : null;
    case "markupOut": return Number(model.ratio?.out) > 0 ? Number(model.ratio.out) : null;
    case "best": {
      const best = bestOffer(group.offers);
      return best ? Number(best.input_rub) : null;
    }
    case "offers": return group.offers.length;
    default: return null;
  }
}

/** Фильтр колонки: слово — подстрока; число — «не меньше»; можно >, <, =, >=, <=. */
function matchesColumn(group, key, raw) {
  const query = String(raw || "").trim();
  if (!query) return true;
  const column = COLUMNS.find((item) => item.key === key);
  const found = query.match(/^(>=|<=|>|<|≥|≤|=)\s*(.+)$/);
  const number = columnNumber(group, key);
  if (found) {
    const target = parseNumber(found[2]);
    if (target === null || number === null) return false;
    const op = found[1] === "≥" ? ">=" : found[1] === "≤" ? "<=" : found[1];
    if (op === ">") return number > target;
    if (op === "<") return number < target;
    if (op === ">=") return number >= target;
    if (op === "<=") return number <= target;
    return number === target;
  }
  // без знака — обычный поиск по тексту: «128» найдёт «128K», «=128» — ровно 128
  return columnText(group, key).toLowerCase().includes(query.toLowerCase());
}

function readFilters() {
  const p = CTX && CTX.params;
  if (!p) return;
  const group = p.get("g") || "all";
  state.group = GROUPS.some(([key]) => key === group) ? group : "all";
  const vis = p.get("vis") || "all";
  state.visibility = ["all", "visible", "hidden"].includes(vis) ? vis : "all";
  state.provider = p.get("p") || "";
  state.sellOnly = p.get("sell") === "1";
  state.ours = p.get("ru") === "1";
  const sort = OLD_SORT[p.get("s")] || p.get("s") || "name";
  state.sort = SORT_KEYS.has(sort) ? sort : "name";
  state.dir = p.get("d") === "desc" ? "desc" : "asc";
  state.sel = p.get("sel") || "";
}

function saveFilters() {
  const p = new URLSearchParams();
  if (state.group !== "all") p.set("g", state.group);
  if (state.visibility !== "all") p.set("vis", state.visibility);
  if (state.provider) p.set("p", state.provider);
  if (state.sellOnly) p.set("sell", "1");
  if (state.ours) p.set("ru", "1");
  if (state.sort !== "name") p.set("s", state.sort);
  if (state.dir === "desc") p.set("d", "desc");
  if (state.sel) p.set("sel", state.sel);
  const query = p.toString();
  try {
    history.replaceState(null, "", "#models" + (query ? "?" + query : ""));
  } catch (_) { /* не наша страница */ }
}

function visibleGroups() {
  let rows = allGroups().filter((group) => {
    if (state.group !== "all" && group.group !== state.group) return false;
    if (state.visibility === "visible" && !group.listed) return false;
    if (state.visibility === "hidden" && group.listed) return false;
    if (state.sellOnly && !group.models.some(hasSell)) return false;
    if (state.ours && !isOurs(group)) return false;
    if (state.provider && !group.offers.some((offer) => offer.provider === state.provider)) return false;
    for (const [key, value] of Object.entries(state.cols)) {
      if (value && !matchesColumn(group, key, value)) return false;
    }
    return true;
  });
  const dir = state.dir === "desc" ? -1 : 1;
  rows.sort((a, b) => {
    let left = null, right = null;
    if (state.sort === "name") { left = a.name.toLowerCase(); right = b.name.toLowerCase(); }
    else if (state.sort === "kind") { left = groupLabel(a.group); right = groupLabel(b.group); }
    else if (state.sort === "listed") { left = a.listed ? 1 : 0; right = b.listed ? 1 : 0; }
    else { left = columnNumber(a, state.sort); right = columnNumber(b, state.sort); }
    if (left === null && right === null) return a.name.localeCompare(b.name, "ru");
    if (left === null) return 1;
    if (right === null) return -1;
    const cmp = typeof left === "string" ? left.localeCompare(right, "ru") : left - right;
    return cmp * dir || a.name.localeCompare(b.name, "ru");
  });
  return rows;
}

// ── разметка ──────────────────────────────────────────────────────────────────

const TEMPLATE = `
<div class="mt" id="mt">
  <div class="mt-pane mt-pane-top" id="mt-top-pane">
    <div class="mt-top">
      <div class="mt-toolbar">
        <div class="chips" id="mt-groups" role="group" aria-label="Тип модели"></div>
        <span class="sp"></span>
        <label class="switch"><input id="mt-sell" type="checkbox" /> только с ценой клиенту</label>
        <span class="fine mt-count" id="mt-count"></span>
      </div>
      <div class="mt-toolbar">
        <label class="field">поставщик
          <select id="mt-provider"><option value="">любой</option></select>
        </label>
        <label class="field">видимость
          <select id="mt-vis">
            <option value="all">все</option>
            <option value="visible">видимые</option>
            <option value="hidden">скрытые</option>
          </select>
        </label>
        <button class="chip" id="mt-refresh" type="button">Обновить цены</button>
      </div>
      <div class="mt-toolbar mt-bulk">
        <label class="field">наценка ×
          <input id="mt-k" type="number" min="0.1" max="20" step="0.05" value="1.3" />
        </label>
        <button class="chip" id="mt-apply-k" type="button">Поставить цену</button>
        <span class="sp"></span>
        <button class="chip" id="mt-show" type="button">Показать</button>
        <button class="chip" id="mt-hide" type="button">Скрыть</button>
      </div>
      <p class="fine" id="mt-note"></p>
    </div>
    <div class="mt-wrap">
      <table id="mt-models">
        <thead>
          <tr></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
  <div class="mt-splitter" id="mt-split" role="separator" aria-orientation="horizontal" tabindex="0" title="Потяните, чтобы изменить высоту таблиц">
    <span></span>
  </div>
  <div class="mt-pane mt-pane-bottom" id="mt-bottom-pane">
    <div class="mt-linked-head">
      <h3 id="mt-sel-name">Поставщики модели</h3>
      <span class="fine" id="mt-sel-meta"></span>
    </div>
    <div class="mt-wrap">
      <table id="mt-offers">
        <thead>
          <tr>
            <th>Поставщик</th>
            <th>Модель у поставщика</th>
            <th class="num">Вход, ₽/1M</th>
            <th class="num">Выход, ₽/1M</th>
            <th class="num">Кэш, ₽/1M</th>
            <th class="num">Контекст</th>
            <th>Состояние</th>
          </tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>
</div>`;

// ── отрисовка ─────────────────────────────────────────────────────────────────

function drawGroups() {
  const box = $("mt-groups");
  if (!box) return;
  const groups = allGroups();
  box.replaceChildren();
  for (const [key, label] of GROUPS) {
    const count = key === "all" ? groups.length : groups.filter((group) => group.group === key).length;
    if (!count && key !== "all") continue;
    const button = el("button", "chip" + (state.group === key ? " on" : ""));
    button.type = "button";
    button.append(el("span", "", label), el("b", "", String(count)));
    button.onclick = () => { state.group = key; draw(); };
    box.append(button);
  }
  const ours = el("button", "chip mt-ours" + (state.ours ? " on" : ""));
  ours.type = "button";
  ours.append(el("span", "", "наши"), el("b", "", String(groups.filter(isOurs).length)));
  ours.title = "Только российские модели: GigaChat, Yandex, Alice и другие";
  ours.onclick = () => { state.ours = !state.ours; draw(); };
  box.append(ours);
}

function drawProviderFilter() {
  const select = $("mt-provider");
  if (!select) return;
  const providers = ((DATA && DATA.providers && DATA.providers.connected) || []).slice();
  select.replaceChildren(el("option", "", "любой"));
  select.firstChild.value = "";
  for (const name of providers) {
    const option = el("option", "", providerLabel(name));
    option.value = name;
    select.append(option);
  }
  select.value = providers.includes(state.provider) ? state.provider : "";
  if (!select.value) state.provider = "";
}

/** Галочка общая для всех id одной строки: клиент не должен видеть дубль наполовину. */
function visibilityCell(group) {
  const cell = el("td", "mt-pick-cell");
  const label = el("label", "mt-check");
  const input = el("input");
  input.type = "checkbox";
  input.checked = group.listed;
  input.setAttribute("aria-label", "Показывать клиентам: " + group.name);
  label.append(input, el("span", "", group.listed ? "Видна" : "Скрыта"));
  const text = label.lastChild;
  input.onclick = (event) => event.stopPropagation();
  input.onchange = async () => {
    const next = input.checked;
    input.disabled = true;
    try {
      for (const model of group.models) {
        if (Boolean(model.listed) === next) continue;
        await API("PUT", `/v1/admin/prices/models/${encodeURIComponent(model.id)}/visibility`, { visible: next });
        model.listed = next;
      }
      group.listed = next;
      text.textContent = next ? "Видна" : "Скрыта";
      const row = cell.closest("tr");
      if (row) row.classList.toggle("mt-off", !next);
      if (!error) setNote("");
    } catch (e) {
      input.checked = !next;
      setNote(`Не сохранено: ${e.message}`);
    } finally {
      input.disabled = false;
    }
  };
  cell.append(label);
  return cell;
}

function drawModels(rows) {
  const body = ROOT.querySelector("#mt-models tbody");
  if (!body) return;
  body.replaceChildren();
  const frag = document.createDocumentFragment();
  for (const group of rows) {
    const model = group.primary;
    const best = bestOffer(group.offers);
    const offers = group.offers;
    const active = group.models.some((item) => item.id === state.sel);
    const tr = el("tr", "mt-row" + (group.listed ? "" : " mt-off") + (active ? " mt-active" : ""));

    tr.append(visibilityCell(group));

    const name = el("td", "mt-model-cell");
    name.title = group.models.map((item) => item.id).join(" · ");
    name.append(el("span", "mt-name", group.name));
    name.append(el("code", "mt-code", model.id));
    if (group.models.length > 1) name.append(el("span", "mt-alias", ` +${group.models.length - 1}`));
    tr.append(name);

    const textCell = (cls, text) => {
      const cell = el("td", cls, text);
      cell.title = text;
      return cell;
    };
    tr.append(textCell("mt-kind", groupLabel(group.group)));
    tr.append(textCell("num", ctxText(model.context)));
    tr.append(textCell("num", purchaseText(model)));
    tr.append(textCell("num", clientText(model)));
    const markupCell = (value) => {
      const cell = el("td", "num", ratioText(value));
      const number = Number(value);
      if (Number.isFinite(number) && number > 0 && number < 1.5) cell.classList.add("mt-low");
      return cell;
    };
    tr.append(markupCell(model.ratio?.in));
    tr.append(markupCell(model.ratio?.out));

    const bestCell = el("td", "mt-best");
    if (best) {
      bestCell.append(el("b", "", `${fmt(best.input_rub)} ₽`));
      bestCell.append(el("span", "fine", " вход · " + providerLabel(best.provider)));
    } else {
      bestCell.append(el("span", "fine", "—"));
    }
    tr.append(bestCell);

    const count = el("td", "num");
    count.append(el("b", "", offers.length ? String(offers.length) : "—"));
    if (offers.length) count.append(el("span", "fine", ` · ${new Set(offers.map((o) => o.provider)).size} пост.`));
    tr.append(count);

    tr.onclick = () => select(model.id);
    if (active) requestAnimationFrame(() => tr.scrollIntoView({ block: "nearest" }));
    frag.append(tr);
  }
  body.append(frag);
}

function select(id) {
  // повторный клик по выбранной строке закрывает нижнюю таблицу
  state.sel = state.sel === id ? "" : id;
  saveFilters();
  drawModels(visibleGroups());
  drawOffers();
}

function splitVisible(show) {
  const box = $("mt");
  const bottom = $("mt-bottom-pane");
  const split = $("mt-split");
  if (bottom) bottom.hidden = !show;
  if (split) split.hidden = !show;
  if (box) box.classList.toggle("mt-single", !show);
}

function drawOffers() {
  const title = $("mt-sel-name");
  const meta = $("mt-sel-meta");
  const body = ROOT.querySelector("#mt-offers tbody");
  if (!body) return;
  body.replaceChildren();
  const group = allGroups().find((item) => item.models.some((model) => model.id === state.sel)) || null;
  if (!group) {
    if (title) title.textContent = "Поставщики модели";
    if (meta) meta.textContent = "";
    splitVisible(false);
    return;
  }
  splitVisible(true);
  if (title) title.textContent = group.name;
  if (meta) {
    const ids = group.models.map((model) => model.id).join(" · ");
    meta.textContent = `${ids} · ${groupLabel(group.group)} · ${group.offers.length} предложений`;
  }
  const offers = group.offers.slice().sort((a, b) => (offerEstimate(a) ?? Infinity) - (offerEstimate(b) ?? Infinity));
  if (!offers.length) {
    const tr = el("tr");
    const td = el("td", "fine", group.group === "text"
      ? "Поставщики цену не публикуют."
      : "У медиамодели предложения поставщиков пока не собираются.");
    td.colSpan = 7;
    tr.append(td);
    body.append(tr);
    return;
  }
  const cheapest = offers.find((offer) => offerEstimate(offer) !== null) || null;
  for (const offer of offers) {
    const tr = el("tr", offer === cheapest ? "mt-cheap" : "");
    const serving = Boolean(offer.selected) && offer._from === group.primary.id;
    if (serving) tr.classList.add("mt-serving");
    tr.append(el("td", "mt-provider", providerLabel(offer.provider)));
    const modelCell = el("td");
    modelCell.append(el("code", "mt-code", offer.model || "—"));
    tr.append(modelCell);
    tr.append(el("td", "num", fmt(offer.input_rub)));
    tr.append(el("td", "num", fmt(offer.output_rub)));
    tr.append(el("td", "num", offer.cached_rub == null ? "—" : fmt(offer.cached_rub)));
    tr.append(el("td", "num", ctxText(offer.context_length)));
    let status = "только сверка";
    let cls = "mt-status";
    if (serving) { status = "сейчас раздаёт"; cls += " is-serving"; }
    else if (offer.eligible) { status = "можно переключить"; cls += " is-ready"; }
    const statusCell = el("td", cls, status);
    statusCell.title = offer.exclusion_reason || status;
    tr.append(statusCell);
    body.append(tr);
  }
}

function drawCounts(rows) {
  const count = $("mt-count");
  if (count) count.textContent = `показано ${rows.length} из ${allGroups().length}`;
  const models = rows.reduce((sum, group) => sum + group.models.length, 0);
  const apply = $("mt-apply-k");
  if (apply) {
    apply.textContent = `Поставить цену (${rows.length})`;
    apply.title = `Закупка × множитель для ${models} моделей в отфильтрованных строках`;
  }
  const show = $("mt-show");
  if (show) show.textContent = `Показать (${rows.length})`;
  const hide = $("mt-hide");
  if (hide) hide.textContent = `Скрыть (${rows.length})`;
}

function bulkIds(rows) {
  const ids = [];
  for (const group of rows) for (const model of group.models) ids.push(model.id);
  return ids;
}

/** Показать или скрыть все модели в отфильтрованных строках. */
async function bulkVisibility(visible) {
  const rows = visibleGroups();
  if (!rows.length) return;
  const ids = bulkIds(rows);
  const verb = visible ? "Показать" : "Скрыть";
  const done = visible ? "Показано" : "Скрыто";
  if (!confirm(`${verb} ${rows.length} строк (${ids.length} моделей)?`)) return;
  const button = $(visible ? "mt-show" : "mt-hide");
  if (button) button.disabled = true;
  try {
    const result = await API("POST", "/v1/admin/prices/bulk/visibility", { ids, visible });
    for (const group of rows) {
      for (const model of group.models) model.listed = visible;
      group.listed = visible;
    }
    draw();
    setNote(`${done}: ${result.updated} из ${result.total}.`);
  } catch (e) {
    setNote(`Не удалось: ${e.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

/** Цена клиенту = закупка × множитель для всех текстовых моделей в фильтре. */
async function bulkPricing() {
  const rows = visibleGroups().filter((group) => group.group === "text" || group.group === "code");
  if (!rows.length) {
    setNote("В фильтре нет текстовых моделей.");
    return;
  }
  const input = $("mt-k");
  const multiplier = Number(input && input.value);
  if (!Number.isFinite(multiplier) || multiplier < 0.1 || multiplier > 20) {
    setNote("Множитель должен быть от 0.1 до 20.");
    return;
  }
  const items = [];
  for (const group of rows) {
    for (const model of group.models) {
      const cost = model.cost || {};
      const item = { id: model.id };
      if (cost.rub_in) item.input = cost.rub_in;
      if (cost.rub_out) item.output = cost.rub_out;
      if (cost.rub_in) item.cached_input = cost.rub_in * 0.1;
      if (item.input || item.output) items.push(item);
    }
  }
  if (!items.length) {
    setNote("У моделей в фильтре нет закупки — умножать нечего.");
    return;
  }
  const sample = rows.slice(0, 3).map((group) => {
    const model = group.primary;
    const cost = model.cost || {};
    const oldIn = model.sell && model.sell.rub_in ? fmt(model.sell.rub_in) : "—";
    const oldOut = model.sell && model.sell.rub_out ? fmt(model.sell.rub_out) : "—";
    const newIn = cost.rub_in ? fmt(cost.rub_in * multiplier) : "—";
    const newOut = cost.rub_out ? fmt(cost.rub_out * multiplier) : "—";
    return `• ${group.name}: ${oldIn} / ${oldOut} → ${newIn} / ${newOut}`;
  }).join("\n");
  if (!confirm(`Поставить цену клиенту = закупка × ${multiplier} для ${rows.length} строк (${items.length} моделей)?\n\n${sample}\n\nПрайс изменится сразу.`)) return;
  const button = $("mt-apply-k");
  if (button) button.disabled = true;
  setNote("Записываем цены…");
  try {
    const result = await API("POST", "/v1/admin/prices/bulk/pricing", { multiplier, items });
    await load(true);
    const failed = (result.failed || []).length;
    setNote(`Цены обновлены: ${result.updated} из ${result.total}${failed ? `, не удалось: ${failed}` : ""}.`);
  } catch (e) {
    setNote(`Не удалось: ${e.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

function setNote(text) {
  const note = $("mt-note");
  if (note) note.textContent = text || "";
}

function syncControls() {
  const vis = $("mt-vis");
  if (vis) vis.value = state.visibility;
  const sell = $("mt-sell");
  if (sell) sell.checked = state.sellOnly;
  drawProviderFilter();
}

/** Шапка: кнопка сортировки и поле-фильтр в каждой колонке. */
function drawHead() {
  const row = ROOT.querySelector("#mt-models thead tr");
  if (!row) return;
  row.replaceChildren();
  for (const column of COLUMNS) {
    const th = el("th", column.cls || "");
    const line = el("div", "mt-th");
    const input = el("input", "mt-col");
    input.type = "text";
    input.placeholder = column.label;
    input.value = state.cols[column.key] || "";
    input.setAttribute("aria-label", "Фильтр: " + column.label);
    input.title = "Слово — поиск по тексту; >5, <2, =1, >=100 — сравнение чисел";
    input.oninput = () => { state.cols[column.key] = input.value; drawBody(); };
    const button = el("button", "mt-sort" + (state.sort === column.key ? " on" : ""));
    button.type = "button";
    button.title = "Сортировать: " + column.label;
    button.textContent = state.sort === column.key ? (state.dir === "desc" ? "↓" : "↑") : "↕";
    button.onclick = () => {
      if (state.sort === column.key) state.dir = state.dir === "asc" ? "desc" : "asc";
      else { state.sort = column.key; state.dir = column.numeric ? "desc" : "asc"; }
      draw();
    };
    line.append(input, button);
    th.append(line);
    if (column.sub) th.append(el("small", "", column.sub));
    row.append(th);
  }
}

function drawBody() {
  const rows = visibleGroups();
  drawCounts(rows);
  drawModels(rows);
  drawOffers();
}

function draw() {
  saveFilters();
  drawHead();
  drawGroups();
  const rows = visibleGroups();
  drawCounts(rows);
  drawModels(rows);
  drawOffers();
}

// ── данные ────────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Обычная загрузка — из кэша релея, без походов на сайты поставщиков. */
async function load(force = false) {
  if (loading) return;
  loading = true;
  const query = new URLSearchParams({ compact: "1" });
  if (force) query.set("refresh", "1");
  try {
    const data = await API("GET", "/v1/admin/prices?" + query.toString());
    DATA = data || {};
    error = "";
    syncControls();
    draw();
    if (force) setNote("");
  } catch (e) {
    error = String(e.message || e);
    setNote(`Не удалось загрузить: ${error}`);
  } finally {
    loading = false;
  }
}

/** Кнопка: собрать прайсы с сайтов поставщиков и пересчитать маршруты. */
async function refreshPrices() {
  const button = $("mt-refresh");
  if (button) button.disabled = true;
  setNote("Собираем цены с поставщиков… это может занять до минуты.");
  try {
    // снимок сравниваем в его собственном формате: у /v1/admin/prices время
    // отдаётся как «2026-10-29 21:03 UTC», у снимка — ISO
    const before = await API("GET", "/v1/admin/provider-prices").catch(() => null);
    const was = (before && before.generated_at) || "";
    await Promise.allSettled([
      API("POST", "/v1/admin/provider-prices/refresh", {}),
      API("POST", "/v1/admin/provider-routing/refresh", {}),
    ]);
    for (let i = 0; i < 30; i += 1) {
      await sleep(3000);
      const snapshot = await API("GET", "/v1/admin/provider-prices").catch(() => null);
      if (snapshot && snapshot.generated_at && snapshot.generated_at !== was) break;
    }
    await load(true);
    setNote(`Цены обновлены: ${(DATA && DATA.generated_at) || "—"}.`);
  } catch (e) {
    setNote(`Не удалось обновить: ${e.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

function wire() {
  const prov = $("mt-provider");
  if (prov) prov.onchange = () => { state.provider = prov.value; draw(); };
  const vis = $("mt-vis");
  if (vis) vis.onchange = () => { state.visibility = vis.value; draw(); };
  const sell = $("mt-sell");
  if (sell) sell.onchange = () => { state.sellOnly = sell.checked; draw(); };
  const refresh = $("mt-refresh");
  if (refresh) refresh.onclick = refreshPrices;
  const apply = $("mt-apply-k");
  if (apply) apply.onclick = bulkPricing;
  const show = $("mt-show");
  if (show) show.onclick = () => bulkVisibility(true);
  const hide = $("mt-hide");
  if (hide) hide.onclick = () => bulkVisibility(false);
}

// ── перегородка между таблицами ───────────────────────────────────────────────

const SPLIT_KEY = "nm.models.split";

/** Границу можно тянуть мышью или стрелками; высота запоминается в браузере. */
function setupSplit() {
  const box = $("mt");
  const split = $("mt-split");
  const top = $("mt-top-pane");
  if (!box || !split || !top) return;
  try {
    const saved = Number(localStorage.getItem(SPLIT_KEY));
    if (Number.isFinite(saved) && saved > 0) top.style.flexBasis = `${saved}px`;
  } catch (_) { /* приватный режим */ }
  let drag = false;
  const clamp = (value) => Math.max(140, Math.min(value, box.getBoundingClientRect().height - 140));
  const move = (event) => {
    if (!drag) return;
    const rect = box.getBoundingClientRect();
    top.style.flexBasis = `${Math.round(clamp(event.clientY - rect.top))}px`;
  };
  const stop = () => {
    if (!drag) return;
    drag = false;
    document.body.classList.remove("mt-resizing");
    try { localStorage.setItem(SPLIT_KEY, String(Math.round(top.getBoundingClientRect().height))); } catch (_) { /* ничего */ }
  };
  split.addEventListener("pointerdown", (event) => {
    drag = true;
    split.setPointerCapture(event.pointerId);
    document.body.classList.add("mt-resizing");
    event.preventDefault();
  });
  split.addEventListener("pointermove", move);
  split.addEventListener("pointerup", stop);
  split.addEventListener("pointercancel", stop);
  split.addEventListener("keydown", (event) => {
    const step = event.key === "ArrowUp" ? -40 : event.key === "ArrowDown" ? 40 : 0;
    if (!step) return;
    event.preventDefault();
    const current = top.getBoundingClientRect().height;
    top.style.flexBasis = `${Math.round(clamp(current + step))}px`;
    try { localStorage.setItem(SPLIT_KEY, String(Math.round(clamp(current + step)))); } catch (_) { /* ничего */ }
  });
}

export function mount(root, ctx) {
  if (!root) return;
  ROOT = root;
  CTX = ctx;
  API = ctx.api;
  if (!root.dataset.started) {
    readFilters();
    root.dataset.started = "1";
  }
  root.innerHTML = TEMPLATE;
  splitVisible(Boolean(state.sel));
  setupSplit();
  wire();
  syncControls();
  drawGroups();
  if (DATA) {
    draw();
  } else {
    load();
  }
}
