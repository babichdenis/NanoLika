// Цены: наше меню с закупкой и розницей, поставщики, рынок и черновики.
// Страница доступна по HTTPS. Административные данные требуют пароля.
// Пароль хранится только в текущей вкладке, если браузер разрешает хранилище.
// Private browsing may disallow storage. Login still works in memory.
const memoryStorage = new Map();
const dirtyPrices = new Set();
const SS = {
  getItem(key) { try { return window.sessionStorage.getItem(key); } catch { return memoryStorage.get(key) || null; } },
  setItem(key, value) { memoryStorage.set(key, value); try { window.sessionStorage.setItem(key, value); } catch {} }
};
let TOKEN = SS.getItem("nm.admin") || SS.getItem("nm.admin.token") || "";

/** Каталог рынка (catalog.json): мировые цены, возможности, качество. Может не загрузиться. */
const catalog = {
  bySlug: new Map(),
  ready: false,
};

const state = {
  data: null,
  k: 5,
  caps: new Set(),      // возможности из каталога: image, audio_in, reasoning, open_weights …
  band: "",             // класс мировой цены
  hereOnly: "",         // "", "yes" (есть у нас), "no" (только на рынке)
  worldIn: null,        // $ за 1M входа, не больше
  ctxMin: 0,            // контекст, не меньше
  iqMin: null,          // качество (индекс), не меньше
  showEmpty: true,      // показывать всё; пустые строки уходят в конец списка
  mode: "",             // режим: chat, embedding, image_generation …
  q: "",
  group: "all",
  vendors: new Set(),
  providers: new Set(),
  visibility: "all",
  priceMin: null,
  priceMax: null,
  sort: "",
  dir: "asc",
  onlySell: false,
};

/** Группы моделей: порядок чипов и подписи. */
const GROUPS = [
  ["all", "Всё"],
  ["text", "Текст"],
  ["code", "Код"],
  ["image", "Картинки"],
  ["video", "Видео"],
  ["audio", "Озвучка и распознавание"],
  ["embedding", "Эмбеддинги"],
  ["rerank", "Ранжирование"],
];

const PROVIDER_NAMES = {
  aitunnel: "AITUNNEL",
  openrouter: "OpenRouter",
  polza: "Polza",
  proxyapi: "ProxyAPI",
  "proxyapi-openai": "ProxyAPI OpenAI",
  routerai: "RouterAI",
  bothub: "Bothub",
  neuroapi: "NeuroAPI",
  own: "DeepSeek официальный",
  deepseek: "DeepSeek",
  openai: "OpenAI",
  byteplus: "BytePlus",
};

/** Вендоры: порядок правил важен, первое совпадение выигрывает. */
const VENDOR_RULES = [
  ["deepseek", "DeepSeek", /\bdeepseek\b/],
  ["anthropic", "Anthropic", /\b(claude|anthropic)\b/],
  ["x-ai", "xAI", /\b(grok|x-ai|xai)\b/],
  ["moonshotai", "Moonshot", /\b(kimi|moonshot|moonshotai)\b/],
  ["z-ai", "Z.ai", /\b(glm|z-ai|zai|chatglm)\b/],
  ["qwen", "Qwen", /\b(qwen|qwen2|qwq|wanx?|tongyi|dashscope)\b/],
  ["minimax", "MiniMax", /\b(minimax|hailuo|abab)\b/],
  ["mistral", "Mistral", /\b(mistral|mixtral|codestral|magistral)\b/],
  ["meta", "Meta", /\b(llama|meta-llama|meta)\b/],
  ["nvidia", "NVIDIA", /\b(nvidia|nemotron)\b/],
  ["google", "Google", /\b(gemini|gemma|veo|imagen|palm|google)\b/],
  ["bytedance", "ByteDance", /\b(doubao|seedream|seedance|seededit|bytedance|volc)\b/],
  ["alibaba", "Alibaba", /\b(alibaba|bailian)\b/],
  ["kuaishou", "Kuaishou", /\b(kling|kuaishou|kolors)\b/],
  ["tencent", "Tencent", /\b(hunyuan|tencent)\b/],
  ["baidu", "Baidu", /\b(ernie|wenxin|baidu)\b/],
  ["openai", "OpenAI", /\b(openai|gpt|chatgpt|davinci|whisper|tts-1|sora|dall-e|o[134])\b/],
  ["black-forest", "Black Forest", /\b(flux|black-forest|bfl)\b/],
  ["stability", "Stability", /\b(stable-diffusion|sdxl|stability)\b/],
  ["runway", "Runway", /\b(runway|gen-?[34])\b/],
  ["luma", "Luma", /\b(luma|ray-?2)\b/],
  ["suno", "Suno", /\b(suno|chirp)\b/],
  ["elevenlabs", "ElevenLabs", /\b(elevenlabs|eleven)\b/],
  ["deepgram", "Deepgram", /\b(deepgram)\b/],
  ["cohere", "Cohere", /\b(command|cohere)\b/],
];

const GROUP_UNITS = { image: "за картинку", video: "за секунду", audio: "за 1000 знаков" };

function h(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = String(text);
  if (cls) el.className = cls;
  return el;
}

function money(v, digits) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const d = digits ?? (Math.abs(v) < 10 ? 2 : 1);
  return v.toLocaleString("ru-RU", { maximumFractionDigits: d, minimumFractionDigits: 0 });
}

/** Число в ячейке: пустая строка, когда значения нет. */
function figure(v, digits) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "";
  return money(Number(v), digits);
}

function pair(a, b) {
  if ((a === null || a === undefined) && (b === null || b === undefined)) return "—";
  return `${money(a)} / ${money(b)}`;
}

function ratioText(r) {
  if (r === null || r === undefined) return "—";
  return "×" + money(r, 2);
}

function stamp(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" });
  } catch {
    return String(value);
  }
}

function providerLabel(name) {
  if (!name) return "";
  if (PROVIDER_NAMES[name]) return PROVIDER_NAMES[name];
  return String(name).replace(/[-_]/g, " ").replace(/\b\p{L}/gu, (c) => c.toUpperCase());
}

/** Начала id, которые вендором не являются: «text-embedding-4» это не вендор text. */
const NOT_A_VENDOR = new Set([
  "text", "chat", "auto", "general", "base", "model", "llm", "tts", "asr", "stt", "embed",
  "embedding", "embeddings", "image", "images", "video", "videos", "audio", "speech", "vision",
  "nano", "lite", "fast", "plus", "pro", "max", "mini", "small", "large", "omni", "flash",
]);

/** Ключ, по которому модель меню ищет себя в каталоге. */
function slugOf(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/\([^)]*\)/g, " ")
    .split(":")[0]
    .split("/")
    .pop()
    .replace(/[-_\s]*(preview|latest|stable|exp|experimental|beta|rc\d*)$/, "")
    .replace(/-(20\d\d)(\d\d)?(\d\d)?$/, "")
    .replace(/[^a-z0-9.]+/g, "-")
    .replace(/^-|-$/g, "");
}

/** Данные каталога для модели меню: по id, имени или «цене как». */
function infoOf(m) {
  if (!catalog.ready) return null;
  for (const key of [m.id, m.name, m.priced_as]) {
    const hit = catalog.bySlug.get(slugOf(key));
    if (hit) return hit;
  }
  return null;
}

