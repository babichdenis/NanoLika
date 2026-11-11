import {fundsView} from './funds.js';
import {navigationTools,performNavigation} from './navigation-tool.js';
import {documentTools,performDocument} from './document-tool.js';
import {ChatSync} from './chat-sync.js?v=20261111-usage';
import {ConversationOpener} from './conversation-open.js';
import {text,locales} from './i18n.js?v=20261103-create1';
import * as db from './storage.js';
import {readReply} from './stream.js?v=20261105-tools';
import {installComposerControls} from './composer-controls.js?v=20261102-reasoning';
import {installModelSheet} from './model-sheet.js?v=20261030-remote';
import {setAvatarMood,refreshAvatarImages} from './brand-avatar.js';
// The entry bridge names the channel; a plain /web/ visit is just Web.
try {
  const entered = new URLSearchParams(location.search).get('channel');
  localStorage.setItem('nl.web.channel', entered === 'telegram' || entered === 'max' ? entered : 'web');
} catch {}

const $=id=>document.getElementById(id);const sessionKey='nl.web.session.v1';
const local={get(key){try{return localStorage.getItem(key);}catch{return null;}},set(key,value){try{localStorage.setItem(key,value);}catch{notice(t('storageBlocked'));}},remove(key){try{localStorage.removeItem(key);}catch{}}};
let lang=local.get('nl.web.language')||'ru';if(!locales[lang])lang='ru';
let session=null;try{session=JSON.parse(local.get(sessionKey)||'null');}catch{}
let sending=false,opening=false,epoch=0;
let chats=[],current=null,controller=null,authMode='code',codeSent=false,installPrompt=null,model=null;
const t=key=>text(lang,key);const authenticated=()=>!!session?.key&&!!session?.owner;
function renderAccount(){const address=session?.email||session?.hint||'Аккаунт';$('account-email').textContent=address;$('account').title=address;$('account').setAttribute('aria-label','Меню аккаунта: '+address);}
function closeSidebar(){document.body.classList.remove('sidebar-open');$('sidebar-toggle').setAttribute('aria-expanded','false');}
function setCollapsed(on){document.body.classList.toggle('sidebar-collapsed',on);local.set('nl.web.sidebar-collapsed',on?'1':'0');$('sidebar-collapse').setAttribute('aria-expanded',String(!on));closeAccountMenu();}
if(local.get('nl.web.sidebar-collapsed')==='1')document.body.classList.add('sidebar-collapsed');
$('sidebar-toggle').onclick=()=>{if(matchMedia('(min-width:761px)').matches){setCollapsed(false);return;}const open=document.body.classList.toggle('sidebar-open');$('sidebar-toggle').setAttribute('aria-expanded',String(open));};
$('sidebar-collapse').onclick=()=>{if(matchMedia('(max-width:760px)').matches)closeSidebar();else setCollapsed(!document.body.classList.contains('sidebar-collapsed'));};
$('sidebar-backdrop').onclick=closeSidebar;document.addEventListener('keydown',e=>{if(e.key==='Escape'&&document.body.classList.contains('sidebar-open')&&$('account-menu').hidden){closeSidebar();$('sidebar-toggle').focus();}});
function notice(message){$('notice').textContent=message||'';$('notice').hidden=!message;}
function fundsNotice(error){const view=fundsView(error,lang);if(!view)return false;notice(view.message);const link=document.createElement('a');link.href=view.topupUrl;link.textContent=view.topupLabel;link.target='_blank';link.rel='noopener noreferrer';link.style.textDecoration='underline';$('notice').append(' ',link);return true;}
function translate(){document.documentElement.lang=lang;document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.dataset.i18n));$('message').placeholder=t('prompt');updateStatus();updateAuth();renderChats();renderConversation();renderAccount();$('export').title=t('export');$('export').setAttribute('aria-label',t('export'));if($('attach')){$('attach').title=t('attach');$('attach').setAttribute('aria-label',t('attach'));}if($('send')){$('send').title=t('send');$('send').setAttribute('aria-label',t('send'));}if($('stop')){$('stop').title=t('stop');$('stop').setAttribute('aria-label',t('stop'));}}
function updateStatus(){composerControls.setBusy(!!controller||sending);setAvatarMood(controller?'working':'idle');$('status').textContent=t(controller?'working':'ready');$('offline').hidden=navigator.onLine;}
function updateAuth(){$('code-label').hidden=authMode!=='code'||!codeSent;$('password-label').hidden=authMode!=='password';$('code').required=authMode==='code'&&codeSent;$('password').required=authMode==='password';$('auth-submit').textContent=t(authMode==='code'&&!codeSent?'sendCode':'signin');$('auth-mode').textContent=t(authMode==='code'?'usePassword':'useCode');}
function showAuth(){location.href='/app/?signin=1&next='+encodeURIComponent(location.origin+location.pathname+location.search);}
/* Два вида вызова: api(path, body) — POST с телом, и api(path, {method, body}) — как
   зовут общие компоненты (пикер, лист моделей): там нужен PUT, а старый помощник
   всегда слал POST и молча не сохранял избранное. */
async function api(path,body){const opts=body&&typeof body==='object'&&typeof body.method==='string'?body:null;const method=opts?String(opts.method).toUpperCase():(body?'POST':'GET');let payload=opts?(typeof opts.body==='string'?(opts.body?JSON.parse(opts.body):null):opts.body):body;const r=await fetch(path,{method,cache:'no-store',headers:{...(payload?{'Content-Type':'application/json'}:{}),...(session?.key?{Authorization:'Bearer '+session.key}:{})},...(payload?{body:JSON.stringify(payload)}:{})});const data=r.status===204?null:await r.json();if(!r.ok){const e=new Error(data?.error?.message||t('requestError'));e.code=data?.error?.code;e.status=r.status;throw e;}return data;}
async function loadChats(){if(!authenticated()){chats=[];current=null;renderChats();return;}try{chats=(await db.list('chats',session.owner)).sort((a,b)=>b.updated-a.updated);if(current?.owner!==session.owner)current=null;renderChats();}catch{notice(t('storageBlocked'));}}
async function persist(chat){try{await db.save('chats',chat);if(session?.owner===chat.owner){const i=chats.findIndex(c=>c.key===chat.key);if(i<0)chats.unshift(chat);else chats[i]=chat;chats.sort((a,b)=>b.updated-a.updated);renderChats();}}catch(e){console.error('persist failed',e);notice(t('storageError'));}}
let chatListSignature='';
let chatQuery='',archivedView=false,selectingChats=false,selectedChats=new Set(),bulkBusy=false;
const pinnedKey=()=> 'nl.web.pins.'+(session?.owner||'');
function pinnedChats(){try{return new Set(JSON.parse(local.get(pinnedKey())||'[]'));}catch{return new Set();}}
function pinId(chat){return chat.key;}
function savePins(pins){local.set(pinnedKey(),JSON.stringify([...pins]));chatListSignature='';renderChats();}
function visibleChats(){const pins=pinnedChats(),q=chatQuery.trim().toLocaleLowerCase();return chats.filter(chat=>Boolean(chat.archived)===archivedView&&(!q||(chat.title||'').toLocaleLowerCase().includes(q)||chat.messages.some(m=>(m.content||'').toLocaleLowerCase().includes(q)))).sort((a,b)=>Number(pins.has(pinId(b)))-Number(pins.has(pinId(a)))||b.updated-a.updated||a.key.localeCompare(b.key));}
/* The folder a chat works in: the workspace title from the computer, Telegram, MAX or
   Web. Older synced chats without the field fall back to the entrance they came from. */
function folderOf(chat){const sent=String(chat.folder||'').trim();if(sent)return sent;const own=chatSync.channel==='telegram'?'Telegram':chatSync.channel==='max'?'MAX':t('folderWeb');if(chat.device){const dev=String(chat.device);if(chat.device==='Telegram'||dev.startsWith('tg-'))return 'Telegram';if(chat.device==='MAX'||dev.startsWith('max-'))return 'MAX';if(dev.startsWith('web-'))return t('folderWeb');if(chat.device!==chatSync.device)return t('folderShared');}return own;}
function folderRank(name){if(name===t('folderWeb'))return 3;if(name===t('folderShared'))return 4;if(name==='Telegram')return 1;if(name==='MAX')return 2;return 0;}
function uiIcon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');for(const d of ({pin:['m9 3 6 0-1 6 4 4H6l4-4-1-6ZM12 13v8'],trash:['M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6m4-6v6'],check:['m5 12 4 4L19 6'],cloud:['M2.25 15a4.5 4.5 0 0 0 4.5 4.5H18a3.75 3.75 0 0 0 1.332-7.257 3 3 0 0 0-3.758-3.848 5.25 5.25 0 0 0-10.233 2.33A4.502 4.502 0 0 0 2.25 15z'],file:['M6 3h8l4 4v14H6z','M14 3v4h4'],paperclip:['M20 11.5 12.4 19a5 5 0 0 1-7.1-7.1l7.4-7.4a3.3 3.3 0 0 1 4.7 4.7l-7.4 7.4a1.7 1.7 0 0 1-2.4-2.4l6.8-6.8'],archive:['M3 4h18v4H3z','M5 8v12h14V8','M9 12h6'],restore:['M3 4h18v4H3z','M5 8v12h14V8','M12 11v6m-3-3 3-3 3 3'],cloudOff:['M2.25 15a4.5 4.5 0 0 0 4.5 4.5H18a3.75 3.75 0 0 0 1.332-7.257 3 3 0 0 0-3.758-3.848 5.25 5.25 0 0 0-10.233 2.33A4.502 4.502 0 0 0 2.25 15z','M3.5 3.5l17 17']})[name]){const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',d);svg.append(path);}return svg;}
/* A chat's cloud: kept on the relay and shown on every device, or kept only in this browser. */
function cloudState(chat){if(chat.local===true)return 'local';if(!chat.syncCid)return 'mine';return chat.device&&chat.device===chatSync.device?'mine':'synced';}
function cloudMark(state){const mark=document.createElement('span');mark.className='chat-cloud-mark'+(state==='local'?' is-local':'');mark.title=state==='local'?t('onlyOnDevice'):t('inCloud');mark.setAttribute('role','img');mark.setAttribute('aria-label',mark.title);mark.append(uiIcon(state==='local'?'cloudOff':'cloud'));return mark;}
/* The relay's refusal in our own words: a machine reason names the message, anything else is the plain sync error. */
function syncReason(error){const key={sync_off:'cloudOffChats',conversation_limit:'cloudLimit',no_conversation:'cloudMissing',chat_missing:'cloudMissing',unknown_cid:'cloudMissing',conversation_deleted:'cloudMissing',incomplete_local_text:'cloudIncomplete'}[error?.code||''];
 return key?t(key):t('syncError');}
