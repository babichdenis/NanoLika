/* No terminal password, card details or account key in URLs. Same-origin checkout. */
(() => {
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const format = value => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB'}).format(value / 100);
  let key = '', sessionOwner = '', config = null, order = params.get('order') || params.get('Shp_order') || '';
  let idempotency = crypto.randomUUID().replaceAll('-', '');
  let busy = false, redirecting = false;
  try {
    const webSession = JSON.parse(localStorage.getItem('nl.web.session.v1') || 'null');
    if (webSession?.key) { key = webSession.key; sessionOwner = webSession.owner || ''; }
    if (!key) key = localStorage.getItem('nm.key') || '';
  } catch (_) {}
  const message = text => { $('message').textContent = text; };
  function clearAuth() {
    const expired = key;
    key = ''; sessionOwner = '';
    try {
      const webSession = JSON.parse(localStorage.getItem('nl.web.session.v1') || 'null');
      if (webSession?.key === expired) localStorage.removeItem('nl.web.session.v1');
      if (localStorage.getItem('nm.key') === expired) localStorage.removeItem('nm.key');
    } catch (_) {}
  }
  function signIn() {
    redirecting = true;
    $('checkout').hidden = true;
    $('purchase').hidden = true;
    location.replace('/app/?signin=1&next=' + encodeURIComponent(location.href));
  }
  async function loadConfig() {
    config = await api('/v1/payments/config');
    $('provider-label').textContent = `nanoLika · ${config.provider_name}${config.test_mode ? ' · тестовый режим' : ''}`;
    $('payment-notice').textContent = config.test_mode
      ? 'Тестовое оформление. Тестовый баланс и условия не меняют рабочий аккаунт.'
      : 'Оплата проходит через защищённую форму платёжного сервиса. Статус подтверждается сервером.';
    $('pay').textContent = `Перейти в ${config.provider_name}${config.test_mode ? ' · тест' : ''}`;
  }
  async function api(path, body) {
    const r = await fetch(path, {method:body ? 'POST' : 'GET', cache:'no-store', headers:{...(body ? {'Content-Type':'application/json'} : {}), ...(key ? {Authorization:'Bearer ' + key} : {})}, ...(body ? {body:JSON.stringify(body)} : {})});
    const data = r.status === 204 ? {} : await r.json();
    if (!r.ok) {
      if (r.status === 401) { clearAuth(); signIn(); }
      throw Error((data.error?.code === 'signup_closed' ? 'Новые регистрации временно закрыты. Существующие аккаунты продолжают работать.' : data.error?.message) || 'Не удалось выполнить запрос. Попробуйте позднее.');
    }
    return data;
  }
  async function check() {
    const data = await api('/v1/payments/orders/' + encodeURIComponent(order));
    $('item').textContent = data.plan ? 'Пакет «' + config.plans[data.plan].name + '»' : 'Пополнение баланса';
    $('amount').textContent = format(data.amount_kopecks);
    $('pay').hidden = true; $('refresh').hidden = false;
    if (data.status === 'REFUNDED' || data.status === 'PARTIAL_REFUNDED') {
      const wallet = await api('/v1/payments/wallet');
      message(data.status === 'REFUNDED'
        ? 'Возвращен полностью · тест. Банк подтвердил возврат всей суммы. Зачисление на тестовый баланс отменено.'
        : 'Возвращена часть платежа: ' + format(data.refunded_amount_kopecks) + '. Остаток зачисления: ' + format(data.net_credited_amount_kopecks) + '.');
      $('wallet').textContent = 'Тестовый баланс: ' + format(wallet.balance_kopecks);
      $('refresh').hidden = data.status === 'REFUNDED';
    } else if (data.credited) {
      const wallet = await api('/v1/payments/wallet');
      message('Оплачено · тест. Банк подтвердил тестовый платёж. Тестовое зачисление выполнено один раз. Реальный баланс не изменён.');
      $('wallet').textContent = 'Тестовый баланс: ' + format(wallet.balance_kopecks) + (wallet.expires_at ? '. Тестовые условия до ' + new Date(wallet.expires_at * 1000).toLocaleString('ru-RU') : '');
      $('refresh').hidden = true;
    } else if (['REJECTED', 'AUTH_FAIL', 'CANCELED', 'DEADLINE_EXPIRED'].includes(data.status)) {
      message('Не получилось оплатить. Платёж не завершён, баланс не пополнен.');
      $('wallet').textContent = '';
      $('refresh').hidden = true;
    } else {
      message('Статус банка: ' + data.status + '. Возврат на эту страницу не подтверждает оплату.');
      if (data.payment_url && data.status === 'NEW') { $('pay').hidden = false; }
    }
    return data;
  }
  async function show() {
    if (!key) { signIn(); return; }
    const me = await api('/v1/me');
    if (!me.account?.id || (sessionOwner && me.account.id !== sessionOwner)) {
      clearAuth(); signIn();
      return;
    }
    sessionOwner = me.account.id;
    await loadConfig();
    $('checkout').hidden = false; $('purchase').hidden = false;
    if (order) { await check(); return; }
    const plan = config.plans[params.get('plan')];
    if (params.has('plan') && !plan) throw Error('Неизвестный тариф. Вернитесь к выбору пакета.');
    const value = params.get('amount') || '';
    if (!plan && !/^\d+(\.\d{1,2})?$/.test(value)) throw Error('Выберите сумму на странице тарифов.');
    $('item').textContent = plan ? 'Пакет «' + plan.name + '»' : 'Пополнение баланса';
    $('amount').textContent = format(plan ? plan.amount : Math.round(Number(value) * 100));
    $('terms').textContent = plan
      ? `Сумма поступит на баланс. Когда оплаченный остаток достигнет ${format(plan.amount)}, включится тариф «${plan.name}» на 30 дней. Деньги останутся на балансе.`
      : 'Пополнение поступит на баланс. Достигнутый порог включит соответствующий тариф на 30 дней; само пополнение действующий срок не продлевает.';
  }
  $('pay').addEventListener('click',async()=>{
    if(busy)return; if(!$('accept-terms').checked){message('Подтвердите ознакомление с условиями.');return;} busy=true;$('pay').disabled=true;
    try {
      let data;
      if(order) data=await api('/v1/payments/orders/'+encodeURIComponent(order));
      else {
        data=await api('/v1/payments/orders',params.has('plan')?{plan:params.get('plan'),idempotency_key:idempotency,accept_terms:true}:{amount:params.get('amount'),idempotency_key:idempotency,accept_terms:true});
        order=data.order_id;history.replaceState(null,'','?order='+encodeURIComponent(order));
      }
      if(data.credited){await check();return;}
      const target=new URL(data.payment_url);
      if(target.protocol!=='https:' || !['pay.tbank-online.com','securepayments.tinkoff.ru','securepayments.tbank.ru','securepay.tinkoff.ru','securepay.tbank.ru','yookassa.ru','yoomoney.ru','auth.robokassa.ru'].includes(target.hostname))throw Error('Недопустимый адрес оплаты.');
      location.assign(target.href);
    }catch(e){message(e.message);}finally{busy=false;$('pay').disabled=false;}
  });
  $('refresh').addEventListener('click',async()=>{if(busy)return;busy=true;$('refresh').disabled=true;try{await check();}catch(e){message(e.message);}finally{busy=false;$('refresh').disabled=false;}});
  $('logout').addEventListener('click',()=>{clearAuth();signIn();});
  show().catch(e=>{if(!redirecting){$('checkout').hidden=false;message(e.message);}});
})();