/** Оценка качества 0–100; null, когда Artificial Analysis модель не мерил. */
function qualityOf(m) {
  const info = infoOf(m);
  const value = info && info.aa ? info.aa.intelligence : null;
  return typeof value === "number" ? value : null;
}

function capsOf(m) {
  const info = infoOf(m);
  return (info && info.caps) || {};
}

function worldInOf(m) {
  const info = infoOf(m);
  if (!info) return null;
  const value = info.world_in_usd ?? (info.aa ? info.aa.price_in_usd : null);
  return typeof value === "number" ? value : null;
}

/** Цена за единицу у медиа (секунда видео, картинка): в $ за токены её не выразить. */
function unitPriceOf(m) {
  const info = infoOf(m);
  return info && info.unit_usd ? info.unit_usd : null;
}

/** Есть ли у модели хоть что-то, кроме названия. */
function hasData(m) {
  const info = infoOf(m);
  if (!info) return false;
  return Boolean(info.has_data);
}

function worldOutOf(m) {
  const info = infoOf(m);
  if (!info) return null;
  const value = info.world_out_usd ?? (info.aa ? info.aa.price_out_usd : null);
  return typeof value === "number" ? value : null;
}

function vendorOf(m) {
  const hay = `${m.id || ""} ${m.name || ""} ${m.priced_as || ""}`.toLowerCase();
  for (const [id, label, rule] of VENDOR_RULES) {
    if (rule.test(hay)) return { id, label };
  }
  const head = String(m.id || "").split(/[-/_.]/)[0].toLowerCase();
  if (head.length >= 3 && !NOT_A_VENDOR.has(head)) {
    return { id: head, label: head.replace(/\b\p{L}/gu, (c) => c.toUpperCase()) };
  }
  return { id: "other", label: "без вендора" };
}

function groupOf(m) {
  const kind = String(m.kind || "chat");
  const lanes = (m.lanes || []).map((lane) => String(lane).toLowerCase());
  const id = String(m.id || "").toLowerCase();
  if (kind === "rerank") return "rerank";
  if (kind === "image") return "image";
  if (kind === "video") return "video";
  if (kind === "embedding" || (kind !== "chat" && /embed/.test(id))) return "embedding";
  if (kind === "audio" || lanes.some((lane) => /speech|audio|transcri|voice|music/.test(lane))) return "audio";
  if (lanes.some((lane) => /code|coding|coder|dev/.test(lane)) || /coder|coding|codestral|devstral/.test(id)) return "code";
  return "text";
}

function groupLabel(id) {
  const found = GROUPS.find(([key]) => key === id);
  return found ? found[1] : "Всё";
}

// -- маршруты и цены поставщиков -------------------------------------------------

function offersOf(m) {
  return (m.routing && m.routing.offers) || [];
}

/** Кто обслуживает модель сейчас: `selected`, иначе поле `provider`. */
function servingProvider(m) {
  if (m.routing && m.routing.selected) return m.routing.selected;
  return m.provider || "";
}

function offerOf(m, provider) {
  if (!provider) return null;
  return offersOf(m).find((offer) => offer.provider === provider) || null;
}

/** Оценка запроса: 1000 входных и 512 выходных токенов, без кеша и инструментов. */
function estimate(offer) {
  if (!offer) return null;
  if (typeof offer.estimated_rub === "number") return offer.estimated_rub;
  const input = Number(offer.input_rub || 0);
  const output = Number(offer.output_rub || 0);
  if (!input && !output) return null;
  return (input * 1000 + output * 512) / 1e6;
}

/** Закупка входа для фильтра по цене: котировка выбранного, иначе прайс конфигурации. */
function entryPrice(m) {
  const served = offerOf(m, servingProvider(m));
  if (served && typeof served.input_rub === "number") return served.input_rub;
  return m.cost ? m.cost.rub_in : null;
}

function cheapestOffer(m) {
  const list = offersOf(m).filter((offer) => estimate(offer) !== null);
  if (!list.length) return null;
  return list.reduce((best, offer) => (estimate(offer) < estimate(best) ? offer : best), list[0]);
}

function connectedProviders() {
  const p = (state.data && state.data.providers) || {};
  const list = p.connected || [];
  const seen = new Set();
  const out = [];
  for (const name of list) {
    if (!name || seen.has(name)) continue;
    seen.add(name);
    out.push(name);
  }
  for (const m of (state.data && state.data.models) || []) {
    for (const offer of offersOf(m)) {
      if (!offer.provider || seen.has(offer.provider)) continue;
      seen.add(offer.provider);
      out.push(offer.provider);
    }
  }
  return out.sort((a, b) => providerLabel(a).localeCompare(providerLabel(b), "ru"));
}

// -- фильтры ---------------------------------------------------------------------

/** Модели, проходящие все фильтры, кроме группы: по ним считаются счётчики чипов. */
function baseRows() {
  const q = state.q.trim().toLowerCase();
  return ((state.data && state.data.models) || []).filter((m) => {
    if (state.onlySell && !(m.sell.rub_in || m.sell.rub_out)) return false;
    if (state.visibility === "visible" && !m.listed) return false;
    if (state.visibility === "hidden" && m.listed) return false;
    if (state.vendors.size && !state.vendors.has(vendorOf(m).id)) return false;
    if (state.providers.size) {
      const has = offersOf(m).some((offer) => state.providers.has(offer.provider)) || state.providers.has(m.provider || "");
      if (!has) return false;
    }
    if (state.priceMin !== null || state.priceMax !== null) {
      const price = entryPrice(m);
      // Нет ставки за миллион токенов (медиа продаётся за штуку) — цена не в этом диапазоне.
      if (price === null || price === undefined || !price) return false;
      if (state.priceMin !== null && price < state.priceMin) return false;
      if (state.priceMax !== null && price > state.priceMax) return false;
    }
    // пустые строки не прячем, а уводим вниз: показываем всё, что есть
    if (!state.showEmpty && !hasData(m)) return false;
    if (state.hereOnly === "yes" && !infoOf(m)) return false;
    if (state.hereOnly === "no" && infoOf(m)) return false;
    if (state.band) {
      const band = infoOf(m) ? infoOf(m).band : null;
      if (state.band === "none" ? Boolean(band) : band !== state.band) return false;
    }
    if (state.mode) {
      const info = infoOf(m);
      if (!info || info.mode !== state.mode) return false;
    }
    if (state.caps.size) {
      const caps = capsOf(m);
      for (const cap of state.caps) if (!caps[cap]) return false;
    }
    if (state.ctxMin) {
      const ctx = (infoOf(m) || {}).context || m.context || 0;
      if (ctx < state.ctxMin) return false;
    }
    if (state.worldIn !== null) {
      const price = worldInOf(m);
      if (price === null || price > state.worldIn) return false;
    }
    if (state.iqMin !== null) {
      const iq = qualityOf(m);
      if (iq === null || iq < state.iqMin) return false;
    }
    if (!q) return true;
    const info = infoOf(m);
    const hay = `${m.name || ""} ${m.id || ""} ${info && info.aa ? info.aa.creator || "" : ""}`.toLowerCase();
    return q.split(/\s+/).every((word) => !word || hay.includes(word));
  });
}

function visibleRows() {
  const rows = baseRows().filter((m) => state.group === "all" || groupOf(m) === state.group);
  return sortRows(rows);
}

