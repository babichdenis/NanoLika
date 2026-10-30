/* Встраиваемый выбор модели для Лики: та же модалка, но в окне приложения.
   Ключ сессии приходит в #key=..., результат уходит хосту через nlPicker
   (Electron-мост) или postMessage (iframe/веб). */
import { openModelPicker } from '/web/model-picker.js?v=20261030-mtok';
import { allModels } from '/web/catalog.js?v=20261030-mtok';

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
    const models = all.filter((model) => favourites.has(model.id));
    openModelPicker({
      models,
      favourites,
      selected,
      title,
      kinds: kinds.length ? kinds : null,
      embedded: true,
      container: document.getElementById('picker'),
      onPick: (id) => send({ type: 'pick', id }),
      onToggleFavourite: async (id, next) => {
        try {
          await api('/v1/me/models', { method: 'PUT', body: JSON.stringify({ enabled: {}, favourites: [...favourites] }) });
          send({ type: 'favourite', id, next });
        } catch (error) {
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
