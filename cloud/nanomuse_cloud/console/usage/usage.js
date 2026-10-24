(() => {
  'use strict';
  const words = {
    ru: { title:'На что уходит баланс', adminTitle:'Модели и учёт расходов', eyebrow:'nanoLika · Ваш кабинет', adminEye:'nanoLika · Управление', intro:'Здесь видно, какие модели использовались и сколько токенов записано в журнал. Одна просьба Лике может включать несколько обращений к модели.', period:'Период, дней', refresh:'Обновить', loading:'Загружаем журнал…', error:'Не удалось получить журнал. Попробуйте ещё раз.', login:'Войдите в nanoLika, чтобы увидеть свой расход.', adminLogin:'Откройте админку и войдите, затем вернитесь сюда.', expired:'Вход больше не действует. Войдите заново.', total:'Сумма по журналу', calls:'Обращения к моделям', models:'Модели', input:'Входящие токены', output:'Исходящие токены', cache:'Из входящих, кеш', unknown:'Не записано', history:'Последние обращения', more:'Показать ещё', none:'За этот период обращений нет.', note:'Исторические суммы рассчитаны по ставкам и курсу, заданным в релее. Это не подтверждённая стоимость поставщика. История переписки и инструкции тоже входят в объём запроса. Тарифные скидки доступны здесь только после их записи в расчётном журнале.', retail:'Суммы взяты из расчётного журнала nanoLika. Себестоимость поставщика ведётся отдельно.', unlimited:'Для этого аккаунта ограничение баланса отключено. Показанные суммы отражают учёт использования, а не подтверждение оплаты.', provider:'Себестоимость поставщика: не подтверждена', providerAmount:'Стоимость по ответу поставщика', route:'Модель в шлюзе', id:'Номер обращения', gateway:'Шлюз моделей: New API', gatewayNote:'Шлюз работает через технический аккаунт. Его статистика не распределена по клиентам. Клиентский расход выше получен из журнала релея.', gatewayUnavailable:'Статистика шлюза сейчас недоступна. Журнал релея показан отдельно.', channels:'Активные подключения', enabled:'Модели в шлюзе', records:'Записи шлюза', back:'← В кабинет', adminBack:'← В админку', footer:'Здесь показаны только сведения об использовании. Тексты сообщений, документы и ключи не загружаются в этот отчёт.', details:'Подробности', kind:{chat:'Диалог',image:'Изображение',video:'Видео',realtime:'Голос'} },
    en: { title:'Where your balance goes', adminTitle:'Models and usage accounting', eyebrow:'nanoLika · Your account', adminEye:'nanoLika · Administration', intro:'See the models used and the tokens recorded in the ledger. One task can involve several model calls.', period:'Period, days', refresh:'Refresh', loading:'Loading the ledger…', error:'Could not load the ledger. Please try again.', login:'Sign in to nanoLika to see your usage.', adminLogin:'Sign in to the admin console, then return here.', expired:'Your sign-in expired. Please sign in again.', total:'Recorded amount', calls:'Model calls', models:'Models', input:'Input tokens', output:'Output tokens', cache:'Cached input tokens', unknown:'Not recorded', history:'Recent calls', more:'Show more', none:'No calls in this period.', note:'Historical amounts use the relay’s configured prices and exchange rate. They are not verified provider charges. Conversation history and instructions count towards input. Plan discounts appear here only when recorded in the billing ledger.', retail:'Amounts come from the nanoLika billing ledger. Provider cost is accounted for separately.', unlimited:'Balance limits are disabled for this account. These amounts describe recorded usage, not proof of payment.', provider:'Provider cost: unverified', providerAmount:'Provider-reported cost', route:'Gateway model', id:'Request ID', gateway:'Model gateway: New API', gatewayNote:'The gateway uses a technical account. Its statistics are not mapped to customers. Customer usage above comes from the relay ledger.', gatewayUnavailable:'Gateway statistics are unavailable. The relay ledger is shown separately.', channels:'Enabled channels', enabled:'Gateway models', records:'Gateway records', back:'← Account', adminBack:'← Admin console', footer:'This report loads usage metadata only, without message text, documents or keys.', details:'Details', kind:{chat:'Chat',image:'Image',video:'Video',realtime:'Voice'} },
    zh: { title:'余额使用明细', adminTitle:'模型与用量核算', eyebrow:'nanoLika · 账户', adminEye:'nanoLika · 管理', intro:'查看所用模型及账本记录的令牌。一个任务可能包含多次模型调用。', period:'天数', refresh:'刷新', loading:'正在读取账本…', error:'无法读取账本，请重试。', login:'请登录 nanoLika 查看用量。', adminLogin:'请先登录管理页面，再返回此处。', expired:'登录已失效，请重新登录。', total:'账本金额', calls:'模型调用次数', models:'模型', input:'输入令牌', output:'输出令牌', cache:'其中缓存输入', unknown:'未记录', history:'最近调用', more:'显示更多', none:'此期间没有调用。', note:'历史金额按中继配置的价格及汇率计算，并非已核实的服务商费用。历史对话及指令也计入输入。套餐折扣仅在计费账本记录后显示。', retail:'金额来自 nanoLika 计费账本，服务商成本另行核算。', unlimited:'此账户未限制余额。这些金额反映用量记录，并不代表实际付款。', provider:'服务商成本：未核实', providerAmount:'服务商报告的成本', route:'网关模型', id:'请求编号', gateway:'模型网关：New API', gatewayNote:'网关使用技术账户，统计尚未对应到各客户。上方客户用量来自中继账本。', gatewayUnavailable:'暂时无法获取网关统计，中继账本单独显示。', channels:'启用的渠道', enabled:'网关模型', records:'网关记录', back:'← 账户', adminBack:'← 管理页面', footer:'此报表只读取用量元数据，不读取消息内容、文档或密钥。', details:'详情', kind:{chat:'对话',image:'图片',video:'视频',realtime:'语音'} }
  };
  const q = new URLSearchParams(location.search), admin = q.get('admin') === '1', web = q.get('source') === 'web';
  let storedLang = 'ru';
  try { storedLang = localStorage.getItem('nm.lang') || 'ru'; } catch (_) { /* storage may be unavailable */ }
  const lang = q.get('lang') || storedLang, T = words[lang] || words.ru;
  document.documentElement.lang = words[lang] ? lang : 'ru';
  document.title = `${admin ? T.adminTitle : T.title} · nanoLika`;
  const el = id => document.getElementById(id);
  const h = (tag, text, cls) => { const n=document.createElement(tag); if(text !== undefined)n.textContent=text; if(cls)n.className=cls; return n; };
  const number = n => new Intl.NumberFormat(document.documentElement.lang).format(n || 0);
  const money = n => new Intl.NumberFormat(document.documentElement.lang,{style:'currency',currency:'RUB',minimumFractionDigits:2,maximumFractionDigits:6}).format(n || 0);
  el('title').textContent = admin ? T.adminTitle : T.title;
  el('eyebrow').textContent = admin ? T.adminEye : T.eyebrow;
  for(const [id, key] of Object.entries({'intro':'intro','period-label':'period','refresh':'refresh','models-title':'models','history-title':'history','more':'more','footer':'footer'}))el(id).textContent=T[key];
  el('back').textContent=admin?T.adminBack:T.back;el('back').href=admin?'../admin/#money':web?'/web/account/':'../';
  let next = null, busy = false, revision = 0;
  function metric(label, value) { const n=h('div',undefined,'metric'); n.append(h('span',label),h('strong',value));return n; }
  function split(row) {const d=h('dl');for(const [label,value] of [[T.input,row.input_tokens ?? row.prompt_tokens],[T.output,row.output_tokens ?? row.completion_tokens],[T.cache,row.cached_tokens]]){d.append(h('dt',label),h('dd',value === undefined || value === null ? T.unknown : number(value)));}return d;}
  function record(row) {
    const box=h('article',undefined,'record'), top=h('div',undefined,'record-head'), time=h('time',new Date(row.ts*1000).toLocaleString(document.documentElement.lang));time.dateTime=new Date(row.ts*1000).toISOString();
    top.append(h('strong',row.model || T.unknown),h('strong',money(row.recorded_charge_rub)),time);box.append(top,h('p',T.kind[row.kind] || row.kind,'muted'),split(row));
    const detail=h('details');detail.append(h('summary',T.details));if(admin)detail.append(h('p',row.provider_cost_rub === null ? T.provider : `${T.providerAmount}: ${money(row.provider_cost_rub)}`,'muted'));if(row.gateway_model)detail.append(h('p',`${T.route}: ${row.gateway_model}`));if(row.request_id)detail.append(h('p',T.id),h('code',row.request_id));box.append(detail);return box;
  }
  async function load(append=false) {
    if(busy && append)return;
    const current=++revision;busy=true;el('refresh').disabled=true;el('more').disabled=true;el('status').textContent=T.loading;
    let token='';try{token=admin?sessionStorage.getItem('nm.admin') || '':web ? JSON.parse(localStorage.getItem('nl.web.session.v1') || 'null')?.key || '' : localStorage.getItem('nm.key') || '';}catch(_){}
    if(!token){el('status').textContent=admin?T.adminLogin:T.login;busy=false;el('refresh').disabled=false;return;}
    const url=`/v1/${admin?'admin':'me'}/model-statement?days=${el('period').value}${append&&next?`&before=${next}`:''}`;
    try {
      const response=await fetch(url,{headers:{[admin?'X-Admin-Token':'Authorization']:admin?token:`Bearer ${token}`},cache:'no-store'});
      if(current!==revision)return;
      if(response.status===401){el('status').textContent=T.expired;return;}
      if(!response.ok)throw new Error('request');const data=await response.json();if(current!==revision)return;
      if(!append){
        el('summary').replaceChildren(metric(T.total,money(data.recorded_charge_rub)),metric(T.calls,number(data.requests)),metric(T.models,number(data.by_model.length)));
        el('notice').textContent=(data.basis==='retail_ledger'?T.retail:T.note)+(data.unlimited_account?' '+T.unlimited:'');
        el('models').replaceChildren(...data.by_model.map(row=>{const n=h('article',undefined,'model');n.append(h('h3',row.model || T.unknown),h('p',money(row.recorded_charge_rub),'amount'),h('p',`${T.calls}: ${number(row.requests)}`),split(row));return n;}));
        el('history').replaceChildren();
        if(admin){const g=data.gateway, node=el('gateway');node.replaceChildren(h('h2',T.gateway),h('p',g?.available?T.gatewayNote:T.gatewayUnavailable));if(g?.available)node.append(h('p',`${T.channels}: ${number(g.enabled_channels)} · ${T.enabled}: ${number(g.enabled_models)} · ${T.records}: ${number(g.log_records)}`));const a=h('a','New API · GitHub');a.href='https://github.com/QuantumNous/new-api';a.target='_blank';a.rel='noopener noreferrer';node.append(a);}
      }
      el('history').append(...data.records.map(record));next=data.next_before;el('more').hidden=!next;el('status').textContent=data.requests?'':T.none;
    }catch(_){if(current===revision)el('status').textContent=T.error;}
    finally{if(current===revision){busy=false;el('refresh').disabled=false;el('more').disabled=false;}}
  }
  el('refresh').onclick=()=>load();el('period').onchange=()=>load();el('more').onclick=()=>load(true);
  addEventListener('storage',e=>{if(!e.key || e.key==='nm.key' || e.key==='nl.web.session.v1'){el('summary').replaceChildren();el('models').replaceChildren();el('history').replaceChildren();next=null;el('more').hidden=true;load();}});
  load();
})();
