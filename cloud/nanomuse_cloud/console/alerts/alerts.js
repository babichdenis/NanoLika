/* Operator alerts: model/provider health and the latest upstream incidents.
 * The admin token is the same one /app/admin/ keeps in sessionStorage. */
const SS = window.sessionStorage;
let token = SS.getItem("nm.admin") || "";
let days = Number(SS.getItem("nm.admin.days") || 1);
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
const fmt = (n) => new Intl.NumberFormat("ru-RU").format(Number(n) || 0);

function drawAuth() {
  $("auth").hidden = Boolean(token);
  $("periods").hidden = !token;
  $("notify-row").hidden = !token;
}

function drawPeriods() {
  $("periods").innerHTML = [1, 7, 30]
    .map((d) => `<button type="button" data-d="${d}" class="${d === days ? "on" : ""}">${d} дн.</button>`)
    .join("");
  $("periods").querySelectorAll("button").forEach((button) => {
    button.onclick = () => {
      days = Number(button.dataset.d);
      SS.setItem("nm.admin.days", String(days));
      drawPeriods();
      load();
    };
  });
}

function table(rows, columns) {
  if (!rows.length) return '<p class="muted">Пока нет данных.</p>';
  return (
    "<table><thead><tr>" +
    columns.map((c) => `<th>${c.title}</th>`).join("") +
    "</tr></thead><tbody>" +
    rows.map((row) => "<tr>" + columns.map((c) => `<td>${c.cell(row)}</td>`).join("") + "</tr>").join("") +
    "</tbody></table>"
  );
}

function render(data) {
  $("totals").textContent =
    `Запросов: ${fmt(data.totals.requests)} · fallback: ${fmt(data.totals.fallback)} · ` +
    `BYOK: ${fmt(data.totals.byok)} · инцидентов: ${fmt(data.totals.incidents)}`;

  const models = table(data.by_model, [
    { title: "Модель", cell: (r) => esc(r.model) },
    { title: "Запросы", cell: (r) => fmt(r.requests) },
    { title: "Fallback", cell: (r) => (r.fallback ? `<span class="bad">${fmt(r.fallback)}</span>` : "0") },
    { title: "BYOK", cell: (r) => fmt(r.byok) },
    {
      title: "Провайдеры",
      cell: (r) => esc(Object.entries(r.providers).map(([name, count]) => `${name}: ${count}`).join(", ")),
    },
  ]);

  const providers = table(data.by_provider, [
    { title: "Провайдер", cell: (r) => esc(r.provider) },
    { title: "Запросы", cell: (r) => fmt(r.requests) },
    { title: "Fallback", cell: (r) => (r.fallback ? `<span class="bad">${fmt(r.fallback)}</span>` : "0") },
    { title: "BYOK", cell: (r) => fmt(r.byok) },
  ]);

  const errors = table(data.error_groups || [], [
    { title: "Тип", cell: (r) => esc(r.kind) },
    { title: "Раз", cell: (r) => fmt(r.count) },
    { title: "Последняя", cell: (r) => new Date(r.last_at * 1000).toLocaleString("ru-RU") },
    { title: "Сообщение", cell: (r) => esc(r.detail) },
  ]);

  const incidents = data.incidents.length
    ? `<table><thead><tr><th>Когда</th><th>Тип</th><th>Детали</th></tr></thead><tbody>${data.incidents
        .map(
          (i) =>
            `<tr><td>${new Date(i.at * 1000).toLocaleString("ru-RU")}</td><td>${esc(i.kind)}</td><td>${esc(i.detail)}</td></tr>`,
        )
        .join("")}</tbody></table>`
    : '<p class="muted">Инцидентов нет.</p>';

  $("content").innerHTML =
    `<h2>Модели</h2>${models}<h2>Провайдеры</h2>${providers}<h2>Ошибки провайдеров</h2>${errors}<h2>Последние инциденты</h2>${incidents}`;
}

async function load() {
  drawAuth();
  if (!token) return;
  $("status").textContent = "Загрузка…";
  try {
    const response = await fetch(`/v1/admin/alerts?days=${days}`, {
      headers: { "X-Admin-Token": token },
      cache: "no-store",
    });
    if (response.status === 401) {
      token = "";
      SS.removeItem("nm.admin");
      drawAuth();
      $("status").textContent = "Токен неверный";
      return;
    }
    if (!response.ok) throw new Error(String(response.status));
    render(await response.json());
    $("status").textContent = `обновлено ${new Date().toLocaleTimeString("ru-RU")}`;
  } catch (error) {
    $("status").textContent = "Ошибка: " + error.message;
  }
}

$("save").onclick = () => {
  token = $("token").value.trim();
  if (!token) return;
  SS.setItem("nm.admin", token);
  load();
};
$("token").addEventListener("keydown", (event) => {
  if (event.key === "Enter") $("save").click();
});
$("notify").onclick = async () => {
  const button = $("notify");
  button.disabled = true;
  $("notify-status").textContent = "Отправляем…";
  try {
    const response = await fetch(`/v1/admin/alerts/notify?days=${days}`, {
      method: "POST",
      headers: { "X-Admin-Token": token },
      cache: "no-store",
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data?.error?.message || String(response.status));
    $("notify-status").textContent = `Отправлено на ${data.to}`;
  } catch (error) {
    $("notify-status").textContent = "Не ушло: " + error.message;
  } finally {
    button.disabled = false;
  }
};

drawAuth();
drawPeriods();
load();
setInterval(() => {
  if (token && !document.hidden) load();
}, 30000);