function setChatSelection(on){selectingChats=on;selectedChats.clear();chatListSignature='';renderChats();}
function renderBulk(){const keys=new Set(chats.map(c=>c.key));for(const key of selectedChats)if(!keys.has(key))selectedChats.delete(key);$('bulk-tools').hidden=!selectingChats;$('bulk-count').textContent=selectedChats.size+' выбрано';$('select-chats').setAttribute('aria-pressed',String(selectingChats));for(const name of ['bulk-pin','bulk-unpin','bulk-delete'])$(name).disabled=!selectedChats.size||bulkBusy||!!controller;$('bulk-all').disabled=bulkBusy;}
$('chat-search').oninput=e=>{chatQuery=e.target.value;chatListSignature='';renderChats();};
$('search-toggle').onclick=()=>{document.body.classList.remove('sidebar-collapsed');local.set('nl.web.sidebar-collapsed','0');$('sidebar-collapse').setAttribute('aria-expanded','true');$('chat-search').focus();};
$('select-chats').onclick=()=>setChatSelection(!selectingChats);
$('bulk-cancel').onclick=()=>setChatSelection(false);
$('bulk-all').onclick=()=>{const rows=visibleChats(),all=rows.every(c=>selectedChats.has(c.key));for(const c of rows)all?selectedChats.delete(c.key):selectedChats.add(c.key);chatListSignature='';renderChats();};
for(const [id,on] of [['bulk-pin',true],['bulk-unpin',false]])$(id).onclick=()=>{const pins=pinnedChats();for(const c of chats.filter(c=>selectedChats.has(c.key)))on?pins.add(pinId(c)):pins.delete(pinId(c));savePins(pins);};
$('bulk-delete').onclick=async()=>{if(bulkBusy||controller||!selectedChats.size)return;const rows=chats.filter(c=>selectedChats.has(c.key));if(!confirm('Удалить выбранные беседы ('+rows.length+')? Они исчезнут на всех устройствах.'))return;bulkBusy=true;renderBulk();try{for(const chat of rows){await chatSync.delete(chat);selectedChats.delete(chat.key);if(current?.key===chat.key)current=null;}await loadChats();renderConversation();}catch{notice(t('syncError'));await loadChats();}finally{bulkBusy=false;renderBulk();}};
function renderChats(){const list=$('chat-list'),pins=pinnedChats();const signature=JSON.stringify([lang,current?.key,chatQuery,archivedView,selectingChats,[...selectedChats],[...pins],chats.map(c=>[c.key,c.title,c.unread||0,c.updated,c.messages.length,c.syncCid||'',c.local?'local':'',c.device||'',c.folder||'',c.archived?'archived':''])]);if(signature===chatListSignature){renderBulk();return;}chatListSignature=signature;const scroll=list.scrollTop;list.replaceChildren();const rows=visibleChats();if(!rows.length){const p=document.createElement('p');p.className='hint';p.textContent=chatQuery?'Беседы не найдены':t('empty');list.append(p);}rows.sort((a,b)=>{const fa=folderOf(a),fb=folderOf(b);return fa===fb?0:folderRank(fa)-folderRank(fb)||fa.localeCompare(fb);});let lastFolder='';for(let chat of rows){const folder=folderOf(chat);if(folder!==lastFolder){lastFolder=folder;const head=document.createElement('div');head.className='chat-folder';head.textContent=folder;list.append(head);}const row=document.createElement('div');row.className='chat-row'+(current?.key===chat.key?' selected':'')+(pins.has(pinId(chat))?' is-pinned':'');
 if(selectingChats){const check=document.createElement('input');check.type='checkbox';check.className='chat-check';check.checked=selectedChats.has(chat.key);check.disabled=bulkBusy;check.setAttribute('aria-label','Выбрать: '+(chat.title||t('untitled')));check.onchange=()=>{check.checked?selectedChats.add(chat.key):selectedChats.delete(chat.key);chatListSignature='';renderChats();};row.append(check);}
 const open=document.createElement('button');open.className='chat-open';open.title=chat.title||t('untitled');const title=document.createElement('span');title.className='chat-title';title.textContent=chat.title||t('untitled');open.append(cloudMark(cloudState(chat)),title);open.onclick=async()=>{if(selectingChats){selectedChats.has(chat.key)?selectedChats.delete(chat.key):selectedChats.add(chat.key);chatListSignature='';renderChats();return;}const chosen=chats.find(c=>c.key===chat.key);if(!chosen)return;chat=chosen;current=chat;applyChatModel(chat);showSection('chats');renderChats();renderConversation();opening=true;try{await chatSync.open(chat);}catch{notice(t('syncError'));}finally{opening=false;}};
 if(current?.key!==chat.key&&chat.unread>0){const badge=document.createElement('span');badge.className='chat-unread';badge.textContent=chat.unread>99?'99+':String(chat.unread);open.append(badge);}row.append(open);
 const pin=document.createElement('button');pin.className='chat-pin icon-button';pin.title=pins.has(pinId(chat))?'Открепить беседу':'Закрепить беседу';pin.setAttribute('aria-label',pin.title+': '+(chat.title||t('untitled')));pin.setAttribute('aria-pressed',String(pins.has(pinId(chat))));pin.append(uiIcon('pin'));pin.onclick=()=>{const p=pinnedChats();p.has(pinId(chat))?p.delete(pinId(chat)):p.add(pinId(chat));savePins(p);};
 const del=document.createElement('button');del.className='chat-delete icon-button';del.title=t('deleteChat');del.setAttribute('aria-label',t('deleteChat')+': '+(chat.title||t('untitled')));del.append(uiIcon('trash'));del.disabled=!!controller;del.onclick=async()=>{if(controller||!confirm(t('deleteChatConfirm')))return;try{await chatSync.delete(chat);if(current?.key===chat.key)current=null;await loadChats();renderConversation();}catch{notice(t('syncError'));}};const archive=document.createElement('button');archive.className='chat-archive icon-button';archive.title=archivedView?'Восстановить беседу':'В архив';archive.setAttribute('aria-label',archive.title+': '+(chat.title||t('untitled')));archive.append(uiIcon(archivedView?'restore':'archive'));archive.disabled=!!controller||bulkBusy;archive.onclick=async()=>{try{await chatSync.setArchived(chat,!archivedView);notice(archivedView?'Беседа восстановлена.':'Беседа перемещена в архив.');chatListSignature='';renderChats();}catch{notice(t('syncError'));}};if(!selectingChats)row.append(pin,cloudAction(chat),archive,del);list.append(row);}list.scrollTop=scroll;renderBulk();}
/* The cloud action of one row: a chat kept here alone goes up; a chat in the cloud comes
   down again with its text kept in this browser. The trash button is the only full delete. */
function cloudAction(chat){const cloud=cloudState(chat),label=cloud==='local'?t('toCloud'):cloud==='mine'?t('fromCloud'):t('deleteFromCloud');
 const button=document.createElement('button');button.type='button';button.className='chat-cloud-action icon-button';button.disabled=!!controller||bulkBusy;
 button.title=cloud==='local'?label:t('cloudOffHint');button.setAttribute('aria-label',label+': '+(chat.title||t('untitled')));button.append(uiIcon(cloud==='local'?'cloud':'cloudOff'));
 button.onclick=async()=>{
  if(cloud!=='local'&&!confirm(t('leaveCloudConfirm')))return;
  button.disabled=true;
  try{await (cloud==='local'?chatSync.sync(chat):chatSync.unsync(chat));chatListSignature='';await loadChats();renderChats();notice(cloud==='local'?t('chatInCloud'):t('chatLeftCloud'));}
  catch(error){notice(syncReason(error));}
  if(current?.key===chat.key)renderConversation();
  chatListSignature='';renderChats();
 };
 return button;}