function sortValue(m, key) {
  if (key.startsWith("prov:")) {
    const offer = offerOf(m, key.slice(5));
    return offer ? offer.input_rub : null;
  }
  switch (key) {
    case "name": return String(m.name || "").toLowerCase();
    case "vendor": return vendorOf(m).label.toLowerCase();
    case "context": return m.context || 0;
    case "max_output": return m.max_output || 0;
    case "cost_in": return m.cost ? m.cost.rub_in : null;
    case "cost_out": return m.cost ? m.cost.rub_out : null;
    case "sell_in": return m.sell ? m.sell.rub_in : null;
    case "sell_out": return m.sell ? m.sell.rub_out : null;
    case "ratio_in": return m.ratio ? m.ratio.in : null;
    case "ratio_out": return m.ratio ? m.ratio.out : null;
    case "iq": return qualityOf(m);
    case "world_in": return worldInOf(m);
    case "world_out": return worldOutOf(m);
    case "estimate": {
      const offer = cheapestOffer(m);
      return offer ? estimate(offer) : null;
    }
    default: return null;
  }
}

/** Строки, у которых есть хоть одна цифра, идут первыми: пустое - это хвост таблицы. */
function dataLast(rows) {
  return rows.slice().sort((a, b) => Number(hasData(b)) - Number(hasData(a)));
}

function sortRows(rows) {
  if (!state.sort) return dataLast(rows);
  const sign = state.dir === "desc" ? -1 : 1;
  return dataLast(rows).sort((a, b) => {
    const left = sortValue(a, state.sort);
    const right = sortValue(b, state.sort);
    const leftEmpty = left === null || left === undefined || left === "";
    const rightEmpty = right === null || right === undefined || right === "";
    if (leftEmpty && rightEmpty) return 0;
    if (leftEmpty) return 1;
    if (rightEmpty) return -1;
    if (typeof left === "number" && typeof right === "number") return (left - right) * sign;
    return String(left).localeCompare(String(right), "ru") * sign;
  });
}

// -- адрес страницы --------------------------------------------------------------

const HASH_KEYS = ["q", "g", "v", "p", "vis", "pi", "pa", "s", "d", "k", "sell"];

function readHash() {
  const raw = location.hash.replace(/^#/, "");
  if (!raw) return false;
  const p = new URLSearchParams(raw);
  if (!HASH_KEYS.some((key) => p.has(key))) return false;
  state.q = p.get("q") || "";
  state.group = p.get("g") || "all";
  state.vendors = new Set((p.get("v") || "").split(",").filter(Boolean));
  state.providers = new Set((p.get("p") || "").split(",").filter(Boolean));
  const vis = p.get("vis") || "all";
  state.visibility = ["all", "visible", "hidden"].includes(vis) ? vis : "all";
  state.priceMin = p.has("pi") && p.get("pi") !== "" ? Number(p.get("pi")) : null;
  state.priceMax = p.has("pa") && p.get("pa") !== "" ? Number(p.get("pa")) : null;
  if (state.priceMin !== null && !Number.isFinite(state.priceMin)) state.priceMin = null;
  if (state.priceMax !== null && !Number.isFinite(state.priceMax)) state.priceMax = null;
  const sort = p.get("s") || "";
  state.sort = SORT_KEYS.some(([key]) => key === sort) ? sort : "";
  state.dir = p.get("d") === "desc" ? "desc" : "asc";
  const k = Number(p.get("k"));
  state.k = Number.isFinite(k) && k > 0 ? k : 5;
  state.onlySell = p.get("sell") === "1";
  return true;
}

function writeHash() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  if (state.group !== "all") p.set("g", state.group);
  if (state.vendors.size) p.set("v", [...state.vendors].join(","));
  if (state.providers.size) p.set("p", [...state.providers].join(","));
  if (state.visibility !== "all") p.set("vis", state.visibility);
  if (state.priceMin !== null) p.set("pi", String(state.priceMin));
  if (state.priceMax !== null) p.set("pa", String(state.priceMax));
  if (state.sort) p.set("s", state.sort);
  if (state.sort && state.dir === "desc") p.set("d", "desc");
  if (state.k !== 5) p.set("k", String(state.k));
  if (state.onlySell) p.set("sell", "1");
  const query = p.toString();
  const base = location.pathname + location.search;
  history.replaceState(null, "", query ? `${base}#${query}` : base);
}

// -- запросы ---------------------------------------------------------------------

async function api(path, options = {}) {
  const r = await fetch(path, { ...options, cache: "no-store", headers: { "X-Admin-Token": TOKEN, "Content-Type": "application/json" } });
  if (r.status === 401) throw new Error("Пароль не подошёл. Введите пароль администратора целиком, не код из письма.");
  if (!r.ok) throw new Error(`ответ ${r.status}`);
  return r.json();
}

/** Пока токена нет, страница спрашивает его и больше ничего не показывает. */
function showGate() {
  document.getElementById("gate").hidden = false;
  document.getElementById("bar").hidden = true;
  document.getElementById("content").hidden = true;
  document.getElementById("gate-token").focus();
}

function drawRate() {
  const box = document.getElementById("rate");
  const rate = state.data.rate || {};
  box.replaceChildren();
  if (state.data.routing?.usd_rub) {
    box.append(h("span", "Курс закупки для USD-маршрутов: "), h("b", `${money(state.data.routing.usd_rub, 2)} ₽/$`));
    box.append(h("div", `Прежние цены из конфигурации пересчитываются отдельно: 1 ¥ = ${money(rate.cny_rub, 2)} ₽. Рублёвые API-котировки повторно не конвертируются.`, "meta"));
    return;
  }
  const value = Number(rate.cny_rub || 0);
  box.append(h("span", "Курс: "));
  box.append(h("b", value ? `1 ¥ = ${money(value, 2)} ₽` : "не задан"));
  if (!rate.working) {
    box.append(h("span", " · "));
    box.append(h("span", `справочный (${rate.source || "нет курса"})`, "bad"));
  }
  if (rate.usd_cny) box.append(h("span", ` · USD/CNY ${money(rate.usd_cny, 2)}`, "meta"));
  if (rate.rub_per_usd) box.append(h("span", ` · ₽ за доллар ${money(rate.rub_per_usd, 2)}`, "meta"));
  if (!rate.working) {
    box.append(
      h("div", "Пока RUB_PER_USD не задан, релей не переводит юани в рубли: ниже рубли посчитаны по справочному курсу из снимка ЦБ, это не рабочая ставка.", "meta"),
    );
  }
}

function drawCounts() {
  const c = state.data.count || {};
  const box = document.getElementById("counts");
  box.replaceChildren();
  const add = (label, value) => {
    if (box.childNodes.length) box.append(h("span", " · ", "meta"));
    box.append(h("span", label + " ", "meta"), h("b", String(value ?? 0)));
  };
  add("моделей", c.models);
  add("с закупкой", c.with_cost);
  add("с ценой продажи", c.with_sell);
}

// -- чипы групп и фильтры --------------------------------------------------------

function drawGroups() {
  const box = document.getElementById("groups");
  box.replaceChildren();
  const rows = baseRows();
  const counts = new Map();
  for (const m of rows) {
    const group = groupOf(m);
    counts.set(group, (counts.get(group) || 0) + 1);
  }
  for (const [id, label] of GROUPS) {
    const n = id === "all" ? rows.length : counts.get(id) || 0;
    const b = h("button", label);
    b.type = "button";
    b.className = "chip" + (state.group === id ? " on" : "");
    b.append(h("b", String(n)));
    b.setAttribute("aria-pressed", state.group === id ? "true" : "false");
    b.onclick = () => {
      state.group = id;
      draw();
    };
    box.append(b);
  }
}

