/* Управление моделями: та же панель, что в вебе (`model-sheet.js`), в окне Лики.
   Страница отдаёт выбор и закрытие хосту через postMessage. */
import { installModelSheet } from '/web/model-sheet.js?v=20261030-ot';

const params = new URLSearchParams(location.hash.replace(/^#/, ''));
const key = params.get('key') || '';
const session = () => ({ owner: 'picker', key });

function send(payload) {
  try {
    if (window.parent && window.parent !== window) window.parent.postMessage(payload, '*');
  } catch (_) { /* нет родителя */ }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      Authorization: 'Bearer ' + key,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    cache: 'no-store',
  });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.status === 204 ? null : response.json();
}

if (!key) {
  document.body.textContent = 'Нет ключа сессии — откройте управление моделями из приложения.';
} else {
  const sheet = installModelSheet({
    api,
    session,
    onPick: (id) => send({ type: 'pick', id }),
    onFavourites: () => send({ type: 'favourite' }),
  });
  const panel = document.querySelector('.model-sheet');
  let opened = false;
  if (panel) {
    new MutationObserver(() => {
      if (opened && panel.dataset.open === '0') send({ type: 'close' });
    }).observe(panel, { attributes: true, attributeFilter: ['data-open'] });
  }
  // The tariff links inside the card belong in the browser, not in this iframe.
  document.addEventListener('click', (event) => {
    const target = event.target instanceof Element ? event.target.closest('a[href*="/web/account/"]') : null;
    if (!target) return;
    event.preventDefault();
    send({ type: 'plan', url: target.href });
  });
  sheet.open();
  opened = true;
}