function renderInline(root,value){
 const text=String(value),pattern=/!\[([^\]]*)\]\((https?:\/\/[^)\s]+)\)|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\x60([^\x60]+)\x60|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*]+)\*/g;
 let cursor=0,match;
 while((match=pattern.exec(text))){
  root.append(document.createTextNode(text.slice(cursor,match.index)));
  if(match[2]){try{const url=new URL(match[2]);if(url.protocol==='https:'||url.protocol==='http:'){const img=document.createElement('img');img.className='message-image';img.src=url.href;img.alt=match[1]||'';img.loading='lazy';img.tabIndex=0;img.title=t('openFull');img.onclick=()=>openImage(url.href);img.onkeydown=event=>{const pressed=event['key'];if(pressed==='Enter'||pressed===' '){event.preventDefault();openImage(url.href);}};root.append(img);}else root.append(document.createTextNode(match[1]));}catch{root.append(document.createTextNode(match[1]));}}
  else if(match[4]){try{const url=new URL(match[4]);if(url.protocol==='https:'||url.protocol==='http:'){const a=document.createElement('a');a.href=url.href;a.rel='noopener noreferrer';a.target='_blank';a.textContent=match[3];root.append(a);}else root.append(document.createTextNode(match[3]));}catch{root.append(document.createTextNode(match[3]));}}
  else if(match[5]){const code=document.createElement('code');code.textContent=match[5];root.append(code);}
  else{const el=document.createElement(match[6]||match[7]?'strong':'em');el.textContent=match[6]||match[7]||match[8];root.append(el);}
  cursor=pattern.lastIndex;
 }
 root.append(document.createTextNode(text.slice(cursor)));
}
function renderSafeHtml(root, nodes){
 const blockTags=new Set(['p','div','section','article','blockquote','h1','h2','h3','h4','h5','h6','ul','ol','li','pre']);
 const inlineTags={b:'strong',strong:'strong',i:'em',em:'em',code:'code',del:'del',s:'del'};
 const copy=(node,parent)=>{
  if(node.nodeType===Node.TEXT_NODE){parent.append(document.createTextNode(node.nodeValue||''));return;}
  if(node.nodeType!==Node.ELEMENT_NODE)return;
  const tag=node.tagName.toLowerCase();
  if(tag==='br'){parent.append(document.createElement('br'));return;}
  if(tag==='a'){
   const href=node.getAttribute('href')||'';
   if(/^https?:\/\//i.test(href)){const a=document.createElement('a');a.href=href;a.target='_blank';a.rel='noopener noreferrer';copyChildren(node,a);parent.append(a);return;}
  }
  const name=inlineTags[tag]||(blockTags.has(tag)?(tag.startsWith('h')?'h3':tag==='section'||tag==='article'||tag==='div'?'p':tag):'');
  if(!name){copyChildren(node,parent);return;}
  let target=parent;
  if(tag==='li'&&!['ul','ol'].includes(parent.tagName?.toLowerCase())){target=document.createElement('ul');parent.append(target);}
  const el=document.createElement(name);copyChildren(node,el);target.append(el);
 };
 const copyChildren=(node,parent)=>{for(const child of node.childNodes)copy(child,parent);};
 for(const node of nodes)copy(node,root);
}
function renderMarkdown(root,value){
 const raw=String(value);
 if(/<\/?(?:p|div|section|article|br|strong|b|em|i|ul|ol|li|h[1-6]|blockquote|pre|code|a)\b/i.test(raw)){
  const parsed=new DOMParser().parseFromString(raw,'text/html');renderSafeHtml(root,parsed.body.childNodes);return;
 }
 const lines=raw.replace(/\r/g,'').split('\n');let inCode=false,code=null,list=null;
 for(const line of lines){
  if(/^\x60\x60\x60/.test(line)){if(!inCode){inCode=true;code=document.createElement('pre');root.append(code);}else{inCode=false;code=null;}list=null;continue;}
  if(inCode){code.append(document.createTextNode(line+'\n'));continue;}
  const heading=line.match(/^#{1,3}\s+(.+)$/);if(heading){list=null;const el=document.createElement('h3');renderInline(el,heading[1]);root.append(el);continue;}
  if(/^\s*[-*]\s+/.test(line)){if(!list){list=document.createElement('ul');root.append(list);}const li=document.createElement('li');renderInline(li,line.replace(/^\s*[-*]\s+/,''));list.append(li);continue;}
  list=null;if(!line.trim()){root.append(document.createElement('br'));continue;}
  const p=document.createElement('p');renderInline(p,line);root.append(p);
 }
}
function renderContent(root,value){
 const raw=String(value).trim();const fenced=raw.match(/^\x60\x60\x60json\s*([\s\S]*?)\s*\x60\x60\x60$/i);
 if(fenced){try{const posts=JSON.parse(fenced[1]);if(Array.isArray(posts)&&posts.length&&posts.every(x=>x&&typeof x.title==='string'&&typeof x.body==='string')){for(const post of posts){const card=document.createElement('section');card.className='feed-card';const h=document.createElement('h3');renderInline(h,post.emoji?post.emoji+' '+post.title:post.title);card.append(h);renderMarkdown(card,post.body);root.append(card);}return;}}catch{}}
 renderMarkdown(root,value);
}
function openImage(src){
 let box=document.getElementById('image-lightbox');
 if(!box){box=document.createElement('div');box.id='image-lightbox';box.className='image-lightbox';box.setAttribute('role','dialog');box.setAttribute('aria-modal','true');box.onclick=event=>{if(event.target===box)closeImage();};const pic=document.createElement('img');pic.className='image-lightbox-pic';pic.alt='';const bar=document.createElement('div');bar.className='image-lightbox-bar';const download=document.createElement('a');download.className='image-lightbox-button';download.textContent=t('download');download.download='lika-picture.jpg';const close=document.createElement('button');close.type='button';close.className='image-lightbox-button';close.textContent=t('close');close.onclick=closeImage;bar.append(download,close);box.append(pic,bar);document.body.append(box);document.addEventListener('keydown',imageEscape);}
 const pic=box.querySelector('.image-lightbox-pic');pic.src=src;const link=box.querySelector('a');link.href=src;const tail=(src.split('?')[0].split('/').pop()||'');try{link.download=decodeURIComponent(tail)||'picture.jpg';}catch{link.download=tail||'picture.jpg';}box.hidden=false;document.body.classList.add('lightbox-open');
}
function closeImage(){const box=document.getElementById('image-lightbox');if(box)box.hidden=true;document.body.classList.remove('lightbox-open');}
function imageEscape(event){if(event['key']==='Escape')closeImage();}
/* ── создание: картинка, клип или звук — вид, модель, размер и цена до отправки ── */
let createMode=null;
const createChoice={image:{model:'',size:'1024x1024'},video:{model:'',seconds:5,resolution:'720P'},audio:{model:'',kind:'music'}};
let createCatalogue=null,createPanel=null,createTimer=null;
const CREATE_SIZES=[['1024x1024','createSizeSquare'],['1536x1024','createSizeWide'],['1024x1536','createSizeTall'],['2048x2048','createSize2k']];
const CREATE_SECONDS=[5,10],CREATE_RES=['480P','720P','1080P'];
function createUsable(m,kind){return !!m&&m.kind===kind&&m.available!==false&&m.plan_locked!==true;}
async function createInfo(force){
 if(createCatalogue&&!force)return createCatalogue;
 createCatalogue=await fetch('/v1/me/models',{headers:{Authorization:'Bearer '+session.key}}).then(r=>r.json()).catch(()=>({}));
 return createCatalogue||{};
}
function createList(info,kind){
 let models=(info.models||[]).filter(m=>createUsable(m,kind));
 if(kind==='audio'){const wanted=createChoice.audio.kind;models=models.filter(m=>(((m.audio&&m.audio.kind)||m.audio_kind||'speech')===wanted));}
 const shelf=info.shelf||{},starred=new Set([...(shelf.favourites||[]),(shelf.default||{})[kind]].filter(Boolean));
 return models.sort((a,b)=>(starred.has(b.id)?1:0)-(starred.has(a.id)?1:0));
}
function createModel(kind,info){
 const list=createList(info,kind),choice=createChoice[kind];
 if(choice.model&&list.some(m=>m.id===choice.model))return choice.model;
 const preferred=(info.shelf?.default||{})[kind];
 return list.some(m=>m.id===preferred)?preferred:(list[0]?.id||'');
}
function createChip(){
 const chip=document.getElementById('create-chip');
 if(!chip)return;
 if(!createMode){chip.hidden=true;chip.replaceChildren();return;}
 const choice=createChoice[createMode];
 const where=createMode==='image'?t('createImage'):createMode==='video'?t('createVideo'):t('createAudio');
 const detail=createMode==='image'?choice.size.replace('x','×'):createMode==='video'?`${choice.seconds} s · ${choice.resolution}`:(choice.kind==='music'?t('createMusic'):t('createSpeech'));
 const label=document.createElement('span');label.textContent=where+' · '+detail;
 const off=document.createElement('button');off.type='button';off.className='create-chip-off';off.setAttribute('aria-label',t('createCancel'));off.textContent='×';
 off.onclick=()=>{createMode=null;createChip();$('message').placeholder=t('prompt');$('message').focus();};
 chip.replaceChildren(label,off);chip.hidden=false;
}
function createPanelElement(){
 if(createPanel)return createPanel;
 const panel=document.createElement('div');panel.id='create-panel';panel.className='create-panel';panel.hidden=true;panel.setAttribute('role','dialog');panel.setAttribute('aria-label',t('createTitle'));
 const tabs=document.createElement('div');tabs.className='create-tabs';tabs.setAttribute('role','tablist');
 for(const kind of ['image','video','audio']){
  const tab=document.createElement('button');tab.type='button';tab.dataset.create=kind;tab.setAttribute('role','tab');
  tab.textContent=t(kind==='image'?'createImage':kind==='video'?'createVideo':'createAudio');
  tab.onclick=()=>{createMode=kind;renderCreate();};
  tabs.append(tab);
 }
 const row=document.createElement('label');row.className='create-row';
 const rowName=document.createElement('span');rowName.textContent=t('createModel');
 const select=document.createElement('select');select.id='create-model';
 select.onchange=()=>{if(createMode)createChoice[createMode].model=select.value;priceCreate();};
 row.append(rowName,select);
 const options=document.createElement('div');options.id='create-options';options.className='create-options';
 const foot=document.createElement('div');foot.className='create-foot';
 const price=document.createElement('span');price.id='create-price';price.className='create-price';
 const go=document.createElement('button');go.type='button';go.id='create-go';go.className='create-go';go.textContent=t('createGo');
 go.onclick=()=>{if(!createMode)return;const info=createCatalogue||{};createChoice[createMode].model=createModel(createMode,info);if(createPanel)createPanel.hidden=true;const placeholder=createMode==='image'?t('drawPlaceholder'):createMode==='video'?t('createVideoPlaceholder'):t('createAudioPlaceholder');$('message').placeholder=placeholder;createChip();$('message').focus();};
 foot.append(price,go);
 panel.append(tabs,row,options,foot);
 document.body.append(panel);
 createPanel=panel;
 return panel;
}
function createOption(labelText,values,current,onChange){
 const row=document.createElement('label');row.className='create-row';
 const name=document.createElement('span');name.textContent=labelText;
 const select=document.createElement('select');
 for(const value of values){
  const option=document.createElement('option');option.value=value;option.textContent=value;
  select.append(option);
 }
 select.value=String(current);
 select.onchange=()=>{onChange(select.value);priceCreate();};
 row.append(name,select);
 return row;
}
function renderCreate(){
 const panel=createPanelElement();
 panel.hidden=false;
 for(const tab of panel.querySelectorAll('.create-tabs button'))tab.setAttribute('aria-selected',String(tab.dataset.create===createMode));
 createInfo().then(info=>{
  const list=createList(info,createMode);
  const select=panel.querySelector('#create-model');
  select.replaceChildren();
  if(!list.length){const option=document.createElement('option');option.value='';option.textContent=t('createNoModel');select.append(option);}
  for(const model of list){const option=document.createElement('option');option.value=model.id;option.textContent=model.name||model.id;select.append(option);}
  createChoice[createMode].model=createModel(createMode,info);
  select.value=createChoice[createMode].model;
  const options=panel.querySelector('#create-options');options.replaceChildren();
  if(createMode==='image'){
   const row=document.createElement('label');row.className='create-row';
   const name=document.createElement('span');name.textContent=t('createSize');
   const size=document.createElement('select');
   for(const [value,word] of CREATE_SIZES){const option=document.createElement('option');option.value=value;option.textContent=t(word);size.append(option);}
   size.value=createChoice.image.size;
   size.onchange=()=>{createChoice.image.size=size.value;priceCreate();};
   row.append(name,size);options.append(row);
  }else if(createMode==='video'){
   const seconds=createOption(t('createSeconds'),CREATE_SECONDS.map(String),String(createChoice.video.seconds),value=>{createChoice.video.seconds=Number(value);});
   const resolution=createOption(t('createResolution'),CREATE_RES,createChoice.video.resolution,value=>{createChoice.video.resolution=value;});
   options.append(seconds,resolution);
  }else{
   const row=document.createElement('label');row.className='create-row';
   const name=document.createElement('span');name.textContent=t('createKind');
   const kind=document.createElement('select');
   for(const value of ['music','speech']){const option=document.createElement('option');option.value=value;option.textContent=t(value==='music'?'createMusic':'createSpeech');kind.append(option);}
   kind.value=createChoice.audio.kind;
   kind.onchange=()=>{createChoice.audio.kind=kind.value;createChoice.audio.model='';renderCreate();};
   row.append(name,kind);options.append(row);
  }
  priceCreate();
  requestAnimationFrame(placeCreatePanel);
 });
}
function closeCreate(){if(createPanel)createPanel.hidden=true;const chip=document.getElementById('create-chip');if(!chip||chip.hidden)createMode=null;}
function placeCreatePanel(){
 const panel=createPanel,button=$('draw');if(!panel||!button)return;
 const box=button.getBoundingClientRect(),width=panel.offsetWidth||360,height=panel.offsetHeight||220;
 panel.style.left=Math.min(Math.max(8,box.left),Math.max(8,window.innerWidth-width-8))+'px';
 panel.style.top=(box.top-height-10>8?box.top-height-10:box.bottom+10)+'px';
}
function toggleCreate(){const panel=createPanelElement();if(panel.hidden){if(!createMode)createMode='image';renderCreate();}else{closeCreate();}}
async function priceCreate(){
 const panel=createPanel;if(!panel)return;
 const price=panel.querySelector('#create-price');price.textContent=t('createCounting');
 if(createTimer)clearTimeout(createTimer);
 createTimer=setTimeout(async()=>{
  try{
   const info=createCatalogue||{};
   const choice=createChoice[createMode];
   if(createMode==='audio'){
    const model=(info.models||[]).find(m=>m.id===choice.model);
    const commerce=model?.commerce||{},rates=commerce.effective_rub||commerce.rates_rub||{};
    const unit=commerce.unit||(choice.kind==='music'?'track':'minute');
    const value=Number(rates[unit]??rates.track??rates.minute??rates.speech??0);
    price.textContent=value>0?`≈ ₽${value.toFixed(2)} · ${unit==='track'?t('createPerTrack'):t('createPerMinute')}`:t('createPriceUnknown');
    return;
   }
   const query=createMode==='image'
    ?`images=1&image_model=${encodeURIComponent(choice.model)}&size=${encodeURIComponent(choice.size)}`
    :`clips=1&video_model=${encodeURIComponent(choice.model)}`;
   const answer=await fetch('/v1/estimate?'+query,{headers:{Authorization:'Bearer '+session.key}}).then(r=>r.json());
   const rub=Number(answer.rub);
   price.textContent=Number.isFinite(rub)?`≈ ₽${rub.toFixed(2)}`:(Number(answer.cny)>0?`≈ ¥${Number(answer.cny).toFixed(2)}`:t('createPriceUnknown'));
  }catch{price.textContent=t('createPriceUnknown');}
 },250);
}
async function finishCreate(chat,owner){
 sending=false;chat.updated=Date.now();if(!chat.folder)chat.folder=chatSync.channel==='telegram'?'Telegram':chatSync.channel==='max'?'MAX':t('folderWeb');await persist(chat);
 if(!chat.local){try{await chatSync.publish(chat);}catch(e){notice(t('syncError'));}}
 $('send').hidden=false;$('stop').hidden=true;updateStatus();if(session?.owner===owner)renderConversation();
}
async function openCreateChat(prompt){
 if(!current)await newChat();
 const chat=current;chat.messages.push({role:'user',content:prompt,at:Date.now()});
 if(chat.messages.filter(m=>m.role==='user').length===1)chat.title=prompt.slice(0,52);
 chat.updated=Date.now();sending=true;updateStatus();await persist(chat);renderConversation();
 return chat;
}
async function uploadCreated(blob,name,mime){
 const form=new FormData();form.append('file',new Blob([blob],{type:mime}),name);
 const response=await fetch('/v1/attachments',{method:'POST',headers:{Authorization:'Bearer '+session.key},body:form});
 const saved=await response.json().catch(()=>({}));
 if(!response.ok||!saved.media_url)throw new Error(t('createFailed'));
 return {name,mime,size:blob.size,url:String(saved.media_url)};
}
async function createVideo(prompt){
 const choice={...createChoice.video,model:createModel('video',createCatalogue||{})};
 const owner=session.owner;
 const chat=await openCreateChat(prompt);
 try{
  if(!choice.model)throw new Error(t('createNoModel'));
  const size=choice.resolution==='1080P'?'1920*1080':choice.resolution==='720P'?'1280*720':'832*480';
  const start=await fetch('/api/v1/services/aigc/video-generation/video-synthesis',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+session.key,'Content-Type':'application/json'},body:JSON.stringify({model:choice.model,input:{prompt},parameters:{size,duration:choice.seconds,resolution:choice.resolution}})});
  const began=await start.json().catch(()=>({}));
  if(!start.ok)throw new Error(began?.message||began?.error?.message||t('createFailed'));
  const task=began?.output?.task_id;
  if(!task)throw new Error(t('createFailed'));
  const deadline=Date.now()+15*60*1000;let done=null;
  while(Date.now()<deadline){
   await new Promise(resolve=>setTimeout(resolve,4000));
   const poll=await fetch('/api/v1/tasks/'+encodeURIComponent(task),{headers:{Authorization:'Bearer '+session.key}});
   const state=await poll.json().catch(()=>({}));
   const status=state?.output?.task_status;
   if(status==='SUCCEEDED'){done=state;break;}
   if(status==='FAILED'||status==='CANCELED')throw new Error(state?.output?.message||t('createFailed'));
  }
  if(!done)throw new Error(t('createTimeout'));
  const url=done?.nanomuse?.media_url||done?.output?.video_url;
  if(!url)throw new Error(t('createFailed'));
  chat.messages.push({role:'assistant',content:'',files:[{name:'lika-clip.mp4',mime:'video/mp4',size:0,url}],at:Date.now()});
 }catch(error){
  console.error('clip failed',error);
  notice(error?.message||t('createFailed'));
  chat.messages.push({role:'assistant',content:t('createFailed'),at:Date.now()});
 }finally{await finishCreate(chat,owner);}
}
async function createAudio(prompt){
 const choice={...createChoice.audio,model:createModel('audio',createCatalogue||{})};
 const owner=session.owner;
 const chat=await openCreateChat(prompt);
 try{
  if(!choice.model)throw new Error(t('createNoModel'));
  const music=choice.kind==='music';
  const response=await fetch(music?'/v1/audio/music':'/v1/audio/speech',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+session.key,'Content-Type':'application/json'},body:JSON.stringify(music?{model:choice.model,prompt}:{model:choice.model,input:prompt,response_format:'mp3'})});
  if(!response.ok){const said=await response.json().catch(()=>({}));throw new Error(said?.error?.message||said?.message||t('createFailed'));}
  const blob=await response.blob();
  const mime=(response.headers.get('content-type')||(music?'audio/mpeg':'audio/mpeg')).split(';')[0];
  const file=await uploadCreated(blob,music?'lika-track.mp3':'lika-speech.mp3',mime);
  chat.messages.push({role:'assistant',content:'',files:[file],at:Date.now()});
 }catch(error){
  console.error('sound failed',error);
  notice(error?.message||t('createFailed'));
  chat.messages.push({role:'assistant',content:t('createFailed'),at:Date.now()});
 }finally{await finishCreate(chat,owner);}
}
async function createFromPrompt(prompt){
 createCatalogue=createCatalogue||await createInfo();
 if(createMode==='image'){await drawPicture(prompt,{model:createModel('image',createCatalogue),size:createChoice.image.size});}
 else if(createMode==='video'){await createVideo(prompt);}
 else if(createMode==='audio'){await createAudio(prompt);}
}
function looksLikeDrawing(text){
 return /^\s*(нарисуй|нарисуйте|нарисуй-ка|draw|sketch)\b/i.test(text)||/^\s*(сгенерируй|сделай|создай)\s+(картин|изображен|рисун|иллюстрац)/i.test(text)||/^\s*(generate|make|create)\s+(an?\s+)?(image|picture|illustration|drawing)/i.test(text)||/^\s*(画|绘制|生成).*(图|画)/.test(text);
}