function drawVisibilityChips() {
  const box = document.getElementById("status");
  box.replaceChildren();
  const rows = baseRows();
  const items = [
    ["all", "Все", rows.length],
    ["visible", "Видно клиенту", rows.filter((m) => m.listed).length],
    ["hidden", "Скрыто", rows.filter((m) => !m.listed).length],
  ];
  for (const [id, label, n] of items) {
    const b = h("button", label);
    b.type = "button";
    b.className = "chip" + (state.visibility === id ? " on" : "");
    b.append(h("b", String(n)));
    b.setAttribute("aria-pressed", state.visibility === id ? "true" : "false");
    b.onclick = () => {
      state.visibility = id;
      draw();
    };
    box.append(b);
  }
}

function drawMulti(summaryId, menuId, entries, selected, onToggle, emptyLabel) {
  const summary = document.getElementById(summaryId);
  const menu = document.getElementById(menuId);
  summary.textContent = selected.size ? `${emptyLabel}: ${selected.size}` : `${emptyLabel}: ${emptyLabel === "Вендор" ? "все" : "любой"}`;
  menu.replaceChildren();
  if (!entries.length) {
    menu.append(h("div", "Нет данных", "meta empty"));
    return;
  }
  for (const { id, label, count } of entries) {
    const row = h("label", undefined, "multi-row");
    const input = h("input");
    input.type = "checkbox";
    input.checked = selected.has(id);
    input.onchange = () => {
      if (input.checked) selected.add(id);
      else selected.delete(id);
      onToggle();
    };
    row.append(input, h("span", label), h("b", String(count), "cnt"));
    menu.append(row);
  }
}

function drawVendors() {
  const rows = baseRows();
  const seen = new Map();
  for (const m of rows) {
    const vendor = vendorOf(m);
    if (!vendor.id) continue;
    seen.set(vendor.id, seen.get(vendor.id) || { id: vendor.id, label: vendor.label, count: 0 });
    seen.get(vendor.id).count += 1;
  }
  const entries = [...seen.values()].sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, "ru"));
  drawMulti("vendor-sum", "vendor-menu", entries, state.vendors, draw, "Вендор");
}

function drawProvidersFilter() {
  const all = (state.data && state.data.models) || [];
  const entries = connectedProviders().map((name) => ({
    id: name,
    label: providerLabel(name),
    count: all.filter((m) => offerOf(m, name) || m.provider === name).length,
  }));
  drawMulti("prov-sum", "prov-menu", entries, state.providers, draw, "Есть у поставщика");
}

const SORT_KEYS = [
  ["", "как в меню"],
  ["name", "название"],
  ["vendor", "вендор"],
  ["context", "контекст"],
  ["max_output", "макс. выход"],
  ["cost_in", "закупка входа"],
  ["cost_out", "закупка выхода"],
  ["sell_in", "цена продажи входа"],
  ["sell_out", "цена продажи выхода"],
  ["ratio_in", "кратность входа"],
  ["ratio_out", "кратность выхода"],
  ["estimate", "оценка запроса"],
];

function drawSortControls() {
  const select = document.getElementById("sort");
  if (!select.options.length) {
    for (const [key, label] of SORT_KEYS) {
      const option = h("option", label);
      option.value = key;
      select.append(option);
    }
    select.onchange = () => {
      state.sort = select.value;
      draw();
    };
  }
  select.value = state.sort;
  const button = document.getElementById("sort-dir");
  button.textContent = state.dir === "desc" ? "↓" : "↑";
  button.disabled = !state.sort;
}

// -- ячейки таблицы меню ---------------------------------------------------------

function visibilityCell(m) {
  const cell = h("td", undefined, "pick");
  const label = h("label");
  const input = h("input"); input.type = "checkbox"; input.checked = Boolean(m.listed);
  input.setAttribute("aria-label", `Показывать клиентам: ${m.name}`);
  const text = h("span", input.checked ? " Видна" : " Скрыта", "pick-text");
  input.onchange = async () => {
    input.disabled = true;
    try {
      const result = await api(`/v1/admin/prices/models/${encodeURIComponent(m.id)}/visibility`, {
        method: "PUT", body: JSON.stringify({visible: input.checked}),
      });
      m.listed = result.visible; input.checked = result.visible;
      text.textContent = result.visible ? " Видна" : " Скрыта";
      document.getElementById("note").textContent = `Видимость «${m.name}» сохранена.`;
    } catch (error) {
      input.checked = Boolean(m.listed);
      document.getElementById("note").textContent = `Не сохранено: ${error.message}`;
    } finally { input.disabled = false; }
  };
  label.append(input, text); cell.append(label); return cell;
}

function modelCell(m) {
  const cell = h("td", undefined, "name");
  cell.append(h("div", m.name));
  const meta = h("div", undefined, "meta");
  meta.append(h("code", m.id));
  const lanes = (m.lanes || []).join(", ");
  if (lanes) meta.append(h("span", ` · ${lanes}`));
  if (m.recommended) meta.append(h("span", " · по умолчанию"));
  if (!m.listed) meta.append(h("span", " · скрыта из меню"));
  if (!m.default_on) meta.append(h("span", " · выключена у нового"));
  cell.append(meta);
  const provider=servingProvider(m);if(provider)cell.append(h("span",providerLabel(provider),"retail-provider"));
  return cell;
}

function mediaCost(m) {
  const cost = m.cost || {};
  const group = groupOf(m);
  const value = cost.rub_image ?? cost.rub_second ?? cost.rub_speech;
  if (value === null || value === undefined) return "";
  return `${money(value)} ${GROUP_UNITS[group] || ""}`.trim();
}

/** Столбец закупки: для текста пара вход/выход, для медиа цена за штуку. */
function costCell(m) {
  const group = groupOf(m);
  if (group === "text" || group === "code") {
    const cell = h("td", pair(m.cost?.rub_in, m.cost?.rub_out), "num cost");
    return cell;
  }
  const text = mediaCost(m);
  const cell = h("td", text || "—", "num cost");
  if (!text) cell.classList.add("meta");
  return cell;
}

