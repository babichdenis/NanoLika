/* Optional account-bound connection guides. QR generation stays in this browser. */
import qrcode from './vendor/qrcode.js';
const COPY={
 ru:{connect:'Подключить',close:'Закрыть',account:'Вы вошли как',intro:'Отсканируйте QR-код камерой телефона или нажмите кнопку ниже. В открывшемся боте нажмите «Начать», если мессенджер попросит. Вводить код не нужно.',expires:'Ссылка действует 10 минут и подходит для одного подключения. Не передавайте QR-код другим людям.',open:'Открыть',check:'Проверить подключение',refresh:'Обновить QR-код',loading:'Готовим ссылку подключения…',expired:'Срок ссылки истёк. Обновите QR-код.',connected:'Подключено. Можно общаться с Ликой в боте.',pending:'Ждём подключения. Откройте бота по QR-коду или кнопке и подтвердите запуск.',error:'Не удалось проверить подключение. Попробуйте ещё раз.',desktop:'компьютер',download:'Скачать приложение',desktopHelp:'Установите Лику и войдите в приложении под той же почтой, что указана здесь. Приложение появится в вашем аккаунте автоматически.',desktopNote:'Для работы с файлами и программами оставьте приложение открытым. Если хотите общаться только в браузере, устанавливать ничего не нужно.',desktopConnected:'Компьютер подключён к вашему аккаунту.',desktopPending:'Приложение пока не на связи. Откройте его и проверьте почту, под которой вошли.',many:'Выберите компьютер.',choose:'Открыть на выбранном компьютере'},
 en:{connect:'Connect',close:'Close',account:'Signed in as',intro:'Scan the QR code with your phone camera or use the button below. If the messenger asks, press Start in the bot. No code entry is needed.',expires:'This one-use link is valid for 10 minutes. Do not share the QR code with anyone else.',open:'Open',check:'Check connection',refresh:'Refresh QR code',loading:'Preparing your connection link…',expired:'This link has expired. Refresh the QR code.',connected:'Connected. You can talk to Lika in the bot.',pending:'Waiting for connection. Open the bot using the QR code or button and confirm Start.',error:'Could not check the connection. Try again.',desktop:'computer',download:'Download the app',desktopHelp:'Install Lika and sign in with the same email shown here. The app will appear in your account automatically.',desktopNote:'Keep the app open for working with files and programs. Nothing needs installing if you only want to use the browser.',desktopConnected:'The computer is connected to your account.',desktopPending:'The app is not online yet. Open it and check the sign-in email.',many:'Choose a computer.',choose:'Open on this computer'}
};
export const BOT_LINKS=Object.freeze({telegram:'https://t.me/nanolika_bot',max:'https://max.ru/id420517592685_bot'});
export const isChannelLinked=(rows,channel)=>Array.isArray(rows)&&rows.some(row=>row.channel===channel);
export function validateStartLink(data,channel){
 const expected=new URL(BOT_LINKS[channel]);const url=new URL(data?.url||'');
 const prefix=channel==='telegram'?'TG':'MX';
 if(data.channel!==channel||url.origin!==expected.origin||url.pathname!==expected.pathname||url.username||url.password||url.hash||[...url.searchParams].length!==1||!new RegExp('^nl_'+prefix+'[A-F0-9]{30}$').test(url.searchParams.get('start')||''))throw new Error('invalid_start_link');
 return url.href;
}
export function qrSvg(url,label){
 const qr=qrcode(0,'M');qr.addData(url,'Byte');qr.make();const size=qr.getModuleCount();
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
 svg.setAttribute('viewBox',`0 0 ${size+8} ${size+8}`);svg.setAttribute('width','232');svg.setAttribute('height','232');svg.setAttribute('role','img');svg.setAttribute('aria-label',label);
 const background=document.createElementNS(ns,'rect');background.setAttribute('width','100%');background.setAttribute('height','100%');background.setAttribute('fill','#fff');svg.append(background);
 const path=document.createElementNS(ns,'path');let d='';for(let row=0;row<size;row++)for(let col=0;col<size;col++)if(qr.isDark(row,col))d+=`M${col+4} ${row+4}h1v1h-1z`;
 path.setAttribute('d',d);path.setAttribute('fill','#253020');path.setAttribute('shape-rendering','crispEdges');svg.append(path);return svg;
}
export class ChannelOnboarding{
 constructor({session,api,language,retry}){Object.assign(this,{session,api,language,retry});const sheet=document.createElement('link');sheet.rel='stylesheet';sheet.href=new URL('./channel-onboarding.css?v=20261026-qr',import.meta.url);document.head.append(sheet);}
 show(channel,{linked=false,targets=[]}={}){
  if(!['telegram','max','desktop'].includes(channel))return;
  this.dialog?.close();this.dialog?.remove();const initial=this.session();if(!initial?.key||!initial.owner)return;const identity={...initial};
  const valid=()=>this.session()?.key===identity.key&&this.session()?.owner===identity.owner;
  const words=COPY[this.language()]||COPY.en,name=channel==='desktop'?words.desktop:channel==='telegram'?'Telegram':'MAX';
  const el=(tag,value,cls)=>{const e=document.createElement(tag);if(value)e.textContent=value;if(cls)e.className=cls;return e;};
  const dialog=el('dialog','','channel-guide');this.dialog=dialog;dialog.setAttribute('aria-labelledby','channel-guide-title');
  const title=el('h2',words.connect+' '+name);title.id='channel-guide-title';
  const close=el('button','×','channel-guide-close');close.type='button';close.setAttribute('aria-label',words.close);close.onclick=()=>dialog.close();
  const address=el('p',words.account,'channel-guide-identity');address.append(el('strong',identity.email||identity.hint||'nanoLika'));
  const status=el('p','','channel-guide-status');status.setAttribute('role','status');const actions=el('div','','channel-guide-actions');
  const button=(label,action)=>{const b=el('button',label);b.type='button';b.onclick=action;return b;};
  const link=(label,href)=>{const a=el('a',label);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;};
  dialog.append(close,title,address);
  let timer=null,deadline=0,checking=false,complete=linked;
  const alive=()=>valid()&&dialog.open;
  const qrBox=el('div','','channel-guide-qr');let botLink=null,refresh=null;
  const stop=()=>{clearInterval(timer);timer=null;};
  const checkConnection=async(manual=false)=>{
   if(!alive()){stop();if(dialog.open)dialog.close();return;}if(checking||complete)return;
   if(channel!=='desktop'&&deadline&&Date.now()>=deadline){stop();qrBox.hidden=true;botLink.hidden=true;refresh.hidden=false;status.textContent=words.expired;return;}
   checking=true;
   try{
    const data=await this.api(channel==='desktop'?'/v1/devices':'/v1/link/channels');if(!alive())return;
    const ready=channel==='desktop'?(data.devices||[]).some(d=>d.online&&d.kind!=='web'&&(d.actions||[]).includes('chat.open_desktop')):isChannelLinked(data,channel);
    if(!ready){if(manual)status.textContent=channel==='desktop'?words.desktopPending:words.pending;return;}
    complete=true;stop();qrBox.hidden=true;if(refresh)refresh.hidden=true;if(botLink)botLink.href=BOT_LINKS[channel];status.textContent=channel==='desktop'?words.desktopConnected:words.connected;this.retry(channel);
   }catch{if(alive()&&manual)status.textContent=words.error;}finally{checking=false;}
  };
  const check=button(words.check,()=>checkConnection(true));
  if(channel==='desktop'){
   dialog.append(el('p',words.desktopHelp));if(targets.length>1){dialog.append(el('p',words.many));const select=el('select');select.setAttribute('aria-label',words.desktop);for(const target of targets){const option=el('option',target.name||target.id);option.value=target.id;select.append(option);}dialog.append(select);actions.append(button(words.choose,()=>{dialog.close();this.retry(channel,select.value);}));}else actions.append(link(words.download,'https://nanolika.ru/download.html#desktop'));
   dialog.append(el('p',words.desktopNote,'channel-guide-note'));actions.append(check);
  }else{
   dialog.append(el('p',linked?words.connected:words.intro));botLink=link(words.open+' '+name,BOT_LINKS[channel]);actions.append(botLink);
   if(!linked){
    botLink.hidden=true;dialog.append(qrBox,el('p',words.expires,'channel-guide-note'));
    const makeLink=async()=>{
     if(!alive())return;stop();refresh.disabled=true;refresh.hidden=true;qrBox.hidden=false;qrBox.replaceChildren();botLink.hidden=true;status.textContent=words.loading;
     try{const data=await this.api('/v1/link/start',{channel});if(!alive())return;const url=validateStartLink(data,channel);deadline=Date.now()+Math.min(600,Math.max(1,Number(data.expires_in_s)||600))*1000;qrBox.append(qrSvg(url,'QR: '+name));botLink.href=url;botLink.hidden=false;status.textContent=words.pending;timer=setInterval(()=>{if(document.visibilityState==='visible')checkConnection();},2500);}catch{if(alive()){status.textContent=words.error;refresh.hidden=false;}}finally{refresh.disabled=false;}
    };
    refresh=button(words.refresh,makeLink);refresh.hidden=true;dialog.append(refresh);actions.append(check);
    dialog.addEventListener('close',stop,{once:true});dialog.addEventListener('ready',makeLink,{once:true});
   }
  }
  dialog.append(actions,status);dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});
  dialog.addEventListener('close',()=>{stop();dialog.remove();},{once:true});document.body.append(dialog);dialog.showModal();dialog.dispatchEvent(new Event('ready'));
 }
}