function renderConversation(){renderConversationActions();const timeline=$('timeline');const stick=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<100;if(!current?.messages.length){timeline.replaceChildren();const welcome=document.createElement('div');welcome.className='welcome';const image=document.createElement('img');image.src='assets/lika.webp';image.dataset.brandAvatar='';image.alt='Лика';image.width=230;image.height=230;const h=document.createElement('h2');h.textContent=t('hello');const p=document.createElement('p');p.textContent=t('welcome');const examples=document.createElement('div');examples.className='examples';for(const example of t('examples')){const btn=document.createElement('button');btn.textContent=example;btn.onclick=()=>{$('message').value=example;$('message').focus();};examples.append(btn);}welcome.append(image,h,p,examples);timeline.append(welcome);$('export').disabled=true;return;}
 timeline.replaceChildren();$('export').disabled=false;for(const message of current.messages){const bubble=document.createElement('article');bubble.className='message '+message.role;const by=document.createElement('div');by.className='by';by.textContent=message.role==='user'?(lang==='ru'?'Вы':lang==='zh'?'你':'You'):'Лика';const content=document.createElement('div');content.className='content';bubble.append(by);if(message.reasoning){const details=document.createElement('details');details.className='thinking';details.open=message.thinking===true;const summary=document.createElement('summary');summary.textContent=message.thinking?t('thinking'):t('thoughts');const body=document.createElement('div');body.className='thinking-text';body.textContent=message.reasoning;details.append(summary,body);bubble.append(details);}if(message.content){renderContent(content,message.content);bubble.append(content);}if(message.image){const picture=document.createElement('img');picture.className='message-image';picture.src=message.image;picture.alt=message.role==='user'?'':t('drawMade');picture.tabIndex=0;picture.title=t('openFull');picture.onclick=()=>openImage(message.image);picture.onkeydown=event=>{const pressed=event['key'];if(pressed==='Enter'||pressed===' '){event.preventDefault();openImage(message.image);}};bubble.append(picture);}if(message.files?.length&&!message.image){const attached=document.createElement('div');attached.className='message-files';for(const file of message.files){const mime=String(file.mime||'');if(/^video\//.test(mime)&&file.url){const clip=document.createElement('video');clip.className='message-video';clip.controls=true;clip.preload='metadata';clip.src=file.url;attached.append(clip);}else if(/^audio\//.test(mime)&&file.url){const sound=document.createElement('audio');sound.className='message-audio';sound.controls=true;sound.src=file.url;attached.append(sound);}else attached.append(fileChip(file));}bubble.append(attached);}if(message.usage&&message.role==='assistant'){const u=message.usage,meta=document.createElement('small');meta.className='message-usage';const label=lang==='ru'?'токенов':lang==='zh'?'令牌':'tokens';const approx=u.estimated?(lang==='ru'?' (оценка)':lang==='zh'?'（估算）':' (estimated)'):'';const cost=typeof u.cost_rub==='number'?' · '+new Intl.NumberFormat(lang,{style:'currency',currency:'RUB',maximumFractionDigits:6}).format(u.cost_rub):'';meta.textContent=new Intl.NumberFormat(lang).format(u.input_tokens+u.output_tokens)+' '+label+approx+cost;bubble.append(meta);}if(message.interrupted&&!message.streaming){const note=document.createElement('p');note.className='partial';note.textContent=t('partial');bubble.append(note);}timeline.append(bubble);}if(stick)timeline.scrollTop=timeline.scrollHeight;}
function showSection(section){closeSidebar();if(section==='chats')archivedView=false;if(section==='archive'){section='chats';archivedView=true;}$('conversation').hidden=section!=='chats';$('documents').hidden=section!=='files';$('show-chats').classList.toggle('active',section==='chats'&&!archivedView);$('show-archive').classList.toggle('active',section==='chats'&&archivedView);$('show-files').classList.toggle('active',section==='files');if(section==='chats')renderChats();if(section==='files')renderFiles();}
async function newChat(){closeSidebar();if(controller)return;if(!authenticated()){showAuth();return;}current={key:session.owner+':'+crypto.randomUUID(),owner:session.owner,title:t('untitled'),folder:chatSync.channel==='telegram'?'Telegram':chatSync.channel==='max'?'MAX':t('folderWeb'),updated:Date.now(),archived:false,local:false,messages:[]};applyChatModel(current);await persist(current);showSection('chats');renderConversation();$('message').value='';pendingFiles=[];renderPending();$('message').focus();}
function renderConversationActions(){$('conversation-actions')?.remove();}
async function selectModel(){return composerControls.model();}
/* Каждый разговор помнит свою модель: выбрали рисующую в одном чате — текстовый
   соседний остался на своей. Пусто — значит как в аккаунте по умолчанию. */
function applyChatModel(chat){composerControls.setChatModel(chat?.model||'');composerControls.setChatReasoning(chat?.reasoning||'');}
function wireMessage(message,seesImages=true){let text=message.content||'';for(const file of message.files||[]){if(!file.text)continue;text+=(text?'\n\n':'')+'--- Файл: '+file.name+' ---\n'+file.text;}const images=[];for(const file of message.files||[]){if(file.dataUrl)images.push({name:file.name,url:file.dataUrl});else if(Array.isArray(file.images))file.images.forEach((url,i)=>images.push({name:file.name+(file.images.length>1?' (стр. '+(i+1)+')':''),url}));}if(!images.length)return text;
 /* Текстовая модель картинку не получит: вместо ошибки релея она сама скажет об этом
    человеку — как в opencode. */
 if(!seesImages)return (text?text+'\n\n':'')+'[К сообщению приложены изображения: '+images.map(image=>image.name).join(', ')+'. Эта модель не видит изображения. Скажи об этом прямо, попроси описать словами или предложи выбрать модель, которая видит картинки. Не пересказывай этот служебный текст.]';
 const parts=[{type:'text',text:text||'Посмотри приложенные изображения: '+images.map(image=>image.name).join(', ')}];for(const image of images)parts.push({type:'image_url',image_url:{url:image.url}});return parts;}
function wireLength(message,seesImages=true){const parts=wireMessage(message,seesImages);return typeof parts==='string'?parts.length:parts.reduce((sum,part)=>sum+(part.type==='text'?part.text.length:4000),0);}
function contextFor(chat,seesImages=true){let n=0;const selected=[];for(const m of chat.messages.filter(m=>(m.content||m.files?.length)&&!m.interrupted).slice(-20).reverse()){const length=wireLength(m,seesImages);if(n+length>65000&&selected.length)break;selected.unshift({role:m.role,content:wireMessage(m,seesImages)});n+=length;}return [{role:'system',content:'You are Lika, a warm and practical personal assistant. Reply in '+({ru:'Russian',zh:'Simplified Chinese',en:'English'}[lang])+'. This browser client has two explicit tools: conversation_open (navigation between the apps of this account) and make_document (a Word document, an Excel spreadsheet or a ZIP archive made on the server and returned as a download link). It has no computer-control, filesystem or publishing tools. When the person asks for a document, letter, plan, report, resume, list, table or archive saved as a file, call make_document with the whole content (docx text, xlsx rows, or zip files), then say the file is ready to download; a document is not a computer action. When the person asks for something that must happen on a computer or another device (open a program, a file or a folder, look at or watch the screen, monitor the computer, click, run a command, print, send mail from their mail program, change system settings), say plainly that the browser page cannot do it, and offer the desktop app: install nanoLika on the computer from https://nanolika.ru/download.html, sign in there with the same account, and that computer joins this account; the assistant on the computer can then use its windows, files and screen. Give the full link, never a page name. Do not offer the download for ordinary text questions. Do not ask what to watch, which program to open, or collect any details, checklist or plan for such a task: the browser cannot do it at all, so one short refusal, then the link and one sentence about what the desktop app adds. Never claim you clicked, changed a file, sent a message or ran background work. Help with explanations, drafts, plans and code. Documents in the local library are not available to you unless the user explicitly provides their content. Do not claim access to other conversations or devices. You cannot draw pictures in this chat: pictures are made with the picture button beside the attachments, so never pretend to draw with text, characters or ASCII art; say the picture button is the way. All synced chats share one account list. New chat creates a separate shared conversation with empty history. Opening an existing chat in a messenger preserves that chat and does not create another conversation. Never say you created a chat when only switching channels, or say no new chat was created when the user pressed New chat. For an explicit request to open this conversation in Telegram, MAX or on the computer, call conversation_open with the chosen destination. Do not claim success until the tool result confirms it. A new conversation requires the New chat control; navigation preserves the current conversation. Do not confuse these actions. Other apps are optional: the browser works on its own. Connection setup is in the account menu under Devices and channels. Do not tell the user to click channel buttons above the chat. Use the conversation_open tool for a spoken or typed navigation request. If it fails, explain the failure briefly; do not investigate infrastructure or repeat the action automatically.'},...selected];}
/* ── вложения: картинки сжимаем в браузере, текстовые файлы читаем в контекст ── */
const ATTACH_MAX=4,ATTACH_IMAGE_SIDE=1200,ATTACH_TEXT_CHARS=20000,ATTACH_FILE_BYTES=50*1024*1024;
let pendingFiles=[];
const isImageFile=file=>/^image\//i.test(file.type||'')||/\.(png|jpe?g|webp|gif|heic|heif|avif|bmp)$/i.test(file.name||'');
const isTextFile=file=>/^text\//i.test(file.type||'')||/\.(txt|md|markdown|csv|tsv|json|log|ya?ml|xml|html?|css|scss|js|mjs|cjs|ts|tsx|jsx|py|rb|go|rs|java|c|h|cpp|sh|bash|zsh|sql|ini|toml)$/i.test(file.name||'');
const isPdfFile=file=>/^application\/pdf$/i.test(file.type||'')||/\.pdf$/i.test(file.name||'');
const isWordFile=file=>/\.docx$/i.test(file.name||'')||/vnd\.openxmlformats-officedocument\.wordprocessingml/i.test(file.type||'');
const isSheetFile=file=>/\.(xlsx|xlsm|xls|ods)$/i.test(file.name||'');
/* Word и Excel читаются здесь же: текст таблицы или документа уходит модели, файл никуда. */
async function wordText(file){
 if(!window.mammoth)throw new Error('unsupported');
 const result=await window.mammoth.extractRawText({arrayBuffer:await file.arrayBuffer()});
 const text=(result.value||'').replace(/\n{3,}/g,'\n\n').trim();
 if(!text)throw new Error('empty');
 return text.slice(0,ATTACH_TEXT_CHARS);
}
async function sheetText(file){
 if(!window.XLSX)throw new Error('unsupported');
 const book=window.XLSX.read(new Uint8Array(await file.arrayBuffer()),{type:'array'});
 const parts=[];
 for(const name of book.SheetNames.slice(0,8)){
  const csv=window.XLSX.utils.sheet_to_csv(book.Sheets[name],{blankrows:false}).trim();
  if(csv)parts.push('--- лист: '+name+' ---\n'+csv);
  if(parts.join('\n\n').length>ATTACH_TEXT_CHARS)break;
 }
 const text=parts.join('\n\n').trim();
 if(!text)throw new Error('empty');
 return text.slice(0,ATTACH_TEXT_CHARS);
}
/* PDF читается здесь, в браузере: текст уходит в модель, сам файл никуда не разбирается.
   Ограничения те же, что у текстовых вложений: страницы и символы. */
const ATTACH_PDF_PAGES=40,ATTACH_SCAN_PAGES=6,ATTACH_SCAN_SIDE=1400;
async function pdfText(file){
 if(!window.pdfjsLib)throw new Error('unsupported');
 if(window.pdfjsLib.GlobalWorkerOptions&&!window.pdfjsLib.GlobalWorkerOptions.workerSrc)window.pdfjsLib.GlobalWorkerOptions.workerSrc='vendor/pdf/pdf.worker.min.js';
 const buffer=await file.arrayBuffer();
 const pdf=await window.pdfjsLib.getDocument({data:new Uint8Array(buffer),isEvalSupported:false,useSystemFonts:true}).promise;
 try{
  const pages=Math.min(pdf.numPages,ATTACH_PDF_PAGES),parts=[];
  for(let n=1;n<=pages;n++){
   const page=await pdf.getPage(n),content=await page.getTextContent();
   let line='';const lines=[];
   for(const item of content.items){if(typeof item.str!=='string')continue;line+=item.str;if(item.hasEOL){lines.push(line);line='';}}
   if(line)lines.push(line);
   parts.push(lines.join('\n'));
  }
  const text=parts.join('\n\n').replace(/[ \t]+\n/g,'\n').replace(/\n{3,}/g,'\n\n').trim();
  /* Скан без текстового слоя — не отказ: страницы рисуются в картинки и уходят модели,
     которая видит. Так же читает и Лика на компьютере, и в Telegram. */
  if(!text)return {scan:await pdfPages(pdf)};
  return {text:text.slice(0,ATTACH_TEXT_CHARS),cut:text.length>ATTACH_TEXT_CHARS||pdf.numPages>pages,pages:pdf.numPages};
 }finally{try{pdf.destroy();}catch{}}
}
async function pdfPages(pdf){
 const count=Math.min(pdf.numPages,ATTACH_SCAN_PAGES),shots=[];
 for(let n=1;n<=count;n++){
  const page=await pdf.getPage(n),base=page.getViewport({scale:1});
  const scale=Math.min(2,ATTACH_SCAN_SIDE/Math.max(base.width||1,base.height||1));
  const viewport=page.getViewport({scale:Math.max(scale,0.5)});
  const canvas=document.createElement('canvas');canvas.width=Math.round(viewport.width);canvas.height=Math.round(viewport.height);
  const pen=canvas.getContext('2d');pen.fillStyle='#ffffff';pen.fillRect(0,0,canvas.width,canvas.height);
  await page.render({canvasContext:pen,viewport}).promise;
  shots.push(canvas.toDataURL('image/jpeg',.82));
 }
 return shots;
}
function sizeLabel(bytes){if(bytes<1024)return bytes+' Б';if(bytes<1048576)return Math.round(bytes/1024)+' КБ';return (bytes/1048576).toFixed(1).replace('.',',')+' МБ';}
/* CSP сайта разрешает img-src 'self' blob:, поэтому превью картинки идёт через blob:, а не data:. */
const previewUrls=new WeakMap();
function previewUrl(file){if(!file.dataUrl)return '';const cached=previewUrls.get(file);if(cached)return cached;try{const comma=file.dataUrl.indexOf(','),mime=(file.dataUrl.slice(5,comma).split(';')[0])||'image/jpeg',binary=atob(file.dataUrl.slice(comma+1)),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);const url=URL.createObjectURL(new Blob([bytes],{type:mime}));previewUrls.set(file,url);return url;}catch{return '';}}
function fileChip(file,{removable=false}={}){const chip=document.createElement('span');chip.className='file-chip';const shot=file.dataUrl||(Array.isArray(file.images)&&file.images[0]);if(shot){const image=document.createElement('img');image.src=file.dataUrl?previewUrl(file):shot;image.alt='';chip.append(image);}else chip.append(uiIcon('file'));const name=document.createElement('span');name.className='file-name';name.textContent=file.name;const meta=document.createElement('span');meta.className='file-meta';meta.textContent=sizeLabel(file.size||0);chip.append(name,meta);if(removable){const remove=document.createElement('button');remove.type='button';remove.setAttribute('aria-label',t('removeAttachment')+': '+file.name);remove.textContent='×';remove.onclick=()=>{pendingFiles=pendingFiles.filter(item=>item!==file);renderPending();$('message').focus();};chip.append(remove);}return chip;}
function renderPending(){const box=$('composer-files');if(!box)return;box.replaceChildren();box.hidden=!pendingFiles.length;for(const file of pendingFiles)box.append(fileChip(file,{removable:true}));}
async function decodeImage(file){if('createImageBitmap'in window){try{return await createImageBitmap(file);}catch{}}return new Promise((resolve,reject)=>{const url=URL.createObjectURL(file),image=new Image();image.onload=()=>{URL.revokeObjectURL(url);resolve(image);};image.onerror=()=>{URL.revokeObjectURL(url);reject(new Error('decode'));};image.src=url;});}
async function shrinkImage(file){const source=await decodeImage(file);const scale=Math.min(1,ATTACH_IMAGE_SIDE/Math.max(source.width||1,source.height||1));const width=Math.max(1,Math.round((source.width||1)*scale)),height=Math.max(1,Math.round((source.height||1)*scale));const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const pen=canvas.getContext('2d');pen.fillStyle='#ffffff';pen.fillRect(0,0,width,height);pen.drawImage(source,0,0,width,height);if(typeof source.close==='function')source.close();return canvas.toDataURL('image/jpeg',.82);}
async function readAttachment(file){if(file.size>ATTACH_FILE_BYTES)throw new Error('big');if(isImageFile(file))return {name:file.name||'image.jpg',mime:'image/jpeg',size:file.size,dataUrl:await shrinkImage(file)};if(isTextFile(file)){const raw=(await file.text()).replace(/\u0000/g,'');const body=raw.slice(0,ATTACH_TEXT_CHARS);return {name:file.name||'file.txt',mime:file.type||'text/plain',size:file.size,text:body,cut:raw.length>body.length};}if(isWordFile(file)){const text=await wordText(file);return {name:file.name||'file.docx',mime:file.type||'application/vnd.openxmlformats-officedocument.wordprocessingml.document',size:file.size,text,cut:text.length>=ATTACH_TEXT_CHARS,blob:file};}if(isSheetFile(file)){const text=await sheetText(file);return {name:file.name||'file.xlsx',mime:file.type||'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',size:file.size,text,cut:text.length>=ATTACH_TEXT_CHARS,blob:file};}if(isPdfFile(file)){const pdf=await pdfText(file);return {name:file.name||'file.pdf',mime:'application/pdf',size:file.size,text:pdf.text||'',cut:pdf.cut,pages:pdf.pages,...(pdf.scan?{images:pdf.scan}:{}),blob:file};}return {name:file.name||'file',mime:file.type||'application/octet-stream',size:file.size,blob:file};}
/* Вложение уходит в S3 через релей и возвращает ссылку: её увидят другие устройства
   и мессенджеры. Не вышло — файл остаётся в браузере, в тексте будет только имя. */
async function uploadAttachment(file){
 try{
  let blob=null;
  if(file.dataUrl){const comma=file.dataUrl.indexOf(','),mime=(file.dataUrl.slice(5,comma).split(';')[0])||file.mime||'image/jpeg',binary=atob(file.dataUrl.slice(comma+1)),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);blob=new Blob([bytes],{type:mime});}
  else if(typeof file.text==='string')blob=new Blob([file.text],{type:file.mime||'text/plain'});
  else if(file.blob)blob=file.blob;
  if(!blob)return '';
  const form=new FormData();form.append('file',blob,file.name);
  const response=await fetch('/v1/attachments',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+session.key},body:form});
  if(!response.ok)return '';
  const data=await response.json();
  // the stable /v1/files link, not the presigned one that expires in a week
  return typeof data?.media_url==='string'?data.media_url:(typeof data?.url==='string'?data.url:'');
 }catch{return '';}
}
if($('attach')&&$('attach-input')){
 $('attach').onclick=()=>$('attach-input').click();
 $('attach-input').onchange=async event=>{const chosen=[...(event.target.files||[])];event.target.value='';if(!chosen.length)return;const room=ATTACH_MAX-pendingFiles.length;if(room<=0){notice(t('attachLimit'));return;}let refused='';for(const file of chosen.slice(0,room)){try{pendingFiles.push(await readAttachment(file));renderPending();}catch(error){const code=error?.message==='big'?'attachBig':error?.message==='scan'?'attachScan':error?.message==='empty'?'attachEmpty':error?.message==='unsupported'?'attachUnsupported':'attachFailed';refused=code;}}if(chosen.length>room)refused='attachLimit';if(refused)notice(t(refused));$('message').focus();};
}
let drawMode=false;
if($('draw')){
 $('draw').setAttribute('aria-label',t('createTitle'));
 $('draw').setAttribute('title',t('createTitle'));
 $('draw').onclick=()=>{if(sending||controller)return;toggleCreate();};
 document.addEventListener('click',event=>{const panel=createPanel;if(!panel||panel.hidden)return;const button=$('draw');if(panel.contains(event.target)||(button&&button.contains(event.target)))return;closeCreate();});
 document.addEventListener('keydown',event=>{if(event['key']==='Escape')closeCreate();});
}
async function shrinkPicture(src,side=1024){
 try{const image=await new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=reject;img.src=src;});
  const scale=Math.min(1,side/Math.max(image.width||1,image.height||1));
  if(scale===1&&src.length<900000)return src;
  const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(image.width*scale));canvas.height=Math.max(1,Math.round(image.height*scale));
  const pen=canvas.getContext('2d');pen.fillStyle='#ffffff';pen.fillRect(0,0,canvas.width,canvas.height);pen.drawImage(image,0,0,canvas.width,canvas.height);
  return canvas.toDataURL('image/jpeg',.86);
 }catch{return src;}
}
function dataUrlBlob(dataUrl){const comma=dataUrl.indexOf(','),mime=(dataUrl.slice(5,comma).split(';')[0])||'image/jpeg',binary=atob(dataUrl.slice(comma+1)),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);return new Blob([bytes],{type:mime});}
async function drawPicture(prompt,options={}){
 const owner=session.owner,key=session.key;
 if(!current)await newChat();
 const chat=current;chat.messages.push({role:'user',content:prompt,at:Date.now()});
 if(chat.messages.filter(m=>m.role==='user').length===1)chat.title=prompt.slice(0,52);
 chat.updated=Date.now();sending=true;
 try{
  updateStatus();await persist(chat);renderConversation();
  const info=await fetch('/v1/me/models',{headers:{Authorization:'Bearer '+key}}).then(r=>r.json());
  const shelf=info?.shelf||{};
  const models=info?.models||[];
  // A plan-locked picture model answers with a refusal, not a picture: the draw takes the
  // first picture model this account may really use, default first.
  const usable=m=>m&&m.kind==='image'&&m.available!==false&&m.plan_locked!==true;
  const byId=id=>models.find(m=>m.id===id);
  const wanted=options.model?byId(options.model):undefined;
  const model=(usable(wanted)&&wanted.id)
    ||(usable(byId(shelf.default&&shelf.default.image))&&shelf.default.image)
    ||((shelf.enabled&&shelf.enabled.image)||[]).find(id=>usable(byId(id)))
    ||models.find(usable)?.id;
  if(!model)throw Object.assign(new Error(t('drawNoModel')),{code:'no_image_model'});
  const response=await fetch('/v1/images/generations',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify({model,prompt,n:1,size:options.size||'1024x1024'})});
  const data=await response.json().catch(()=>({}));
  if(!response.ok){const error=new Error(data?.error?.message||t('requestError'));error.status=response.status;error.code=data?.error?.code;throw error;}
  const item=(data.data||[])[0]||{};
  let src=item.url||'';
  if(!src&&item.b64_json){const b64=String(item.b64_json);const mime=b64.startsWith('/9j/')?'image/jpeg':b64.startsWith('iVBOR')?'image/png':'image/jpeg';src=await shrinkPicture('data:'+mime+';base64,'+b64);}
  if(!src)throw new Error(t('requestError'));
  // The picture goes to the same S3 bucket as any attachment, and the message keeps the
  // relay's stable /v1/files link: it opens on every device (and in a messenger) and does
  // not expire like the presigned URL from the upload does.
  let remote='';
  try{if(src.startsWith('data:')){const blob=dataUrlBlob(src);const form=new FormData();form.append('file',blob,'lika-picture.jpg');const upload=await fetch('/v1/attachments',{method:'POST',headers:{Authorization:'Bearer '+key},body:form});const saved=await upload.json().catch(()=>({}));if(upload.ok&&saved.media_url)remote=String(saved.media_url);}}catch{}
  if(!remote&&!chat.syncCid)chat.local=true;
  const pictureSize=remote?(()=>{try{const comma=src.indexOf(',');return Math.floor(atob(src.slice(comma+1)).length);}catch{return 0;}})():0;
  chat.messages.push({role:'assistant',content:'',image:remote||src,at:Date.now(),...(remote?{files:[{name:'lika-picture.jpg',mime:'image/jpeg',size:pictureSize,url:remote}]}:{})});
 }catch(error){
  console.error('draw failed', error);
  if(error.code==='insufficient_funds'||error.status===402){if(!fundsNotice(error))notice(error.message||t('drawFailed'));}
  else notice(error.message||t('drawFailed'));
  chat.messages.push({role:'assistant',content:t('drawFailed'),at:Date.now()});
 }finally{
  sending=false;chat.updated=Date.now();await persist(chat);
  if(!chat.local){try{await chatSync.publish(chat);}catch(e){notice(t('syncError'));}}
  $('send').hidden=false;$('stop').hidden=true;updateStatus();if(session?.owner===owner)renderConversation();
 }
}
$('composer').onsubmit=async event=>{event.preventDefault();if(controller||sending||opening)return;if(!authenticated()){showAuth();return;}const value=$('message').value.trim();if(!value&&!pendingFiles.length)return;if(value.length>30000){notice(t('tooLong'));return;}if(!navigator.onLine){notice(t('offline'));return;}if(createMode&&value){$('message').value='';await createFromPrompt(value);return;}
 if(!createMode&&!pendingFiles.length&&looksLikeDrawing(value)){$('message').value='';await drawPicture(value);return;}const files=pendingFiles.map(file=>({...file}));sending=true;if(files.length)await Promise.all(files.map(async file=>{const url=await uploadAttachment(file);if(url)file.url=url;}));if(!current)await newChat();const chat=current,owner=session.owner,key=session.key,runEpoch=epoch;notice('');pendingFiles=[];renderPending();chat.messages.push({role:'user',content:value,at:Date.now(),...(files.length?{files}:{})});if(chat.messages.filter(m=>m.role==='user').length===1)chat.title=(value||files.map(file=>file.name).join(', ')).slice(0,52);chat.updated=Date.now();await persist(chat);try{await chatSync.publish(chat);}catch(e){notice(t('syncError'));}const messages=contextFor(chat,composerControls.modelVision()!=='no');const responseMessage={mid:crypto.randomUUID(),role:'assistant',content:'',reasoning:'',thinking:false,at:Date.now(),interrupted:false,streaming:true};chat.messages.push(responseMessage);$('message').value='';controller=new AbortController();const signal=controller.signal;$('send').hidden=true;$('stop').hidden=false;updateStatus();renderConversation();
 let usageCalls=0;try{const selected=await selectModel();for(let round=0;round<2;round++){
 const response=await fetch('/v1/chat/completions',{method:'POST',cache:'no-store',signal,headers:{Authorization:'Bearer '+key,'Content-Type':'application/json','X-NanoLika-Message-ID':responseMessage.mid},body:JSON.stringify({model:selected,messages,stream:true,store:false,max_tokens:4096,...(chat.reasoning?{reasoning_effort:chat.reasoning}:{}),...(round===0?{tools:[...navigationTools,...documentTools],parallel_tool_calls:false}:{})})});
 if(response.ok)usageCalls++;let calls=[];const answer=await readReply(response,value=>{responseMessage.content=value;responseMessage.thinking=false;if(session?.owner===owner&&current?.key===chat.key)renderConversation();},value=>{calls=value;},value=>{responseMessage.reasoning=value;responseMessage.thinking=true;if(session?.owner===owner&&current?.key===chat.key)renderConversation();});
 if(!calls.length)break;
 if(round!==0)throw new Error('Repeated tool request');
 messages.push({role:'assistant',content:answer||null,tool_calls:calls});
 const documentCall=calls[0]?.function?.name==='make_document';
 try{
  if(documentCall){
   const made=await performDocument(calls,{key,valid:()=>!signal.aborted&&session?.key===key&&session?.owner===owner&&current?.key===chat.key});
   responseMessage.files=[...(responseMessage.files||[]),made.file];
   messages.push({role:'tool',tool_call_id:made.tool_call_id,content:made.content});
  }else{
   messages.push(await performNavigation(calls,{cid:chat.syncCid,open:(cid,channel,device)=>channelOpener.open(cid,channel,device),valid:()=>!signal.aborted&&session?.key===key&&session?.owner===owner&&current?.key===chat.key}));
  }
 }
 catch(error){if(signal.aborted)throw error;messages.push(...calls.map(call=>({role:'tool',tool_call_id:call.id,content:JSON.stringify(documentCall?{created:false,error:error.message}:{opened:false,error:error.message})})));}
 responseMessage.content='';
 }responseMessage.interrupted=false;responseMessage.streaming=false;}
 catch(e){responseMessage.streaming=false;if(!responseMessage.content)chat.messages.pop();if(e.name!=='AbortError'){if(!fundsNotice(e)){const friendly=e.code==='provider_routes_unavailable'?t('modelNoRoutes'):e.code==='too_large'?t('tooLargeRequest'):'';notice(friendly||(e.status===401?t('needsLogin'):navigator.onLine?(e.message||t('requestError')):t('networkError')));}if(e.status===401)showAuth();}}
 finally{controller=null;sending=false;responseMessage.thinking=false;responseMessage.streaming=false;chat.updated=Date.now();if(runEpoch===epoch&&!chat.deleted){if(usageCalls){try{for(let attempt=0;attempt<3;attempt++){const r=await fetch('/v1/me/message-usage/'+encodeURIComponent(responseMessage.mid),{headers:{Authorization:'Bearer '+key},cache:'no-store'});if(!r.ok)break;const u=(await r.json()).usage;if(u?.requests>=usageCalls){responseMessage.usage=u;break;}if(attempt<2)await new Promise(resolve=>setTimeout(resolve,300));}}catch{}}await persist(chat);try{await chatSync.publish(chat);}catch(e){notice(t('syncError'));}}$('send').hidden=false;$('stop').hidden=true;updateStatus();if(session?.owner===owner)renderConversation();}};
