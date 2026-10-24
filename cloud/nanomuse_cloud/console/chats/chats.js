// Чаты кабинета: список, переименование, удаление. Ключ — тот же, что у кабинета.
(function () {
  "use strict";

  var key = window.localStorage.getItem("nm.key") || "";
  var state = { rows: [], query: "" };

  var gate = document.getElementById("gate");
  var tools = document.getElementById("tools");
  var list = document.getElementById("list");
  var empty = document.getElementById("empty");
  var note = document.getElementById("note");
  var search = document.getElementById("search");
  var count = document.getElementById("count");

  function say(text, bad) {
    note.textContent = text || "";
    note.style.color = bad ? "#c0392b" : "";
  }

  function when(ts) {
    if (!ts) return "";
    var d = new Date(ts * 1000);
    return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "long" }) +
      ", " + d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  }

  function api(path, opts) {
    var o = opts || {};
    o.headers = Object.assign({ Authorization: "Bearer " + key }, o.headers || {});
    return fetch(path, o).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        if (!r.ok) throw new Error((body.error && body.error.message) || ("HTTP " + r.status));
        return body;
      });
    });
  }

  function draw() {
    var q = state.query.trim().toLowerCase();
    var rows = state.rows.filter(function (r) {
      return !q || (r.title || "").toLowerCase().indexOf(q) !== -1;
    });
    list.textContent = "";
    count.textContent = rows.length + " из " + state.rows.length;
    empty.hidden = state.rows.length !== 0;

    rows.forEach(function (row) {
      var li = document.createElement("li");
      li.className = "row";

      var info = document.createElement("div");
      var title = document.createElement("div");
      title.className = "title";
      title.textContent = row.title || "Без названия";
      var meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = row.messages + " сообщ. · обновлён " + when(row.updated_at);
      info.appendChild(title);
      info.appendChild(meta);

      var actions = document.createElement("div");
      actions.className = "actions";

      var rename = document.createElement("button");
      rename.type = "button";
      rename.textContent = "Переименовать";
      rename.addEventListener("click", function () { startRename(li, row, title, actions); });

      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger";
      remove.textContent = "Удалить";
      remove.addEventListener("click", function () { removeChat(row); });

      actions.appendChild(rename);
      actions.appendChild(remove);
      li.appendChild(info);
      li.appendChild(actions);
      list.appendChild(li);
    });
  }

  function startRename(li, row, titleEl, actions) {
    var input = document.createElement("input");
    input.className = "rename";
    input.value = row.title || "";
    input.maxLength = 120;
    li.replaceChild(input, titleEl);
    actions.textContent = "";

    var save = document.createElement("button");
    save.type = "button";
    save.textContent = "Сохранить";
    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "Отмена";

    function done() {
      api("/v1/sync/conversations/" + encodeURIComponent(row.cid), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: input.value.trim() })
      }).then(function () {
        row.title = input.value.trim();
        say("Название сохранено");
        draw();
      }).catch(function (e) { say("Не удалось переименовать: " + e.message, true); });
    }

    save.addEventListener("click", done);
    cancel.addEventListener("click", draw);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") done();
      if (e.key === "Escape") draw();
    });
    actions.appendChild(save);
    actions.appendChild(cancel);
    input.focus();
  }

  function removeChat(row) {
    if (!window.confirm("Удалить чат «" + (row.title || "Без названия") + "»? Он исчезнет на всех ваших устройствах.")) return;
    api("/v1/sync/conversations/" + encodeURIComponent(row.cid), { method: "DELETE" })
      .then(function () {
        state.rows = state.rows.filter(function (r) { return r.cid !== row.cid; });
        say("Чат удалён");
        draw();
      })
      .catch(function (e) { say("Не удалось удалить: " + e.message, true); });
  }

  function load() {
    if (!key) {
      gate.hidden = false;
      tools.hidden = true;
      return;
    }
    api("/v1/sync/conversations").then(function (body) {
      state.rows = body.conversations || [];
      gate.hidden = true;
      tools.hidden = false;
      draw();
    }).catch(function (e) {
      say("Не удалось загрузить чаты: " + e.message, true);
    });
  }

  search.addEventListener("input", function () {
    state.query = search.value;
    draw();
  });

  load();
})();
