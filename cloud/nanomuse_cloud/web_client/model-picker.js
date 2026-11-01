/* Единый выбор модели: модальное окно с поиском, категориями и звёздами.
   Один компонент для веб-чата, кабинета и приложения: разметка и стили живут
   здесь, хост передаёт только список моделей и обработчики. */
let styled = false;
function injectStyles() {
  if (styled) return;
  styled = true;
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = new URL('./model-picker.css?v=20261101-tip', import.meta.url).href;
  document.head.append(link);
}

const CATEGORIES = [
  ["chat", "Текст"],
  ["image", "Изображения"],
  ["video", "Видео"],
  ["audio", "Аудио"],
  ["other", "Другое"],
];

/** Цена строкой: у текста две цифры через слэш (вход / выход за 1M), у медиа — одна. */
function shortPrice(model) {
  const num = (price) => price
    ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(price.value)
    : "—";
  if (model.kind === "chat") {
    if (!model.price && !model.priceOut) return "";
    return num(model.price) + " / " + num(model.priceOut);
  }
  return model.price ? num(model.price) + " ₽/" + model.price.unit : "";
}

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
 *                  onPick(id), onPickOther(id, kind), onToggleFavourite(id, next)}) → Promise<id|null>
 * Модалка сверху не влияет на разметку хоста: <dialog> живёт в top layer.
 */
export function openModelPicker({
  models = [],
  favourites = new Set(),
  selected = "",
  title = "Выбор модели",
  searchPlaceholder = "Поиск модели",
  onPick,
  onPickOther,
  onToggleFavourite,
  onManage,
  payUrl = '/web/account/?pay=1&plan=lika-plus&source=models',
  embedded = false,
  container = null,
  kinds = null,
  onPlan,
  onClose,
  // A model of another kind («картинки», «видео») assigns that slot instead of closing the
  // picker: the person sees what happened and can pick the chat model next.
  closeOnOther = true,
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

    // What a pick of another kind did, said where the person is looking: above the list,
    // not under the card where a narrow window clips it.
    const notice = document.createElement("p");
    notice.className = "mp-notice";
    notice.setAttribute("role", "status");
    notice.hidden = true;

    // «Все модели / Избранные» — над поиском, справа. Показываем, только если список
    // действительно шире избранного: в композере веба он и так состоит из избранного.
    const bars = document.createElement("div");
    bars.className = "mp-bars";
    const favCount = models.filter((model) => favourites.has(model.id)).length;
    const hasOthers = favCount < models.length;
    let scope = "all";
    try {
      const kept = localStorage.getItem("nm.picker.scope");
      if (kept === "fav" && favCount > 0) scope = "fav";
    } catch (_) { /* приватное окно молчит */ }
    const scopeButtons = [];
    const paintScope = () => {
      for (const button of scopeButtons) {
        const on = button.dataset.scope === scope;
        button.className = "mp-scope" + (on ? " on" : "");
        button.setAttribute("aria-pressed", on ? "true" : "false");
      }
    };
    if (hasOthers) {
      for (const [value, label] of [["all", "Все модели"], ["fav", "Избранные"]]) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mp-scope";
        button.dataset.scope = value;
        button.textContent = label;
        button.onclick = () => {
          scope = value;
          try { localStorage.setItem("nm.picker.scope", value); } catch (_) { /* тихо */ }
          paintScope();
          render();
        };
        scopeButtons.push(button);
        bars.append(button);
      }
      paintScope();
    }

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
    dialog.append(head, notice, hasOthers ? bars : document.createElement("span"), search, list, manage);
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
      const num = (price) => price
        ? new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(price.value)
        : "—";
      // Цены текста — за миллион токенов, вход / выход, просто цифрами: «2 / 30».
      // У картинок и клипов цена одна, и подпись единицы там нужна.
      const money = (price) => price ? num(price) + " ₽ / " + price.unit : "—";
      add("Цена", model.kind === "chat"
        ? num(model.price) + " / " + num(model.priceOut)
        : money(model.price));
      const rect = row.getBoundingClientRect();
      const dialogRect = dialog.getBoundingClientRect();
      const width = 280;
      // Сбоку от карточки, а не поверх списка: в окне приложения карточка стоит слева и
      // место есть справа, в веб-модалке — слева. Нет места вовсе — подсказка молчит,
      // но строки не закрывает.
      const toRight = dialogRect.right + 12;
      const toLeft = dialogRect.left - width - 12;
      const x = toRight + width <= window.innerWidth - 8
        ? toRight
        : toLeft >= 8 ? toLeft : null;
      if (x === null) {
        tip.hidden = true;
        return;
      }
      tip.hidden = false;
      tip.style.left = x + "px";
      tip.style.top = Math.max(8, Math.min(rect.top, window.innerHeight - 170)) + "px";
    };
    let scrolledToSelected = false;
    const render = () => {
      const query = search.value.trim().toLowerCase();
      const source = scope === "fav" ? models.filter((model) => favourites.has(model.id)) : models;
      list.replaceChildren();
      for (const [kind, label] of CATEGORIES) {
        if (Array.isArray(kinds) && !kinds.includes(kind)) continue;
        const items = source.filter(
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
          // В окне приложения подсказка-visio накрывала бы список: цена стоит в строке.
          const inline = inPage ? shortPrice(model) : "";
          if (inline) {
            const price = document.createElement("em");
            price.className = "mp-price";
            price.textContent = inline;
            pick.append(price);
          }
          // Модель другого вида не отвечает текстом, но её можно назначить для её дела:
          // картинки — рисовать, видео — снимать. Без обработчика строка остаётся серой.
          const other = !model.forChat;
          pick.disabled = Boolean(model.locked) || (other && !onPickOther);
          if (model.locked) pick.title = "Доступно на другом тарифе";
          else if (other) {
            pick.title = onPickOther
              ? "Назначить моделью для «" + label.toLowerCase() + "»"
              : "Только для изображений, видео или аудио";
          }
          pick.onclick = async () => {
            if (model.locked) return;
            if (other) {
              // The handler may say what became of it; the words stay above the list while
              // the panel is open, so taking a picture model never looks like nothing.
              const said = await Promise.resolve(onPickOther?.(model.id, model.kind)).catch(() => undefined);
              notice.hidden = !(typeof said === "string" && said);
              notice.textContent = typeof said === "string" ? said : "";
              if (closeOnOther) finish(model.id);
              return;
            }
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
        empty.textContent = scope === "fav" ? "В избранном пока пусто — отметьте модель звездой." : "Ничего не нашлось";
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
