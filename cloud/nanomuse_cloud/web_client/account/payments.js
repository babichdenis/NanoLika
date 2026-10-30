import {amountValue,bankTarget} from './payment-utils.js?v=20261030-auth-gate';
const words={
ru:{title:'Пополнить баланс',intro:'Пополнения остаются на балансе. Когда оплаченный остаток достигает порога, соответствующий тариф включается на 30 дней.',close:'Закрыть',select:'Выбрать',custom:'Любая сумма пополнения',amount:'Сумма пополнения, ₽',amountHint:'От 0,01 до 100 000 ₽, до двух знаков после запятой.',customNote:'Оплаченные пополнения накапливаются, а расходы уменьшают оплаченный остаток. При достижении порога тариф включается на 30 дней; бонусы не учитываются. Новое пополнение не продлевает активный срок.',allBalance:'Весь платёж остаётся на балансе · это порог тарифа',discount:'Скидка',days:'дней',noConnect:'Без связки устройств',connect:'Связка всех ваших поддерживаемых устройств',basic:'Для личных дел',plus:'Между домом и работой',team:'Если дел много',basicNote:'Разобрать почту, найти нужное и не забыть о важном.',plusNote:'Начать на компьютере и продолжить в мессенджере.',teamNote:'Больше работы с Ликой при меньшей стоимости запросов.',terms:'Я ознакомился с ',offer:'офертой',privacy:'обработкой данных',and:' и ',demo:'Сейчас тестовый режим. Реальные деньги не списываются, рабочий баланс не пополняется.',pay:'Перейти к оплате',testPay:'Перейти к тестовой оплате',loading:'Проверяем условия…',preparing:'Готовим платёж…',error:'Не удалось подготовить оплату. Попробуйте ещё раз: повторная попытка не создаст второй заказ для этого выбора.',unavailable:'Приём оплаты пока недоступен. Выбор пакета и сумма сохранятся до закрытия окна.',invalidAmount:'Укажите сумму от 0,01 до 100 000 ₽, не больше двух знаков после запятой.',accept:'Подтвердите ознакомление с условиями.',choose:'Выберите порог тарифа или укажите свою сумму.',summary:'К оплате',left:'Неиспользованный остаток сохраняется.',login:'Войдите в аккаунт ещё раз.',invalidBank:'Банк не вернул допустимый адрес оплаты. Повторите проверку статуса.'},
en:{title:'Top up your balance',intro:'Top-ups stay in your balance. Reaching a paid-balance threshold activates the matching tariff for 30 days.',close:'Close',select:'Select',custom:'Custom amount',amount:'Top-up amount, RUB',amountHint:'From RUB 0.01 to 100,000, up to two decimal places.',customNote:'Paid top-ups accumulate and spending reduces the paid remainder. Reaching a threshold activates the tariff for 30 days; bonuses do not count. A new top-up does not extend an active term.',allBalance:'Full payment stays in balance · tariff threshold',discount:'Discount',days:'days',noConnect:'Without device connection',connect:'Connect all your supported devices',basic:'For everyday tasks',plus:'Between home and work',team:'When there is more to do',basicNote:'Handle email, find what you need and remember important things.',plusNote:'Start on your computer and continue in a messenger.',teamNote:'More work with Lika at lower request prices.',terms:'I have read the ',offer:'offer',privacy:'data processing information',and:' and ',demo:'Test mode. No real funds are collected and your working balance is not credited.',pay:'Continue to payment',testPay:'Continue to test payment',loading:'Checking the terms…',preparing:'Preparing payment…',error:'Could not prepare payment. Try again: retrying this selection will not create a second order.',unavailable:'Payment is currently unavailable. Your selection stays until this dialog closes.',invalidAmount:'Enter RUB 0.01 to 100,000 with up to two decimal places.',accept:'Confirm that you have read the terms.',choose:'Choose a tariff threshold or enter a custom amount.',summary:'To pay',left:'Unused balance is kept.',login:'Please sign in again.',invalidBank:'The bank did not provide an allowed payment URL. Check the order status.'},
zh:{title:'充值余额',intro:'充值金额保留在余额中。达到付费余额门槛后套餐生效一个月。',close:'关闭',select:'选择',custom:'自定义金额',amount:'充值金额，卢布',amountHint:'0.01 至 100000 卢布，最多两位小数。',customNote:'付费充值会累计，消费会减少付费余额。达到门槛后套餐生效 30 天；赠送余额不计入。有效期内再次充值不会延长期限。',allBalance:'全额保留在余额中 · 套餐门槛',discount:'折扣',days:'天',noConnect:'不含设备互联',connect:'连接所有受支持的设备',basic:'日常事务',plus:'家庭与工作之间',team:'更多事务',basicNote:'整理邮件、寻找资料、记住重要事项。',plusNote:'在电脑上开始，在聊天应用中继续。',teamNote:'以较低的请求价格处理更多事务。',terms:'我已阅读',offer:'服务协议',privacy:'数据处理说明',and:'及',demo:'当前为测试模式，不收取真实资金，也不计入实际余额。',pay:'前往支付',testPay:'前往测试支付',loading:'正在核对条件…',preparing:'正在准备支付…',error:'无法准备支付，请重试。重复同一选择不会创建第二个订单。',unavailable:'暂不可支付，关闭窗口前保留所选内容。',invalidAmount:'请输入 0.01 至 100000 卢布，最多两位小数。',accept:'请确认已阅读条款。',choose:'请选择套餐门槛或输入自定义金额。',summary:'应付金额',left:'未使用余额保留。',login:'请重新登录。',invalidBank:'银行未返回有效支付地址，请核对订单状态。'}
};
export function installPaymentDialog({lang,request,getSession,fromConsole}){
 const T=words[lang]||words.ru,$=id=>document.getElementById(id),dialog=$('payment-dialog');
 const h=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
 const money=n=>new Intl.NumberFormat(lang,{style:'currency',currency:'RUB'}).format(n/100);
 let config=null,busy=false,loaded=false,selected='',draft=null,authenticated=false;
 const close=h('button','×','payment-close');close.type='button';close.setAttribute('aria-label',T.close);
 const heading=h('h2',T.title);heading.id='payment-title';
 const plans=h('div',undefined,'payment-plans'),custom=h('div',undefined,'custom-payment'),amount=h('input');amount.id='payment-custom-amount';amount.type='text';amount.inputMode='decimal';amount.placeholder='200';amount.autocomplete='off';amount.setAttribute('aria-describedby','payment-amount-hint');
 const fieldset=h('fieldset'),customLabel=h('label'),customRadio=h('input');customRadio.type='radio';customRadio.name='payment-option';customRadio.value='custom';customRadio.setAttribute('aria-label',T.custom);customLabel.append(customRadio,h('strong',T.custom));const amountLabel=h('label',T.amount);amountLabel.htmlFor=amount.id;
 const hint=h('p',T.amountHint,'fine');hint.id='payment-amount-hint';custom.append(customLabel,amountLabel,amount,hint);fieldset.append(plans,custom);
 const acceptance=h('label',undefined,'payment-accept'),accept=h('input');accept.type='checkbox';accept.id='payment-accept';const copy=h('span');copy.append(document.createTextNode(T.terms));for(const [i,[label,url]] of [[T.offer,'https://nanolika.ru/legal.html#offer'],[T.privacy,'https://nanolika.ru/legal.html#privacy']].entries()){if(i)copy.append(document.createTextNode(T.and));const a=h('a',label);a.href=url;a.target='_blank';a.rel='noopener';copy.append(a);}acceptance.append(accept,copy);
 const total=h('p',undefined,'payment-total'),mode=h('p',undefined,'payment-mode'),status=h('p');status.id='payment-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');const pay=h('button',T.pay,'button primary');pay.type='button';pay.id='payment-submit';pay.disabled=true;
 const legal=h('a',lang==='ru'?'Официальная информация':lang==='zh'?'法律信息':'Legal information','payment-legal');legal.href='https://nanolika.ru/legal.html';legal.target='_blank';legal.rel='noopener';
 dialog.append(close,heading,h('p',T.intro),fieldset,acceptance,total,mode,status,pay);
 dialog.setAttribute('aria-labelledby','payment-title');
 const signIn=()=>{dialog.close();location.replace('/app/?signin=1&next='+encodeURIComponent(location.href));};
 const option=()=>selected==='custom'?amountValue(amount.value):config?.plans?.[selected]?{cents:config.plans[selected].amount,plan:selected}:null;
 function update(){const pick=option();total.textContent=pick?`${T.summary}: ${money(pick.cents)}`:'';pay.disabled=busy||!authenticated||!config||!pick||!accept.checked;}
 function renderPlans(){
  plans.replaceChildren();
  for(const id of ['lika','plus','lika-plus','team']){
   const p=config.plans?.[id];if(!p||!Number.isSafeInteger(p.amount)||p.amount<1||p.amount>10000000)continue;
   const card=h('label',undefined,'payment-plan'),radio=h('input');radio.type='radio';radio.name='payment-option';radio.value=id;radio.setAttribute('aria-label',p.name);
   const detail=h('span',undefined,'payment-plan-detail');detail.append(h('strong',p.name),h('small',`${T.discount} ${p.discount}% · ${p.days||30} ${T.days}`));
   card.append(radio,detail,h('strong',money(p.amount),'plan-amount'));plans.append(card);
  }
 }
 function preselect(choice={}){
  if(choice.plan&&config?.plans?.[choice.plan])selected=choice.plan;
  else if(choice.amount){selected='custom';amount.value=String(choice.amount);}
  for(const radio of fieldset.querySelectorAll('input[name="payment-option"]'))radio.checked=radio.value===selected;
  update();
 }
 async function open(choice={}){
  if(!dialog.open)dialog.showModal();document.body.classList.add('payment-open');
  authenticated=false;fieldset.hidden=true;acceptance.hidden=true;total.hidden=true;mode.hidden=true;pay.hidden=true;
  fieldset.disabled=true;pay.disabled=true;status.textContent=T.loading;
  try{
   const session=await getSession();if(!session?.key||!session?.owner)throw Object.assign(new Error('login'),{status:401});
   const me=await request('/v1/me');if(!me.account?.id||me.account.id!==session.owner)throw Object.assign(new Error('identity'),{status:401});
   if(!loaded){config=await request('/v1/payments/config');renderPlans();loaded=true;}
   authenticated=true;fieldset.hidden=false;acceptance.hidden=false;total.hidden=false;mode.hidden=false;pay.hidden=false;
   mode.textContent=config.test_mode===true?T.demo:'';pay.textContent=config.test_mode===true?T.testPay:T.pay;
   status.textContent='';
   preselect(choice);
  }catch(e){if(e.status===401){signIn();return;}status.textContent=T.unavailable;}
  finally{fieldset.disabled=!authenticated;update();}
 }

 fieldset.onchange=e=>{if(e.target.name==='payment-option'){selected=e.target.value;update();}};
 accept.onchange=update;
 amount.oninput=()=>{selected='custom';customRadio.checked=true;update();};
 close.onclick=()=>{if(!busy)dialog.close();};dialog.addEventListener('cancel',e=>{if(busy)e.preventDefault();});dialog.addEventListener('close',()=>{document.body.classList.remove('payment-open');});
 if($('fund-account'))$('fund-account').onclick=()=>open();
 pay.onclick=async()=>{
  if(busy||!authenticated||!config)return;
  const pick=option();if(!pick){status.textContent=selected==='custom'?T.invalidAmount:T.choose;return;}
  if(!accept.checked){status.textContent=T.accept;return;}
  busy=true;fieldset.disabled=true;pay.disabled=true;close.disabled=true;status.textContent=T.preparing;
  try{
   const session=await getSession();if(!session?.key||!session?.owner){authenticated=false;fieldset.hidden=true;acceptance.hidden=true;total.hidden=true;mode.hidden=true;pay.hidden=true;signIn();return;}
   const me=await request('/v1/me');if(!me.account?.id||me.account.id!==session.owner){authenticated=false;fieldset.hidden=true;acceptance.hidden=true;total.hidden=true;mode.hidden=true;pay.hidden=true;signIn();return;}
   const selection=pick.plan?{plan:pick.plan}:{amount:pick.amount};
   const scope=JSON.stringify({account:session.owner,selection}),draftKey='nl.account.payment.draft.'+encodeURIComponent(scope);
   try{const saved=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(saved?.scope===scope&&/^[a-zA-Z0-9_-]{16,64}$/.test(saved.id))draft=saved;}catch{}
   if(draft?.scope!==scope)draft={scope,id:crypto.randomUUID().replaceAll('-','')};
   sessionStorage.setItem(draftKey,JSON.stringify(draft));
   const data=await request('/v1/payments/orders',{method:'POST',body:JSON.stringify({...selection,idempotency_key:draft.id,accept_terms:true})});
   if(!data.order_id)throw Error('order');
   sessionStorage.setItem('nl.payment.context.'+data.order_id,JSON.stringify({account:session.owner,source:fromConsole?'console':'web',draft_key:draftKey}));
   const result='/app/payments/?order='+encodeURIComponent(data.order_id);
   if(data.credited||data.status!=='NEW'){location.assign(result);return;}
   const target=bankTarget(data.payment_url);if(!target){status.textContent=T.invalidBank;return;}location.assign(target);
  }catch(e){if(e.status===401){authenticated=false;fieldset.hidden=true;acceptance.hidden=true;total.hidden=true;mode.hidden=true;pay.hidden=true;signIn();}else status.textContent=T.error;}
  finally{busy=false;fieldset.disabled=!authenticated;update();close.disabled=false;}
 };
 return {open,close:()=>{if(dialog.open)dialog.close();}};
}
