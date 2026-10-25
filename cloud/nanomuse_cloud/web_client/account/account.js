import {ChannelOnboarding,isChannelLinked} from '../channel-onboarding.js?v=20261026-qr';
import {installAutopay} from './autopay.js';
import {installUsageChart} from './usage-chart.js?v=20261025-account';
import {installPaymentDialog} from './payments.js';
import {groupWebDevices} from './payment-utils.js';
import {clearOwner} from '../storage.js';
const words={
ru:{autopay:'Карта и автопополнение',title:'Ваш кабинет',back:'Вернуться к Лике',overview:'Обзор',usage:'Расход и модели',devices:'Устройства и каналы',security:'Вход и безопасность',settings:'Настройки',eyebrow:'nanoLika Cloud',refresh:'Обновить',balance:'Доступный баланс',pay:'Пополнение и тарифы',plan:'Ваш тариф',period:'Период, дней',statement:'Все обращения и токены →',deviceNote:'Здесь устройства и способы входа, привязанные к этому аккаунту.',channels:'Связанные аккаунты',sessions:'Активные входы',password:'Пароль аккаунта',current:'Текущий пароль',newPassword:'Новый пароль',repeat:'Повторите новый пароль',save:'Сохранить пароль',language:'Язык',localNote:'Документы и вложения остаются в вашем браузере. Очистка данных браузера может удалить локальные файлы.',privacy:'Обработка данных',logout:'Выйти из аккаунта',clear:'Удалить локальные данные',loading:'Загружаем кабинет…',error:'Не удалось обновить данные. Попробуйте ещё раз.',login:'Войдите в Лику, чтобы открыть свой кабинет.',unlimited:'Без ограничения',balanceNote:'Остаток показан по данным вашего аккаунта.',unlimitedNote:'Для этого аккаунта ограничение баланса отключено.',planUnknown:'Данные о тарифе пока не передаются сервером.',noPlan:'Активного тарифа нет',until:'Действует до',discount:'Скидка',connected:'Связь между устройствами доступна',disconnected:'Связь между устройствами ограничена',usageNote:'Суммы взяты из журнала nanoLika. Одна просьба может включать несколько обращений к модели.',legacy:'Исторические суммы рассчитаны по настройкам релея. Это не подтверждённый счёт поставщика.',input:'Вход',output:'Ответ',calls:'Обращения',empty:'Пока ничего нет.',unavailable:'Данные сейчас недоступны.',online:'На связи',offline:'Не на связи',currentSession:'Этот вход',revoke:'Завершить вход',revokeConfirm:'Завершить этот вход на другом устройстве?',clearConfirm:'Удалить из этого браузера все беседы и документы аккаунта? Это действие нельзя отменить.',saved:'Пароль сохранён.',mismatch:'Пароли не совпадают.',short:'Минимальная длина пароля',characters:'символов',passwordError:'Не удалось сохранить пароль. Проверьте текущий пароль и попробуйте ещё раз.',tokenUnit:'токенов',usageError:'Расход сейчас недоступен.',deleting:'Удаляем…',unlink:'Отвязать',unlinkConfirm:'Отвязать чат {name} от аккаунта? Чат останется, но перестанет быть этим аккаунтом: своя касса, своя история. Аккаунт в приложении не изменится.',unlinked:'Чат {name} отвязан от аккаунта.',unlinkIsAccount:'Этот чат и есть аккаунт: с него аккаунт начался. Отвязать его нельзя, не удалив аккаунт.',unlinkError:'Не удалось отвязать чат. Попробуйте ещё раз.'},
en:{autopay:'Card and automatic top-ups',title:'Your account',back:'Back to Lika',overview:'Overview',usage:'Usage and models',devices:'Devices and channels',security:'Sign-in and security',settings:'Settings',eyebrow:'nanoLika Cloud',refresh:'Refresh',balance:'Available balance',pay:'Top up and plans',plan:'Your plan',period:'Period, days',statement:'All calls and tokens →',deviceNote:'Devices and sign-in methods linked to this account.',channels:'Linked accounts',sessions:'Active sign-ins',password:'Account password',current:'Current password',newPassword:'New password',repeat:'Repeat the new password',save:'Save password',language:'Language',localNote:'Documents and attachments stay in your browser. Clearing browser data may remove local files.',privacy:'Data processing',logout:'Sign out',clear:'Delete local data',loading:'Loading your account…',error:'Could not refresh your account. Please try again.',login:'Sign in to Lika to open your account.',unlimited:'No limit',balanceNote:'The balance is reported by your account.',unlimitedNote:'Balance limits are disabled for this account.',planUnknown:'The server does not report plan information yet.',noPlan:'No active plan',until:'Valid until',discount:'Discount',connected:'Device connection is available',disconnected:'Device connection is restricted',usageNote:'Amounts come from the nanoLika ledger. One task can involve several model calls.',legacy:'Historical amounts use the relay configuration and are not a verified provider invoice.',input:'Input',output:'Output',calls:'Calls',empty:'Nothing here yet.',unavailable:'Data is currently unavailable.',online:'Connected',offline:'Not connected',currentSession:'This sign-in',revoke:'End sign-in',revokeConfirm:'End this sign-in on the other device?',clearConfirm:'Delete all conversations and documents for this account from this browser? This cannot be undone.',saved:'Password saved.',mismatch:'Passwords do not match.',short:'Minimum password length',characters:'characters',passwordError:'Could not save the password. Check your current password and try again.',tokenUnit:'tokens',usageError:'Usage is currently unavailable.',deleting:'Deleting…',unlink:'Unlink',unlinkConfirm:'Unlink the {name} chat from the account? The chat stays, but stops being this account: its own allowance, its own history. The account in the app is unchanged.',unlinked:'The {name} chat is unlinked from the account.',unlinkIsAccount:'This chat is the account itself: the account began there. It cannot be unlinked without deleting the account.',unlinkError:'Could not unlink the chat. Try again.'},
zh:{autopay:'银行卡与自动充值',title:'你的账户',back:'返回 Lika',overview:'概览',usage:'用量与模型',devices:'设备与渠道',security:'登录与安全',settings:'设置',eyebrow:'nanoLika Cloud',refresh:'刷新',balance:'可用余额',pay:'充值与套餐',plan:'你的套餐',period:'天数',statement:'所有调用及令牌 →',deviceNote:'此账户绑定的设备及登录方式。',channels:'已关联账户',sessions:'有效登录',password:'账户密码',current:'当前密码',newPassword:'新密码',repeat:'再次输入新密码',save:'保存密码',language:'语言',localNote:'文档及附件保存在浏览器中，清除浏览器数据可能删除本地文件。',privacy:'数据处理',logout:'退出登录',clear:'删除本地数据',loading:'正在读取账户…',error:'无法刷新，请重试。',login:'请先登录 Lika。',unlimited:'无限制',balanceNote:'余额来自账户记录。',unlimitedNote:'此账户未限制余额。',planUnknown:'服务器暂未提供套餐信息。',noPlan:'没有有效套餐',until:'有效期至',discount:'折扣',connected:'设备互联可用',disconnected:'设备互联受限',usageNote:'金额来自 nanoLika 账本，一个任务可能包含多次模型调用。',legacy:'历史金额按中继配置计算，并非已核实的服务商账单。',input:'输入',output:'输出',calls:'调用次数',empty:'暂无记录。',unavailable:'暂时无法获取数据。',online:'在线',offline:'不在线',currentSession:'当前登录',revoke:'结束登录',revokeConfirm:'结束另一设备上的此登录？',clearConfirm:'删除此浏览器中此账户的所有对话及文档？此操作无法撤销。',saved:'密码已保存。',mismatch:'两次密码不同。',short:'密码最少长度',characters:'字符',passwordError:'无法保存，请检查当前密码后重试。',tokenUnit:'令牌',usageError:'暂时无法读取用量。',deleting:'正在删除…',unlink:'解除绑定',unlinkConfirm:'把 {name} 聊天与账号解除绑定？聊天会保留，但不再是这个账号：自己的额度，自己的记录。App 里的账号不变。',unlinked:'{name} 聊天已解除绑定。',unlinkIsAccount:'这个聊天就是账号本身：账号是从这里开始的。不删除账号就无法解除绑定。',unlinkError:'无法解除绑定，请重试。'}
};
const $=id=>document.getElementById(id),fromConsole=new URLSearchParams(location.search).get('source')==='console',sessionKey=fromConsole?'nm.key':'nl.web.session.v1';let session=null,lang='ru',me=null,minPassword=8,revision=0;
try{session=fromConsole?{key:localStorage.getItem(sessionKey),owner:null}:JSON.parse(localStorage.getItem(sessionKey)||'null');lang=localStorage.getItem('nl.web.language')||'ru';}catch{}
if(!words[lang])lang='ru';const T=words[lang];document.documentElement.lang=lang;document.title=`${T.title} · nanoLika`;
for(const n of document.querySelectorAll('[data-word]'))n.textContent=T[n.dataset.word];$('language').value=lang;
$('statement').href='/app/usage/?source='+(fromConsole?'console':'web')+'&lang='+encodeURIComponent(lang);if(fromConsole){for(const a of document.querySelectorAll('.account-top a'))a.href='/app/';}
const h=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
const money=n=>new Intl.NumberFormat(lang,{style:'currency',currency:'RUB',maximumFractionDigits:6}).format(n);
const count=n=>new Intl.NumberFormat(lang).format(n||0);
const date=n=>n?new Date(n*1000).toLocaleDateString(lang):'—';
async function api(path,options={}){const r=await fetch(path,{...options,cache:'no-store',headers:{Authorization:'Bearer '+session.key,...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});if(r.status===401){$('private-content').hidden=true;$('identity').textContent='';const e=new Error('expired');e.status=401;throw e;}if(!r.ok)throw new Error('request');return r.status===204?null:r.json();}
function row(title,subtitle){const n=h('div',undefined,'account-row'),text=h('div');text.append(h('strong',title),h('p',subtitle||''));n.append(text);return n;}
function empty(node,message=T.empty){node.replaceChildren(h('p',message,'pad'));}
/* A chat tied to the account, with the way out beside it.
 *
 * The account itself is not offered here: the relay refuses it (`channel_is_the_account`)
 * because a chat that made the account IS the account, and only deleting the account ends
 * that. The button appears for the chats a code joined, which are the ones a person can let
 * go of without losing anything but that chat's tie. */
function channelRow(c){
 const name=c.channel==='telegram'?'Telegram':c.channel==='max'?'MAX':c.channel;
 const n=row(name,c.hint||'');
 const button=h('button',T.unlink,'quiet');
 button.type='button';
 button.onclick=async()=>{
  if(!confirm(T.unlinkConfirm.replace('{name}',name)))return;
  button.disabled=true;
  try{
   await api('/v1/link/channels/'+encodeURIComponent(c.channel),{method:'DELETE'});
   $('status').textContent=T.unlinked.replace('{name}',name);
   await load();
  }catch(error){
   $('status').textContent=error.status===409?T.unlinkIsAccount:T.unlinkError;
   button.disabled=false;
  }
 };
 n.append(button);
 return n;
}

function renderMe(){
 const a=me.account||{},s=me.spend||{};session.email=a.email||session.email;session.hint=a.hint||session.hint;$('identity').textContent=a.email||a.hint||session.hint||'nanoLika';
 $('balance').textContent=s.unlimited?T.unlimited:Number.isFinite(s.left_rub)?money(s.left_rub):'—';$('balance-note').textContent=s.unlimited?T.unlimitedNote:T.balanceNote;
 if(!Object.hasOwn(me,'plan')){$('plan-name').textContent='—';$('plan-detail').textContent=T.planUnknown;}
 else{const p=me.plan;$('plan-name').textContent=p?.active?(p.name||p.plan||p.id||'nanoLika'):T.noPlan;$('plan-detail').textContent=p?.active?[p.ends_at?`${T.until}: ${date(p.ends_at)}`:'',p.discount_pct!==undefined?`${T.discount}: ${p.discount_pct}%`:''].filter(Boolean).join(' · '):'';}
 $('connect').textContent=me.connect?me.connect.allowed?T.connected:T.disconnected:'';
 const channels=a.linked||[];$('channels').replaceChildren(...channels.map(c=>channelRow(c)));if(!channels.length)empty($('channels'));
 $('current-row').hidden=!a.has_password;$('current-password').required=!!a.has_password;
}
async function loadUsage(){await usageChart.load();}
async function load(){
 if(!session?.key||(!fromConsole&&!session?.owner)){$('status').textContent=T.login;return;}$('refresh').disabled=true;$('status').textContent=T.loading;
 try{me=await api('/v1/me');if(!me.account?.id||(session.owner&&me.account.id!==session.owner))throw new Error('identity');session.owner=me.account.id;renderMe();$('private-content').hidden=false;autopay.load();
 const results=await Promise.allSettled([api('/v1/devices'),api('/v1/me/sessions'),api('/v1/config'),loadUsage()]);
 const [devices,sessions,config,usage]=results;
 if(devices.status==='fulfilled'){const list=groupWebDevices(devices.value.devices||[]);$('device-list').replaceChildren(...list.map(d=>{
 if(d.kind!=='web')return row(d.name||d.kind||'nanoLika',(d.online?T.online:T.offline)+(!d.online&&d.last_seen?' · '+new Date(d.last_seen*1000).toLocaleString(lang):''));
 const box=h('details',undefined,'web-device-group'),summary=h('summary'),title=lang==='ru'?'Веб-консоль':lang==='zh'?'网页控制台':'Web console';
 summary.append(h('strong',title),h('span',(lang==='ru'?'Подключений: ':lang==='zh'?'连接数：':'Connections: ')+count(d.connections.length),'muted'));box.append(summary,h('p',lang==='ru'?'Вкладки и браузеры показаны одной группой. Их соединения остаются отдельными, чтобы не отключать друг друга.':lang==='zh'?'标签页及浏览器合并显示，连接保持独立，以免相互断开。':'Tabs and browsers are grouped here. Connections stay separate so tabs do not disconnect each other.','pad'));
 d.connections.forEach((tab,i)=>box.append(row((lang==='ru'?'Подключение ':lang==='zh'?'连接 ':'Connection ')+(i+1),(tab.os?tab.os+' · ':'')+(tab.online?T.online:T.offline))));return box;
 }));if(!list.length)empty($('device-list'));}else empty($('device-list'),T.unavailable);
 if(sessions.status==='fulfilled'){const list=sessions.value.sessions||[];$('sessions').replaceChildren(...list.map(s=>{const n=row(s.device||'nanoLika',`${s.current?T.currentSession+' · ':''}${date(s.last_used_at||s.created_at)}`);if(!s.current){const b=h('button',T.revoke);b.type='button';b.onclick=async()=>{if(!confirm(T.revokeConfirm))return;b.disabled=true;try{await api('/v1/me/sessions/'+encodeURIComponent(s.prefix),{method:'DELETE'});await load();}catch(_){$('status').textContent=T.error;b.disabled=false;}};n.append(b);}return n;}));if(!list.length)empty($('sessions'));}else empty($('sessions'),T.unavailable);
 if(config.status==='fulfilled')minPassword=Math.max(1,Number(config.value.password_min_len)||8);if(usage.status==='rejected')$('status').textContent=T.usageError;
 $('status').textContent='';
 }catch(e){$('private-content').hidden=true;$('identity').textContent='';$('status').textContent=e.status===401||e.message==='identity'?T.login:T.error;}finally{$('refresh').disabled=false;}
}
$('refresh').onclick=load;
$('password-form').onsubmit=async e=>{e.preventDefault();const password=$('new-password').value,status=$('password-status'),button=e.currentTarget.querySelector('button');status.textContent='';if(password.length<minPassword){status.textContent=`${T.short}: ${minPassword} ${T.characters}`;return;}if(password!==$('repeat-password').value){status.textContent=T.mismatch;return;}button.disabled=true;try{await api('/v1/auth/password',{method:'POST',body:JSON.stringify({password,current:me.account.has_password?$('current-password').value:undefined})});e.target.reset();await load();status.textContent=T.saved;}catch(_){status.textContent=T.passwordError;}finally{button.disabled=false;}};
$('language').onchange=()=>{try{localStorage.setItem('nl.web.language',$('language').value);}catch{}location.reload();};
$('logout').onclick=async()=>{const b=$('logout');b.disabled=true;try{await api('/v1/auth/sign-out',{method:'POST',body:'{}'});}catch{}try{localStorage.removeItem(sessionKey);localStorage.removeItem('nm.key');localStorage.removeItem('nm.hint');localStorage.removeItem('nm.acct');localStorage.removeItem('nl.web.session.v1');}catch{}location.replace('/v1/auth/logout?next='+encodeURIComponent(location.origin+'/app/?signin=1&next='+encodeURIComponent(location.origin+'/web/')));};
$('clear-data').onclick=async()=>{if(!confirm(T.clearConfirm))return;const b=$('clear-data');b.disabled=true;$('status').textContent=T.deleting;try{await clearOwner(session.owner);location.href=fromConsole?'/app/':'../';}catch(_){$('status').textContent=T.error;b.disabled=false;}};
const connectionGuide=new ChannelOnboarding({session:()=>session,language:()=>lang,api:(path,body)=>api(path,body?{method:'POST',body:JSON.stringify(body)}:{}),retry:()=>load()});
$('connection-intro').textContent=lang==='ru'?'Ликой можно пользоваться только в браузере. Подключайте другие приложения, если хотите продолжать беседы в них.':'You can use Lika entirely in your browser. Connect other apps only if you want to continue conversations there.';
for(const [channel,label] of [['telegram','Telegram'],['max','MAX'],['desktop',lang==='ru'?'Компьютер':'Computer']]){const button=h('button',label);button.type='button';button.onclick=async()=>{button.disabled=true;try{const rows=channel==='desktop'?[]:await api('/v1/link/channels');connectionGuide.show(channel,{linked:isChannelLinked(rows,channel)});}catch{$('status').textContent=T.error;}finally{button.disabled=false;}};$('connection-options').append(button);}
const autopay=installAutopay({lang,request:api});
const usageChart=installUsageChart({lang,request:api});
const paymentDialog=installPaymentDialog({lang,request:api,getSession:()=>session,fromConsole});
addEventListener('storage',e=>{if(e.key===sessionKey||!e.key){paymentDialog.close();$('private-content').hidden=true;$('identity').textContent='';location.reload();}});
load();

let installOffer=null;window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installOffer=e;});
$('install-app').onclick=async()=>{if(installOffer){await installOffer.prompt();await installOffer.userChoice;installOffer=null;}else{$('install-note').textContent=lang==='en'?'Use your browser menu to install the app or add it to your home screen.':'В меню браузера выберите «Установить приложение» или «На экран Домой».';}};

// Share sign-out/account changes with the site's same-origin account bridge and other tabs.
window.addEventListener('storage',event=>{if(event.key!==sessionKey||!session?.key)return;let next=null;try{next=event.newValue?(sessionKey==='nm.key'?{key:event.newValue}:JSON.parse(event.newValue)):null;}catch{}if(next?.key===session.key)return;if(next?.key){location.reload();return;}location.replace('/app/?signin=1&next='+encodeURIComponent(location.origin+'/web/'));});
