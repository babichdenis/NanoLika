// «Поставщики»: та же связка, что в «Моделях», только наоборот.
//
// Сверху — привычная таблица кошельков поставщиков. Нажал на строку — снизу
// открылась связанная таблица моделей, которые мы берём у этого поставщика:
// выбранный маршрут, модели по умолчанию (в основном медиа) и последний
// фактический запрос. Перегородку можно тянуть, как в «Моделях».
//
// Источник — /v1/admin/prices?compact=1 (там есть routing и last_request) и
// /v1/admin/provider-balances. Автообновление раз в минуту, пока вкладка открыта.

const LABELS = {
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

const SPLIT_KEY = "nm.providers.split";
const COL_WIDTH_KEY = "nm.providers.col-widths";

let ROOT = null;
let API = null;
let DATA = null;
let BAL = null;
let open = "";
let loading = false;
let error = "";
let timer = null;
let updatedAt = 0;
let COL_WIDTHS = null;

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

const stateOf = (entry, bal) => {
  if (!entry) return { text: "нет данных", cls: "fine" };
  if (entry.error) return { text: entry.error, cls: "bad" };
  if (entry.disabled_until) return { text: "приостановлен", cls: "warn" };
  const floor = entry.currency === "USD" ? bal?.minimum_balance_usd : bal?.minimum_balance_rub;
  const value = entry.currency === "CAPS" ? entry.rub_estimate : entry.amount;
  if (Number.isFinite(Number(floor)) && Number(value) <= Number(floor)) return { text: "ниже порога", cls: "warn" };
  return { text: "можно тратить", cls: "ok" };
};

/** Сводка по моделям: кто выбран, кто стоит по умолчанию и где был последний запрос. */
function statsFor(data) {
  const map = new Map();
  const ensure = (id) => {
    if (!id) return null;
    let row = map.get(id);
    if (!row) {
      row = { selected: [], configured: [], last: [], offerModels: new Set() };
      map.set(id, row);
    }
    return row;
  };
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
      if (row) row.offerModels.add(model.id);
    }
  }
  return map;
}

const emptyStats = () => ({ selected: [], configured: [], last: [], offerModels: new Set() });
const statsOf = (stats, id) => stats.get(id) || emptyStats();

/** Строки верхней таблицы: привычные кошельки, плюс поставщики без кошелька, если они что-то раздают. */
function walletRows(stats) {
  const balances = BAL?.balances || {};
  const rows = Object.entries(balances).map(([id, entry]) => ({ id, entry, stats: statsOf(stats, id) }));
  const seen = new Set(rows.map((row) => row.id));
  for (const [id, row] of stats) {
    if (seen.has(id)) continue;
    // Поставщик без ручки баланса, но с ценами в снимке, всё равно нужен в
    // списке: иначе Jiugx не видно, хотя его предложения уже в таблице моделей.
    if (row.selected.length || row.configured.length || row.last.length || row.offerModels.size) rows.push({ id, entry: null, stats: row });
  }
  const rub = (row) => Number(row.entry?.rub_estimate ?? row.entry?.amount ?? 0) || 0;
  return rows.sort((a, b) => rub(b) - rub(a) || b.stats.selected.length - a.stats.selected.length || providerLabel(a.id).localeCompare(providerLabel(b.id), "ru"));
}

function countLine(row) {
  const parts = [];
  if (row.stats.selected.length) parts.push(`${row.stats.selected.length} сейчас`);
  if (row.stats.configured.length) parts.push(`${row.stats.configured.length} по умолчанию`);
  if (row.stats.last.length) parts.push(`${row.stats.last.length} последних`);
  return parts.join(" · ");
}

/** Все модели поставщика без повторов, с пометками, почему она здесь. */
function detailModels(row) {
  const map = new Map();
  const add = (model, field) => {
    let item = map.get(model.id);
    if (!item) {
      item = { model, selected: false, configured: false, last: false };
      map.set(model.id, item);
    }
    item[field] = true;
  };
  for (const model of row.selected) add(model, "selected");
  for (const model of row.configured) add(model, "configured");
  for (const model of row.last) add(model, "last");
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
    lastCell.textContent = `${when(last.at)} · ${providerLabel(last.provider)}${last.cost_rub != null ? ` · ${fmt(last.cost_rub)} ₽` : ""}`;
    if (last.provider !== provider.id) lastCell.classList.add("pt-fallback");
  } else {
    lastCell.textContent = "—";
  }
  tr.append(lastCell);
  return tr;
}

