// Входящие с сайта: чат на первой странице и форма поддержки.
// Пароль администратора живёт только в этой вкладке, как в остальной админке.
// Страница показывает то, что уже лежит в базе релея: ничего не досылает и не меняет,
// кроме отметки «прочитано».
const memoryStorage = new Map();
const SS = {
  getItem(key) { try { return window.sessionStorage.getItem(key); } catch { return memoryStorage.get(key) || null; } },
  setItem(key, value) { memoryStorage.set(key, value); try { window.sessionStorage.setItem(key, value); } catch {} }
};
let TOKEN = SS.getItem("nm.admin") || SS.getItem("nm.admin.token") || "";

const state = {
  source: "",     // "" — всё, chat | feedback
  q: "",
  messages: [],
  counts: {},
  before: null,
  loading: false
};

const PAGE = 100;

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const note = document.getElementById("note");

function when(ts) {
  if (!ts) return "";
  const date = new Date(Number(ts) * 1000);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getDate())}.${pad(date.getMonth() + 1)}.${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

async function api(path, options = {}) {
  const r = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { "X-Admin-Token": TOKEN, "Content-Type": "application/json" }
  });
  if (r.status === 401) throw new Error("Пароль не подошёл. Введите пароль администратора целиком, не код из письма.");
  if (!r.ok) throw new Error(`ответ ${r.status}`);
  return r.json();
}

function showGate() {
  document.getElementById("gate").hidden = false;
  document.getElementById("content").hidden = true;
  document.getElementById("gate-token").focus();
}

function drawCounts() {
  const box = document.getElementById("counts");
  box.replaceChildren();
  const chat = state.counts.chat || { total: 0, unread: 0 };
  const feedback = state.counts.feedback || { total: 0, unread: 0 };
  const part = (label, figures) => {
    box.append(el("span", "", `${label}: ${figures.total}`));
    if (figures.unread) {
      box.append(el("span", "", " · "));
      box.append(el("b", "", `не прочитано ${figures.unread}`));
    }
  };
  part("Вопросов в чате", chat);
  box.append(el("span", "", " · "));
  part("Сообщений из формы", feedback);
}

function drawSources() {
  const box = document.getElementById("sources");
  box.replaceChildren();
  const options = [
    ["", "Всё"],
    ["chat", "Чат на сайте"],
    ["feedback", "Форма поддержки"]
  ];
  for (const [value, label] of options) {
    const button = el("button", "chip", label);
    button.type = "button";
    button.setAttribute("aria-pressed", state.source === value ? "true" : "false");
    button.onclick = () => {
      state.source = value;
      state.before = null;
      void load(true);
    };
    box.append(button);
  }
}

function messageCard(row) {
  const card = el("article", `msg${row.read_at ? "" : " unread"}`);
  card.dataset.id = String(row.id);

  const head = el("div", "msg-head");
  head.append(el("span", "when", when(row.at)));
  const kind = row.source === "feedback" ? "feedback" : "chat";
  head.append(el("span", `badge ${kind}`, kind === "feedback" ? "форма поддержки" : "чат на сайте"));
  if (!row.read_at) head.append(el("span", "badge unread", "новое"));
  if (row.page) head.append(el("span", "where", `страница: ${row.page}`));
  card.append(head);

  const whoLine = [];
  if (row.topic) whoLine.push(`Тема: ${row.topic}`);
  if (row.email) whoLine.push(row.email);
  if (row.ip) whoLine.push(`IP ${row.ip}`);
  if (whoLine.length) card.append(el("div", "who", whoLine.join(" · ")));

  if (row.topic) card.append(el("div", "topic", row.topic));
  card.append(el("p", "text", row.body || ""));

  if (row.reply) {
    const answer = el("div", "answer");
    answer.append(el("span", "answer-label", "Ответ Лики"));
    answer.append(document.createTextNode(row.reply));
    card.append(answer);
  }

  const foot = el("div", "msg-foot");
  if (row.session) foot.append(el("span", "meta", `сессия ${row.session}`));
  if (row.code) foot.append(el("span", "meta", row.code));
  foot.append(el("span", "meta", `id ${row.id}`));
  if (!row.read_at) {
    const mark = el("button", "chip", "Прочитано");
    mark.type = "button";
    mark.onclick = async () => {
      try {
        await api("/v1/admin/inbox/read", { method: "POST", body: JSON.stringify({ ids: [row.id] }) });
        row.read_at = Math.floor(Date.now() / 1000);
        card.classList.remove("unread");
        card.querySelector(".badge.unread")?.remove();
        mark.remove();
        await load(false, true);
      } catch (error) {
        note.textContent = String(error.message || error);
      }
    };
    foot.append(mark);
  }
  card.append(foot);
  return card;
}

function draw() {
  const list = document.getElementById("list");
  // перерисовываем только когда список изменился: кнопка «прочитано» не должна прыгать
  list.replaceChildren();
  if (!state.messages.length) {
    list.append(el("p", "empty", state.q || state.source
      ? "По этому запросу ничего нет."
      : "Пока никто ничего не написал."));
  } else {
    for (const row of state.messages) list.append(messageCard(row));
  }
  document.getElementById("more").hidden = state.messages.length < PAGE;
  drawCounts();
  drawSources();
}

async function load(reset = false, quiet = false) {
  if (state.loading) return;
  state.loading = true;
  if (!quiet) note.textContent = "";
  try {
    if (reset) state.before = null;
    const params = new URLSearchParams();
    if (state.source) params.set("source", state.source);
    if (state.q.trim()) params.set("q", state.q.trim());
    if (state.before) params.set("before", String(state.before));
    params.set("limit", String(PAGE));
    const body = await api(`/v1/admin/inbox?${params.toString()}`);
    state.counts = body.counts || {};
    const rows = body.messages || [];
    state.messages = reset || !state.before ? rows : [...state.messages, ...rows];
    if (rows.length) state.before = Number(rows[rows.length - 1].id);
    document.getElementById("gate").hidden = true;
    document.getElementById("content").hidden = false;
    draw();
    document.getElementById("footer").textContent =
      "Собирается на сервере nanoLika. Чат присылает копию после каждого ответа, форма — сразу при отправке. " +
      "Удаление и выгрузка здесь не делаются нарочно: это журнал обращений.";
  } catch (error) {
    note.textContent = String(error.message || error);
    if (!state.messages.length) showGate();
  } finally {
    state.loading = false;
  }
}

document.getElementById("gate-form").onsubmit = (event) => {
  event.preventDefault();
  const field = document.getElementById("gate-token");
  const value = field.value.trim();
  if (!value) return;
  TOKEN = value;
  SS.setItem("nm.admin", value);
  field.value = "";
  void load(true);
};

document.getElementById("refresh").onclick = () => void load(true);
document.getElementById("read-all").onclick = async () => {
  try {
    await api("/v1/admin/inbox/read", { method: "POST", body: "{}" });
    await load(true);
  } catch (error) {
    note.textContent = String(error.message || error);
  }
};
document.getElementById("more").onclick = () => void load(false);

let searchTimer;
document.getElementById("q").oninput = (event) => {
  state.q = event.target.value;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => void load(true), 350);
};

if (!TOKEN) showGate();
else void load(true);
