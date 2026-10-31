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

const COL_WIDTH_KEY = "nm.models.col-widths";
let COL_WIDTHS = null;

const state = {
  group: "all",
  visibility: "all", // all | visible | hidden
  provider: "",
  sellOnly: false,
  ours: false,   // только российские модели (GigaChat, Yandex, Alice…)
  jumps: false,  // только модели, где следующий поставщик дороже в 1.8+ раза
  mergeNames: false, // склеивать одинаковые названия в одну строку (по умолчанию — как у клиентов)
  duplicates: false, // только строки, у которых есть видимый тёзка
  allVariants: false, // в нижней таблице показывать все варианты семьи, а не по одному на поставщика
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
  jiugx: "Jiugx",
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

/** Цена без лишних нулей, но с точностью для мелких ставок (0,0146). */
function priceText(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const digits = number > 0 && number < 0.1 ? 4 : number > 0 && number < 1 ? 3 : 2;
  return fmt(number, digits);
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

/** «2026-10-29» → «29.10.2026»: дата первого появления в снимке прайсов. */
function dateText(value) {
  const text = String(value || "");
  if (!text) return "—";
  const parts = text.slice(0, 10).split("-");
  return parts.length === 3 && parts.every(Boolean) ? `${parts[2]}.${parts[1]}.${parts[0]}` : text;
}

function firstSeenModel(group) {
  return group.models.find((model) => model.first_seen) || group.primary;
}

function referenceModel(group) {
  return group.models.find((model) => model.reference) || null;
}

function usdNumber(value, digits = 3) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "—";
  return number.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function usdText(value, digits = 3) {
  const text = usdNumber(value, digits);
  return text === "—" ? text : "$" + text;
}

function officialText(group) {
  const model = referenceModel(group);
  const ref = model && model.reference;
  if (!ref) return "—";
  const price = [usdText(ref.input_usd), usdText(ref.output_usd)].join(" / ");
  return ref.status && ref.status !== "ga" ? `${price} · ${ref.status}` : price;
}

const RATE_LABELS = {
  input: "вход",
  output: "выход",
  cached_input: "кэш",
  image: "картинка",
  image_2k: "картинка 2K",
  image_input: "вход картинки",
  second: "секунда",
  speech: "1000 знаков",
  minute: "минута",
};

const RATE_UNITS = {
  input: "₽ / 1M",
  output: "₽ / 1M",
  cached_input: "₽ / 1M",
  image: "₽ / шт.",
  image_2k: "₽ / шт. 2K",
  image_input: "₽ / шт. вход",
  second: "₽ / сек.",
  speech: "₽ / 1000 зн.",
  minute: "₽ / мин.",
};

function rateLabel(key) {
  return RATE_LABELS[key] || key;
}

function rateUnit(key) {
  return RATE_UNITS[key] || "₽";
}

function rateValueText(key, value) {
  return `${fmt(value)} ${rateUnit(key)}`;
}

/** Какие ставки показывать в диалоге: только те, что есть у этого типа модели. */
function rateFields(group, rates) {
  if (group.group === "text" || group.group === "code") return ["input", "output", "cached_input"];
  if (group.group === "image") return ["image", "image_2k", "image_input"];
  if (group.group === "video") return ["second"];
  if (group.group === "audio") return ["speech", "minute"];
  return Object.keys(rates).filter((key) => Number(rates[key]) > 0);
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

/** Наша закупка: в ячейке только числа, размерность — в шапке и подсказке. */
function purchaseText(model) {
  const cost = model.cost || {};
  if (cost.rub_image != null) return fmt(cost.rub_image);
  if (cost.rub_second != null) return fmt(cost.rub_second);
  if (cost.rub_speech != null) return fmt(cost.rub_speech);
  if (cost.rub_minute != null) return fmt(cost.rub_minute);
  if (cost.rub_in != null || cost.rub_out != null) return `${fmt(cost.rub_in)} / ${fmt(cost.rub_out)}`;
  return "—";
}

/** Короткая метка выбранного варианта: 1017, 0731, latest. */
function routeLabel(value) {
  const bare = String(value || "").split("/").pop();
  const tail = bare.match(/(\d{3,4}|latest|vision|exp)$/);
  return tail ? tail[1] : bare.slice(0, 14);
}

/** Полная размерность закупки — во всплывающей подсказке, а не в каждой ячейке. */
function purchaseTitle(model) {
  const cost = model.cost || {};
  const routing = model.routing || {};
  const selected = routing.selected_model
    ? ` · маршрут: ${providerLabel(routing.selected)} ${routing.selected_model}`
    : "";
  const exact = (routing.offers || []).find((offer) => normalizedModel(offer.model) === normalizedModel(model.upstream));
  const exactLine = exact && normalizedModel(exact.model) !== normalizedModel(routing.selected_model) && exact.input_rub != null
    ? `\nТочная версия ${model.upstream}: ${providerLabel(exact.provider)} ${fmt(exact.input_rub)} / ${fmt(exact.output_rub)}${exact.eligible ? "" : " (только сверка)"}`
    : "";
  const source = model.cost_offer?.provider ? ` · ${providerLabel(model.cost_offer.provider)}` : "";
  if (cost.rub_image != null) return `${fmt(cost.rub_image)} ₽ за изображение${source}${selected}`;
  if (cost.rub_second != null) return `${fmt(cost.rub_second)} ₽ за секунду${source}${selected}`;
  if (cost.rub_speech != null) return `${fmt(cost.rub_speech)} ₽ за 1000 знаков${source}${selected}`;
  if (cost.rub_minute != null) return `${fmt(cost.rub_minute)} ₽ за минуту${source}${selected}`;
  if (cost.rub_in != null || cost.rub_out != null) {
    return `Закупка выбранного маршрута${selected}: вход ${fmt(cost.rub_in)} ₽/1M, выход ${fmt(cost.rub_out)} ₽/1M${exactLine}`;
  }
  return "Закупка неизвестна: поставщик не дал цену";
}

/** Наша цена клиенту: у текста ставка за 1M, у медиа — подпись тарифа. */
function clientText(model) {
  const sell = model.sell || {};
  if (sell.rub_image) return `${fmt(sell.rub_image)} ₽ / шт.`;
  if (sell.rub_second) return `${fmt(sell.rub_second)} ₽ / сек.`;
  if (sell.rub_speech) return `${fmt(sell.rub_speech)} ₽ / 1000 зн.`;
  if (sell.rub_minute) return `${fmt(sell.rub_minute)} ₽ / мин.`;
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

/**
 * Базовая единица медиа: поставщики считают одну и ту же работу по-разному
 * (Polza — за 1M символов, Bothub — за 1000). Сравнивать сырые числа нельзя,
 * поэтому сначала приводим к одной единице, а в подсказке показываем исходную.
 */
function mediaBaseUnit(group) {
  const units = new Set((group.offers || []).map((offer) => String(offer.unit || "")));
  if (group.group === "image") {
    if (units.has("изображение")) return "изображение";
    if (units.has("запрос")) return "запрос";
  }
  if (group.group === "video" && units.has("секунда")) return "секунда";
  if (group.group === "audio") {
    if (units.has("1000 символов") || units.has("1M символов")) return "1000 символов";
    if (units.has("минута")) return "минута";
  }
  return [...units][0] || "";
}

function unitPhrase(unit) {
  return {
    секунда: "секунду",
    минута: "минуту",
  }[unit] || unit;
}

function mediaUnitPrice(offer, base) {
  const price = Number(offer.price_rub);
  const unit = String(offer.unit || "");
  if (!Number.isFinite(price) || price <= 0) return null;
  if (unit === base) return price;
  if (base === "1000 символов" && unit === "1M символов") return price / 1000;
  if (base === "изображение" && unit === "запрос") return price;
  if (base === "запрос" && unit === "изображение") return price;
  return null;
}

function bestOffer(group) {
  const media = group.group !== "text" && group.group !== "code";
  const base = media ? mediaBaseUnit(group) : "";
  let best = null;
  let bestPrice = null;
  for (const offer of group.offers || []) {
    // Сравниваем ровно то число, которое печатаем: вход для текста, цену за
    // базовую единицу для медиа. Иначе «дешевле всех» выбирался бы по оценке
    // запроса, а в ячейке стояла цена входа другого поставщика.
    const price = media ? mediaUnitPrice(offer, base) : Number(offer.input_rub) || null;
    if (price === null || price <= 0) continue;
    if (bestPrice === null || price < bestPrice) {
      best = offer;
      bestPrice = price;
    }
  }
  return best;
}

function bestPriceOf(group, offer) {
  const media = group.group !== "text" && group.group !== "code";
  return media ? mediaUnitPrice(offer, mediaBaseUnit(group)) : Number(offer.input_rub);
}

/** Поставщик и полная размерность — в подсказке; в ячейке только число. */
function bestTitle(group, offer) {
  const media = group.group !== "text" && group.group !== "code";
  if (media) {
    const base = mediaBaseUnit(group);
    const price = mediaUnitPrice(offer, base);
    const lines = [`Дешевле всех: ${providerLabel(offer.provider)} · ${priceText(price)} ₽ за ${unitPhrase(base)}`];
    if (String(offer.unit || "") !== base) {
      lines.push(`В прайсе: ${priceText(offer.price_rub)} ₽ за ${unitPhrase(offer.unit)}`);
    }
    for (const other of group.offers) {
      if (other === offer) continue;
      lines.push(`${providerLabel(other.provider)}: ${priceText(other.price_rub)} ₽ за ${unitPhrase(other.unit || "единицу")}`);
    }
    return lines.join("\n");
  }
  return `Дешевле всех по входу: ${providerLabel(offer.provider)} · вход ${fmt(offer.input_rub)} ₽/1M · выход ${fmt(offer.output_rub)} ₽/1M`;
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

function normalizedModel(value) {
  return String(value || "").toLowerCase().split("/").pop().replace(/[^a-z0-9]+/g, "");
}

function compareRank(left, right) {
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] < right[index] ? -1 : 1;
  }
  return 0;
}

/**
 * Один поставщик — одна строка. Релей отдаёт все варианты семейства
 * (1017, 0731, vision, latest), чтобы был failover, но в таблице модели это
 * выглядело как несколько одинаковых поставщиков. Оставляем лучший вариант:
 * сначала совпадающий с upstream самой модели, потом выбранный маршрут,
 * потом доступный, потом дешёвый.
 */
function unionOffers(models, includeAll = false) {
  const kind = models.length ? groupOf(models[0]) : "text";
  const media = kind !== "text" && kind !== "code";
  const upstreams = new Set();
  for (const model of models) {
    for (const value of [model.upstream, model.priced_as, model.id]) {
      const key = normalizedModel(value);
      if (key) upstreams.add(key);
    }
  }
  const collected = [];
  const seen = new Set();
  for (const model of models) {
    for (const offer of offersOf(model)) {
      const fingerprint = [offer.provider, offer.model, offer.unit, offer.input_rub, offer.output_rub, offer.cached_rub, offer.price_rub].join("|");
      if (includeAll && seen.has(fingerprint)) continue;
      seen.add(fingerprint);
      collected.push({ offer, from: model.id, exact: upstreams.has(normalizedModel(offer.model)) });
    }
  }
  if (includeAll) {
    return collected.map(({ offer, from, exact }) => {
      offer._from = from;
      offer._exact = Boolean(exact);
      return offer;
    });
  }
  const base = media ? mediaBaseUnit({ group: kind, offers: collected.map((item) => item.offer) }) : "";
  const byProvider = new Map();
  for (const item of collected) {
    const price = media ? mediaUnitPrice(item.offer, base) : Number(item.offer.input_rub);
    const rank = [
      media && base && !Number.isFinite(price) ? 1 : 0,
      item.exact ? 0 : 1,
      item.offer.selected ? 0 : 1,
      item.offer.eligible ? 0 : 1,
      Number.isFinite(price) && price > 0 ? price : Infinity,
    ];
    const key = String(item.offer.provider || "");
    const old = byProvider.get(key);
    if (!old || compareRank(rank, old.rank) < 0) byProvider.set(key, { ...item, rank });
  }
  return [...byProvider.values()].map(({ offer, from, exact }) => {
    offer._from = from;
    offer._exact = Boolean(exact);
    return offer;
  });
}

function oneGroup(model) {
  return {
    key: model.id,
    name: model.name || model.id,
    group: groupOf(model),
    models: [model],
    primary: model,
    listed: Boolean(model.listed),
    offers: unionOffers([model], state.allVariants),
  };
}

/** Сводная строка по одинаковому названию: только если оператор сам её включил. */
function namedGroups() {
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
    // Показываем состояние канонической строки, а не «все скрыты»: иначе
    // скрытый алиас делал вид, что скрыта вся модель у клиентов.
    group.listed = Boolean(group.primary.listed);
    group.offers = unionOffers(group.models, state.allVariants);
  }
  return groups;
}

function allGroups() {
  const models = (DATA && DATA.models) || [];
  return state.mergeNames ? namedGroups() : models.map(oneGroup);
}

/** Видимые модели с одинаковым названием: именно они дают две позиции у клиента. */
function duplicateNames() {
  const map = new Map();
  for (const model of (DATA && DATA.models) || []) {
    if (!model.listed) continue;
    // Сравниваем именно то, что видит клиент: «GigaChat-3-Pro» и
    // «GigaChat 3 Pro» — разные строки, хотя для склейки это одно название.
    const key = String(model.name || model.id).trim().toLowerCase().replace(/\s+/g, " ");
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(model);
  }
  const out = new Map();
  for (const list of map.values()) {
    if (list.length < 2) continue;
    for (const model of list) out.set(model.id, list);
  }
  return out;
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
  { key: "firstSeen", label: "Появилась", numeric: true },
  { key: "context", label: "Контекст", numeric: true },
  { key: "purchase", label: "Закупка", sub: "₽/1M · за ед.", numeric: true },
  { key: "client", label: "Клиенту", sub: "₽/1M · медиа в токенах", numeric: true },
  { key: "official", label: "Официально", sub: "$ за 1M", numeric: true },
  { key: "markupIn", label: "Наценка вход", numeric: true },
  { key: "markupOut", label: "Наценка выход", numeric: true },
  { key: "best", label: "Дешевле всех", sub: "вход, ₽/1M · за ед.", numeric: true },
  { key: "offers", label: "Поставщиков", numeric: true },
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

/** Наценка строки: клик открывает поле, Enter кладёт новую цену в черновик. */
function editMarkup(cell, group, key, value) {
  if (cell.querySelector("input")) return;
  const model = group.primary;
  const cost = Number(key === "in" ? model.cost?.rub_in : model.cost?.rub_out);
  if (!cost) {
    setNote("У этой модели нет закупки — наценку считать не от чего.");
    return;
  }
  cell.replaceChildren();
  const input = el("input", "mt-markup-input");
  input.type = "number";
  input.min = "0.1";
  input.max = "20";
  input.step = "0.05";
  input.value = Number(value).toFixed(2);
  input.title = "Enter — сохранить в черновик, Esc — отмена";
  cell.append(input);
  input.focus();
  input.select();
  input.onclick = (event) => event.stopPropagation();
  input.onkeydown = (event) => {
    event.stopPropagation();
    if (event.key === "Escape") {
      event.preventDefault();
      draw();
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    const markup = Number(input.value);
    if (!Number.isFinite(markup) || markup < 0.1 || markup > 20) {
      setNote("Наценка должна быть от 0,1 до 20.");
      input.focus();
      return;
    }
    const rates = key === "in"
      ? { input: cost * markup, cached_input: cost * 0.1 * markup }
      : { output: cost * markup };
    input.disabled = true;
    API("PUT", `/v1/admin/prices/models/${encodeURIComponent(model.id)}/draft`, { rates_rub: rates })
      .then(() => load(true))
      .then(() => setNote(`Наценка ${key === "in" ? "входа" : "выхода"} ×${fmt(markup, 2)} — в черновике.`))
      .catch((error) => {
        setNote(`Не удалось: ${error.message}`);
        input.disabled = false;
        input.focus();
      });
  };
}

function columnText(group, key) {
  const model = group.primary;
  switch (key) {
    case "listed": return group.listed ? "Видна" : "Скрыта";
    case "name": return `${group.name} ${group.models.map((item) => item.id).join(" ")}`;
    case "kind": return groupLabel(group.group);
    case "firstSeen": {
      const seen = firstSeenModel(group);
      return `${dateText(seen.first_seen)}${seen.new_model ? " новая" : ""}`;
    }
    case "context": return ctxText(model.context);
    case "purchase": return purchaseText(model);
    case "client": return clientText(model);
    case "official": return officialText(group);
    case "markupIn": return ratioText(model.ratio?.in);
    case "markupOut": return ratioText(model.ratio?.out);
    case "best": {
      const best = bestOffer(group);
      return best ? `${priceText(bestPriceOf(group, best))} ${providerLabel(best.provider)}` : "—";
    }
    case "offers": return String(new Set(group.offers.map((offer) => offer.provider)).size);
    default: return "";
  }
}

function columnNumber(group, key) {
  const model = group.primary;
  switch (key) {
    case "firstSeen": {
      const seen = firstSeenModel(group);
      const parsed = Date.parse(seen.first_seen || "");
      return Number.isFinite(parsed) ? parsed : null;
    }
    case "context": return Number(model.context) || null;
    case "purchase": {
      const cost = model.cost || {};
      return cost.rub_in ?? cost.rub_image ?? cost.rub_second ?? cost.rub_speech ?? cost.rub_minute ?? null;
    }
    case "client": return Number(model.sell?.rub_in) || null;
    case "official": {
      const ref = referenceModel(group)?.reference;
      return ref && Number.isFinite(Number(ref.input_usd)) ? Number(ref.input_usd) : null;
    }
    case "markupIn": return Number(model.ratio?.in) > 0 ? Number(model.ratio.in) : null;
    case "markupOut": return Number(model.ratio?.out) > 0 ? Number(model.ratio.out) : null;
    case "best": {
      const best = bestOffer(group);
      return best ? bestPriceOf(group, best) : null;
    }
    case "offers": return new Set(group.offers.map((offer) => offer.provider)).size;
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
  state.jumps = p.get("jump") === "1";
  state.mergeNames = p.get("merge") === "1";
  state.duplicates = p.get("dup") === "1";
  state.allVariants = p.get("var") === "1";
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
  if (state.jumps) p.set("jump", "1");
  if (state.mergeNames) p.set("merge", "1");
  if (state.duplicates) p.set("dup", "1");
  if (state.allVariants) p.set("var", "1");
  if (state.sort !== "name") p.set("s", state.sort);
  if (state.dir === "desc") p.set("d", "desc");
  if (state.sel) p.set("sel", state.sel);
  const query = p.toString();
  try {
    history.replaceState(null, "", "#models" + (query ? "?" + query : ""));
  } catch (_) { /* не наша страница */ }
}

/** Скачок между поставщиками: следующий дороже самого дешёвого в 1.8+ раза. */
function mediaPriceText(offer, group) {
  const media = group.group !== "text" && group.group !== "code";
  if (!media) return `${fmt(offer.input_rub)} ₽`;
  return `${fmt(offer.price_rub)} ₽ / ${offer.unit || "ед."}`;
}

function priceJump(group) {
  const media = group.group !== "text" && group.group !== "code";
  const offers = group.offers.filter((offer) => (media ? Number(offer.price_rub) > 0 : offer.eligible && Number(offer.input_rub) > 0));
  if (offers.length < 2) return null;
  const buckets = new Map();
  for (const offer of offers) {
    const key = media ? String(offer.unit || "") : "";
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(offer);
  }
  let best = null;
  for (const list of buckets.values()) {
    const price = (offer) => Number(media ? offer.price_rub : offer.input_rub);
    const sorted = list.slice().sort((a, b) => price(a) - price(b));
    const first = sorted[0];
    const next = sorted.find((offer) => offer.provider !== first.provider);
    if (!next) continue;
    const ratio = price(next) / price(first);
    if (ratio >= 1.8 && (!best || ratio > best.ratio)) best = { ratio, first, next, unit: media ? first.unit : "" };
  }
  return best;
}

function visibleGroups() {
  const duplicates = duplicateNames();
  let rows = allGroups().filter((group) => {
    if (state.group !== "all" && group.group !== state.group) return false;
    if (state.visibility === "visible" && !group.listed) return false;
    if (state.visibility === "hidden" && group.listed) return false;
    if (state.sellOnly && !group.models.some(hasSell)) return false;
    if (state.ours && !isOurs(group)) return false;
    if (state.jumps && !priceJump(group)) return false;
    if (state.duplicates && !duplicates.has(group.primary.id)) return false;
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
        <span class="fine mt-reference" id="mt-reference"></span>
      </div>
      <div class="mt-toolbar mt-bulk">
        <label class="field">наценка ×
          <input id="mt-k" type="number" min="0.1" max="20" step="0.05" value="1.3" />
        </label>
        <button class="chip" id="mt-apply-k" type="button">Отложить цену</button>
        <span class="sp"></span>
        <button class="chip mt-publish" id="mt-publish" type="button" hidden>Опубликовать</button>
        <button class="chip" id="mt-discard" type="button" hidden>Сбросить черновики</button>
        <button class="chip" id="mt-show" type="button">Показать</button>
        <button class="chip" id="mt-hide" type="button">Скрыть</button>
      </div>
      <p class="fine" id="mt-note"></p>
    </div>
    <div class="mt-wrap">
      <table id="mt-models">
        <colgroup></colgroup>
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
      <label class="switch mt-variants"><input id="mt-variants" type="checkbox" /> все варианты семьи</label>
      <span class="fine" id="mt-sel-meta"></span>
      <button class="chip mt-collapse" id="mt-collapse" type="button" title="Свернуть таблицу поставщиков и вернуть место основной таблице">Свернуть</button>
    </div>
    <div class="mt-wrap">
      <table id="mt-offers">
        <thead><tr></tr></thead>
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
  const jumps = el("button", "chip mt-jumps" + (state.jumps ? " on" : ""));
  jumps.type = "button";
  jumps.append(el("span", "", "скачки"), el("b", "", String(groups.filter(priceJump).length)));
  jumps.title = "Модели, где следующий поставщик дороже самого дешёвого в 1,8 раза и больше";
  jumps.onclick = () => { state.jumps = !state.jumps; draw(); };
  box.append(jumps);

  const dupLists = new Map();
  for (const list of duplicateNames().values()) {
    dupLists.set(list.map((model) => model.id).sort().join("|"), list);
  }
  const dupFilter = el("button", "chip mt-dups" + (state.duplicates ? " on" : ""));
  dupFilter.type = "button";
  dupFilter.append(el("span", "", "дубли"), el("b", "", String(dupLists.size)));
  dupFilter.title = "Названия, которые у клиентов показаны больше одного раза";
  dupFilter.onclick = () => { state.duplicates = !state.duplicates; draw(); };
  box.append(dupFilter);

  const merge = el("button", "chip mt-merge" + (state.mergeNames ? " on" : ""));
  merge.type = "button";
  merge.append(el("span", "", "склеивать названия"));
  merge.title = "Свернуть одинаковые названия в одну строку. Выключено: таблица повторяет список, который видят клиенты";
  merge.onclick = () => { state.mergeNames = !state.mergeNames; draw(); };
  box.append(merge);
}

function drawProviderFilter() {
  const select = $("mt-provider");
  if (!select) return;
  // Пока данные не пришли, не сбрасываем фильтр из адреса: иначе ссылка
  // «Открыть в таблице моделей» из карты поставщиков теряет `p=`.
  if (!DATA) return;
  const providers = ((DATA.providers && DATA.providers.connected) || []).slice();
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
  if (group.models.length > 1) {
    label.title = `Переключит все ${group.models.length} specs этой строки, включая скрытые`;
  }
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
      if (!error) {
        setNote("");
        draw();
      }
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
  const duplicates = duplicateNames();
  for (const group of rows) {
    const model = group.primary;
    const best = bestOffer(group);
    const offers = group.offers;
    const active = group.models.some((item) => item.id === state.sel);
    const tr = el("tr", "mt-row" + (group.listed ? "" : " mt-off") + (active ? " mt-active" : ""));

    tr.append(visibilityCell(group));

    const name = el("td", "mt-model-cell");
    name.title = group.models.map((item) => item.id).join(" · ");
    name.append(el("span", "mt-name", group.name));
    name.append(el("code", "mt-code", model.id));
    if (group.models.length > 1) name.append(el("span", "mt-alias", ` +${group.models.length - 1}`));
    const twins = duplicates.get(model.id);
    if (twins) {
      const badge = el("span", "mt-dup-badge", "дубль");
      badge.title = `У клиентов то же название ещё у: ${twins.filter((item) => item.id !== model.id).map((item) => item.id).join(", ")}`;
      name.append(badge);
    }
    const logButton = el("button", "mt-log-btn", "лог");
    logButton.type = "button";
    logButton.title = "История модели: появление, изменения цены, видимость и заметки";
    logButton.setAttribute("aria-label", "История модели " + group.name);
    logButton.onclick = (event) => {
      event.stopPropagation();
      openModelLog(group);
    };
    name.append(logButton);
    tr.append(name);

    const textCell = (cls, text) => {
      const cell = el("td", cls, text);
      cell.title = text;
      return cell;
    };
    tr.append(textCell("mt-kind", groupLabel(group.group)));

    const seen = firstSeenModel(group);
    const seenCell = el("td", "num mt-seen", dateText(seen.first_seen));
    seenCell.title = seen.first_seen
      ? `Первое появление в снимке прайсов поставщиков: ${dateText(seen.first_seen)}`
      : "Нет в снимке прайсов поставщиков";
    if (seen.new_model) {
      const fresh = el("span", "mt-new", "новая");
      fresh.title = "Впервые появилась у поставщиков меньше 14 дней назад";
      seenCell.append(fresh);
    }
    tr.append(seenCell);

    const contextCell = el("td", "num", ctxText(model.context));
    if (model.context_source === "reference") {
      contextCell.classList.add("mt-ref");
      contextCell.append(el("span", "mt-ref-mark", "*"));
      contextCell.title = "Контекст из справочника Ferro Labs: у нас не задан";
    } else {
      contextCell.title = model.context ? "Контекст модели" : "Контекст не указан";
    }
    tr.append(contextCell);

    const purchaseCell = el("td", "num", purchaseText(model));
    purchaseCell.title = purchaseTitle(model);
    const selectedModel = model.routing?.selected_model;
    if (selectedModel && normalizedModel(selectedModel) !== normalizedModel(model.upstream)) {
      const badge = el("span", "mt-route-badge", routeLabel(selectedModel));
      badge.title = `Закупка — от выбранного маршрута ${providerLabel(model.routing.selected)} ${selectedModel}, не от ${model.upstream}`;
      purchaseCell.append(badge);
    }
    tr.append(purchaseCell);

    const clientCell = el("td", "num mt-client");
    clientCell.title = "✎ — изменить цену клиенту; двойной клик по ячейке открывает тот же диалог";
    clientCell.ondblclick = (event) => {
      event.stopPropagation();
      openPriceDialog(group);
    };
    clientCell.append(el("span", "mt-now", clientText(model)));
    if (model.draft) {
      const draft = el("span", "mt-draft");
      draft.append(el("span", "mt-draft-arrow", "→"));
      const parts = Object.entries(model.draft.changed || {}).map(([key, value]) => rateValueText(key, value));
      draft.append(el("span", "", parts.join(" · ")));
      const badge = el("span", "mt-draft-badge" + (model.draft.stale ? " is-stale" : ""), model.draft.stale ? "конфликт" : "черновик");
      badge.title = model.draft.stale
        ? "Опубликованная цена изменилась после черновика: снимите его и задайте заново"
        : "Клиенты увидят цену после «Опубликовать»";
      draft.append(badge);
      clientCell.append(draft);
      clientCell.title = "Черновик: " + Object.entries(model.draft.changed || {}).map(([key, value]) => `${rateLabel(key)} ${fmt(value)}`).join(", ") + " · ✎ изменить";
    }
    const edit = el("button", "mt-edit", "✎");
    edit.type = "button";
    edit.title = "Изменить цену клиенту";
    edit.setAttribute("aria-label", "Изменить цену клиенту: " + group.name);
    edit.onclick = (event) => {
      event.stopPropagation();
      openPriceDialog(group);
    };
    clientCell.append(edit);
    tr.append(clientCell);

    const officialCell = el("td", "num mt-official");
    const refModel = referenceModel(group);
    const ref = refModel && refModel.reference;
    if (ref) {
      officialCell.append(el("span", "", `${usdNumber(ref.input_usd)} / ${usdNumber(ref.output_usd)}`));
      const status = String(ref.status || "ga");
      if (status !== "ga") {
        const label = { preview: "превью", deprecated: "устарела", sunset: "снята", beta: "бета" }[status] || status;
        const badge = el("span", "mt-ref-status" + (status === "deprecated" || status === "sunset" ? " is-dead" : ""), label);
        badge.title = [
          `Статус в справочнике: ${status}`,
          ref.deprecation_date ? `устаревает: ${dateText(ref.deprecation_date)}` : "",
          ref.sunset_date ? `снимается: ${dateText(ref.sunset_date)}` : "",
          ref.successor ? `замена: ${ref.successor}` : "",
        ].filter(Boolean).join(" · ");
        officialCell.append(badge);
      }
      const bits = [
        `Официальная ставка ${ref.key || refModel.id}`,
        ref.context ? `контекст ${ctxText(ref.context)}` : "",
        ref.max_output ? `выход ${ctxText(ref.max_output)}` : "",
        "источник: Ferro Labs",
      ].filter(Boolean);
      officialCell.title = bits.join(" · ");
    } else {
      officialCell.append(el("span", "fine", "—"));
      officialCell.title = "Нет в справочнике Ferro Labs";
    }
    tr.append(officialCell);

    const markupCell = (key, value) => {
      const cell = el("td", "num mt-markup");
      const number = Number(value);
      const draft = model.draft && model.draft.ratio ? Number(model.draft.ratio[key]) : null;
      cell.append(el("span", "mt-markup-now", ratioText(number)));
      if (Number.isFinite(draft) && draft > 0 && (!Number.isFinite(number) || Math.abs(draft - number) > 0.005)) {
        cell.append(el("span", "mt-markup-draft", "→ " + ratioText(draft)));
        cell.classList.add("mt-has-draft");
      }
      if (Number.isFinite(number) && number > 0 && number < 1.5) cell.classList.add("mt-low");
      if (Number.isFinite(number) && number > 0) {
        cell.classList.add("mt-editable");
        cell.title = `Наценка ${key === "in" ? "входа" : "выхода"} ${ratioText(number)}`
          + (Number.isFinite(draft) && draft > 0 ? ` → ${ratioText(draft)} в черновике` : "")
          + ". Нажмите, чтобы изменить";
        cell.onclick = (event) => {
          event.stopPropagation();
          editMarkup(cell, group, key, Number.isFinite(draft) && draft > 0 ? draft : number);
        };
      }
      return cell;
    };
    tr.append(markupCell("in", model.ratio?.in));
    tr.append(markupCell("out", model.ratio?.out));

    const bestCell = el("td", "mt-best");
    if (best) {
      bestCell.append(el("b", "", priceText(bestPriceOf(group, best))));
      const textModel = group.group === "text" || group.group === "code";
      if (textModel && !best.eligible) {
        bestCell.classList.add("mt-ineligible");
        bestCell.title = `${bestTitle(group, best)} · маршрут недоступен: ${best.exclusion_reason || "только сверка"}`;
      } else {
        bestCell.title = bestTitle(group, best);
      }
    } else {
      bestCell.append(el("span", "fine", "—"));
    }
    const jump = priceJump(group);
    if (jump) {
      const badge = el("span", "mt-jump", " ⚠ ×" + fmt(jump.ratio, 1));
      const first = mediaPriceText(jump.first, group);
      const next = mediaPriceText(jump.next, group);
      badge.title = `Следующий поставщик дороже: ${providerLabel(jump.first.provider)} ${first} → ${providerLabel(jump.next.provider)} ${next}`;
      bestCell.append(badge);
    }
    tr.append(bestCell);

    const providerCount = new Set(offers.map((offer) => offer.provider)).size;
    const exactCount = offers.filter((offer) => offer._exact).length;
    const familyCount = offers.length - exactCount;
    const count = el("td", "num");
    count.append(el("b", "", providerCount ? String(providerCount) : "—"));
    count.title = providerCount
      ? [
          `${providerCount} поставщиков`,
          familyCount ? `${exactCount} точных версий, ${familyCount} семейных` : "все отдают точную версию",
          state.allVariants && offers.length > providerCount ? `${offers.length} вариантов семьи` : "",
        ]
          .filter(Boolean)
          .join(" · ")
      : "Поставщики цену не публикуют";
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

function drawOfferHead(media) {
  const row = ROOT.querySelector("#mt-offers thead tr");
  if (!row) return;
  row.replaceChildren();
  const labels = media
    ? ["Поставщик", "Модель", "Цена, ₽", "Единица", "Состояние"]
    : ["Поставщик", "Модель", "Тариф", "Вход, ₽/1M", "Выход, ₽/1M", "Кэш, ₽/1M", "Контекст", "Состояние"];
  for (const label of labels) {
    const th = el("th", label.startsWith("Вход") || label.startsWith("Выход") || label.startsWith("Кэш") || label === "Цена" ? "num" : "", label);
    row.append(th);
  }
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
  const media = group.group !== "text" && group.group !== "code";
  drawOfferHead(media);
  if (title) title.textContent = group.name;
  if (meta) {
    const ids = group.models.map((model) => model.id).join(" · ");
    const providerCount = new Set(group.offers.map((offer) => offer.provider)).size;
    const exactCount = group.offers.filter((offer) => offer._exact).length;
    const familyCount = group.offers.length - exactCount;
    const word = providerCount === 1 ? "поставщик" : "поставщиков";
    const variants = state.allVariants && group.offers.length > providerCount ? ` · ${group.offers.length} вариантов семьи` : "";
    const families = familyCount ? ` · ${exactCount} точных, ${familyCount} семейных` : "";
    meta.textContent = `${ids} · ${groupLabel(group.group)} · ${providerCount} ${word}${families}${variants}`;
  }
  const base = media ? mediaBaseUnit(group) : "";
  const offers = group.offers.slice().sort((a, b) => {
    // Тот же показатель, что в колонке «Дешевле всех»: вход для текста,
    // цена за базовую единицу для медиа. Иначе поставщик из ячейки оказывался
    // не первым в списке, и казалось, что его внизу нет.
    const left = media ? (mediaUnitPrice(a, base) ?? Infinity) : (Number(a.input_rub) || Infinity);
    const right = media ? (mediaUnitPrice(b, base) ?? Infinity) : (Number(b.input_rub) || Infinity);
    return left - right;
  });
  if (!offers.length) {
    const tr = el("tr");
    const td = el("td", "fine", media
      ? "Поставщики цену не публикуют."
      : "Поставщики цену не публикуют.");
    td.colSpan = media ? 5 : 8;
    tr.append(td);
    body.append(tr);
    return;
  }
  const cheapest = offers[0];
  for (const offer of offers) {
    const tr = el("tr", offer === cheapest ? "mt-cheap" : "");
    const serving = Boolean(offer.selected) && offer._from === group.primary.id;
    if (serving) tr.classList.add("mt-serving");
    tr.append(el("td", "mt-provider", providerLabel(offer.provider)));
    const modelCell = el("td");
    modelCell.append(el("code", "mt-code", offer.model || "—"));
    if (offer._exact === false) {
      const family = el("span", "mt-family", "семья");
      family.title = "Поставщик отдаёт другую версию семейства, не точный upstream этой модели";
      modelCell.append(family);
    }
    tr.append(modelCell);
    const endpoint = String(offer.endpoint || "");
    const tier = endpoint ? endpoint.split("/").slice(-2).join("/") : "—";
    if (!media) {
      const tierCell = el("td", "mt-tier", tier);
      if (/flex/i.test(endpoint)) {
        tierCell.classList.add("is-flex");
        tierCell.title = `${endpoint}: гибкий тариф — дешевле, но медленнее обычного`;
      } else {
        tierCell.title = endpoint || "обычный тариф";
      }
      tr.append(tierCell);
      const inputCell = el("td", "num", fmt(offer.input_rub));
      const estimate = offerEstimate(offer);
      inputCell.title = estimate == null
        ? `Вход ${fmt(offer.input_rub)} ₽/1M`
        : `Вход ${fmt(offer.input_rub)} ₽/1M · при 1000 вх + 512 вых ≈ ${fmt(estimate)} ₽`;
      tr.append(inputCell);
      tr.append(el("td", "num", fmt(offer.output_rub)));
      tr.append(el("td", "num", offer.cached_rub == null ? "—" : fmt(offer.cached_rub)));
      tr.append(el("td", "num", ctxText(offer.context_length)));
    } else {
      tr.append(el("td", "num", fmt(offer.price_rub)));
      tr.append(el("td", "mt-unit", offer.unit || "—"));
    }
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
    apply.textContent = `Отложить цену (${rows.length})`;
    apply.title = `Закупка × множитель → черновик для ${models} моделей в отфильтрованных строках`;
  }
  const drafts = (DATA && DATA.price_drafts && DATA.price_drafts.count) || 0;
  const stale = (DATA && DATA.models ? DATA.models : []).filter((model) => model.draft && model.draft.stale).length;
  const publish = $("mt-publish");
  if (publish) {
    publish.hidden = !drafts;
    publish.textContent = `Опубликовать (${drafts})`;
    publish.title = stale ? `${stale} черновиков устарели и не опубликуются` : "Показать клиентам черновики цен";
  }
  const discard = $("mt-discard");
  if (discard) {
    discard.hidden = !drafts;
    discard.textContent = `Сбросить черновики (${drafts})`;
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
  if (!confirm(`Отложить цену клиенту = закупка × ${multiplier} для ${rows.length} строк (${items.length} моделей)?\n\n${sample}\n\nЭто черновик: клиенты увидят цены после «Опубликовать».`)) return;
  const button = $("mt-apply-k");
  if (button) button.disabled = true;
  setNote("Считаем и откладываем в черновик…");
  try {
    const result = await API("POST", "/v1/admin/prices/bulk/pricing", { multiplier, items });
    await load(true);
    const failed = (result.failed || []).length;
    setNote(`В черновике: ${result.staged ?? result.updated} из ${result.total}${failed ? `, не удалось: ${failed}` : ""}. Нажмите «Опубликовать», чтобы показать клиентам.`);
  } catch (e) {
    setNote(`Не удалось: ${e.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

/** Диалог одной модели: правим клиентские ставки и кладём их в черновик. */
function openPriceDialog(group) {
  const model = group.primary;
  const rates = (model.rates && model.rates.rates_rub) || {};
  const draft = model.draft || null;
  const fields = rateFields(group, rates);
  if (!fields.length && !(draft && Object.keys(draft.changed || {}).length)) {
    setNote("У этой модели нет ставок, которые можно изменить этим диалогом.");
    return;
  }
  let dialog = $("mt-price");
  if (!dialog) {
    dialog = el("dialog", "mt-dialog");
    dialog.id = "mt-price";
    if (ROOT) ROOT.append(dialog);
  }
  dialog.replaceChildren();
  const form = el("form", "mt-price-form");
  form.method = "dialog";
  form.addEventListener("submit", (event) => event.preventDefault());
  form.append(el("h3", "", "Цена клиенту"));
  form.append(el("p", "fine", `${model.id} · опубликовано: ревизия ${(model.rates && model.rates.revision) || 0}`));
  if (group.models.length > 1) {
    form.append(el("p", "fine", `В строке ещё ${group.models.length - 1} модель(ей): правка относится только к ${model.id}.`));
  }
  const grid = el("div", "mt-price-grid");
  for (const key of fields) {
    const label = el("label", "field");
    label.append(el("span", "", `${rateLabel(key)}, ${rateUnit(key)}`));
    const input = el("input");
    input.type = "number";
    input.min = "0";
    input.step = "0.0001";
    input.inputMode = "decimal";
    const value = draft && draft.changed && key in draft.changed ? draft.changed[key] : rates[key];
    input.value = Number(value) > 0 ? String(value) : "";
    input.dataset.rate = key;
    label.append(input);
    grid.append(label);
  }
  form.append(grid);
  if (draft) {
    const stale = draft.stale
      ? "Опубликованная цена изменилась после черновика — публикация не пройдёт, задайте цену заново."
      : `В черновике: ${Object.entries(draft.changed || {}).map(([key, value]) => `${rateLabel(key)} ${fmt(value)} ${rateUnit(key)}`).join(", ")}`;
    form.append(el("p", "fine mt-draft-note" + (draft.stale ? " is-stale" : ""), stale));
  }
  const actions = el("div", "mt-dialog-actions");
  const cancel = el("button", "chip", "Отмена");
  cancel.type = "button";
  cancel.onclick = () => dialog.close();
  actions.append(cancel);
  if (draft) {
    const drop = el("button", "chip", "Убрать черновик");
    drop.type = "button";
    drop.onclick = async () => {
      drop.disabled = true;
      try {
        await API("DELETE", `/v1/admin/prices/models/${encodeURIComponent(model.id)}/draft`);
        dialog.close();
        await load(true);
        setNote("Черновик снят.");
      } catch (e) {
        setNote(`Не удалось: ${e.message}`);
        drop.disabled = false;
      }
    };
    actions.append(drop);
  }
  const save = el("button", "chip mt-primary", "В черновик");
  save.type = "button";
  save.onclick = async () => {
    const values = {};
    for (const input of grid.querySelectorAll("input[data-rate]")) {
      const value = Number(input.value);
      if (Number.isFinite(value) && value > 0) values[input.dataset.rate] = value;
    }
    if (!Object.keys(values).length) {
      setNote("Укажите хотя бы одну цену больше нуля.");
      return;
    }
    save.disabled = true;
    try {
      await API("PUT", `/v1/admin/prices/models/${encodeURIComponent(model.id)}/draft`, { rates_rub: values });
      dialog.close();
      await load(true);
      setNote("Цена в черновике. Нажмите «Опубликовать», чтобы показать её клиентам.");
    } catch (e) {
      setNote(`Не удалось: ${e.message}`);
      save.disabled = false;
    }
  };
  actions.append(save);
  form.append(actions);
  dialog.append(form);
  dialog.showModal();
}

function logTime(seconds) {
  const date = new Date(Number(seconds) * 1000);
  if (!Number.isFinite(date.getTime())) return "—";
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function logDialog() {
  let dialog = $("mt-log");
  if (dialog) return dialog;
  dialog = el("dialog", "mt-log-dialog");
  dialog.id = "mt-log";
  dialog.innerHTML = `
    <div class="mt-log-head">
      <div><h3 id="mt-log-title">История модели</h3><p class="fine" id="mt-log-sub"></p></div>
      <button class="mt-log-close" id="mt-log-close" type="button" aria-label="Закрыть">×</button>
    </div>
    <div class="mt-log-body">
      <div class="mt-log-top">
        <table id="mt-log">
          <thead><tr><th>Когда</th><th>Тип</th><th>Событие</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <div class="mt-log-bottom">
        <div class="mt-log-detail" id="mt-log-detail">Выберите запись</div>
        <form class="mt-log-note" id="mt-log-form">
          <textarea id="mt-log-note" rows="3" placeholder="Добавить заметку по модели…"></textarea>
          <button class="chip mt-primary" type="submit">Добавить</button>
        </form>
      </div>
    </div>`;
  ROOT.append(dialog);
  dialog.querySelector("#mt-log-close").onclick = () => dialog.close();
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  return dialog;
}

function selectLogEvent(dialog, event, row) {
  for (const item of dialog.querySelectorAll(".mt-log-row.mt-active")) item.classList.remove("mt-active");
  if (row) row.classList.add("mt-active");
  const detail = dialog.querySelector("#mt-log-detail");
  const author = event.author ? ` · ${event.author}` : "";
  const kind = event.kind === "note" ? "Заметка" : "Система";
  detail.textContent = `${logTime(event.at)} · ${kind}${author}\n\n${event.body || event.title}`;
}

async function refreshModelLog(group, dialog) {
  const model = group.primary;
  const body = dialog.querySelector("#mt-log tbody");
  const detail = dialog.querySelector("#mt-log-detail");
  body.replaceChildren();
  detail.textContent = "Загрузка…";
  let data;
  try {
    data = await API("GET", `/v1/admin/models/${encodeURIComponent(model.id)}/log`);
  } catch (error) {
    detail.textContent = `Не удалось загрузить историю: ${error.message}`;
    return;
  }
  const events = data.events || [];
  let first = null;
  for (const event of events) {
    const row = el("tr", "mt-log-row " + (event.kind === "note" ? "is-note" : "is-system"));
    row.append(el("td", "mt-log-when", logTime(event.at)));
    const kind = el("td");
    kind.append(el("span", "mt-log-type " + (event.kind === "note" ? "is-note" : "is-system"), event.kind === "note" ? "заметка" : "система"));
    row.append(kind);
    const what = el("td", "mt-log-what");
    what.append(el("span", "mt-log-title", event.title));
    if (event.body) what.append(el("span", "mt-log-preview", String(event.body).split("\n")[0].slice(0, 140)));
    row.append(what);
    row.onclick = () => selectLogEvent(dialog, event, row);
    body.append(row);
    if (!first) first = { event, row };
  }
  if (first) selectLogEvent(dialog, first.event, first.row);
  else detail.textContent = "Событий пока нет. Добавьте заметку ниже.";
}

async function openModelLog(group) {
  const dialog = logDialog();
  const model = group.primary;
  dialog.querySelector("#mt-log-title").textContent = `История · ${group.name}`;
  dialog.querySelector("#mt-log-sub").textContent = model.id;
  const form = dialog.querySelector("#mt-log-form");
  const note = dialog.querySelector("#mt-log-note");
  note.value = "";
  form.onsubmit = async (event) => {
    event.preventDefault();
    const text = note.value.trim();
    if (!text) return;
    const button = form.querySelector("button");
    button.disabled = true;
    try {
      await API("POST", `/v1/admin/models/${encodeURIComponent(model.id)}/notes`, { body: text });
      note.value = "";
      await refreshModelLog(group, dialog);
      setNote("Заметка добавлена.");
    } catch (error) {
      setNote(`Заметка не сохранена: ${error.message}`);
    } finally {
      button.disabled = false;
    }
  };
  dialog.showModal();
  await refreshModelLog(group, dialog);
}

async function publishDrafts() {
  const count = (DATA && DATA.price_drafts && DATA.price_drafts.count) || 0;
  if (!count) return;
  const stale = (DATA.models || []).filter((model) => model.draft && model.draft.stale).length;
  const question = stale
    ? `В черновике ${count} моделей, из них ${stale} устарели. Опубликовать остальные?`
    : `Опубликовать ${count} черновиков? Клиенты увидят новые цены сразу.`;
  if (!confirm(question)) return;
  const button = $("mt-publish");
  if (button) button.disabled = true;
  setNote("Публикуем…");
  try {
    const result = await API("POST", "/v1/admin/prices/drafts/publish", {});
    await load(true);
    const failed = (result.failed || []).length;
    setNote(`Опубликовано: ${result.published} из ${result.total}${failed ? `, не удалось: ${failed}` : ""}.`);
  } catch (e) {
    setNote(`Не удалось: ${e.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

async function discardDrafts() {
  const count = (DATA && DATA.price_drafts && DATA.price_drafts.count) || 0;
  if (!count) return;
  if (!confirm(`Сбросить ${count} черновиков? Опубликованные цены не изменятся.`)) return;
  try {
    const result = await API("POST", "/v1/admin/prices/drafts/discard", {});
    await load(true);
    setNote(`Сброшено черновиков: ${result.removed}.`);
  } catch (e) {
    setNote(`Не удалось: ${e.message}`);
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
  const variants = $("mt-variants");
  if (variants) variants.checked = state.allVariants;
  drawProviderFilter();
}

/** Шапка: кнопка сортировки и поле-фильтр в каждой колонке. */
function drawHead() {
  const row = ROOT.querySelector("#mt-models thead tr");
  if (!row) return;
  row.replaceChildren();
  for (const [index, column] of COLUMNS.entries()) {
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
    if (index < COLUMNS.length - 1) {
      const grip = el("span", "mt-col-resize");
      grip.title = "Потяните, чтобы изменить ширину; двойной клик — сбросить";
      grip.setAttribute("aria-hidden", "true");
      grip.onpointerdown = (event) => startColumnResize(event, index);
      grip.ondblclick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        COL_WIDTHS = null;
        try { localStorage.removeItem(COL_WIDTH_KEY); } catch (_) { /* private mode */ }
        applyColumnWidths();
      };
      th.append(grip);
    }
    row.append(th);
  }
  applyColumnWidths();
}

const savedWidths = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(COL_WIDTH_KEY) || "null");
    if (Array.isArray(raw) && raw.length === COLUMNS.length && raw.every((value) => Number.isFinite(Number(value)) && Number(value) >= 48)) {
      return raw.map((value) => Math.round(Number(value)));
    }
  } catch (_) { /* private mode */ }
  return null;
};

function tableCols() {
  return [...(ROOT ? ROOT.querySelectorAll("#mt-models colgroup col") : [])];
}

function applyColumnWidths() {
  const table = $("mt-models");
  const cols = tableCols();
  if (!table || !cols.length) return;
  if (!COL_WIDTHS) {
    cols.forEach((col) => { col.style.width = ""; });
    table.style.width = "";
    return;
  }
  let total = 0;
  cols.forEach((col, index) => {
    const width = COL_WIDTHS[index];
    col.style.width = width ? `${width}px` : "";
    if (width) total += width;
  });
  table.style.width = total ? `${total}px` : "";
}

function setupColumnResize() {
  const table = $("mt-models");
  if (!table) return;
  let group = table.querySelector("colgroup");
  if (!group) {
    group = el("colgroup");
    table.prepend(group);
  }
  group.replaceChildren(...COLUMNS.map(() => el("col")));
  COL_WIDTHS = savedWidths();
  applyColumnWidths();
}

function startColumnResize(event, index) {
  const table = $("mt-models");
  if (!table) return;
  const headers = [...table.querySelectorAll("thead th")];
  if (!COL_WIDTHS) COL_WIDTHS = headers.map((th) => Math.round(th.getBoundingClientRect().width));
  if (!COL_WIDTHS[index]) return;
  const grip = event.currentTarget;
  const startX = event.clientX;
  const startWidth = COL_WIDTHS[index];
  try { grip.setPointerCapture?.(event.pointerId); } catch (_) { /* synthetic or already gone */ }
  document.body.classList.add("mt-col-resizing");
  const move = (moveEvent) => {
    COL_WIDTHS[index] = Math.max(48, Math.min(720, Math.round(startWidth + moveEvent.clientX - startX)));
    applyColumnWidths();
  };
  const stop = () => {
    grip.removeEventListener("pointermove", move);
    grip.removeEventListener("pointerup", stop);
    grip.removeEventListener("pointercancel", stop);
    document.body.classList.remove("mt-col-resizing");
    try { localStorage.setItem(COL_WIDTH_KEY, JSON.stringify(COL_WIDTHS)); } catch (_) { /* private mode */ }
  };
  grip.addEventListener("pointermove", move);
  grip.addEventListener("pointerup", stop);
  grip.addEventListener("pointercancel", stop);
  event.preventDefault();
  event.stopPropagation();
}

/** Подпись справочника: сколько моделей сверено и на какую дату. */
function drawReference() {
  const node = $("mt-reference");
  if (!node) return;
  const ref = DATA && DATA.reference;
  const info = (ref && ref.info) || {};
  if (!ref || !ref.count) {
    node.textContent = "справочник Ferro не загружен";
    node.title = "Официальные ставки подтянутся при обновлении цен";
    return;
  }
  const total = (DATA.models || []).length;
  node.textContent = `Ferro ${info.version || ""}: ${ref.matched} из ${total}`;
  node.title = [
    `Справочник Ferro Labs: ${info.count || ref.count} моделей`,
    info.generated_at ? `выгрузка ${dateText(info.generated_at)}` : "",
    info.license ? `лицензия ${info.license}` : "",
    "Только для сверки: биллинг, маршруты и розница его не читают",
  ].filter(Boolean).join(" · ");
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
  drawReference();
  const rows = visibleGroups();
  drawCounts(rows);
  drawModels(rows);
  drawOffers();
}

// ── данные ────────────────────────────────────────────────────────────────────

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Обычная загрузка — из кэша релея, без походов на сайты поставщиков. */
async function load(force = false, reference = false) {
  if (loading) return;
  loading = true;
  const query = new URLSearchParams({ compact: "1" });
  if (force) query.set("refresh", "1");
  if (reference) query.set("reference", "1");
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
    await load(true, true);
    setNote(`Цены и справочник обновлены: ${(DATA && DATA.generated_at) || "—"}.`);
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
  const variants = $("mt-variants");
  if (variants) variants.onchange = () => { state.allVariants = variants.checked; draw(); };
  const collapse = $("mt-collapse");
  if (collapse) collapse.onclick = () => {
    state.sel = "";
    saveFilters();
    draw();
  };
  const refresh = $("mt-refresh");
  if (refresh) refresh.onclick = refreshPrices;
  const apply = $("mt-apply-k");
  if (apply) apply.onclick = bulkPricing;
  const publish = $("mt-publish");
  if (publish) publish.onclick = publishDrafts;
  const discard = $("mt-discard");
  if (discard) discard.onclick = discardDrafts;
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
  setupColumnResize();
  wire();
  syncControls();
  drawGroups();
  if (DATA) {
    draw();
  } else {
    load();
  }
}
