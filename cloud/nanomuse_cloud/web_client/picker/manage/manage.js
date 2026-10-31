/* Управление моделями: та же панель, что в вебе (`model-sheet.js`), в окне Лики.
   Страница отдаёт выбор и закрытие хосту через postMessage. */
import { installModelSheet } from '/web/model-sheet.js?v=20261031-pick1';

const params = new URLSearchParams(location.hash.replace(/^#/, ''));
const key = params.get('key') || '';
const kind = params.get('kind') || 'chat';
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
    pickKind: kind === 'all' ? 'any' : kind,
    showBalance: true,
    onPick: (id, pickedKind) => send({ type: 'pick', id, kind: pickedKind }),
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
  // В композере — только избранное: панель открывается на «★ Избранное». Управление из
  // настроек (kind=all и другие) по-прежнему показывает весь каталог: там звёзды и ставят.
  const favouriteTab = () => document.querySelector('.model-kind-btn[data-kind="__fav"]');
  const favouriteCount = () => Number(favouriteTab()?.querySelector('b')?.textContent || 0) || 0;
  const wantedKind = () => (kind === 'chat' && favouriteCount() > 0 ? '__fav' : kind);
  if (kind && kind !== 'all') {
    const applyKind = () => {
      const tab = document.querySelector(`.model-kind-btn[data-kind="${wantedKind()}"]`);
      if (!tab) return false;
      tab.click();
      return true;
    };
    applyKind();
    const observer = new MutationObserver(() => { if (applyKind()) observer.disconnect(); });
    observer.observe(document.body, { childList: true, subtree: true });
  }
}