/** Правка цены продажи: пересчитывает кратность и маржу на месте. */
function sellCell(m) {
  const cell=h("td",undefined,"num sell-edit"), commerce=m.commerce;
  if(!commerce){cell.textContent="Нет рабочего прайса";return cell;}
  const labels={input:"Вход / 1M",cached_input:"Кеш / 1M",output:"Ответ / 1M",image:"Картинка",image_2k:"Картинка 2K",image_input:"Исходная картинка",second:"Секунда видео",speech:"1000 знаков",minute:"Минута записи"};
  if(commerce.source==="legacy_cost")cell.append(h("small","Розничная ставка не задана. Сохраните цену продажи.","bad"));
  const fields=m.kind==='chat'?['input','cached_input','output']:m.kind==='image'?['image','image_2k']:m.kind==='video'?['second']:commerce.unit==='speech'?['speech']:['minute'];
  const inputs={};for(const key of fields){const label=h('label',labels[key],'retail-field'),input=h('input');input.type='number';input.min='0.000001';input.step='0.01';input.value=commerce.rates_rub[key]||'';input.className='sell-input';input.setAttribute('aria-label',`${labels[key]}: ${m.name}`);input.oninput=()=>dirtyPrices.add(m.id);inputs[key]=input;label.append(input);cell.append(label);}
  const save=h('button','Сохранить','chip'),status=h('span','','meta');save.type='button';save.onclick=async()=>{const values={};for(const [key,input] of Object.entries(inputs)){const n=Number(input.value);if(!Number.isFinite(n)||n<=0){status.textContent='Укажите положительную цену';return;}values[key]=n;}save.disabled=true;status.textContent='Сохраняем…';try{m.commerce=await api('/v1/admin/prices/models/'+encodeURIComponent(m.id)+'/pricing',{method:'PUT',body:JSON.stringify({revision:m.commerce.revision,rates_rub:values})});m.sell.rub_in=m.commerce.rates_rub.input;m.sell.rub_out=m.commerce.rates_rub.output;m.sell.rub_cached_in=m.commerce.rates_rub.cached_input;dirtyPrices.delete(m.id);status.textContent='Сохранено. Клиенты получат новый прайс при обновлении каталога.';recalcRow(m);}catch(e){status.textContent='Не сохранено: '+e.message;}finally{save.disabled=false;}};cell.append(save,status);return cell;
}

function numberOrNull(value) {
  const n = Number(String(value).replace(",", "."));
  return Number.isFinite(n) && n > 0 ? n : 0;
}

function ratioCell(m) {
  const cell = h("td", undefined, "num ratio");
  const hasSell = Boolean(m.sell.rub_in || m.sell.rub_out);
  if (hasSell && (m.ratio.in !== null || m.ratio.out !== null)) {
    cell.textContent = `${ratioText(m.ratio.in)} / ${ratioText(m.ratio.out)}`;
    const low = [m.ratio.in, m.ratio.out].some((r) => r !== null && r < 1.5);
    if (low) cell.classList.add("ratio-low");
  } else {
    cell.textContent = "—";
    cell.classList.add("meta");
  }
  return cell;
}

/** Ставка за миллион токенов есть только у текста: медиа продаётся за штуку. */
function perMillion(m) {
  const group = groupOf(m);
  return (group === "text" || group === "code") && Boolean(m.cost?.rub_in || m.cost?.rub_out);
}

function markupCell(m) {
  const k = state.k;
  const cell = h("td", undefined, "num markup");
  if (!perMillion(m)) {
    cell.textContent = "—";
    cell.classList.add("meta");
    cell.title = "Не помиллионная ставка: считается за штуку, секунду или знак";
    return cell;
  }
  const costIn = m.cost.rub_in;
  const costOut = m.cost.rub_out;
  cell.textContent = pair(costIn * k, costOut === null || costOut === undefined ? null : costOut * k);
  return cell;
}

function marginCell(m) {
  const k = state.k;
  const cell = h("td", undefined, "num margin");
  if (!perMillion(m)) {
    cell.textContent = "—";
    cell.classList.add("meta");
    return cell;
  }
  const costIn = m.cost.rub_in;
  const costOut = m.cost.rub_out;
  const sellIn = m.sell.rub_in || 0;
  const sellOut = m.sell.rub_out || 0;
  const hasSell = Boolean(sellIn || sellOut);
  const baseIn = hasSell && sellIn ? sellIn : costIn * k;
  const baseOut = hasSell && sellOut ? sellOut : costOut === null || costOut === undefined ? null : costOut * k;
  cell.textContent = pair(baseIn - costIn, baseOut === null ? null : baseOut - costOut);
  return cell;
}

function lastRequestCell(m) {
  const cell = h("td", undefined, "last");
  const request = m.last_request;
  if (!request) { cell.textContent = "Пока нет измерений"; cell.classList.add("meta"); return cell; }
  cell.append(h("div", `${providerLabel(request.provider)}: ${money(request.cost_rub, 6)} ₽`));
  cell.append(h("div", `${request.input_tokens} вход / ${request.output_tokens} ответ`, "meta"));
  const basis = request.basis === "catalog_estimate" ? "Расчёт по каталогу" : ["reported_rub", "reported_usd_converted"].includes(request.basis) ? "Стоимость из ответа API" : "Стоимость не подтверждена";
  cell.append(h("div", `${basis} · ${stamp(request.at * 1000)}`, "meta"));
  return cell;
}

/** Ячейка поставщика: цена входа и под ней цена выхода, ₽ за 1M. */
function providerCell(m, name) {
  const offer = offerOf(m, name);
  const served = servingProvider(m);
  const cell = h("td", undefined, "num prov-cell");
  if (served === name) {
    cell.classList.add("chosen");
    cell.append(h("span", "по расчёту", "now"));
  }
  if (!offer) {
    // Своя котировка без предложения: показываем ставку конфигурации, если она помиллионная.
    // У медиа ставки за миллион нет, оно продаётся за штуку: ячейка остаётся пустой.
    const own = m.cost?.rub_in;
    const ownOut = m.cost?.rub_out;
    if (served === name && (own || ownOut)) {
      cell.append(h("div", figure(own), "in"), h("div", figure(ownOut), "out"));
    }
    cell.title = `Нет предложения ${providerLabel(name)}`;
    return cell;
  }
  cell.append(h("div", figure(offer.input_rub), "in"), h("div", figure(offer.output_rub), "out"));
  const note = [];
  if (offer.eligible === false) note.push("вне диапазона автопереключения");
  else if (offer.eligible === true) note.push("доступен для автопереключения");
  if (offer.cached_rub !== undefined && offer.cached_rub !== null) note.push(`кеш ${money(offer.cached_rub)}`);
  const est = estimate(offer);
  if (est !== null) note.push(`запрос ${money(est, 4)} ₽`);
  if (note.length) cell.title = note.join(" · ");
  return cell;
}

/** Пересчёт кратности и маржи после правки цены продажи. */
function recalcRow(m) {
  const served = offerOf(m, servingProvider(m));
  const cost = m.cost || {};
  const sellIn = m.sell.rub_in || 0;
  const sellOut = m.sell.rub_out || 0;
  let inPrice = served && typeof served.input_rub === "number" ? served.input_rub : cost.rub_in;
  let outPrice = served && typeof served.output_rub === "number" ? served.output_rub : cost.rub_out;
  if (cost.rub_in !== null && cost.rub_in !== undefined) inPrice = inPrice ?? cost.rub_in;
  m.ratio = {
    in: inPrice && sellIn ? Math.round((sellIn / inPrice) * 100) / 100 : null,
    out: outPrice && sellOut ? Math.round((sellOut / outPrice) * 100) / 100 : null,
  };
  const row = document.querySelector(`#models tr[data-id="${cssEscape(m.id)}"]`);
  if (!row) return;
  const ratio = row.querySelector("td.ratio");
  const margin = row.querySelector("td.margin");
  if (ratio) {
    const fresh = ratioCell(m);
    ratio.textContent = fresh.textContent;
    ratio.className = fresh.className;
  }
  if (margin) {
    const fresh = marginCell(m);
    margin.textContent = fresh.textContent;
    margin.className = fresh.className;
  }
}