function drawModels(row) {
  const bottom = $("pt-bottom-pane");
  const body = $("pt-models-body");
  const title = $("pt-sel-name");
  const meta = $("pt-sel-meta");
  const link = $("pt-open-models");
  if (!bottom || !body) return;
  if (!row) {
    splitVisible(false);
    return;
  }
  splitVisible(true);
  if (title) title.textContent = providerLabel(row.id);
  if (meta) {
    meta.textContent = `${row.stats.selected.length} сейчас · ${row.stats.configured.length} по умолчанию · ${row.stats.last.length} последних`;
  }
  if (link) link.href = "#models?p=" + encodeURIComponent(row.id);
  body.replaceChildren();

  const models = detailModels(row.stats);
  if (!models.length) {
    const tr = el("tr");
    const td = el("td", "fine", row.stats.offerModels.size
      ? `Сейчас в маршруте нет моделей. В таблице моделей у поставщика ${row.stats.offerModels.size} предложений.`
      : "У поставщика нет моделей в текущем маршруте.");
    td.colSpan = 7;
    tr.append(td);
    body.append(tr);
    return;
  }
  for (const item of models) body.append(modelRow(item, row));
}

function draw() {
  const body = $("pt-rows");
  if (!body) return;
  const stats = statsFor(DATA || {});
  const rows = walletRows(stats);
  if (open && !rows.some((row) => row.id === open)) open = "";
  body.replaceChildren();

  for (const row of rows) {
    const tr = el("tr", "pt-row" + (row.id === open ? " mt-active" : ""));
    tr.onclick = () => {
      open = open === row.id ? "" : row.id;
      draw();
    };
    const name = el("td");
    name.append(el("span", "pt-provider", providerLabel(row.id)));
    const sub = [row.entry ? stateOf(row.entry, BAL).text : "без кошелька", countLine(row), row.stats.offerModels.size ? `${row.stats.offerModels.size} с ценой` : ""].filter(Boolean).join(" · ");
    name.append(el("div", "pt-sub", sub));
    tr.append(name);

    const total = row.stats.selected.length + row.stats.configured.length;
    const modelsCell = el("td", "num", String(total));
    modelsCell.title = `${row.stats.selected.length} сейчас + ${row.stats.configured.length} по умолчанию`;
    tr.append(modelsCell);

    tr.append(el("td", "num", money(row.entry)));
    const rub = row.entry?.rub_estimate;
    tr.append(el("td", "num hide-sm", rub == null ? "—" : `₽${fmt(rub)}`));
    tr.append(el("td", "hide-sm fine", when(row.entry?.checked)));
    const state = stateOf(row.entry, BAL);
    const stateCell = el("td");
    stateCell.append(el("span", state.cls, state.text));
    tr.append(stateCell);
    body.append(tr);
  }

  const selected = rows.find((row) => row.id === open) || null;
  drawModels(selected);

  const updated = $("pt-updated");
  if (updated) updated.textContent = updatedAt ? `обновлено ${new Date(updatedAt).toLocaleTimeString("ru-RU")}` : "";
  const note = $("pt-note");
  if (note) {
    note.textContent = error
      ? `Не удалось обновить: ${error}`
      : "Кошельки поставщиков. Нажми на строку — снизу откроются модели, которые мы берём у этого поставщика: сейчас, по умолчанию и последний запрос. Автообновление раз в минуту.";
    note.classList.toggle("warn", Boolean(error));
  }
}

function splitVisible(show) {
  const bottom = $("pt-bottom-pane");
  const split = $("pt-split");
  const box = $("pt");
  if (bottom) bottom.hidden = !show;
  if (split) split.hidden = !show;
  if (box) box.classList.toggle("mt-single", !show);
}

