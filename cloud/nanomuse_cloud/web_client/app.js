import {fundsView} from './funds.js';
import {navigationTools,performNavigation} from './navigation-tool.js';
import {ChatSync} from './chat-sync.js?v=20261102-reasoning';
import {ConversationOpener} from './conversation-open.js';
import {text,locales} from './i18n.js?v=20261102-files';
import * as db from './storage.js';
import {readReply} from './stream.js';
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
function renderChats(){const list=$('chat-list'),pins=pinnedChats();const signature=JSON.stringify([lang,current?.key,chatQuery,archivedView,selectingChats,[...selectedChats],[...pins],chats.map(c=>[c.key,c.title,c.unread||0,c.updated,c.messages.length,c.syncCid||'',c.local?'local':'',c.device||'',c.archived?'archived':''])]);if(signature===chatListSignature){renderBulk();return;}chatListSignature=signature;const scroll=list.scrollTop;list.replaceChildren();const rows=visibleChats();if(!rows.length){const p=document.createElement('p');p.className='hint';p.textContent=chatQuery?'Беседы не найдены':t('empty');list.append(p);}for(let chat of rows){const row=document.createElement('div');row.className='chat-row'+(current?.key===chat.key?' selected':'')+(pins.has(pinId(chat))?' is-pinned':'');
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
 const text=String(value),pattern=/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\x60([^\x60]+)\x60|\*\*([^*]+)\*\*|__([^_]+)__|\*([^*]+)\*/g;
 let cursor=0,match;
 while((match=pattern.exec(text))){
  root.append(document.createTextNode(text.slice(cursor,match.index)));
  if(match[2]){try{const url=new URL(match[2]);if(url.protocol==='https:'||url.protocol==='http:'){const a=document.createElement('a');a.href=url.href;a.rel='noopener noreferrer';a.target='_blank';a.textContent=match[1];root.append(a);}else root.append(document.createTextNode(match[1]));}catch{root.append(document.createTextNode(match[1]));}}
  else if(match[3]){const code=document.createElement('code');code.textContent=match[3];root.append(code);}
  else{const el=document.createElement(match[4]||match[5]?'strong':'em');el.textContent=match[4]||match[5]||match[6];root.append(el);}
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
function renderConversation(){renderConversationActions();const timeline=$('timeline');const stick=timeline.scrollHeight-timeline.scrollTop-timeline.clientHeight<100;if(!current?.messages.length){timeline.replaceChildren();const welcome=document.createElement('div');welcome.className='welcome';const image=document.createElement('img');image.src='assets/lika.webp';image.dataset.brandAvatar='';image.alt='Лика';image.width=230;image.height=230;const h=document.createElement('h2');h.textContent=t('hello');const p=document.createElement('p');p.textContent=t('welcome');const examples=document.createElement('div');examples.className='examples';for(const example of t('examples')){const btn=document.createElement('button');btn.textContent=example;btn.onclick=()=>{$('message').value=example;$('message').focus();};examples.append(btn);}welcome.append(image,h,p,examples);timeline.append(welcome);$('export').disabled=true;return;}
 timeline.replaceChildren();$('export').disabled=false;for(const message of current.messages){const bubble=document.createElement('article');bubble.className='message '+message.role;const by=document.createElement('div');by.className='by';by.textContent=message.role==='user'?(lang==='ru'?'Вы':lang==='zh'?'你':'You'):'Лика';const content=document.createElement('div');content.className='content';if(message.content){renderContent(content,message.content);bubble.append(by,content);}else bubble.append(by);if(message.image){const picture=document.createElement('img');picture.className='message-image';picture.src=message.image;picture.alt=message.role==='user'?'':t('drawMade');bubble.append(picture);}if(message.files?.length&&!message.image){const attached=document.createElement('div');attached.className='message-files';for(const file of message.files)attached.append(fileChip(file));bubble.append(attached);}if(message.interrupted){const note=document.createElement('p');note.className='partial';note.textContent=t('partial');bubble.append(note);}timeline.append(bubble);}if(stick)timeline.scrollTop=timeline.scrollHeight;}
function showSection(section){closeSidebar();if(section==='chats')archivedView=false;if(section==='archive'){section='chats';archivedView=true;}$('conversation').hidden=section!=='chats';$('documents').hidden=section!=='files';$('show-chats').classList.toggle('active',section==='chats'&&!archivedView);$('show-archive').classList.toggle('active',section==='chats'&&archivedView);$('show-files').classList.toggle('active',section==='files');if(section==='chats')renderChats();if(section==='files')renderFiles();}
async function newChat(){closeSidebar();if(controller)return;if(!authenticated()){showAuth();return;}current={key:session.owner+':'+crypto.randomUUID(),owner:session.owner,title:t('untitled'),updated:Date.now(),archived:false,local:false,messages:[]};applyChatModel(current);await persist(current);showSection('chats');renderConversation();$('message').value='';pendingFiles=[];renderPending();$('message').focus();}
function renderConversationActions(){$('conversation-actions')?.remove();}
async function selectModel(){return composerControls.model();}
/* Каждый разговор помнит свою модель: выбрали рисующую в одном чате — текстовый
   соседний остался на своей. Пусто — значит как в аккаунте по умолчанию. */
function applyChatModel(chat){composerControls.setChatModel(chat?.model||'');composerControls.setChatReasoning(chat?.reasoning||'');}
function wireMessage(message,seesImages=true){let text=message.content||'';for(const file of message.files||[]){if(!file.text)continue;text+=(text?'\n\n':'')+'--- Файл: '+file.name+' ---\n'+file.text;}const images=(message.files||[]).filter(file=>file.dataUrl);if(!images.length)return text;
 /* Текстовая модель картинку не получит: вместо ошибки релея она сама скажет об этом
    человеку — как в opencode. */
 if(!seesImages)return (text?text+'\n\n':'')+'[К сообщению приложены изображения: '+images.map(file=>file.name).join(', ')+'. Эта модель не видит изображения. Скажи об этом прямо, попроси описать словами или предложи выбрать модель, которая видит картинки. Не пересказывай этот служебный текст.]';
 const parts=[{type:'text',text:text||'Посмотри приложенные изображения: '+images.map(file=>file.name).join(', ')}];for(const image of images)parts.push({type:'image_url',image_url:{url:image.dataUrl}});return parts;}
function wireLength(message,seesImages=true){const parts=wireMessage(message,seesImages);return typeof parts==='string'?parts.length:parts.reduce((sum,part)=>sum+(part.type==='text'?part.text.length:4000),0);}
function contextFor(chat,seesImages=true){let n=0;const selected=[];for(const m of chat.messages.filter(m=>(m.content||m.files?.length)&&!m.interrupted).slice(-20).reverse()){const length=wireLength(m,seesImages);if(n+length>65000&&selected.length)break;selected.unshift({role:m.role,content:wireMessage(m,seesImages)});n+=length;}return [{role:'system',content:'You are Lika, a warm and practical personal assistant. Reply in '+({ru:'Russian',zh:'Simplified Chinese',en:'English'}[lang])+'. This browser client has one explicit conversation_open navigation tool, but no computer-control, filesystem or publishing tools. Never claim you clicked, changed a file, sent a message or ran background work. Help with explanations, drafts, plans and code. Documents in the local library are not available to you unless the user explicitly provides their content. Do not claim access to other conversations or devices. You cannot draw pictures in this chat: pictures are made with the picture button beside the attachments, so never pretend to draw with text, characters or ASCII art; say the picture button is the way. All synced chats share one account list. New chat creates a separate shared conversation with empty history. Opening an existing chat in a messenger preserves that chat and does not create another conversation. Never say you created a chat when only switching channels, or say no new chat was created when the user pressed New chat. For an explicit request to open this conversation in Telegram, MAX or on the computer, call conversation_open with the chosen destination. Do not claim success until the tool result confirms it. A new conversation requires the New chat control; navigation preserves the current conversation. Do not confuse these actions. Other apps are optional: the browser works on its own. Connection setup is in the account menu under Devices and channels. Do not tell the user to click channel buttons above the chat. Use the conversation_open tool for a spoken or typed navigation request. If it fails, explain the failure briefly; do not investigate infrastructure or repeat the action automatically.'},...selected];}
/* ── вложения: картинки сжимаем в браузере, текстовые файлы читаем в контекст ── */
const ATTACH_MAX=4,ATTACH_IMAGE_SIDE=1200,ATTACH_TEXT_CHARS=20000,ATTACH_FILE_BYTES=50*1024*1024;
let pendingFiles=[];
const isImageFile=file=>/^image\//i.test(file.type||'')||/\.(png|jpe?g|webp|gif|heic|heif|avif|bmp)$/i.test(file.name||'');
const isTextFile=file=>/^text\//i.test(file.type||'')||/\.(txt|md|markdown|csv|tsv|json|log|ya?ml|xml|html?|css|scss|js|mjs|cjs|ts|tsx|jsx|py|rb|go|rs|java|c|h|cpp|sh|bash|zsh|sql|ini|toml)$/i.test(file.name||'');
function sizeLabel(bytes){if(bytes<1024)return bytes+' Б';if(bytes<1048576)return Math.round(bytes/1024)+' КБ';return (bytes/1048576).toFixed(1).replace('.',',')+' МБ';}
/* CSP сайта разрешает img-src 'self' blob:, поэтому превью картинки идёт через blob:, а не data:. */
const previewUrls=new WeakMap();
function previewUrl(file){if(!file.dataUrl)return '';const cached=previewUrls.get(file);if(cached)return cached;try{const comma=file.dataUrl.indexOf(','),mime=(file.dataUrl.slice(5,comma).split(';')[0])||'image/jpeg',binary=atob(file.dataUrl.slice(comma+1)),bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);const url=URL.createObjectURL(new Blob([bytes],{type:mime}));previewUrls.set(file,url);return url;}catch{return '';}}
function fileChip(file,{removable=false}={}){const chip=document.createElement('span');chip.className='file-chip';if(file.dataUrl){const image=document.createElement('img');image.src=previewUrl(file);image.alt='';chip.append(image);}else chip.append(uiIcon('file'));const name=document.createElement('span');name.className='file-name';name.textContent=file.name;const meta=document.createElement('span');meta.className='file-meta';meta.textContent=sizeLabel(file.size||0);chip.append(name,meta);if(removable){const remove=document.createElement('button');remove.type='button';remove.setAttribute('aria-label',t('removeAttachment')+': '+file.name);remove.textContent='×';remove.onclick=()=>{pendingFiles=pendingFiles.filter(item=>item!==file);renderPending();$('message').focus();};chip.append(remove);}return chip;}
function renderPending(){const box=$('composer-files');if(!box)return;box.replaceChildren();box.hidden=!pendingFiles.length;for(const file of pendingFiles)box.append(fileChip(file,{removable:true}));}
async function decodeImage(file){if('createImageBitmap'in window){try{return await createImageBitmap(file);}catch{}}return new Promise((resolve,reject)=>{const url=URL.createObjectURL(file),image=new Image();image.onload=()=>{URL.revokeObjectURL(url);resolve(image);};image.onerror=()=>{URL.revokeObjectURL(url);reject(new Error('decode'));};image.src=url;});}
async function shrinkImage(file){const source=await decodeImage(file);const scale=Math.min(1,ATTACH_IMAGE_SIDE/Math.max(source.width||1,source.height||1));const width=Math.max(1,Math.round((source.width||1)*scale)),height=Math.max(1,Math.round((source.height||1)*scale));const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;const pen=canvas.getContext('2d');pen.fillStyle='#ffffff';pen.fillRect(0,0,width,height);pen.drawImage(source,0,0,width,height);if(typeof source.close==='function')source.close();return canvas.toDataURL('image/jpeg',.82);}
async function readAttachment(file){if(file.size>ATTACH_FILE_BYTES)throw new Error('big');if(isImageFile(file))return {name:file.name||'image.jpg',mime:'image/jpeg',size:file.size,dataUrl:await shrinkImage(file)};if(isTextFile(file)){const raw=(await file.text()).replace(/\u0000/g,'');const body=raw.slice(0,ATTACH_TEXT_CHARS);return {name:file.name||'file.txt',mime:file.type||'text/plain',size:file.size,text:body,cut:raw.length>body.length};}return {name:file.name||'file',mime:file.type||'application/octet-stream',size:file.size,blob:file};}
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
  return typeof data?.url==='string'?data.url:'';
 }catch{return '';}
}
if($('attach')&&$('attach-input')){
 $('attach').onclick=()=>$('attach-input').click();
 $('attach-input').onchange=async event=>{const chosen=[...(event.target.files||[])];event.target.value='';if(!chosen.length)return;const room=ATTACH_MAX-pendingFiles.length;if(room<=0){notice(t('attachLimit'));return;}let refused='';for(const file of chosen.slice(0,room)){try{pendingFiles.push(await readAttachment(file));renderPending();}catch(error){const code=error?.message==='big'?'attachBig':error?.message==='unsupported'?'attachUnsupported':'attachFailed';refused=code;}}if(chosen.length>room)refused='attachLimit';if(refused)notice(t(refused));$('message').focus();};
}
let drawMode=false;
if($('draw')){
 $('draw').onclick=()=>{if(sending||controller)return;drawMode=!drawMode;$('draw').setAttribute('aria-pressed',String(drawMode));$('message').placeholder=t(drawMode?'drawPlaceholder':'prompt');$('message').focus();};
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
async function drawPicture(prompt){
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
  const model=(usable(byId(shelf.default&&shelf.default.image))&&shelf.default.image)
    ||((shelf.enabled&&shelf.enabled.image)||[]).find(id=>usable(byId(id)))
    ||models.find(usable)?.id;
  if(!model)throw Object.assign(new Error(t('drawNoModel')),{code:'no_image_model'});
  const response=await fetch('/v1/images/generations',{method:'POST',cache:'no-store',headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify({model,prompt,n:1,size:'1024x1024'})});
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
$('composer').onsubmit=async event=>{event.preventDefault();if(controller||sending||opening)return;if(!authenticated()){showAuth();return;}const value=$('message').value.trim();if(!value&&!pendingFiles.length)return;if(value.length>30000){notice(t('tooLong'));return;}if(!navigator.onLine){notice(t('offline'));return;}if(drawMode){drawMode=false;$('draw').setAttribute('aria-pressed','false');$('message').placeholder=t('prompt');$('message').value='';await drawPicture(value);return;}const files=pendingFiles.map(file=>({...file}));sending=true;if(files.length)await Promise.all(files.map(async file=>{const url=await uploadAttachment(file);if(url)file.url=url;}));if(!current)await newChat();const chat=current,owner=session.owner,key=session.key,runEpoch=epoch;notice('');pendingFiles=[];renderPending();chat.messages.push({role:'user',content:value,at:Date.now(),...(files.length?{files}:{})});if(chat.messages.filter(m=>m.role==='user').length===1)chat.title=(value||files.map(file=>file.name).join(', ')).slice(0,52);chat.updated=Date.now();await persist(chat);try{await chatSync.publish(chat);}catch(e){notice(t('syncError'));}const messages=contextFor(chat,composerControls.modelVision()!=='no');const responseMessage={role:'assistant',content:'',at:Date.now(),interrupted:true};chat.messages.push(responseMessage);$('message').value='';controller=new AbortController();const signal=controller.signal;$('send').hidden=true;$('stop').hidden=false;updateStatus();renderConversation();
 try{const selected=await selectModel();for(let round=0;round<2;round++){
 const response=await fetch('/v1/chat/completions',{method:'POST',cache:'no-store',signal,headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:JSON.stringify({model:selected,messages,stream:true,store:false,max_tokens:4096,...(chat.reasoning?{reasoning_effort:chat.reasoning}:{}),...(round===0?{tools:navigationTools,parallel_tool_calls:false}:{})})});
 let calls=[];const answer=await readReply(response,value=>{responseMessage.content=value;if(session?.owner===owner&&current?.key===chat.key)renderConversation();},value=>{calls=value;});
 if(!calls.length)break;
 if(round!==0)throw new Error('Repeated navigation request');
 messages.push({role:'assistant',content:answer||null,tool_calls:calls});
 try{messages.push(await performNavigation(calls,{cid:chat.syncCid,open:(cid,channel,device)=>channelOpener.open(cid,channel,device),valid:()=>!signal.aborted&&session?.key===key&&session?.owner===owner&&current?.key===chat.key}));}
 catch(error){if(signal.aborted)throw error;messages.push(...calls.map(call=>({role:'tool',tool_call_id:call.id,content:JSON.stringify({opened:false,error:error.message})})));}
 responseMessage.content='';
 }responseMessage.interrupted=false;}
 catch(e){if(!responseMessage.content)chat.messages.pop();if(e.name!=='AbortError'){if(!fundsNotice(e)){const friendly=e.code==='provider_routes_unavailable'?t('modelNoRoutes'):e.code==='too_large'?t('tooLargeRequest'):'';notice(friendly||(e.status===401?t('needsLogin'):navigator.onLine?(e.message||t('requestError')):t('networkError')));}if(e.status===401)showAuth();}}
 finally{controller=null;sending=false;chat.updated=Date.now();if(runEpoch===epoch&&!chat.deleted){await persist(chat);try{await chatSync.publish(chat);}catch(e){notice(t('syncError'));}}$('send').hidden=false;$('stop').hidden=true;updateStatus();if(session?.owner===owner)renderConversation();}};
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