function cssEscape(value) {
  if (window.CSS && CSS.escape) return CSS.escape(value);
  return String(value).replace(/["\\]/g, "\\$&");
}

// -- таблица ---------------------------------------------------------------------

function sortableTh(label, key, cls) {
  const th = h("th", undefined, cls);
  const button = h("button", label, "sort-btn");
  button.type = "button";
  if (state.sort === key) {
    th.classList.add("sorted");
    button.append(h("span", state.dir === "desc" ? " ↓" : " ↑", "dir"));
  }
  button.onclick = () => {
    if (state.sort === key) state.dir = state.dir === "desc" ? "asc" : "desc";
    else {
      state.sort = key;
      state.dir = "asc";
    }
    draw();
  };
  th.append(button);
  return th;
}

function drawHead(providers, rows) {
  const head = document.querySelector("#models thead");
  head.replaceChildren();
  const top = h("tr");
  const group = "Показывать";
  top.append(h("th", group, "pick sticky-1"));
  const nameTh = sortableTh("Модель", "name", "name sticky-2");
  top.append(nameTh);
  top.append(sortableTh("Вендор", "vendor", "vendor"));
  top.append(sortableTh("Контекст", "context", "num ctx"));
  top.append(sortableTh("Макс. выход", "max_output", "num ctx"));
  top.append(sortableTh("$ вход", "world_in", "num world"));
  top.append(sortableTh("$ выход", "world_out", "num world"));
  top.append(sortableTh("Качество", "iq", "num iq"));
  top.append(h("th", "Умеет", "caps"));

  const stats = providerStats(rows, providers);
  for (const name of providers) {
    const th = sortableTh(providerLabel(name), `prov:${name}`, "prov num");
    const button = th.querySelector(".sort-btn");
    button.classList.add("p-name");
    const stat = stats.get(name) || { covered: 0, cheaper: 0, chosen: false };
    if (stat.chosen) {
      th.classList.add("chosen");
      button.prepend(h("span", "расчёт", "now"));
    }
    const sub = h("div", undefined, "p-stat");
    sub.append(h("span", `моделей: ${stat.covered}`));
    sub.append(h("span", ` · дешевле выбранного: ${stat.cheaper}`));
    th.append(sub);
    th.append(h("div", "вход / выход, ₽ за 1M", "p-unit"));
    th.dataset.provider = name;
    top.append(th);
  }

  top.append(sortableTh("Закупка, ₽", "cost_in", "num"));
  top.append(sortableTh("Продажа, ₽", "sell_in", "num"));
  top.append(sortableTh("Кратность", "ratio_in", "num"));
  top.append(h("th", "При наценке, ₽", "num markup"));
  top.append(h("th", "Маржа, ₽", "num margin"));
  top.append(h("th", "Последний запрос", "last"));
  head.append(top);
}

/** Итог по поставщику в шапке: сколько моделей покрывает и у скольких дешевле выбранного. */
function providerStats(rows, providers) {
  const chosenOf = (m) => servingProvider(m);
  const stats = new Map();
  for (const name of providers) stats.set(name, { covered: 0, cheaper: 0, chosen: false });
  for (const m of rows) {
    const served = chosenOf(m);
    const servedOffer = served ? offerOf(m, served) : null;
    const base = served ? estimate(servedOffer) : null;
    for (const name of providers) {
      const stat = stats.get(name);
      const offer = offerOf(m, name);
      if (served === name) stat.chosen = true;
      if (!offer) continue;
      stat.covered += 1;
      const est = estimate(offer);
      if (est === null) continue;
      if (name === served) continue;
      if (base === null || est < base) stat.cheaper += 1;
    }
  }
  return stats;
}

function drawTable() {
  const providers = connectedProviders();
  const rows = visibleRows();
  drawHead(providers, rows);
  const body = document.querySelector("#models tbody");
  body.replaceChildren();
  for (const m of rows) body.append(buildRow(m, providers));
  document.getElementById("chat-count").textContent = `${rows.length} из ${(state.data.models || []).length}`;
  const found = document.getElementById("found");
  found.textContent = `показано ${rows.length}`;
  drawRoutingNote();
}

function buildRow(m, providers) {
  const tr = h("tr");
  tr.dataset.id = m.id;
  const hasSell = Boolean(m.sell.rub_in || m.sell.rub_out);
  if (hasSell) tr.classList.add("has-sell");
  if (!m.cost || (m.cost.rub_in === null && m.cost.rub_out === null)) tr.classList.add("no-cost");

  const pick = visibilityCell(m);
  pick.classList.add("sticky-1");
  tr.append(pick);

  const name = modelCell(m);
  if(m.below_cost)name.append(h("span","Ниже закупки при скидке 30%","bad retail-warning"));
  name.classList.add("sticky-2");
  tr.append(name);

  const vendor = vendorOf(m);
  const vendorCell = h("td", undefined, "vendor");
  vendorCell.append(h("span", vendor.label, "vendor-label"));
  const g = groupOf(m);
  vendorCell.append(h("span", groupLabel(g), "meta group-tag"));
  tr.append(vendorCell);

  tr.append(h("td", m.context ? money(m.context, 0) : "—", "num ctx"));
  tr.append(h("td", m.max_output ? money(m.max_output, 0) : "—", "num ctx"));
  const media = unitPriceOf(m);
  tr.append(media ? h("td", `${money(media.usd, 4)} / ${media.unit}`, "num world unit") : worldCell(worldInOf(m)));
  tr.append(worldCell(worldOutOf(m)));
  const iq = qualityOf(m);
  tr.append(h("td", iq === null ? "—" : String(Math.round(iq)), iq === null ? "num iq dim" : "num iq"));
  tr.append(capsCell(capsOf(m)));

  for (const provider of providers) tr.append(providerCell(m, provider));

  tr.append(costCell(m));
  tr.append(sellCell(m));
  tr.append(ratioCell(m));
  tr.append(markupCell(m));
  tr.append(marginCell(m));
  tr.append(lastRequestCell(m));
  return tr;
}

/** Доллар за миллион токенов: мельче копейки округляем до четырёх знаков. */
function worldCell(value) {
  if (value === null || value === undefined) return h("td", "—", "num world dim");
  const text = value >= 1 ? money(value, 2) : String(value.toFixed(value < 0.01 ? 4 : 3));
  return h("td", text, "num world");
}

const CAP_SHORT = {
  text: "текст", code: "код", tools: "инстр.", image: "карт.", audio_in: "звук→",
  audio_out: "звук←", video: "видео", pdf: "PDF", reasoning: "разм.",
  open_weights: "откр.", caching: "кэш", structured: "схема", computer_use: "комп.",
  web_search: "поиск",
};

function capsCell(caps) {
  const td = h("td", undefined, "caps");
  for (const [key, label] of Object.entries(CAP_SHORT)) {
    if (!caps[key]) continue;
    td.append(h("span", label, "cap"));
  }
  if (!td.childNodes.length) td.append(h("span", "—", "dim"));
  return td;
}

function drawRoutingNote() {
  const note = document.getElementById("routing-note");
  const providers = connectedProviders();
  const rows = visibleRows();
  const chosen = new Map();
  for (const m of rows) {
    const served = servingProvider(m);
    if (!served) continue;
    chosen.set(served, (chosen.get(served) || 0) + 1);
  }
  const parts = [];
  if (!providers.length) {
    note.textContent = "Свежих котировок нет: столбцов поставщиков не будет, пока релей не получит прайсы.";
    return;
  }
  note.replaceChildren();
  parts.push(h("span", "Провайдер выбирается по самой низкой оценке 1000 входных + 512 выходных токенов, при отказе берётся следующий по цене."));
  const tail = [...chosen.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, n]) => `${providerLabel(name)}: ${n}`)
    .join(" · ");
  if (tail) parts.push(h("span", ` По текущей оценке запроса выберутся: ${tail}. Фактический поставщик последнего обращения показан справа.`, "meta"));
  for (const p of parts) note.append(p);
}

