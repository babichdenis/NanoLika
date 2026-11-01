/* Встраиваемый выбор модели для Лики: та же модалка, но в окне приложения.
   Ключ сессии приходит в #key=..., результат уходит хосту через nlPicker
   (Electron-мост) или postMessage (iframe/веб). */
import { openModelPicker } from '/web/model-picker.js?v=20261101-scope';
import { allModels } from '/web/catalog.js?v=20261101-scope';

const params = new URLSearchParams(location.hash.replace(/^#/, ''));
const key = params.get('key') || '';
const selected = params.get('sel') || '';
const title = params.get('title') || 'Выбор модели';
const kinds = (params.get('kind') || '').split(',').map((kind) => kind.trim()).filter(Boolean);
const status = document.getElementById('status');

function send(payload) {
  try { window.nlPicker?.send(payload); } catch (_) { /* не Electron */ }
  try {
    if (window.parent && window.parent !== window) window.parent.postMessage(payload, '*');
  } catch (_) { /* нет родителя */ }
}

async function api(path, options = {}) {
  const headers = { Authorization: 'Bearer ' + key, ...(options.body ? { 'Content-Type': 'application/json' } : {}) };
  const response = await fetch(path, { ...options, headers, cache: 'no-store' });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.status === 204 ? null : response.json();
}

// the window is transparent around the card: a click on the paper closes it
document.addEventListener('click', (event) => {
  if (!(event.target instanceof Element)) return;
  if (event.target.closest('.mp-embed')) return;
  send({ type: 'close' });
});

async function start() {
  if (!key) {
    status.textContent = 'Нет ключа сессии — откройте выбор модели из приложения.';
    return;
  }
  try {
    const data = await api('/v1/me/models');
    const all = allModels({ data: data.models || [] });
    const favourites = new Set(data.shelf?.favourites || []);
    // The shelf per kind: a picture or a clip picked here is put on its own shelf, so the
    // chat model stays what it was. Known ids only — the relay refuses a model of another kind.
    const enabled = { ...(data.shelf?.enabled || {}) };
    // Весь список, а не только избранное: человек имеет право взять любую модель напрямую.
    // Избранные идут первыми и помечены звездой; поиск и категории остаются.
    const models = [...all].sort((a, b) =>
      Number(favourites.has(b.id)) - Number(favourites.has(a.id)) ||
      Number(Boolean(a.locked)) - Number(Boolean(b.locked)) ||
      Number(b.forChat) - Number(a.forChat));
    openModelPicker({
      models,
      favourites,
      selected,
      title,
      kinds: kinds.length ? kinds : null,
      embedded: true,
      container: document.getElementById('picker'),
      onPick: (id) => send({ type: 'pick', id }),
      // A model of another kind assigns that kind's slot and says so in place; the picker
      // stays open, because the chat model is still to be chosen.
      closeOnOther: false,
      onPickOther: async (id, kind) => {
        if (!id || !kind) return;
        const ids = Array.isArray(enabled[kind]) ? [...enabled[kind]] : [];
        if (!ids.includes(id)) ids.push(id);
        try {
          const saved = await api('/v1/me/models', { method: 'PUT', body: JSON.stringify({ enabled: { [kind]: ids }, default: { [kind]: id } }) });
          if (saved?.shelf?.enabled) Object.assign(enabled, saved.shelf.enabled);
          const label = { image: 'картинок', video: 'видео', audio: 'звука' }[kind] || kind;
          const row = all.find((model) => model.id === id);
          send({ type: 'pick-other', id, kind });
          // The picker shows these words above its list; the chat model is untouched.
          return '«' + (row?.name || id) + '» — теперь модель для ' + label + '.';
        } catch (error) {
          return 'Не удалось назначить модель: ' + error.message;
        }
      },
      onToggleFavourite: async (id, next) => {
        // The star in this page is the same list the picker shows: remember the change
        // before saving, or the server gets the previous list and the star undoes itself.
        const before = new Set(favourites);
        if (next) favourites.add(id); else favourites.delete(id);
        try {
          await api('/v1/me/models', { method: 'PUT', body: JSON.stringify({ enabled: {}, favourites: [...favourites] }) });
          send({ type: 'favourite', id, next });
        } catch (error) {
          favourites.clear();
          before.forEach((kept) => favourites.add(kept));
          status.textContent = 'Избранное не сохранилось: ' + error.message;
        }
      },
      onManage: () => send({ type: 'manage' }),
      onPlan: () => send({ type: 'plan' }),
      onClose: () => send({ type: 'close' }),
      payUrl: 'https://cloud.nanolika.ru/web/account/?pay=1&plan=lika-plus&source=picker',
    });
  } catch (error) {
    status.textContent = 'Не удалось загрузить модели: ' + error.message;
  }
}

start();
