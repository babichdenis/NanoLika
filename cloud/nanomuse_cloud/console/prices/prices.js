// Цены: наше меню с закупкой и розницей, поставщики, рынок и черновики.
// Токен берётся там же, где держит его остальная админка (sessionStorage nm.admin).
const SS = window.sessionStorage;
const TOKEN = SS.getItem("nm.admin") || SS.getItem("nm.admin.token") || "";

const state = { data: null, k: 5, q: "", lane: "", onlySell: false };

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

async function api(path) {
  const r = await fetch(path, { headers: { "X-Admin-Token": TOKEN } });
  if (r.status === 401) throw new Error("нужен админский токен: откройте /app/admin/ и войдите");
  if (!r.ok) throw new Error(`ответ ${r.status}`);
  return r.json();
}

function drawRate() {
  const box = document.getElementById("rate");
  const rate = state.data.rate || {};
  box.replaceChildren();
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

function chats() {
  const q = state.q.trim().toLowerCase();
  return state.data.models.filter((m) => {
    if (m.kind !== "chat") return false;
    if (state.lane && !(m.lanes || []).includes(state.lane)) return false;
    if (state.onlySell && !(m.sell.rub_in || m.sell.rub_out)) return false;
    if (!q) return true;
    return m.name.toLowerCase().includes(q) || m.id.includes(q) || String(m.provider || "").includes(q);
  });
}

function drawChips() {
  const all = state.data.models.filter((m) => m.kind === "chat");
  const box = document.getElementById("chips");
  box.replaceChildren();
  const lanes = [
    ["", "Все", all.length],
    ["chat", "Переписка", all.filter((m) => (m.lanes || []).includes("chat")).length],
    ["gui", "Руки", all.filter((m) => (m.lanes || []).includes("gui")).length],
  ];
  for (const [id, label, n] of lanes) {
    const b = h("button", label);
    b.className = "chip" + (state.lane === id ? " on" : "");
    b.append(h("b", String(n)));
    b.onclick = () => {
      state.lane = state.lane === id ? "" : id;
      draw();
    };
    box.append(b);
  }
}

function modelRow(m) {
  const tr = h("tr");
  const sellIn = m.sell.rub_in || 0;
  const sellOut = m.sell.rub_out || 0;
  const hasSell = Boolean(sellIn || sellOut);
  const costIn = m.cost.rub_in;
  const costOut = m.cost.rub_out;
  if (hasSell) tr.className = "has-sell";
  if (costIn === null && costOut === null) tr.classList.add("no-cost");

  const name = h("td", undefined, "name");
  name.append(h("div", m.name));
  const meta = h("div", undefined, "meta");
  meta.append(h("code", m.id));
  if (m.recommended) meta.append(h("span", " · по умолчанию"));
  if (!m.listed) meta.append(h("span", " · скрыта из меню"));
  if (!m.default_on) meta.append(h("span", " · выключена у нового"));
  name.append(meta);
  tr.append(name);

  tr.append(h("td", (m.lanes || []).join(", ") || "—", "meta"));

  const cost = h("td", pair(costIn, costOut), "num cost");
  tr.append(cost);

  const now = h("td", hasSell ? pair(sellIn, sellOut) : "не задана", "num sell");
  tr.append(now);

  const ratio = h("td", undefined, "num");
  if (hasSell && (m.ratio.in !== null || m.ratio.out !== null)) {
    ratio.textContent = `${ratioText(m.ratio.in)} / ${ratioText(m.ratio.out)}`;
    const low = [m.ratio.in, m.ratio.out].some((r) => r !== null && r < 1.5);
    if (low) ratio.className = "num ratio-low";
  } else {
    ratio.textContent = "—";
    ratio.className = "num meta";
  }
  tr.append(ratio);

  const k = state.k;
  const offer = h("td", undefined, "num");
  if (costIn === null && costOut === null) {
    offer.textContent = "—";
    offer.className = "num meta";
  } else {
    offer.textContent = pair(costIn === null ? null : costIn * k, costOut === null ? null : costOut * k);
  }
  tr.append(offer);

  const margin = h("td", undefined, "num");
  if (costIn === null && costOut === null) {
    margin.textContent = "—";
    margin.className = "num meta";
  } else {
    const baseIn = hasSell && sellIn ? sellIn : costIn === null ? null : costIn * k;
    const baseOut = hasSell && sellOut ? sellOut : costOut === null ? null : costOut * k;
    const mIn = baseIn === null ? null : baseIn - costIn;
    const mOut = baseOut === null ? null : baseOut - costOut;
    margin.textContent = pair(mIn, mOut);
  }
  tr.append(margin);
  return tr;
}

function drawModels() {
  const rows = chats();
  const body = document.querySelector("#models tbody");
  body.replaceChildren();
  for (const m of rows) body.append(modelRow(m));
  document.getElementById("chat-count").textContent = `${rows.length} из ${state.data.count.chat}`;
}

function mediaRow(m) {
  const tr = h("tr");
  const name = h("td", undefined, "name");
  name.append(h("div", m.name), h("div", undefined, "meta"));
  name.lastChild.append(h("code", m.id));
  tr.append(name);
  tr.append(h("td", m.kind, "meta"));
  const cost = m.cost.rub_image ?? m.cost.rub_second ?? m.cost.rub_speech;
  const unit = m.cost.rub_image !== null ? "за картинку" : m.cost.rub_second !== null ? "за секунду" : m.cost.rub_speech !== null ? "за 1000 знаков" : "";
  tr.append(h("td", cost === null || cost === undefined ? "—" : `${money(cost)} ${unit}`, "num"));
  tr.append(h("td", m.sell.hint, "meta"));
  return tr;
}

function drawMedia() {
  const rows = state.data.models.filter((m) => m.kind !== "chat");
  const body = document.querySelector("#media tbody");
  body.replaceChildren();
  for (const m of rows) body.append(mediaRow(m));
  document.getElementById("media-count").textContent = `${rows.length}`;
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
    tr.append(h("td", row.seller, "name"), h("td", money(row.rub_per_1m), "num"));
    mixed.append(tr);
  }

  const questions = document.getElementById("questions");
  questions.replaceChildren();
  for (const q of d.open_questions || []) questions.append(h("li", q));
}

function draw() {
  drawCounts();
  drawChips();
  drawModels();
}

async function load() {
  const note = document.getElementById("note");
  try {
    state.data = await api("/v1/admin/prices");
    drawRate();
    draw();
    drawMedia();
    drawProviders();
    drawRetail();
    drawDrafts();
    document.getElementById("footer").textContent = `снимок прайсов: ${stamp(state.data.generated_at)} · страница ничего не сохраняет`;
    note.textContent = "";
  } catch (e) {
    note.textContent = String(e.message || e);
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
load();
