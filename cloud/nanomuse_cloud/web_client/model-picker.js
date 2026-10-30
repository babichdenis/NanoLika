/* Единый выбор модели: модальное окно с поиском, категориями и звёздами.
   Один компонент для веб-чата, кабинета и приложения: разметка и стили живут
   здесь, хост передаёт только список моделей и обработчики. */
let styled = false;
function injectStyles() {
  if (styled) return;
  styled = true;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = new URL('./model-picker.css?v=20261030-css', import.meta.url).href;
  document.head.append(link);
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
  payUrl = '/web/account/?pay=1&plan=lika-plus&source=models',
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
          pick.disabled = !model.forChat || Boolean(model.locked);
          if (model.locked) pick.title = "Доступно на другом тарифе";
          else if (!model.forChat) pick.title = "Только для картинок, видео или аудио";
          pick.onclick = () => {
            if (!model.forChat) return;
            selected = model.id;
            onPick?.(model.id);
            finish(model.id);
          };
          if (model.locked) {
            const plan = document.createElement("a");
            plan.className = "mp-plan";
            plan.href = payUrl;
            plan.textContent = "Лика+";
            plan.title = "Открыть тарифы и подключить Лика+";
            plan.onclick = (event) => event.stopPropagation();
            row.append(pick, plan);
          } else {
            row.append(pick);
          }
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
          row.append(star);
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
