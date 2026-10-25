/* Account-bound connection guide. No model calls or automatic account transfers. */
const COPY = {
 ru: {
  connect: 'Подключить', close: 'Закрыть', account: 'Сейчас вы вошли как',
  intro: 'Чтобы продолжить эту же беседу, подключите канал к этому аккаунту. Тогда история и баланс будут общими.',
  codeAction: 'Получить код подключения',
  codeHelp: 'Откройте бота и отправьте этот код отдельным сообщением. Затем вернитесь сюда и проверьте подключение.',
  expires: 'Код действует 10 минут и подходит для одного подключения. Не передавайте его другим людям.',
  copy: 'Скопировать код', copied: 'Код скопирован', open: 'Открыть бота', check: 'Проверить подключение',
  desktopPending: 'Приложение пока не на связи. Откройте его и проверьте почту, под которой вошли.',
  pending: 'Подключение пока не появилось. Отправьте код боту отдельным сообщением, дождитесь подтверждения и повторите проверку.',
  error: 'Не удалось проверить подключение. Попробуйте ещё раз.',
  desktop: 'компьютер', download: 'Скачать приложение',
  desktopHelp: 'Установите Лику и войдите в приложении под той же почтой, что указана здесь. Оставьте приложение открытым и нажмите «Проверить подключение».',
  desktopNote: 'Открытая веб-страница сама по себе не подключает компьютер к работе с файлами и программами.',
  linked: 'Этот канал уже подключён к вашему аккаунту. Можно общаться с Ликой в боте.',
  many: 'Найдено несколько компьютеров. Выберите, где открыть беседу.', choose: 'Открыть на выбранном компьютере',
  none: 'Компьютер пока не подключён к этому аккаунту.'
 },
 en: {
  connect: 'Connect', close: 'Close', account: 'You are signed in as',
  intro: 'Connect this channel to this account to continue the same conversation with shared history and balance.',
  codeAction: 'Get a connection code',
  codeHelp: 'Open the bot and send this code as a separate message. Then return here to check the connection.',
  expires: 'Valid for 10 minutes and one connection. Do not share it with anyone else.',
  copy: 'Copy code', copied: 'Code copied', open: 'Open bot', check: 'Check connection',
  desktopPending: 'The desktop app is not online yet. Open it and check the email used to sign in.',
  pending: 'Not connected yet. Send the code as a separate message, wait for confirmation, then check again.',
  error: 'Could not check the connection. Try again.', desktop: 'computer', download: 'Download the app',
  desktopHelp: 'Install Lika and sign in with the same email shown here. Keep the app open, then choose Check connection.',
  desktopNote: 'An open browser page does not connect your computer for working with files and programs.',
  linked: 'This channel is connected to your account. You can talk to Lika in the bot.',
  many: 'More than one computer is available. Choose where to open the conversation.', choose: 'Open on this computer',
  none: 'No computer is connected to this account yet.'
 }
};
export const BOT_LINKS = Object.freeze({telegram:'https://t.me/nanolika_bot',max:'https://max.ru/id420517592685_bot'});
export function isChannelLinked(rows, channel) {
 return Array.isArray(rows) && rows.some(row => row.channel === channel);
}
export class ChannelOnboarding {
 constructor({session, api, language, retry}) {
  Object.assign(this,{session,api,language,retry});
  const sheet=document.createElement('link');sheet.rel='stylesheet';sheet.href=new URL('./channel-onboarding.css',import.meta.url);document.head.append(sheet);
 }
 show(channel,{linked=false,targets=[]}={}) {
  this.dialog?.close();this.dialog?.remove();
  const initial=this.session();if(!initial?.key||!initial.owner)return;
  const identity={...initial};
  const valid=()=>this.session()?.key===identity.key&&this.session()?.owner===identity.owner;
  const words=COPY[this.language()]||COPY.en,name=channel==='desktop'?words.desktop:channel==='telegram'?'Telegram':'MAX';
  const dialog=document.createElement('dialog');this.dialog=dialog;dialog.className='channel-guide';dialog.setAttribute('aria-labelledby','channel-guide-title');
  const el=(tag,value,cls)=>{const e=document.createElement(tag);if(value)e.textContent=value;if(cls)e.className=cls;return e;};
  const title=el('h2',words.connect+' '+name);title.id='channel-guide-title';
  const close=el('button','×','channel-guide-close');close.type='button';close.setAttribute('aria-label',words.close);close.onclick=()=>dialog.close();
  const address=el('p',words.account,'channel-guide-identity');address.append(el('strong',identity.email||identity.hint||'nanoLika'));
  const status=el('p','','channel-guide-status');status.setAttribute('role','status');
  const actions=el('div','','channel-guide-actions');
  const button=(label,action)=>{const b=el('button',label);b.type='button';b.onclick=action;return b;};
  const link=(label,href)=>{const a=el('a',label);a.href=href;a.target='_blank';a.rel='noopener noreferrer';return a;};
  const guarded=async(b,action)=>{if(!valid()){dialog.close();return;}b.disabled=true;status.textContent='';try{await action();}catch{if(valid()&&dialog.open)status.textContent=words.error;}finally{b.disabled=false;}};
  dialog.append(close,title,address);
  if(channel==='desktop') {
   dialog.append(el('p',words.desktopHelp));if(targets.length>1)dialog.append(el('p',words.many));
   if(targets.length>1){const select=el('select');select.setAttribute('aria-label',words.desktop);for(const target of targets){const option=el('option',target.name||target.id);option.value=target.id;select.append(option);}dialog.append(select);actions.append(button(words.choose,()=>{dialog.close();this.retry(channel,select.value);}));}
   else actions.append(link(words.download,'https://nanolika.ru/download.html#desktop'));
   dialog.append(el('p',words.desktopNote,'channel-guide-note'));
  } else {
   dialog.append(el('p',linked?words.linked:words.intro));
   if(!linked){const codeArea=el('div','','channel-guide-code-area');const create=button(words.codeAction,()=>guarded(create,async()=>{
     const data=await this.api('/v1/link/code',{});if(!valid()||!dialog.open)return;
     if(!/^[A-Z2-9]{6}$/.test(data?.code||''))throw new Error('invalid_code');
     const code=el('code',data.code,'channel-guide-code');
     const copy=button(words.copy,async()=>{try{await navigator.clipboard.writeText(data.code);status.textContent=words.copied;}catch{const selection=getSelection();const range=document.createRange();range.selectNodeContents(code);selection.removeAllRanges();selection.addRange(range);}});
     codeArea.replaceChildren(el('p',words.codeHelp),code,copy,el('p',words.expires,'channel-guide-note'));create.hidden=true;
    }));dialog.append(create,codeArea);}
   actions.append(link(words.open,BOT_LINKS[channel]));
  }
  const check=button(words.check,()=>guarded(check,async()=>{
   if(channel!=='desktop') {const rows=await this.api('/v1/link/channels');if(!valid()||!dialog.open)return;if(!isChannelLinked(rows,channel)){status.textContent=words.pending;return;}}
   if(channel==='desktop'){const data=await this.api('/v1/devices');if(!valid()||!dialog.open)return;if(!(data.devices||[]).some(d=>d.online&&d.kind!=='web'&&(d.actions||[]).includes('chat.open_desktop'))){status.textContent=words.desktopPending;return;}}
   if(!valid()||!dialog.open)return;dialog.close();this.retry(channel);
  }));actions.append(check);dialog.append(actions,status);
  dialog.addEventListener('click',e=>{if(e.target!==dialog)return;const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();});
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();
 }
}
