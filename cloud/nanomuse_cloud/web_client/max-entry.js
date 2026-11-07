(() => {
  'use strict';
  const fragment = new URLSearchParams(location.hash.slice(1));
  const rawLaunch = fragment.getAll('WebAppData').length === 1 ? fragment.get('WebAppData') : '';
  let accountDestination = false;
  const destination = new URLSearchParams(location.search).get('next') === 'account' ? '/web/account/?source=max' : '/web/?channel=max';
  history.replaceState(null, '', location.pathname);
  const status = document.getElementById('status');
  const retry = document.getElementById('retry');
  const login = document.getElementById('login');
  const sdkReady = new Promise(resolve => {
    if (window.WebApp) { resolve(); return; }
    const sdk = document.createElement('script');
    sdk.src = 'https://st.max.ru/js/max-web-app.js';
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
      if (!rawLaunch && !window.WebApp?.initData) await sdkReady;
      const initData = rawLaunch || window.WebApp?.initData || '';
      accountDestination = new URLSearchParams(initData).get('start_param') === 'account';
      if (!initData) { status.textContent = 'Откройте приложение кнопкой в MAX или войдите в аккаунт.'; login.hidden = false; return; }
      status.textContent = 'Входим в ваш аккаунт Лики…';
      const response = await fetch('/v1/auth/max-webapp', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: initData})});
      if (!response.ok) {
        status.textContent = response.status === 409 ? 'Этот MAX ещё не привязан. Войдите в аккаунт и подключите MAX в разделе «Устройства и подключения».' : response.status === 401 ? 'Данные запуска устарели. Закройте и заново откройте приложение в MAX.' : response.status === 403 ? 'Вход в этот аккаунт сейчас недоступен. Откройте страницу входа или обратитесь в поддержку.' : 'Не удалось войти. Повторите попытку или откройте страницу входа.';
        retry.hidden = response.status === 401 || response.status === 403 || response.status === 409; login.hidden = false; return;
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
        localStorage.setItem('nl.web.channel', 'max');
      } catch {
        status.textContent = 'Вход подтверждён, но MAX не разрешил сохранить его. Разрешите хранение данных и повторите попытку.';
        retry.hidden = false; login.hidden = false; return;
      }
      location.replace(accountDestination ? '/web/account/?source=max' : destination);
    } catch { status.textContent = 'Не удалось связаться с сервером. Повторите попытку или откройте страницу входа.'; retry.hidden = false; login.hidden = false; }
    finally { running = false; }
  }
  retry.onclick = signIn;
  signIn();
})();
