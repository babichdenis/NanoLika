(() => {
  'use strict';
  // MAX passes the launch parameters in the fragment (`#WebAppData=…&WebAppPlatform=…`) and,
  // once its bridge is up, in `window.WebApp.initData`. Both are the same URL-encoded string;
  // the relay checks the HMAC with the bot token before it issues a key.
  const fragment = new URLSearchParams(location.hash.slice(1));
  const initData = fragment.get('WebAppData') || window.WebApp?.initData || '';
  history.replaceState(null, '', location.pathname);
  const status = document.getElementById('status');
  const retry = document.getElementById('retry');
  const login = document.getElementById('login');
  // The MAX client injects `window.WebApp` itself; the fragment is enough to sign in, so
  // the page loads no script from the platform at all.
  try { window.WebApp?.ready?.(); } catch {}
  async function signIn() {
    retry.hidden = true; login.hidden = true;
    if (!initData) { status.textContent = 'Откройте приложение из MAX или войдите по почте.'; login.hidden = false; return; }
    status.textContent = 'Проверяем подключение вашего аккаунта…';
    try {
      const response = await fetch('/v1/auth/max-webapp', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({init_data: initData})});
      if (!response.ok) {
        status.textContent = response.status === 409 ? 'Этот MAX ещё не привязан. Войдите по почте и подключите MAX в разделе «Устройства и подключения».' : response.status === 401 ? 'Данные запуска устарели или не прошли проверку. Закройте и заново откройте приложение в MAX.' : 'Не удалось войти. Попробуйте ещё раз или войдите по почте.';
        retry.hidden = response.status === 401 || response.status === 409; login.hidden = false; return;
      }
      const data = await response.json();
      if (!data.api_key || !data.account?.id) throw new Error('Invalid session');
      const session = {key: data.api_key, owner: data.account.id, hint: data.account.hint || ''};
      localStorage.setItem('nl.web.session.v1', JSON.stringify(session));
      localStorage.setItem('nm.key', session.key);
      localStorage.setItem('nm.acct', session.owner);
      localStorage.setItem('nm.hint', session.hint);
      try { localStorage.setItem('nl.web.channel', 'max'); } catch {}
      location.replace('/web/?channel=max');
    } catch { status.textContent = 'Не удалось сохранить вход или связаться с сервером. Разрешите хранение данных и попробуйте ещё раз.'; retry.hidden = false; login.hidden = false; }
  }
  retry.onclick = signIn;
  signIn();
})();