function setupSplit() {
  const box = $("pt");
  const split = $("pt-split");
  const top = $("pt-top-pane");
  if (!box || !split || !top) return;
  try {
    const saved = Number(localStorage.getItem(SPLIT_KEY));
    if (Number.isFinite(saved) && saved > 0) top.style.flexBasis = `${saved}px`;
  } catch (_) { /* private mode */ }
  let drag = false;
  const clamp = (value) => Math.max(150, Math.min(value, box.getBoundingClientRect().height - 150));
  const move = (event) => {
    if (!drag) return;
    const rect = box.getBoundingClientRect();
    top.style.flexBasis = `${Math.round(clamp(event.clientY - rect.top))}px`;
  };
  const stop = () => {
    if (!drag) return;
    drag = false;
    document.body.classList.remove("mt-resizing");
    try { localStorage.setItem(SPLIT_KEY, String(Math.round(top.getBoundingClientRect().height))); } catch (_) { /* nothing */ }
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
    try { localStorage.setItem(SPLIT_KEY, String(Math.round(clamp(current + step)))); } catch (_) { /* nothing */ }
  });
}

const savedWidths = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(COL_WIDTH_KEY) || "null");
    const count = ROOT ? ROOT.querySelectorAll("#pt-models thead th").length : 0;
    if (Array.isArray(raw) && raw.length === count && raw.every((value) => Number.isFinite(Number(value)) && Number(value) >= 48)) {
      return raw.map((value) => Math.round(Number(value)));
    }
  } catch (_) { /* private mode */ }
  return null;
};

function tableCols() {
  return [...(ROOT ? ROOT.querySelectorAll("#pt-models colgroup col") : [])];
}

function applyColumnWidths() {
  const table = $("pt-models");
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

function startColumnResize(event, index) {
  const table = $("pt-models");
  if (!table) return;
  const headers = [...table.querySelectorAll("thead th")];
  if (!COL_WIDTHS) COL_WIDTHS = headers.map((th) => Math.round(th.getBoundingClientRect().width));
  if (!COL_WIDTHS[index]) return;
  const grip = event.currentTarget;
  const startX = event.clientX;
  const startWidth = COL_WIDTHS[index];
  try { grip.setPointerCapture?.(event.pointerId); } catch (_) { /* synthetic or gone */ }
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

function setupColumnResize() {
  const table = $("pt-models");
  if (!table) return;
  let group = table.querySelector("colgroup");
  if (!group) {
    group = el("colgroup");
    table.prepend(group);
  }
  const headers = [...table.querySelectorAll("thead th")];
  group.replaceChildren(...headers.map(() => el("col")));
  headers.forEach((th, index) => {
    if (index === headers.length - 1 || th.querySelector(".mt-col-resize")) return;
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
  });
  COL_WIDTHS = savedWidths();
  applyColumnWidths();
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
<div class="mt pt" id="pt">
  <div class="mt-pane mt-pane-top" id="pt-top-pane">
    <div class="mt-top">
      <div class="mt-toolbar">
        <strong>Поставщики</strong>
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
          <th class="num" title="Сейчас в маршруте + модели по умолчанию">Моделей</th>
          <th class="num">Доступно</th>
          <th class="num hide-sm">Оценка, ₽</th>
          <th class="hide-sm">Проверено</th>
          <th>Состояние</th>
        </tr></thead>
        <tbody id="pt-rows"></tbody>
      </table>
    </div>
  </div>
  <div class="mt-split" id="pt-split" role="separator" tabindex="0" aria-orientation="horizontal" aria-label="Высота таблицы поставщиков"><span></span></div>
  <div class="mt-pane mt-pane-bottom" id="pt-bottom-pane" hidden>
    <div class="mt-linked-head">
      <h3 id="pt-sel-name">Модели поставщика</h3>
      <span class="fine" id="pt-sel-meta"></span>
      <span class="sp"></span>
      <a class="pt-link" id="pt-open-models" href="#models">Открыть в таблице моделей →</a>
    </div>
    <div class="mt-wrap">
      <table id="pt-models">
        <colgroup></colgroup>
        <thead><tr>
          <th>Модель</th>
          <th>Тип</th>
          <th>Как берём</th>
          <th class="num">Закупка, ₽</th>
          <th class="num">Клиенту, ₽</th>
          <th class="num">Контекст</th>
          <th>Последний запрос</th>
        </tr></thead>
        <tbody id="pt-models-body"></tbody>
      </table>
    </div>
  </div>
</div>`;

export function mount(root, ctx) {
  if (!root) return;
  ROOT = root;
  API = ctx.api;
  root.innerHTML = TEMPLATE;
  const button = $("pt-refresh");
  if (button) button.onclick = () => void load(true);
  setupSplit();
  setupColumnResize();
  splitVisible(false);
  if (DATA) draw();
  void load(true);
  startTimer();
}
