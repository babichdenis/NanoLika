(() => {
  'use strict';
  const fragment = new URLSearchParams(location.hash.slice(1));
  const initData = fragment.get('tgWebAppData') || window.Telegram?.WebApp?.initData || '';
  history.replaceState(null, '', location.pathname);
  const status = document.getElementById('status');
  const retry = document.getElementById('retry');
  const login = document.getElementById('login');
  const sdk = document.createElement('script');
  sdk.src = 'https://telegram.org/js/telegram-web-app.js';
  sdk.onload = () => { window.Telegram?.WebApp?.ready(); window.Telegram?.WebApp?.expand(); };
  document.head.append(sdk);
  async function signIn() {
    retry.hidden = true; login.hidden = true;
    if (!initData) { status.textContent = 'Откройте веб-приложение из Telegram или войдите по почте.'; login.hidden = false; return; }
    status.textContent = 'Проверяем подключение вашего аккаунта…';
    try {
      const response = await fetch('/v1/auth/telegram-webapp', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: initData})});
      if (!response.ok) {
        status.textContent = response.status === 409 ? 'Этот Telegram ещё не привязан. Войдите по почте и подключите Telegram в разделе «Устройства и подключения».' : response.status === 401 ? 'Данные запуска устарели или не прошли проверку. Закройте и заново откройте веб-приложение в Telegram.' : 'Не удалось войти. Попробуйте ещё раз или войдите по почте.';
        retry.hidden = response.status === 401 || response.status === 409; login.hidden = false; return;
      }
      const data = await response.json();
      if (!data.api_key || !data.account?.id) throw new Error('Invalid session');
      const session = {key: data.api_key, owner: data.account.id, hint: data.account.hint || ''};
      localStorage.setItem('nl.web.session.v1', JSON.stringify(session));
      localStorage.setItem('nm.key', session.key);
      localStorage.setItem('nm.acct', session.owner);
      localStorage.setItem('nm.hint', session.hint);
      location.replace('/web/');
    } catch { status.textContent = 'Не удалось сохранить вход или связаться с сервером. Разрешите хранение данных и попробуйте ещё раз.'; retry.hidden = false; login.hidden = false; }
  }
  retry.onclick = signIn;
  signIn();
})();