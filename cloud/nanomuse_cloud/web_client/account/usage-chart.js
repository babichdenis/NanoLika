const words={ru:{day:'День',week:'Неделя',month:'Месяц',spent:'Расход за период',calls:'Обращения к моделям',tokens:'Токены',loading:'Загружаем расход…',error:'Не удалось загрузить расход. Попробуйте обновить кабинет.',empty:'За этот период платных списаний нет.',part:'от общей суммы',hour:'За час',date:'За день',graph:'Расход по моделям и времени',unknown:'Модель не указана',legacy:'Исторические суммы рассчитаны по настройкам релея.',all:'Все модели'},en:{day:'Day',week:'Week',month:'Month',spent:'Period spend',calls:'Model calls',tokens:'Tokens',loading:'Loading usage…',error:'Could not load usage. Refresh your account to retry.',empty:'No paid usage in this period.',part:'of total spend',hour:'This hour',date:'This day',graph:'Spend by model and time',unknown:'Unspecified model',legacy:'Historical amounts use the relay configuration.',all:'All models'},zh:{day:'日',week:'周',month:'月',spent:'期间费用',calls:'模型调用',tokens:'令牌',loading:'正在读取用量…',error:'无法读取用量，请刷新账户重试。',empty:'此期间无付费扣款。',part:'占总费用',hour:'此小时',date:'此日',graph:'按模型及时间的费用',unknown:'未指定模型',legacy:'历史金额按中继配置计算。',all:'所有模型'}};
export function installUsageChart({lang,request}){
 const T=words[lang]||words.ru,$=id=>document.getElementById(id),root=$('usage-chart');let period='week',generation=0,locked=null;
 const h=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
 const svg=(tag,attrs)=>{const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;};
 const money=n=>new Intl.NumberFormat(lang,{style:'currency',currency:'RUB',maximumFractionDigits:6}).format(n||0);
 const pct=n=>new Intl.NumberFormat(lang,{style:'percent',maximumFractionDigits:1}).format(n||0);
 const assigned=new Map(),usedHues=new Set();
 const color=id=>{if(assigned.has(id))return assigned.get(id);let hash=0;for(const c of id)hash=(hash*31+c.charCodeAt(0))>>>0;let hue=hash%360;let attempts=0;while(usedHues.has(hue)&&attempts++<360)hue=(hue+37)%360;usedHues.add(hue);const value=`hsl(${hue} 46% 45%)`;assigned.set(id,value);return value;};
 const controls=$('usage-periods');for(const p of ['day','week','month']){const b=h('button',T[p]);b.type='button';b.dataset.period=p;b.setAttribute('aria-pressed',String(p===period));b.onclick=()=>{period=p;load();};controls.append(b);}
 async function load(){
  const seq=++generation;root.replaceChildren(h('p',T.loading));for(const b of controls.children)b.setAttribute('aria-pressed',String(b.dataset.period===period));
  try{const tz=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';const data=await request('/v1/me/usage-chart?period='+period+'&tz='+encodeURIComponent(tz));if(seq!==generation)return;draw(data);}catch(_){if(seq===generation)root.replaceChildren(h('p',T.error));}
 }
 function draw(data){
  locked=null;const series=(data.series||[]).filter(s=>Number(s.total_rub)>0),buckets=data.buckets||[];
  root.replaceChildren();const metrics=h('div',undefined,'usage-metrics');for(const [label,value]of [[T.spent,money(data.total_rub)],[T.calls,new Intl.NumberFormat(lang).format(data.requests||0)],[T.tokens,new Intl.NumberFormat(lang,{notation:'compact',maximumFractionDigits:1}).format(data.tokens||0)]]){const metric=h('div',undefined,'usage-metric');metric.append(h('span',label),h('strong',value));metrics.append(metric);}root.append(metrics);
  if(!series.length||!buckets.length){root.append(h('p',T.empty));return;}
  const W=Math.max(300,Math.min(900,root.clientWidth-28)),H=230,left=W<500?62:86,right=20,top=16,bottom=42,plotW=W-left-right,plotH=H-top-bottom;
  const totals=buckets.map((_,i)=>series.reduce((n,s)=>n+Number(s.values[i]||0),0)),max=Math.max(...totals,0.000001);
  const graph=svg('svg',{viewBox:`0 0 ${W} ${H}`,role:'img','aria-label':T.graph}),group=svg('g',{});graph.append(group);
  for(let i=0;i<3;i++){const y=top+plotH*(1-i/2),line=svg('line',{x1:left,x2:W-right,y1:y,y2:y,stroke:'currentColor','stroke-opacity':'.15'}),label=svg('text',{x:left-10,y:y+4,'text-anchor':'end',class:'axis-label'});label.textContent=money(max*i/2);graph.append(line,label);}
  const fmt=ts=>new Intl.DateTimeFormat(lang,{timeZone:data.timezone,...(data.period==='day'?{hour:'2-digit',minute:'2-digit'}:{day:'numeric',month:'short'})}).format(ts*1000);
  const info=h('p',`${T.all}: ${money(data.total_rub)}`,'usage-chart-info');info.id='usage-chart-info';info.setAttribute('aria-live','polite');
  const tip=h('div',undefined,'usage-tooltip');tip.hidden=true;tip.setAttribute('aria-hidden','true');
  function showTip(index,event){tip.replaceChildren();const header=h('div',undefined,'usage-tooltip-title');header.append(h('strong',fmt(buckets[index])),h('strong',money(totals[index])));tip.append(header);for(const item of series){const v=Number(item.values[index]||0);if(v<=0)continue;const line=h('div',undefined,'usage-tooltip-row'),dot=h('span',undefined,'usage-model-dot');dot.style.backgroundColor=color(item.model);line.append(dot,h('span',item.model||T.unknown),h('strong',money(v)));tip.append(line);}tip.hidden=false;const box=root.getBoundingClientRect();const x=event?.clientX?event.clientX-box.left:root.clientWidth/2;tip.style.left=Math.max(8,Math.min(root.clientWidth-tip.offsetWidth-8,x-tip.offsetWidth/2))+'px';tip.style.top=(graph.offsetTop+12)+'px';}
  const rects=[],buttons=[];const legend=h('div',undefined,'usage-chart-legend');legend.setAttribute('aria-label',T.all);
  function highlight(model,detail){const chosen=series.find(s=>s.model===model);for(const [n,id]of rects)n.setAttribute('opacity',!model||id===model?'1':'.18');for(const[b,id]of buttons){b.classList.toggle('model-muted',!!model&&id!==model);b.setAttribute('aria-pressed',String(locked===id));}info.textContent=detail||(chosen?`${chosen.model||T.unknown}: ${money(chosen.total_rub)} · ${pct(chosen.total_rub/data.total_rub)} ${T.part}`:`${T.all}: ${money(data.total_rub)}`);}
  buckets.forEach((timestamp,i)=>{
   let height=0;const step=plotW/buckets.length,barW=Math.max(4,step*.66),x=left+i*step+(step-barW)/2;
   for(const s of series){const value=Number(s.values[i]||0);if(value<=0)continue;const size=value/max*plotH,r=svg('rect',{x,y:top+plotH-height-size,width:barW,height:size,fill:color(s.model),tabindex:'0',role:'button','aria-label':`${fmt(timestamp)} · ${s.model||T.unknown}: ${money(value)}`});height+=size;
    const detail=()=>`${fmt(timestamp)} · ${s.model||T.unknown}: ${money(value)} · ${pct(value/totals[i])} (${data.period==='day'?T.hour:T.date}). ${money(s.total_rub)} · ${pct(s.total_rub/data.total_rub)} ${T.part}`;
    r.addEventListener('pointerenter',e=>{highlight(s.model,detail());showTip(i,e);});r.addEventListener('pointerleave',()=>{highlight(locked);tip.hidden=true;});r.addEventListener('focus',()=>{highlight(s.model,detail());showTip(i);});r.addEventListener('blur',()=>{highlight(locked);tip.hidden=true;});r.addEventListener('click',()=>{locked=locked===s.model?null:s.model;highlight(locked);});r.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();r.dispatchEvent(new Event('click'));}});group.append(r);rects.push([r,s.model]);
   }
   const every=Math.ceil(buckets.length/(W<500?3:7));if(i%every===0||i===buckets.length-1){const n=svg('text',{x:x+barW/2,y:H-14,'text-anchor':i===buckets.length-1?'end':'middle',class:'axis-label'});n.textContent=fmt(timestamp);graph.append(n);}
  });
  for(const s of series){const b=h('button',undefined,'usage-model');b.type='button';const dot=h('span',undefined,'usage-model-dot');dot.style.backgroundColor=color(s.model);dot.setAttribute('aria-hidden','true');b.append(dot,h('span',s.model||T.unknown),h('strong',money(s.total_rub)));b.setAttribute('aria-pressed','false');b.onpointerenter=()=>highlight(s.model);b.onpointerleave=()=>highlight(locked);b.onfocus=()=>highlight(s.model);b.onblur=()=>highlight(locked);b.onclick=()=>{locked=locked===s.model?null:s.model;highlight(locked);};buttons.push([b,s.model]);legend.append(b);}
  root.append(graph,info,legend,tip);if(data.basis==='legacy_config_ledger')root.append(h('p',T.legacy,'usage-chart-basis'));
 }
 return {load};
}