function drawBalances() {
  const routing = state.data.routing; const body = document.getElementById("balances-body");
  body.replaceChildren();
  if (!routing) { document.getElementById("routing-status").textContent = "Данные маршрутизации недоступны"; return; }
  document.getElementById("routing-status").textContent = `Автовыбор ${routing.enabled ? "включён" : "выключен"}. Каталоги: ${stamp(routing.updated * 1000)}. Курс для долларовой закупки: ${money(routing.usd_rub)} ₽/$; комиссия пополнения ${money(routing.usd_funding_fee_pct)}%. Балансы проверяются раз в две минуты.`;
  for (const [name, balance] of Object.entries(routing.balances || {})) {
    const row = h("tr");
    const status = balance.error ? (name.startsWith("proxyapi") && balance.error === "HTTP 403" ? "Включите «Запрос баланса» в правах ключа" : `Не удалось проверить: ${balance.error}`) : (balance.amount <= 0 ? "Недостаточно средств" : "Доступен");
    const note = balance.lim !== undefined && balance.usage !== undefined
      ? ` (лимит ${money(balance.lim, 2)}, израсходовано ${money(balance.usage, 2)})`
      : "";
    row.append(h("td", providerLabel(name), "name"), h("td", balance.amount === null ? "—" : `${money(balance.amount, 4)} ${balance.currency}${note}`),
      h("td", balance.currency === "USD" ? `≈ ${money(balance.rub_estimate, 2)} ₽` : "—"),
      h("td", stamp(balance.checked * 1000)), h("td", status));
    body.append(row);
  }
}

function drawProviders() {
  const p = state.data.providers || {};
  document.getElementById("providers-when").textContent = p.at
    ? `Снимок прайсов: ${stamp(p.at)}${p.usd_rub ? `, доллар ${money(p.usd_rub, 2)} ₽ (${p.rate_source || "—"})` : ""}. Подключены сейчас: ${(p.connected || []).join(", ") || "никто"}. Некоторые наши поставщики в снимок не попали: он собирался раньше, чем их добавили.`
    : "Снимка прайсов ещё нет.";
  const body = document.querySelector("#providers tbody");
  body.replaceChildren();
  for (const row of p.snapshot || []) {
    const tr = h("tr");
    tr.append(h("td", row.name, "name"));
    tr.append(h("td", row.connected ? "да" : "—", row.connected ? "meta" : "meta"));
    tr.append(h("td", money(row.offers, 0), "num meta"));
    tr.append(h("td", row.min_in === undefined ? "—" : money(row.min_in), "num"));
    tr.append(h("td", row.median_in === undefined ? "—" : money(row.median_in), "num"));
    tr.append(h("td", row.note, "meta"));
    body.append(tr);
  }
}

function card(name, s) {
  const el = h("div", undefined, "card");
  el.append(h("h4", name));
  el.append(h("p", s.model || "", "how"));
  const dl = h("dl");
  const add = (label, value) => {
    if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length)) return;
    dl.append(h("dt", label), h("dd", Array.isArray(value) ? value.join(" · ") : String(value)));
  };
  const plans = s.plans || {};
  const planText = Object.entries(plans)
    .slice(0, 4)
    .map(([k, v]) => (typeof v === "string" ? `${k}: ${v}` : `${k}: ${v.rub ?? ""}${v.sparks ? ` · ${v.sparks} искр` : ""}${v.credits ? ` · ${v.credits} кредитов` : ""}`))
    .join("; ");
  add("тарифы", planText);
  add("моделей в прайсе", s.priced || "");
  add("замерено", s.measured);
  el.append(dl);
  if (s.ops && s.ops.length) {
    const ul = h("ul");
    for (const op of s.ops.slice(0, 5)) ul.append(h("li", `${op.name}: ${money(op.rub)} ₽`));
    el.append(ul);
  }
  if (s.note) el.append(h("p", s.note, "fine"));
  return el;
}

function drawRetail() {
  const retail = state.data.retail || {};
  document.getElementById("retail-when").textContent = retail.collected_at ? `собрано ${retail.collected_at}` : "";
  document.getElementById("retail-note").textContent = retail.note || "";
  const box = document.getElementById("retail");
  box.replaceChildren();
  for (const [name, s] of Object.entries(retail.services || {})) box.append(card(name, s));
}

function drawDrafts() {
  const d = state.data.drafts || {};
  document.getElementById("drafts-note").textContent = d.note || "";
  const market = d.market;
  const mBox = document.getElementById("market");
  mBox.replaceChildren();
  if (market) {
    const [lo, hi] = market.retail_rub_per_1m || [];
    const [clo, chi] = market.our_purchase_rub_per_1m || [];
    mBox.append(h("b", "Рынок розницы: "));
    mBox.append(h("span", `от ${money(lo)} до ${money(hi)} ₽ за миллион токенов. Наша закупка `));
    mBox.append(h("b", `${money(clo)}–${money(chi)} ₽`));
    mBox.append(h("span", ". " + (market.note || "")));
  }

  const box = document.getElementById("drafts");
  box.replaceChildren();
  for (const draft of d.drafts || []) {
    const el = h("div", undefined, "draft");
    el.append(h("h4", draft.title));
    const status = h("div", draft.status || "", "status" + (draft.status && draft.status.includes("действует") ? " on" : ""));
    el.append(status, h("p", `${draft.date || ""}`, "meta"));
    if (draft.note) el.append(h("p", draft.note, "note"));
    const table = h("table");
    const head = h("thead");
    const headRow = h("tr");
    for (const label of ["что", "себестоимость", "цена", "кратность"]) headRow.append(h("th", label));
    head.append(headRow);
    table.append(head);
    const tbody = h("tbody");
    for (const row of draft.rows || []) {
      const tr = h("tr");
      tr.append(h("td", row.what), h("td", row.cost, "num"), h("td", row.price, "num"), h("td", row.ratio, "num"));
      tbody.append(tr);
    }
    table.append(tbody);
    el.append(table);
    box.append(el);
  }

  const sellers = document.querySelector("#sellers tbody");
  sellers.replaceChildren();
  for (const row of d.deepseek_flash_sellers || []) {
    const tr = h("tr");
    tr.append(h("td", row.seller, "name"));
    tr.append(h("td", row.in === null ? "—" : money(row.in), "num"));
    tr.append(h("td", row.out === null ? "—" : money(row.out), "num"));
    tr.append(h("td", row.cached_in === null ? "—" : money(row.cached_in), "num"));
    tr.append(h("td", row.source || "", "meta"));
    sellers.append(tr);
  }

  const mixed = document.querySelector("#mixed tbody");
  mixed.replaceChildren();
  for (const row of d.deepseek_flash_mixed || []) {
    const tr = h("tr");
    tr.append(h("td", row.seller, "name"), h("td", money(row.rate ?? row.rub_per_1m), "num"));
    mixed.append(tr);
  }

  const questions = document.getElementById("questions");
  questions.replaceChildren();
  for (const q of d.open_questions || []) questions.append(h("li", q));
}

