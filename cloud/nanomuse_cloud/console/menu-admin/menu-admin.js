// Прилавок: весь рынок, галочка «видно человеку», полки. Токен берётся там же, где его
// держит остальная админка (sessionStorage nm.admin), поэтому второй раз входить не нужно.
const SS = window.sessionStorage;
const TOKEN = SS.getItem("nm.admin") || SS.getItem("nm.admin.token") || "";
const SHELVES = [
  ["chat", "Переписка"],
  ["coding", "Кодинг"],
  ["reasoning", "Размышления"],
  ["image", "Картинки"],
  ["video", "Видео"],
  ["speech", "Озвучка"],
  ["transcribe", "Распознавание"],
];
const JUNK_RU = {
  "junk:embedding": "вектора",
  "junk:moderation": "модерация",
  "junk:stale": "устаревшая версия",
  "junk:deprecated": "снята продавцом",
  "junk:free": "free-маршрут; не включать в платный каталог",
  "junk:no-price": "нет цены",
  "junk:duplicate": "дубль",
  "junk:audio-tool": "служебная аудио",
  "junk:image-tool": "только картинки",
  "junk:no-kind": "не для клиента",
};

const state = { models: [], dirty: false, shelf: "", q: "", hideJunk: true, onlyOn: false };

function h(tag, text, cls) {
  const el = document.createElement(tag);
  if (text !== undefined && text !== null) el.textContent = String(text);
  if (cls) el.className = cls;
  return el;
}

function money(v) {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("ru-RU", { maximumFractionDigits: v < 10 ? 2 : 1 });
}

async function api(path, init) {
  const headers = { "X-Admin-Token": TOKEN };
  if (init && init.body) headers["Content-Type"] = "application/json";
  const r = await fetch(path, { ...(init || {}), headers });
  if (r.status === 401) throw new Error("нужен админский токен: откройте /app/admin/ и войдите");
  if (!r.ok) throw new Error(`ответ ${r.status}`);
  return r.json();
}

function counts() {
  const clean = state.models.filter((m) => !m.junk);
  const on = clean.filter((m) => m.on);
  const box = document.getElementById("counts");
  box.replaceChildren();
  box.append(
    h("span", "на рынке ", "meta"),
    h("b", String(state.models.length)),
    h("span", " · после отсева ", "meta"),
    h("b", String(clean.length)),
    h("span", " · на витрине ", "meta"),
    h("b", String(on.length)),
  );
}

function chips() {
  const box = document.getElementById("chips");
  box.replaceChildren();
  const all = h("button", "Все");
  all.className = "chip" + (state.shelf === "" ? " on" : "");
  all.onclick = () => { state.shelf = ""; render(); };
  box.append(all);
  for (const [id, label] of SHELVES) {
    const n = state.models.filter((m) => !m.junk && (m.shelves || []).includes(id)).length;
    const b = h("button", label);
    b.className = "chip" + (state.shelf === id ? " on" : "");
    b.append(h("b", String(n)));
    b.onclick = () => { state.shelf = state.shelf === id ? "" : id; render(); };
    box.append(b);
  }
}

