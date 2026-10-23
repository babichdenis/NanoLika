/* No terminal password, card details or account key in URLs. Same-origin checkout. */
(() => {
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const format = value => new Intl.NumberFormat('ru-RU', {style:'currency',currency:'RUB'}).format(value / 100);
  let key = '', config = null, order = params.get('order') || '';
  let idempotency = crypto.randomUUID().replaceAll('-', '');
  let busy = false;
  try { key = localStorage.getItem('nm.key') || ''; } catch (_) {}
  const message = text => { $('message').textContent = text; };
  async function api(path, body) {
    const r = await fetch(path, {method:body ? 'POST' : 'GET', cache:'no-store', headers:{...(body ? {'Content-Type':'application/json'} : {}), ...(key ? {Authorization:'Bearer ' + key} : {})}, ...(body ? {body:JSON.stringify(body)} : {})});
    const data = r.status === 204 ? {} : await r.json();
    if (!r.ok) {
      if (r.status === 401) { key = ''; $('login').hidden = false; $('purchase').hidden = true; }
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
    if (!key) { $('login').hidden = false; return; }
    await api('/v1/me');
    $('login').hidden = true; $('purchase').hidden = false;
    if (order) { await check(); return; }
    const plan = config.plans[params.get('plan')];
    if (params.has('plan') && !plan) throw Error('Неизвестный тариф. Вернитесь к выбору пакета.');
    const value = params.get('amount') || '';
    if (!plan && !/^\d+(\.\d{1,2})?$/.test(value)) throw Error('Выберите сумму на странице тарифов.');
    $('item').textContent = plan ? 'Пакет «' + plan.name + '»' : 'Пополнение баланса';
    $('amount').textContent = format(plan ? plan.amount : Math.round(Number(value) * 100));
    $('terms').textContent = plan ? 'Вся сумма на тестовый баланс. Скидка ' + plan.discount + '% на 30 дней. ' + (plan.connect ? 'Связка ваших устройств.' : 'Без связки устройств.') + ' Остаток сохраняется.' : 'Пополнение добавляет деньги без продления скидки и связки.';
  }
  $('email-form').addEventListener('submit', async e => {
    e.preventDefault(); const button=e.submitter; button.disabled=true;
    try { await api('/v1/auth/code', {identifier:$('email').value.trim()}); $('code-form').hidden=false; message('Введите код из письма.'); $('code').focus(); } catch(e) { message(e.message); } finally { button.disabled=false; }
  });
  $('code-form').addEventListener('submit', async e => {
    e.preventDefault(); const button=e.submitter;button.disabled=true;
    try { const data=await api('/v1/auth/verify',{identifier:$('email').value.trim(),code:$('code').value.trim(),device:'Оплата nanoLika'});key=data.api_key;try{localStorage.setItem('nm.key',key);}catch(_){} message('');await show(); } catch(e){message(e.message);} finally{button.disabled=false;}
  });
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
      if(target.protocol!=='https:' || !['pay.tbank-online.com','securepayments.tinkoff.ru','securepayments.tbank.ru','securepay.tinkoff.ru','securepay.tbank.ru'].includes(target.hostname))throw Error('Недопустимый адрес оплаты.');
      location.assign(target.href);
    }catch(e){message(e.message);}finally{busy=false;$('pay').disabled=false;}
  });
  $('refresh').addEventListener('click',async()=>{if(busy)return;busy=true;$('refresh').disabled=true;try{await check();}catch(e){message(e.message);}finally{busy=false;$('refresh').disabled=false;}});
  $('logout').addEventListener('click',()=>{key='';try{localStorage.removeItem('nm.key');}catch(_){}$('purchase').hidden=true;$('login').hidden=false;message('Введите почту другого аккаунта.');});
  api('/v1/payments/config').then(data=>{config=data;return show();}).catch(e=>message(e.message));
})();