function draw() {
  writeHash();
  drawGroups();
  drawVisibilityChips();
  drawVendors();
  drawProvidersFilter();
  drawSortControls();
  drawTable();
  const summary = document.getElementById("vendor-sum");
  if (summary) summary.title = "Вендоры: отметьте одного или нескольких";
}

/** Поля фильтров должны показывать то, что написано в адресе страницы. */
function syncControls() {
  document.getElementById("q").value = state.q;
  document.getElementById("price-min").value = state.priceMin === null ? "" : String(state.priceMin);
  document.getElementById("price-max").value = state.priceMax === null ? "" : String(state.priceMax);
  document.getElementById("k").value = String(state.k);
  document.getElementById("only-sell").checked = state.onlySell;
  document.getElementById("sort").value = state.sort;
  for (const [id, value] of [["mode-filter", state.mode], ["here-filter", state.hereOnly],
    ["band-filter", state.band], ["ctx-min", state.ctxMin || ""],
    ["world-in", state.worldIn === null ? "" : state.worldIn],
    ["iq-min", state.iqMin === null ? "" : state.iqMin]]) {
    const el = document.getElementById(id);
    if (el) el.value = String(value);
  }
}

/** Каталог рынка: если его нет, страница работает как раньше, без лишних колонок. */
async function loadCatalog() {
  try {
    const response = await fetch("catalog.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    catalog.bySlug = new Map((data.models || []).map((model) => [model.id, model]));
    catalog.ready = true;
    catalog.counts = data.counts || {};
    catalog.generated = data.generated_at || "";
  } catch (error) {
    catalog.ready = false;
    const note = document.getElementById("note");
    if (note && !note.textContent) {
      note.textContent = `Каталог рынка не загрузился (${error.message}): колонки мировых цен и качества скрыты.`;
    }
  }
}

/** Новые фильтры каталога: чипы возможностей и пять полей. */
function wireCatalogFilters() {
  const chips = document.getElementById("caps-filter");
  if (chips) {
    chips.replaceChildren();
    for (const [key, label] of Object.entries(CAP_SHORT)) {
      const button = h("button", label, "chip cap-chip");
      button.type = "button";
      button.dataset.cap = key;
      if (state.caps.has(key)) button.classList.add("on");
      button.onclick = () => {
        if (state.caps.has(key)) state.caps.delete(key);
        else state.caps.add(key);
        button.classList.toggle("on");
        draw();
      };
      chips.append(button);
    }
  }
  const bind = (id, apply) => {
    const el = document.getElementById(id);
    if (el) el.onchange = (e) => { apply(e.target.value); draw(); };
  };
  bind("mode-filter", (v) => { state.mode = v; });
  bind("here-filter", (v) => { state.hereOnly = v; });
  bind("band-filter", (v) => { state.band = v; });
  bind("ctx-min", (v) => { state.ctxMin = Number(v) || 0; });
  bind("world-in", (v) => { state.worldIn = v === "" ? null : Number(v); });
  bind("iq-min", (v) => { state.iqMin = v === "" ? null : Number(v); });
  const showEmpty = document.getElementById("show-empty");
  if (showEmpty) {
    showEmpty.checked = state.showEmpty;
    showEmpty.onchange = (e) => { state.showEmpty = e.target.checked; draw(); };
  }
}

async function load() {
  const note = document.getElementById("note");
  const gate = document.getElementById("gate");
  if (!TOKEN) {
    showGate();
    return;
  }
  try {
    state.data = await api("/v1/admin/prices");
    await loadCatalog();
    gate.hidden = true;
    document.getElementById("bar").hidden = false;
    document.getElementById("content").hidden = false;
    wireCatalogFilters();
    drawRate();
    drawCounts();
    syncControls();
    draw();
    drawProviders();
    drawBalances();
    drawRetail();
    drawDrafts();
    document.getElementById("footer").textContent = `Справочный снимок рынка: ${stamp(state.data.generated_at)}. Галочки сохраняются на сервере. Розничные ставки сохраняются кнопкой «Сохранить». Обновление закупочных котировок не меняет их автоматически.`;
    note.textContent = "";
  } catch (e) {
    note.textContent = String(e.message || e);
    if (!state.data) showGate();
  }
}

document.getElementById("k").oninput = (e) => {
  const value = Number(e.target.value);
  state.k = Number.isFinite(value) && value > 0 ? value : 1;
  if (state.data) draw();
};
document.getElementById("q").oninput = (e) => {
  state.q = e.target.value;
  if (state.data) draw();
};
document.getElementById("only-sell").onchange = (e) => {
  state.onlySell = e.target.checked;
  if (state.data) draw();
};
document.getElementById("price-min").oninput = (e) => {
  const value = Number(e.target.value);
  state.priceMin = e.target.value !== "" && Number.isFinite(value) ? value : null;
  if (state.data) draw();
};
document.getElementById("price-max").oninput = (e) => {
  const value = Number(e.target.value);
  state.priceMax = e.target.value !== "" && Number.isFinite(value) ? value : null;
  if (state.data) draw();
};
document.getElementById("sort-dir").onclick = () => {
  state.dir = state.dir === "desc" ? "asc" : "desc";
  if (state.data) draw();
};
document.getElementById("reset").onclick = () => {
  state.q = "";
  state.group = "all";
  state.vendors.clear();
  state.providers.clear();
  state.visibility = "all";
  state.priceMin = null;
  state.priceMax = null;
  state.sort = "";
  state.dir = "asc";
  state.onlySell = false;
  state.caps.clear();
  state.band = "";
  state.hereOnly = "";
  state.worldIn = null;
  state.ctxMin = 0;
  state.iqMin = null;
  state.mode = "";
  state.showEmpty = false;
  const showEmpty = document.getElementById("show-empty");
  if (showEmpty) showEmpty.checked = false;
  for (const el of document.querySelectorAll("#caps-filter .cap-chip")) el.classList.remove("on");
  for (const [id, value] of [["mode-filter", ""], ["here-filter", ""], ["band-filter", ""],
    ["ctx-min", ""], ["world-in", ""], ["iq-min", ""]]) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
  syncControls();
  if (state.data) draw();
};
document.getElementById("gate-form").onsubmit = (event) => {
  event.preventDefault();
  const field = document.getElementById("gate-token");
  const value = field.value.trim();
  if (!value) return;
  TOKEN = value;
  SS.setItem("nm.admin", value);
  field.value = "";
  document.getElementById("note").textContent = "";
  load();
};

readHash();
load();

window.addEventListener("hashchange", () => {
  readHash();
  syncControls();
  if (state.data) draw();
});

document.getElementById("refresh-routing").onclick = async (event) => {
  const button = event.currentTarget; button.disabled = true;
  document.getElementById("note").textContent = "Обновляем котировки и балансы…";
  try { await api("/v1/admin/provider-routing/refresh", {method: "POST"}); await load(); }
  catch (error) { document.getElementById("note").textContent = `Не удалось обновить: ${error.message}`; }
  finally { button.disabled = false; }
};
setInterval(() => {
  if (TOKEN && state.data && !dirtyPrices.size && !document.hidden &&
      !document.querySelector("#refresh-routing:disabled, #content input:disabled")) load();
}, 120000);

document.getElementById("detailed-columns")?.addEventListener("change",e=>document.body.classList.toggle("prices-detailed",e.target.checked));

window.addEventListener('beforeunload',event=>{if(dirtyPrices.size){event.preventDefault();event.returnValue='';}});
