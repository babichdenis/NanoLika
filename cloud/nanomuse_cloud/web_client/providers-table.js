// «Поставщики»: обратная сторона таблицы моделей.
//
// В «Моделях» выбираешь модель и видишь, кто её отдаёт. Здесь наоборот:
// одна строка — один поставщик; нажал — видно, какие модели мы берём у него
// сейчас, какие стоят у него по умолчанию и по каким моделям был последний
// фактический запрос. Источник — тот же /v1/admin/prices (compact), поэтому
// выбранный маршрут и последний запрос всегда рядом.
//
// Данные обновляются на сервере: кнопка «Обновить» и автообновление раз в
// минуту, пока вкладка открыта.

const LABELS = {
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

let ROOT = null;
let CTX = null;
let API = null;
let DATA = null;
let BAL = null;
let open = "";
let loading = false;
let error = "";
let timer = null;
let updatedAt = 0;

const $ = (id) => (ROOT ? ROOT.querySelector("#" + id) : null);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

const fmt = (value, digits = 2) => {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
};

const ctxText = (value) => {
  const n = Number(value) || 0;
  if (!n) return "—";
  if (n >= 1_000_000) return fmt(n / 1_000_000, 1) + "M";
  if (n >= 1000) return fmt(n / 1000, 0) + "K";
  return fmt(n, 0);
};

const kindLabel = (kind) => {
  const value = String(kind || "chat");
  if (value.startsWith("image")) return "Изображение";
  if (value.startsWith("video")) return "Видео";
  if (value.startsWith("audio")) return "Аудио";
  if (value === "embedding") return "Эмбеддинг";
  if (value === "chat" || value === "responses" || value === "completion") return "Текст";
  return value;
};

const purchaseText = (model) => {
  const cost = model.cost || {};
  if (cost.rub_image != null) return `${fmt(cost.rub_image)} ₽ / шт.`;
  if (cost.rub_second != null) return `${fmt(cost.rub_second)} ₽ / сек.`;
  if (cost.rub_speech != null) return `${fmt(cost.rub_speech)} ₽ / 1000 зн.`;
  if (cost.rub_in != null || cost.rub_out != null) return `${fmt(cost.rub_in)} / ${fmt(cost.rub_out)}`;
  return "—";
};

const clientText = (model) => {
  const sell = model.sell || {};
  if (sell.rub_in || sell.rub_out) return `${fmt(sell.rub_in)} / ${fmt(sell.rub_out)}`;
  if (sell.hint) return sell.hint;
  return "—";
};

const when = (ts) => {
  const n = Number(ts);
  if (!n) return "—";
  return new Date(n * 1000).toLocaleString("ru-RU", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

const providerLabel = (name) => LABELS[name] || name || "—";

const money = (entry) => {
  if (!entry || entry.amount == null) return "—";
  const amount = Number(entry.amount);
  if (!Number.isFinite(amount)) return "—";
  if (entry.currency === "USD") return `$${fmt(amount, 2)}`;
  if (entry.currency === "CAPS") return `${fmt(Math.round(amount), 0)} CAPS`;
  return `₽${fmt(amount, 2)}`;
};

const walletNote = (entry, bal) => {
  if (!entry) return null;
  if (entry.error) return { text: entry.error, cls: "warn" };
  if (entry.disabled_until) return { text: "приостановлен", cls: "warn" };
  const floor = entry.currency === "USD" ? bal?.minimum_balance_usd : bal?.minimum_balance_rub;
  const value = entry.currency === "CAPS" ? entry.rub_estimate : entry.amount;
  if (Number.isFinite(Number(floor)) && Number(value) <= Number(floor)) return { text: "ниже порога", cls: "warn" };
  return { text: "можно тратить", cls: "ok" };
};

const STATUS = (provider) => {
  if (provider.connected) return provider.routing_supported ? "подключён · маршрутизация" : "подключён · без маршрутизации";
  if (provider.known?.key_configured) return provider.routing_supported ? "ключ есть · маршрутизация" : "ключ есть · без маршрутизации";
  return "не подключён";
};

/** Один проход по моделям: кто выбран сейчас, кто стоит по умолчанию, где был последний запрос. */
function buildProviders(data, balances) {
  const rows = new Map();
  const known = new Map((data.providers?.known || []).map((row) => [row.name, row]));
  const connected = new Set(data.providers?.connected || []);
  const wallet = balances?.balances || {};
  const ensure = (id) => {
    if (!id) return null;
    let row = rows.get(id);
    if (!row) {
      row = {
        id,
        known: known.get(id) || null,
        connected: connected.has(id),
        routing_supported: Boolean(known.get(id)?.routing_supported),
        balance: wallet[id] || null,
        selected: [],
        configured: [],
        last: [],
        offerModels: new Set(),
        offers: 0,
      };
      rows.set(id, row);
    }
    return row;
  };

  for (const id of known.keys()) ensure(id);
  for (const id of Object.keys(wallet)) ensure(id);

  for (const model of data.models || []) {
    const routing = model.routing || {};
    const selected = routing.selected || "";
    const configured = routing.configured?.provider || "";
    const last = model.last_request?.provider || "";
    if (selected) ensure(selected).selected.push(model);
    if (configured && !selected) ensure(configured).configured.push(model);
    if (last) ensure(last).last.push(model);
    for (const offer of routing.offers || []) {
      const row = ensure(offer.provider);
      if (!row) continue;
      row.offerModels.add(model.id);
      row.offers += 1;
    }
  }

  return [...rows.values()]
    .filter((row) => row.connected || row.known?.key_configured || row.selected.length || row.configured.length || row.last.length || row.balance)
    .sort((a, b) => b.selected.length - a.selected.length || b.configured.length - a.configured.length || providerLabel(a.id).localeCompare(providerLabel(b.id), "ru"));
}

/** Все модели поставщика без повторов, с пометками, почему она здесь. */
function detailModels(provider) {
  const map = new Map();
  const add = (model, field) => {
    let row = map.get(model.id);
    if (!row) {
      row = { model, selected: false, configured: false, last: false };
      map.set(model.id, row);
    }
    row[field] = true;
  };
  for (const model of provider.selected) add(model, "selected");
  for (const model of provider.configured) add(model, "configured");
  for (const model of provider.last) add(model, "last");
  return [...map.values()].sort((a, b) => Number(b.selected) - Number(a.selected) || Number(b.configured) - Number(a.configured) || String(a.model.name || a.model.id).localeCompare(String(b.model.name || b.model.id), "ru"));
}

function modelRow(item, provider) {
  const model = item.model;
  const tr = el("tr");
  const name = el("td");
  name.append(el("span", "pt-model-name", model.name || model.id));
  name.append(el("code", "pt-model-id", model.id));
  tr.append(name, el("td", "", kindLabel(model.kind)));

  const basis = el("td");
  if (item.selected) basis.append(el("span", "pt-badge is-now", "сейчас"));
  if (item.configured) basis.append(el("span", "pt-badge", "по умолчанию"));
  if (item.last) basis.append(el("span", "pt-badge is-last", "последний запрос"));
  tr.append(basis);

  tr.append(el("td", "num", purchaseText(model)), el("td", "num", clientText(model)), el("td", "num", ctxText(model.context)));

  const last = model.last_request;
  const lastCell = el("td", "fine");
  if (last) {
    const same = last.provider === provider.id;
    lastCell.textContent = `${when(last.at)} · ${providerLabel(last.provider)}${last.cost_rub != null ? ` · ${fmt(last.cost_rub)} ₽` : ""}`;
    if (!same) lastCell.classList.add("pt-fallback");
  } else {
    lastCell.textContent = "—";
  }
  tr.append(lastCell);
  return tr;
}

function detail(provider) {
  const tr = el("tr", "pt-detail");
  const td = el("td");
  td.colSpan = 6;
  const box = el("div", "pt-detail-inner");
  const head = el("div", "pt-detail-head");
  head.append(el("b", "", providerLabel(provider.id)));
  head.append(el("span", "fine", `${provider.selected.length} сейчас · ${provider.configured.length} по умолчанию · ${provider.last.length} последних`));
  const link = el("a", "pt-link", "Открыть в таблице моделей →");
  link.href = "#models?p=" + encodeURIComponent(provider.id);
  link.onclick = (event) => event.stopPropagation();
  head.append(link);
  box.append(head);

  const models = detailModels(provider);
  if (!models.length) {
    box.append(el("p", "pt-empty", provider.offerModels.size
      ? `Сейчас в маршруте нет моделей. В таблице моделей у поставщика ${provider.offerModels.size} предложений.`
      : "У поставщика нет моделей в текущем маршруте."));
  } else {
    const table = el("table", "pt-models");
    const thead = el("thead");
    const hr = el("tr");
    for (const label of ["Модель", "Тип", "Как берём", "Закупка, ₽", "Клиенту, ₽", "Контекст", "Последний запрос"]) {
      hr.append(el("th", label.startsWith("Закупка") || label.startsWith("Клиенту") || label === "Контекст" ? "num" : "", label));
    }
    thead.append(hr);
    const tbody = el("tbody");
    for (const item of models) tbody.append(modelRow(item, provider));
    table.append(thead, tbody);
    const wrap = el("div", "pt-models-wrap");
    wrap.append(table);
    box.append(wrap);
  }
  td.append(box);
  tr.append(td);
  return tr;
}

function draw() {
  const body = $("pt-rows");
  if (!body) return;
  const providers = buildProviders(DATA || {}, BAL || {});
  if (open && !providers.some((row) => row.id === open)) open = "";
  body.replaceChildren();

  for (const provider of providers) {
    const tr = el("tr", "pt-row" + (provider.selected.length ? " is-live" : ""));
    tr.onclick = () => {
      open = open === provider.id ? "" : provider.id;
      draw();
    };
    const name = el("td");
    name.append(el("span", "pt-provider", providerLabel(provider.id)));
    name.append(el("span", "pt-sub", `${provider.id} · ${STATUS(provider)}`));
    tr.append(name);

    const count = (value, cls = "") => el("td", "num " + cls, value ? String(value) : "—");
    tr.append(count(provider.selected.length, provider.selected.length ? "ok" : ""));
    tr.append(count(provider.configured.length));
    tr.append(count(provider.last.length));
    tr.append(count(provider.offerModels.size));

    const wallet = el("td", "num");
    wallet.append(document.createTextNode(money(provider.balance)));
    const cash = provider.balance;
    if (cash && cash.currency !== "RUB" && cash.rub_estimate != null) wallet.append(el("div", "fine", `≈ ₽${fmt(cash.rub_estimate)}`));
    const note = walletNote(provider.balance, BAL);
    if (note) wallet.append(el("div", "fine " + note.cls, note.text));
    tr.append(wallet);

    body.append(tr);
    if (open === provider.id) body.append(detail(provider));
  }

  const updated = $("pt-updated");
  if (updated) updated.textContent = updatedAt ? `обновлено ${new Date(updatedAt).toLocaleTimeString("ru-RU")}` : "";
  const note = $("pt-note");
  if (note) {
    note.textContent = error
      ? `Не удалось обновить: ${error}`
      : "Сейчас — выбранный маршрут; по умолчанию — модели без динамического маршрута (в основном медиа); последний запрос — что реально ответило. Автообновление раз в минуту.";
    note.classList.toggle("warn", Boolean(error));
  }
}

async function load(force = true) {
  if (loading) return;
  loading = true;
  const button = $("pt-refresh");
  if (button) button.disabled = true;
  try {
    const query = new URLSearchParams({ compact: "1" });
    if (force) query.set("refresh", "1");
    const [prices, balances] = await Promise.all([
      API("GET", "/v1/admin/prices?" + query.toString()),
      API("GET", "/v1/admin/provider-balances").catch(() => null),
    ]);
    DATA = prices || {};
    BAL = balances || {};
    error = "";
    updatedAt = Date.now();
    draw();
  } catch (exc) {
    error = String(exc.message || exc);
    draw();
  } finally {
    loading = false;
    if (button) button.disabled = false;
  }
}

function startTimer() {
  if (timer) clearInterval(timer);
  timer = setInterval(() => {
    if (!ROOT || !ROOT.isConnected) {
      clearInterval(timer);
      timer = null;
      return;
    }
    if (!document.hidden) void load(true);
  }, 60_000);
}

const TEMPLATE = `
<div class="mt pt">
  <div class="mt-top">
    <div class="mt-toolbar">
      <strong>Карта поставщиков</strong>
      <span class="fine" id="pt-updated"></span>
      <span class="sp"></span>
      <button class="chip" id="pt-refresh" type="button">Обновить</button>
    </div>
    <p class="fine" id="pt-note"></p>
  </div>
  <div class="mt-wrap">
    <table>
      <thead><tr>
        <th>Поставщик</th>
        <th class="num">Сейчас</th>
        <th class="num hide-sm">По умолчанию</th>
        <th class="num hide-sm">Последние</th>
        <th class="num hide-sm">Моделей с ценой</th>
        <th class="num">Кошелёк</th>
      </tr></thead>
      <tbody id="pt-rows"></tbody>
    </table>
  </div>
</div>`;

export function mount(root, ctx) {
  if (!root) return;
  ROOT = root;
  CTX = ctx;
  API = ctx.api;
  root.innerHTML = TEMPLATE;
  const button = $("pt-refresh");
  if (button) button.onclick = () => void load(true);
  if (DATA) draw();
  void load(true);
  startTimer();
}
