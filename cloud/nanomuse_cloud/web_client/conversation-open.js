/* One stable browser control connection. No model requests or server administration. */
const WORDS={"ru": {"busy": "Дождитесь завершения переключения.", "choose": "Выберите беседу и мессенджер.", "login": "Войдите в аккаунт Лики.", "account": "Аккаунт изменился.", "timeout": "Не удалось подтвердить переключение. Проверьте мессенджер перед повтором.", "unavailable": "В кабинете откройте «Устройства и каналы», подключите выбранное приложение и повторите переключение.", "failed": "Не удалось открыть беседу. Проверьте подключение приложения или бота.", "unconfirmed": "Переключение не подтверждено.", "connect": "Не удалось подключиться к мессенджерам.", "closed": "Соединение закрыто до подтверждения переключения."}, "en": {"busy": "Wait for the current switch to finish.", "choose": "Choose a conversation and messenger.", "login": "Sign in to your Lika account.", "account": "The account changed.", "timeout": "The switch was not confirmed. Check the messenger before trying again.", "unavailable": "Messenger connection is unavailable. Open the bot menu.", "failed": "Could not select the conversation. Check the bot connection.", "unconfirmed": "The conversation switch was not confirmed.", "connect": "Could not connect to the messengers.", "closed": "Connection closed before the switch was confirmed."}, "zh": {"busy": "请等待当前切换完成。", "choose": "请选择对话和聊天应用。", "login": "请登录 Lika 账号。", "account": "账号已更改。", "timeout": "无法确认切换，请先检查聊天应用再重试。", "unavailable": "聊天应用连接不可用，请打开机器人菜单。", "failed": "无法选择对话，请检查机器人连接。", "unconfirmed": "未确认对话切换。", "connect": "无法连接聊天应用。", "closed": "连接在确认切换前已关闭。"}};
export class ConversationOpener {
 constructor({session,device,WebSocketImpl=globalThis.WebSocket,url,language=()=>'ru'}){this.session=session;this.device=device;this.WebSocketImpl=WebSocketImpl;this.url=url;this.busy=false;this.language=language;}
 error(key,details={}){return Object.assign(new Error((WORDS[this.language()]||WORDS.ru)[key]),details);}
 async open(cid,channel,device=""){
  if(this.busy)throw this.error('busy');
  if(!['telegram','max','desktop'].includes(channel)||typeof cid!=='string'||!cid||cid.length>160)throw this.error('choose');
  const initial=this.session(),session=initial?{key:initial.key,owner:initial.owner}:null;if(!session?.key||!session.owner)throw this.error('login');
  this.busy=true;
  try{return await new Promise((resolve,reject)=>{
   const endpoint=this.url||new URL('/v1/hub',location.origin).href.replace(/^http/,'ws');
   const socket=new this.WebSocketImpl(endpoint);const id=crypto.randomUUID();let done=false,target='',sent=false;
   const valid=()=>this.session()?.key===session.key&&this.session()?.owner===session.owner;
   const finish=(error,value)=>{if(done)return;done=true;clearTimeout(timer);socket.close();error?reject(error):resolve(value);};
   const timer=setTimeout(()=>finish(this.error('timeout')),45000);
   socket.onopen=()=>{if(!valid())return finish(this.error('account'));socket.send(JSON.stringify({type:'hello',key:session.key,device:{id:this.device,name:'nanoLika Web',kind:'web',actions:[]}}));};
   socket.onmessage=event=>{let frame;try{frame=JSON.parse(event.data);}catch{return;}
    if(!valid())return finish(this.error('account'));
    if(frame.type==='devices'&&!sent){const action=channel==='desktop'?'chat.open_desktop':'chat.open_channel';const hosts=(frame.devices||[]).filter(d=>d.online&&d.kind!=='web'&&(d.actions||[]).includes(action)&&(!device||d.id===device));
     if(hosts.length!==1)return finish(this.error('unavailable',{code:'target_unavailable',channel,targets:hosts.map(({id,name})=>({id,name}))}));
     target=hosts[0].id;sent=true;socket.send(JSON.stringify({type:'call',id,to:target,action,args:{cid,channel}}));
    }else if(frame.id===id&&frame.type==='result'){
     if(frame.from?.id!==target)return;
     if(frame.ok===false)return finish(this.error('failed'));
     const body=frame.body;if(body?.opened!==true||body.cid!==cid||body.channel!==channel||(channel==='desktop'&&body.client_view_changed!==true))return finish(this.error('unconfirmed'));
     finish(null,body);
    }else if(frame.type==='error'&&(!frame.id||frame.id===id))finish(this.error('failed'));
   };
   socket.onerror=()=>finish(this.error('connect'));
   socket.onclose=()=>{if(!done)finish(this.error('closed'));};
  });}finally{this.busy=false;}
 }
}