function row(model) {
  const el = h("div", undefined, "row" + (model.junk ? " junk" : ""));

  const box = h("input");
  box.type = "checkbox";
  box.checked = model.on;
  box.disabled = Boolean(model.junk);
  box.onchange = () => { model.on = box.checked; state.dirty = true; document.getElementById("save").disabled = false; counts(); };
  el.append(box);

  const left = h("div");
  left.append(h("div", model.name, "name"));
  const meta = h("div", undefined, "meta");
  meta.append(h("code", model.slug), h("span", " · " + model.unit));
  if (model.new_model) meta.append(h("span", " · новая модель — проверьте до включения", "tag"));
  if (model.price_changed) meta.append(h("span", " · цена изменилась", "tag"));
  if (model.free) meta.append(h("span", " · free", "tag free"));
  if (model.context_length) meta.append(h("span", ` · контекст ${Number(model.context_length).toLocaleString("ru-RU")}`));
  if (model.max_output) meta.append(h("span", ` · ответ до ${Number(model.max_output).toLocaleString("ru-RU")}`));
  if (model.junk) meta.append(h("span", " · " + (JUNK_RU[model.junk] || model.junk), "tag"));
  left.append(meta);
  el.append(left);

  const prices = h("div", undefined, "prices");
  prices.append(h("b", model.free ? "free" : money(model.cheapest_rub) + " ₽"));
  const pricedOffers = (model.offers || []).filter((o) => o.rub !== null && o.rub !== undefined);
  const eligibleOffers = pricedOffers.filter((o) => o.route_eligible !== false);
  const inputs = eligibleOffers.filter((o) => o.direction === "input");
  const best = (inputs.length ? inputs : eligibleOffers).sort((a, b) => a.rub - b.rub)[0];
  if (best) prices.append(h("div", "у " + best.provider, "meta"));
  el.append(prices);

  const shelves = h("div", undefined, "shelves");
  for (const id of model.shelves || []) {
    const label = (SHELVES.find((s) => s[0] === id) || [id, id])[1];
    const chip = h("button", label, "shelf on");
    chip.onclick = () => {
      model.shelves = (model.shelves || []).filter((s) => s !== id);
      state.dirty = true;
      document.getElementById("save").disabled = false;
      render();
    };
    shelves.append(chip);
  }
  const missing = SHELVES.filter(([id]) => !(model.shelves || []).includes(id));
  if (missing.length && !model.junk) {
    const add = h("button", "+ полка", "shelf");
    add.onclick = () => {
      const [id] = missing[0];
      model.shelves = [...(model.shelves || []), id];
      state.dirty = true;
      document.getElementById("save").disabled = false;
      render();
    };
    shelves.append(add);
  }
  el.append(shelves);

  const offers = h("div", undefined, "meta offers");
  offers.textContent = (model.offers || [])
    .filter((o) => o.rub !== null && o.rub !== undefined)
    .sort((a, b) => a.rub - b.rub)
    .slice(0, 3)
    .map((o) => `${o.provider} ${o.free ? "free" : `${o.direction === "input" ? "вход" : o.direction === "output" ? "выход" : ""} ${money(o.rub)} ₽`}${o.route_eligible === false ? ` · ${o.exclusion_reason || "исключено из маршрутизации"}` : ""}${o.unit && o.unit !== model.unit ? " за " + o.unit : ""}`)
    .join(" · ");
  el.append(offers);
  return el;
}

function render() {
  counts();
  chips();
  const q = state.q.trim().toLowerCase();
  const list = document.getElementById("list");
  list.replaceChildren();
  const rows = state.models.filter((m) => {
    if (state.hideJunk && m.junk) return false;
    if (state.onlyOn && !m.on) return false;
    if (state.shelf && !(m.shelves || []).includes(state.shelf)) return false;
    if (!q) return true;
    return m.name.toLowerCase().includes(q) || m.slug.includes(q) || (m.offers || []).some((o) => String(o.provider).includes(q));
  });
  if (!rows.length) list.append(h("p", "Ничего не нашлось.", "empty"));
  for (const model of rows) list.append(row(model));
  document.getElementById("footer").textContent = `показано ${rows.length} из ${state.models.length}`;
}

async function load() {
  const note = document.getElementById("note");
  try {
    const data = await api("/v1/admin/menu");
    state.models = data.models;
    const saved = data.saved_at ? new Date(data.saved_at * 1000).toLocaleString("ru-RU") : "ещё не сохранён";
    note.textContent = `цены собраны ${data.prices_at || "—"} · выбор: ${saved}`;
    document.getElementById("hide-junk").checked = true;
    document.getElementById("only-on").checked = false;
    render();
  } catch (e) {
    note.textContent = String(e.message || e);
  }
}

function save() {
  const note = document.getElementById("note");
  const models = {};
  for (const m of state.models) models[m.name] = { on: m.on, shelves: m.shelves || [] };
  api("/v1/admin/menu", { method: "PUT", body: JSON.stringify({ models }) })
    .then((r) => {
      note.textContent = `сохранено: ${r.saved} моделей, на витрине ${r.on}`;
      document.getElementById("save").disabled = true;
      state.dirty = false;
    })
    .catch((e) => { note.textContent = String(e.message || e); });
}

document.getElementById("q").oninput = (e) => { state.q = e.target.value; render(); };
document.getElementById("hide-junk").onchange = (e) => { state.hideJunk = e.target.checked; render(); };
document.getElementById("only-on").onchange = (e) => { state.onlyOn = e.target.checked; render(); };
document.getElementById("save").onclick = save;
load();