$('stop').onclick=()=>controller?.abort();$('new-chat').onclick=newChat;$('show-chats').onclick=()=>showSection('chats');$('show-archive').onclick=()=>showSection('archive');$('show-files').onclick=()=>{if(!authenticated())showAuth();else showSection('files');};
function closeAccountMenu(restore=false){$('account-menu').hidden=true;$('account').setAttribute('aria-expanded','false');if(restore)$('account').focus();}
$('account').onclick=()=>{if(!authenticated()){showAuth();return;}const open=$('account-menu').hidden;$('account-menu').hidden=!open;$('account').setAttribute('aria-expanded',String(open));};
$('account-menu-logout').onclick=()=>$('logout').click();
document.addEventListener('click',e=>{if(!e.target.closest('.sidebar-bottom'))closeAccountMenu();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('account-menu').hidden)closeAccountMenu(true);});
$('account-menu').addEventListener('focusout',()=>{setTimeout(()=>{if(!$('account-menu').contains(document.activeElement)&&document.activeElement!==$('account'))closeAccountMenu();},0);});
$('auth-close').onclick=()=>$('auth-dialog').close();document.querySelectorAll('[data-close]').forEach(btn=>btn.onclick=()=>btn.closest('dialog').close());
$('auth-mode').onclick=()=>{authMode=authMode==='code'?'password':'code';$('auth-error').textContent='';updateAuth();};
$('auth-form').onsubmit=async event=>{event.preventDefault();$('auth-error').textContent='';$('auth-submit').disabled=true;try{const identifier=$('email').value.trim();if(authMode==='code'&&!codeSent){await api('/v1/auth/code',{identifier});codeSent=true;updateAuth();$('auth-error').textContent=t('sent');$('code').focus();return;}const data=await api(authMode==='code'?'/v1/auth/verify':'/v1/auth/login',authMode==='code'?{identifier,code:$('code').value.trim(),device:'nanoLika Web'}:{identifier,password:$('password').value,device:'nanoLika Web'});if(!data.api_key||!data.account?.id)throw new Error(t('loginError'));session={key:data.api_key,owner:data.account.id,hint:data.account.hint||identifier};local.set(sessionKey,JSON.stringify(session));model=null;current=null;await loadChats();await chatSync.refresh().catch(()=>{});chatSync.start();$('auth-dialog').close();$('password').value='';$('code').value='';notice('');renderConversation();}catch(e){$('auth-error').textContent=e.message||t('loginError');}finally{$('auth-submit').disabled=false;}};
$('logout').onclick=async()=>{if($('logout').disabled)return;$('logout').disabled=true;try{await api('/v1/auth/sign-out',{});}catch{}epoch++;controller?.abort();for(const name of ['nm.key','nm.hint','nm.acct',sessionKey])local.remove(name);session=null;model=null;current=null;chats=[];location.replace('/v1/auth/logout?next='+encodeURIComponent(location.origin+'/app/?signin=1&next='+encodeURIComponent(location.origin+'/web/')));};
function download(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$('export').onclick=()=>{if(!current)return;download(new Blob([current.messages.map(m=>(m.role==='user'?'Вы':'Лика')+':\n'+m.content+(m.files?.length?'\n[вложения: '+m.files.map(file=>file.name).join(', ')+']':'')).join('\n\n')],{type:'text/plain;charset=utf-8'}),(current.title||'Lika').replace(/[^\p{L}\p{N} _-]/gu,'').slice(0,70)+'.txt');};
$('file-input').onchange=async()=>{if(!authenticated())return;const owner=session.owner,fileEpoch=epoch;for(const file of $('file-input').files){if(epoch!==fileEpoch)break;if(file.size>10*1024*1024){notice(t('fileLimit'));continue;}try{await db.save('files',{key:owner+':'+crypto.randomUUID(),owner,name:file.name,type:file.type,size:file.size,blob:file,created:Date.now()});}catch{notice(t('storageError'));}}$('file-input').value='';if(session?.owner===owner)await renderFiles();};
async function renderFiles(){const root=$('file-list');root.replaceChildren();if(!authenticated())return;let files;try{files=await db.list('files',session.owner);}catch{notice(t('storageBlocked'));return;}if(!files.length){const p=document.createElement('p');p.textContent=t('noFiles');root.append(p);return;}for(const file of files){const row=document.createElement('article');row.className='file-row';const title=document.createElement('strong');title.textContent=file.name;const size=document.createElement('small');size.textContent=new Intl.NumberFormat(lang,{maximumFractionDigits:1}).format(file.size/1024)+' KB';const actions=document.createElement('div');actions.className='file-actions';for(const [label,action] of [['download',()=>download(file.blob,file.name)],['preview',async()=>{const isText=file.type.startsWith('text/')||/\.(txt|md|csv|json|js|py|html|css|xml|log)$/i.test(file.name);$('preview-title').textContent=file.name;$('preview-text').textContent=isText?(await file.blob.text()).slice(0,150000):t('unsupported');$('preview-dialog').showModal();}],['delete',async()=>{if(confirm(t('deleteConfirm'))){await db.remove('files',file.key);renderFiles();}}]]){const button=document.createElement('button');button.className=label==='delete'?'danger':'quiet';button.textContent=t(label);button.onclick=action;actions.append(button);}row.append(title,size,actions);root.append(row);}}
$('clear-data').onclick=async()=>{if(!authenticated()||!confirm(t('clearConfirm')))return;epoch++;controller?.abort();await db.clearOwner(session.owner);current=null;pendingFiles=[];renderPending();await loadChats();renderConversation();renderFiles();$('account-dialog').close();};
window.addEventListener('online',()=>{updateStatus();notice('');});window.addEventListener('offline',updateStatus);
const chatSync=new ChatSync({session:()=>session,getChats:()=>chats,setChats:rows=>{chats=rows;if(current&&!rows.some(c=>c.key===current.key))current=null;},current:()=>current,save:chat=>db.save('chats',chat),remove:key=>db.remove('chats',key),onChange:change=>{renderChats();if(change?.messages)renderConversation();if(current)applyChatModel(current);}});
const channelOpener=new ConversationOpener({session:()=>session,device:chatSync.device,language:()=>lang});

const composerControls=installComposerControls({form:$('composer'),message:$('message'),select:$('model-select'),reasoningSelect:$('reasoning-select'),refresh:$('models-refresh'),status:$('model-status'),session:()=>session,api,words:{loading:t('modelsLoading'),empty:t('modelsEmpty'),error:t('modelsError')},onOtherPicked:(kind,name,ok)=>{if(!ok){notice(t('requestError'));return;}const label={image:t('pickedImage'),video:t('pickedVideo'),audio:t('pickedAudio')}[kind]||t('pickedModel');notice(label+' '+name);},onPick:id=>{const chat=current;if(!chat||chat.local)return;chat.model=id;chat.reasoning='';chat.updated=Date.now();void persist(chat);void chatSync.publish(chat).catch(()=>notice(t('syncError')));},onReasoning:level=>{const chat=current;if(!chat||chat.local)return;chat.reasoning=level;chat.updated=Date.now();void persist(chat);void chatSync.publish(chat).catch(()=>notice(t('syncError')));}});
// Фильтры списка моделей: тип и поиск по названию. Список показывает все типы, чтобы
// рисующие модели было видно и можно было проверить, что они есть.
{const search=$('model-search'),kind=$('model-kind');
 if(search)search.addEventListener('input',()=>composerControls.setQuery(search.value));
 if(kind)kind.addEventListener('change',()=>composerControls.setKind(kind.value));}

// Кнопка открывает панель с карточками: провайдеры слева, типы и поиск сверху.
const modelSheet=installModelSheet({api,session:()=>session,
 onPick:id=>composerControls.selectModel(id),
 onFavourites:()=>composerControls.load().catch(()=>{})});
{const open=$('model-browse');if(open)open.addEventListener('click',()=>{modelSheet.setPicked($('model-select')?.value||'');modelSheet.open();});}

if(!authenticated())location.replace('/app/?signin=1&next='+encodeURIComponent(location.origin+location.pathname+location.search));
translate();await db.openDB().catch(()=>notice(t('storageBlocked')));await loadChats();renderConversation();
if(authenticated()&&navigator.onLine){try{const me=await api('/v1/me');if(me.account?.id!==session.owner)throw new Error('identity_mismatch');session.email=me.account.email||'';session.hint=me.account.hint||session.hint;local.set(sessionKey,JSON.stringify(session));renderAccount();}catch(e){if(e.status===401||e.message==='identity_mismatch'){session=null;local.remove(sessionKey);chats=[];current=null;renderChats();renderConversation();notice(t('needsLogin'));}}}
if(authenticated()){composerControls.load().catch(()=>{});await chatSync.refresh().catch(()=>{});chatSync.start();}
document.addEventListener('visibilitychange',()=>{if(document.visibilityState!=='hidden'){if(authenticated())chatSync.start();chatSync.refresh().catch(()=>{});}});
window.addEventListener('online',()=>{if(authenticated()){chatSync.start();chatSync.refresh().catch(()=>{});}});
if(location.hash==='#documents'&&authenticated())showSection('files');
if('serviceWorker' in navigator)navigator.serviceWorker.register('./sw.js',{scope:'./',updateViaCache:'none'}).catch(()=>{});

// Share sign-out/account changes with the site's same-origin account bridge and other tabs.
window.addEventListener('storage',event=>{if(event.key!==sessionKey||!session?.key)return;let next=null;try{next=event.newValue?(sessionKey==='nm.key'?{key:event.newValue}:JSON.parse(event.newValue)):null;}catch{}if(next?.key===session.key)return;if(next?.key){location.reload();return;}location.replace('/app/?signin=1&next='+encodeURIComponent(location.origin+'/web/'));});
