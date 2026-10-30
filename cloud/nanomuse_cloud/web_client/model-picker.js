/* Единый выбор модели: модальное окно с поиском, категориями и звёздами.
   Один компонент для веб-чата, кабинета и приложения: разметка и стили живут
   здесь, хост передаёт только список моделей и обработчики. */
let styled = false;
function injectStyles() {
  if (styled) return;
  styled = true;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = new URL('./model-picker.css?v=20261030-compact', import.meta.url).href;
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
  embedded = false,
  container = null,
  kinds = null,
  onPlan,
  onClose,
} = {}) {
  injectStyles();
  return new Promise((resolve) => {
    const inPage = Boolean(embedded && container);
    const dialog = document.createElement(inPage ? "div" : "dialog");
    dialog.className = inPage ? "mp-embed" : "mp-dialog";

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
    if (inPage) container.append(dialog); else document.body.append(dialog);
    const tip = document.createElement("div");
    tip.className = "mp-tip";
    tip.hidden = true;
    dialog.append(tip);

    const finish = (value = null) => {
      if (dialog.open) dialog.close();
      if (!inPage) dialog.remove();
      resolve(value);
    };
    const closePicker = () => {
      if (inPage) onClose?.();
      finish(null);
    };

    const contextText = (value) => {
      const n = Number(value) || 0;
      if (!n) return "—";
      if (n >= 1000000) return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(n / 1000000) + "M";
      if (n >= 1000) return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(n / 1000) + "K";
      return new Intl.NumberFormat("ru-RU").format(n);
    };
    const hideTip = () => { tip.hidden = true; };
    const showTip = (model, row) => {
      tip.replaceChildren();
      const title = document.createElement("b");
      title.textContent = model.name;
      const add = (label, value) => {
        const line = document.createElement("span");
        const em = document.createElement("em");
        em.textContent = label + ": ";
        line.append(em, document.createTextNode(value));
        tip.append(line);
      };
      tip.append(title);
      add("Изготовитель", model.vendor || "—");
      add("Контекст", contextText(model.context));
      add("Тип", (CATEGORIES.find(([key]) => key === categoryOf(model)) || ["", "Другое"])[1]);
      const price = model.price
        ? "от " + new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(model.price.value) + " ₽ / " + model.price.unit
        : "—";
      add("Цена", price);
      const rect = row.getBoundingClientRect();
      const dialogRect = dialog.getBoundingClientRect();
      tip.hidden = false;
      const width = 258;
      let left = dialogRect.right + 12;
      if (left + width > window.innerWidth - 8) left = dialogRect.left - width - 12;
      tip.style.left = Math.max(8, left) + "px";
      tip.style.top = Math.max(8, Math.min(rect.top, window.innerHeight - 170)) + "px";
    };
    let scrolledToSelected = false;
    const render = () => {
      const query = search.value.trim().toLowerCase();
      list.replaceChildren();
      for (const [kind, label] of CATEGORIES) {
        if (Array.isArray(kinds) && !kinds.includes(kind)) continue;
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
            plan.onclick = (event) => {
              event.stopPropagation();
              if (onPlan) { event.preventDefault(); onPlan(); }
            };
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
          row.addEventListener("mouseenter", () => showTip(model, row));
          row.addEventListener("mouseleave", hideTip);
          pick.addEventListener("focus", () => showTip(model, row));
          pick.addEventListener("blur", hideTip);
          list.append(row);
        }
      }
      if (!list.children.length) {
        const empty = document.createElement("p");
        empty.className = "mp-empty";
        empty.textContent = models.length ? "Ничего не нашлось" : "Нет избранных моделей";
        list.append(empty);
      }
    };

    search.oninput = render;
    closeButton.onclick = () => closePicker();
    if (!inPage) {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) closePicker();
      });
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        closePicker();
      });
    }
    if (inPage) document.addEventListener("keydown", (event) => { if (event.key === "Escape") closePicker(); });
    render();
    if (selected && !scrolledToSelected) {
      scrolledToSelected = true;
      requestAnimationFrame(() => list.querySelector(".mp-item.on")?.scrollIntoView({ block: "center" }));
    }
    if (!inPage) dialog.showModal();
    search.focus();
  });
}

// Стили нужны и до первого открытия: кнопка-триггер живёт в хосте.
injectStyles();
