(() => {
  'use strict';
  const fragment = new URLSearchParams(location.hash.slice(1));
  const rawLaunch = fragment.get('tgWebAppData') || '';
  const destination = new URLSearchParams(location.search).get('next') === 'account' ? '/web/account/?source=telegram' : '/web/?channel=telegram';
  history.replaceState(null, '', location.pathname);
  const status = document.getElementById('status');
  const retry = document.getElementById('retry');
  const login = document.getElementById('login');
  login.href = '/app/?signin=1&next=' + encodeURIComponent(new URL(destination, location.origin).href);
  const sdkReady = new Promise(resolve => {
    if (window.Telegram?.WebApp) { resolve(); return; }
    const sdk = document.createElement('script');
    sdk.src = 'https://telegram.org/js/telegram-web-app.js';
    const timeout = setTimeout(resolve, 6000);
    sdk.onload = sdk.onerror = () => { clearTimeout(timeout); resolve(); };
    document.head.append(sdk);
  });
  let running = false;
  async function signIn() {
    if (running) return;
    running = true;
    retry.hidden = true; login.hidden = true;
    try {
      if (!rawLaunch && !window.Telegram?.WebApp?.initData) await sdkReady;
      window.Telegram?.WebApp?.ready();
      window.Telegram?.WebApp?.expand();
      const initData = rawLaunch || window.Telegram?.WebApp?.initData || '';
      if (!initData) { status.textContent = 'Откройте приложение кнопкой в Telegram или войдите в аккаунт.'; login.hidden = false; return; }
      status.textContent = 'Входим в ваш аккаунт Лики…';
      const response = await fetch('/v1/auth/telegram-webapp', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: initData})});
      if (!response.ok) {
        status.textContent = response.status === 401 ? 'Данные запуска устарели. Закройте и заново откройте приложение в Telegram.' : response.status === 403 ? 'Вход в этот аккаунт сейчас недоступен. Откройте страницу входа или обратитесь в поддержку.' : 'Не удалось войти. Повторите попытку или откройте страницу входа.';
        retry.hidden = response.status === 401 || response.status === 403; login.hidden = false; return;
      }
      const data = await response.json();
      if (!data.api_key || !data.account?.id) throw new Error('Invalid session');
      const session = {key: data.api_key, owner: data.account.id, hint: data.account.hint || ''};
      // The lite client still requires its bearer session in local storage.
      try {
        localStorage.setItem('nl.web.session.v1', JSON.stringify(session));
        localStorage.setItem('nm.key', session.key);
        localStorage.setItem('nm.acct', session.owner);
        localStorage.setItem('nm.hint', session.hint);
        localStorage.setItem('nl.web.channel', 'telegram');
      } catch {
        status.textContent = 'Вход подтверждён, но Telegram не разрешил сохранить его. Разрешите хранение данных и повторите попытку.';
        retry.hidden = false; login.hidden = false; return;
      }
      location.replace(destination);
    } catch { status.textContent = 'Не удалось связаться с сервером. Повторите попытку или откройте страницу входа.'; retry.hidden = false; login.hidden = false; }
    finally { running = false; }
  }
  retry.onclick = signIn;
  signIn();
})();
