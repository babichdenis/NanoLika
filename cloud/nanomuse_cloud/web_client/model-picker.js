/* Единый выбор модели: модальное окно с поиском, категориями и звёздами.
   Один компонент для веб-чата, кабинета и приложения: разметка и стили живут
   здесь, хост передаёт только список моделей и обработчики. */
let styled = false;
function injectStyles() {
  if (styled) return;
  styled = true;
  const style = document.createElement('style');
  style.textContent = `
.mp-wrap{position:relative;display:flex;align-items:center;min-width:0}
.mp-wrap .mp-trigger{appearance:none;border:1px solid var(--line,#d8d1c1);background:var(--surface,#fffdf8);color:var(--ink,#38372f);border-radius:8px;padding:7px 30px 7px 10px;font:inherit;font-size:13px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;cursor:pointer;text-align:left;position:relative}
.mp-trigger::after{content:"▾";position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:11px;opacity:.6}
.mp-dialog{position:fixed;inset:auto 0 0 0;margin:0 auto;width:min(560px,100vw);max-height:min(72vh,560px);overflow:auto;background:var(--surface,#fffdf8);color:var(--ink,#38372f);border:0;border-top:1px solid var(--line,#d8d1c1);border-radius:16px 16px 0 0;box-shadow:0 -18px 40px rgba(40,35,20,.18);padding:12px 12px calc(12px + env(safe-area-inset-bottom))}
.mp-dialog::backdrop{background:rgba(40,35,20,.38)}
.mp-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:2px 4px 10px}
.mp-head strong{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted,#66675b)}
.mp-dialog .mp-close{border:0;background:none;color:var(--muted,#66675b);font-size:18px;line-height:1;padding:6px;cursor:pointer;border-radius:7px}
.mp-dialog .mp-close:hover{background:var(--fill,#e7eadc)}
.mp-dialog .mp-search{width:100%;box-sizing:border-box;margin:0 0 6px;padding:9px 11px;border:1px solid var(--line,#d8d1c1);border-radius:9px;background:var(--paper,#f7f1e6);color:var(--ink,#38372f);font:inherit;font-size:14px}
.mp-list{display:block}
.mp-row{display:flex;align-items:center;gap:2px;width:100%}
.mp-dialog .mp-item{display:flex;align-items:center;flex:1 1 auto;min-width:0;text-align:left;border:0;background:none;color:var(--ink,#38372f);border-radius:7px;padding:8px 9px;font:inherit;font-size:14px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mp-dialog .mp-item:hover{background:var(--fill,#e7eadc)}
.mp-dialog .mp-item.on{background:var(--fill,#e7eadc);font-weight:600}
.mp-dialog .mp-item:disabled{color:var(--muted,#66675b);opacity:.55;cursor:default}
.mp-dialog .mp-star{flex:0 0 32px;width:32px;height:32px;display:grid;place-items:center;border:0;background:none;color:var(--action,#52694e);border-radius:7px;font-size:16px;line-height:1;cursor:pointer}
.mp-dialog .mp-star:hover{background:var(--fill,#e7eadc)}
.mp-dialog .mp-cat{margin:12px 10px 4px;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted,#66675b)}
.mp-dialog .mp-empty{margin:0 10px 6px;font-size:12px;color:var(--muted,#66675b)}
.mp-dialog .mp-manage{display:block;width:100%;margin-top:10px;padding:12px 10px 4px;border:0;border-top:1px solid var(--line,#d8d1c1);background:none;color:var(--action,#52694e);font:600 11px/1.4 inherit;letter-spacing:.12em;text-transform:uppercase;text-align:left;cursor:pointer}
.mp-dialog .mp-manage:hover{color:var(--ink,#38372f)}
@media (min-width:720px){.mp-dialog{bottom:24px;border-radius:16px;border:1px solid var(--line,#d8d1c1)}}
`;
  document.head.append(style);
}

const CATEGORIES = [
  ["chat", "Текст"],
  ["image", "Картинки"],
  ["video", "Видео"],
  ["audio", "Аудио"],
  ["other", "Другое"],
];

function categoryOf(model) {
  const kind = String(model.kind || "");
  if (kind === "chat") return "chat";
  if (kind === "image") return "image";
  if (kind === "video") return "video";
  if (kind === "audio") return "audio";
  return "other";
}

/**
 * openModelPicker({models, favourites, selected, title, searchPlaceholder,
 *                  onPick(id), onToggleFavourite(id, next)}) → Promise<id|null>
 * Модалка сверху не влияет на разметку хоста: <dialog> живёт в top layer.
 */
export function openModelPicker({
  models = [],
  favourites = new Set(),
  selected = "",
  title = "Выбор модели",
  searchPlaceholder = "Поиск модели",
  onPick,
  onToggleFavourite,
  onManage,
} = {}) {
  injectStyles();
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "mp-dialog";

    const head = document.createElement("div");
    head.className = "mp-head";
    const heading = document.createElement("strong");
    heading.textContent = title;
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "mp-close";
    closeButton.setAttribute("aria-label", "Закрыть");
    closeButton.textContent = "✕";
    head.append(heading, closeButton);

    const search = document.createElement("input");
    search.type = "search";
    search.className = "mp-search";
    search.placeholder = searchPlaceholder;
    search.setAttribute("aria-label", searchPlaceholder);

    const list = document.createElement("div");
    list.className = "mp-list";
    const manage = document.createElement("button");
    manage.type = "button";
    manage.className = "mp-manage";
    manage.textContent = "Управление моделями";
    manage.onclick = () => { finish(null); onManage?.(); };
    dialog.append(head, search, list, manage);
    document.body.append(dialog);

    const finish = (value = null) => {
      if (dialog.open) dialog.close();
      dialog.remove();
      resolve(value);
    };

    const render = () => {
      const query = search.value.trim().toLowerCase();
      list.replaceChildren();
      for (const [kind, label] of CATEGORIES) {
        const items = models.filter(
          (model) =>
            categoryOf(model) === kind &&
            (!query || model.name.toLowerCase().includes(query) || model.id.toLowerCase().includes(query)),
        );
        if (!items.length) continue;
        const caption = document.createElement("p");
        caption.className = "mp-cat";
        caption.textContent = label;
        list.append(caption);
        for (const model of items) {
          const row = document.createElement("div");
          row.className = "mp-row";
          const pick = document.createElement("button");
          pick.type = "button";
          pick.className = "mp-item" + (model.id === selected ? " on" : "");
          pick.textContent = model.name;
          pick.disabled = !model.forChat;
          if (!model.forChat) pick.title = "Только для картинок, видео или аудио";
          pick.onclick = () => {
            if (!model.forChat) return;
            selected = model.id;
            onPick?.(model.id);
            finish(model.id);
          };
          const star = document.createElement("button");
          star.type = "button";
          star.className = "mp-star";
          star.textContent = favourites.has(model.id) ? "★" : "☆";
          star.title = favourites.has(model.id) ? "Убрать из избранного" : "В избранное";
          star.setAttribute("aria-label", `${star.title}: ${model.name}`);
          star.onclick = (event) => {
            event.stopPropagation();
            const next = !favourites.has(model.id);
            if (next) favourites.add(model.id);
            else favourites.delete(model.id);
            onToggleFavourite?.(model.id, next);
            render();
          };
          row.append(pick, star);
          list.append(row);
        }
      }
    };

    search.oninput = render;
    closeButton.onclick = () => finish(null);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) finish(null);
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      finish(null);
    });
    render();
    dialog.showModal();
    search.focus();
  });
}

// Стили нужны и до первого открытия: кнопка-триггер живёт в хосте.
injectStyles();
