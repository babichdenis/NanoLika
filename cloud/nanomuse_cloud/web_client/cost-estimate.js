/* Local arithmetic only: declared rates are never replaced with guessed prices. */
export function costEstimate(model, rates, language = 'ru') {
  const ru = language === 'ru', zh = language === 'zh';
  const h = (tag, text, cls) => { const n = document.createElement(tag); if(text !== undefined)n.textContent=text; if(cls)n.className=cls; return n; };
  const root=h('form',undefined,'cost-estimate'); root.onsubmit=e=>e.preventDefault();
  const heading=h('div',undefined,'cost-estimate-heading');heading.append(h('strong',model.name||model.id),h('code',model.id));root.append(heading);
  const kind=model.kind, audio=model.audio?.kind||model.commerce?.unit;
  const fields=kind==='chat' ? [['input',ru?'Токены запроса':zh?'输入令牌':'Input tokens',1000,1000000],['output',ru?'Токены ответа':zh?'输出令牌':'Output tokens',500,1000000]]
    : kind==='image' ? [['image',ru?'Изображения':zh?'图片':'Images',1,1]]
    : kind==='video' ? [['second',ru?'Секунды видео':zh?'视频秒数':'Video seconds',5,1]]
    : audio==='speech' ? [['speech',ru?'Знаки текста':zh?'字符数':'Characters',1000,1000]]
    : audio==='music'||audio==='track' ? [['track',ru?'Треки':zh?'曲目':'Tracks',1,1]]
    : audio==='transcribe'||audio==='minute' ? [['minute',ru?'Минуты записи':zh?'录音分钟':'Audio minutes',1,1]] : [];
  const controls=h('div',undefined,'cost-estimate-controls'), inputs=[];
  for(const [key,label,value,scale] of fields){const field=h('label'),input=h('input');input.type='number';input.min='0';input.max='100000000';input.step=key==='second'||key==='minute'?'0.1':'1';input.value=String(value);field.append(h('span',label),input);controls.append(field);inputs.push({key,input,scale});}
  const total=h('div',undefined,'cost-estimate-total'),label=h('span',ru?'Примерная стоимость':zh?'预计费用':'Estimated cost'),value=h('output');value.setAttribute('aria-live','polite');total.append(label,value);controls.append(total);root.append(controls);
  const note=h('p',ru?'Расчёт по указанным ценам. Реальный расход зависит от объёма ответа и параметров генерации. Кэш и дополнительные обращения здесь не учитываются.':zh?'按当前价格估算，实际费用取决于回复长度和生成设置，不包括缓存及额外调用。':'Estimate at the displayed rates. Actual cost depends on output and generation settings; cache and additional calls are not included.');root.append(note);
  function update(){let cost=0,valid=inputs.length>0;for(const {key,input,scale}of inputs){const n=Number(input.value),rate=rates?.[key];if(input.value===''||!Number.isFinite(n)||n<0||!input.checkValidity()){valid=false;break;}if(n>0&&(typeof rate!=='number'||!Number.isFinite(rate)||rate<0)){valid=false;break;}cost+=n*(rate||0)/scale;}value.textContent=valid?new Intl.NumberFormat(language,{style:'currency',currency:'RUB',maximumFractionDigits:0}).format(cost):(ru?'Цена уточняется':zh?'价格待定':'Price unavailable');}
  for(const {input}of inputs)input.oninput=update;update();return root;
}
