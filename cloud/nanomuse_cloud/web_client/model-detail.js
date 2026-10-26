/** Customer prices and a non-billable estimate, all supplied by the relay. */
export function modelDetails(row,{api}){
 const facts=row.nanomuse||row, commerce=facts.commerce||{}, box=document.createElement('div');box.className='model-details';
 const add=(tag,text)=>{const e=document.createElement(tag);e.textContent=text;box.append(e);return e;};
 const money=n=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:6}).format(n)+' ₽';
 if(facts.context_length)add('p',`Контекст: ${Number(facts.context_length).toLocaleString('ru-RU')} токенов${facts.max_output?' · Ответ до '+Number(facts.max_output).toLocaleString('ru-RU'):''}`);
 const labels={input:'Вход / 1 млн токенов',cached_input:'Кешированный вход / 1 млн',output:'Ответ / 1 млн токенов',image:'Картинка',image_2k:'Картинка 2K',image_input:'Исходная картинка',second:'Секунда видео',speech:'1000 знаков',minute:'Минута записи'};
 const kind=facts.kind||'chat', unit=commerce.unit;
 const fields=kind==='chat'?['input','cached_input','output']:kind==='image'?['image','image_2k','image_input']:kind==='video'?['second']:unit==='speech'?['speech']:['minute'];
 const rates=commerce.effective_rub||{};
 for(const key of fields)if(Number(rates[key])>0)add('p',`${labels[key]}: ${money(rates[key])}`);
 if(commerce.discount_pct)add('p',`Учтена скидка ${commerce.discount_pct}% действующего тарифа.`);
 const controls=commerce.controls||[];if(!controls.length){add('p','Для этого режима пока нет расчёта.');return box;}
 const form=document.createElement('div');form.className='model-estimate-controls';box.append(form);
 let params={model:row.id},timer=0,revision=0;
 const result=add('output',''),note=add('small',kind==='chat'?'Оценка по заданному числу токенов. История, инструменты и дополнительные рассуждения могут увеличить расход.':'Предварительный расчёт, генерация не запускается.');note.className='model-estimate-note';
 async function calculate(){const version=++revision;result.textContent='Считаем…';try{const q=await api('/v1/models/quote',{method:'POST',body:JSON.stringify(params)});if(version===revision)result.textContent='Примерная стоимость: '+money(q.total_rub);}catch{if(version===revision)result.textContent='Не удалось рассчитать. Проверьте параметры.';}}
 for(const control of controls){const label=document.createElement('label');label.textContent=control.label;const input=document.createElement(control.type==='select'?'select':'input');input.setAttribute('aria-label',control.label);if(control.type==='select'){for(const value of control.options||[]){const opt=document.createElement('option');opt.value=value;opt.textContent=value;input.append(opt);}}else{input.type='number';input.min='0';input.step='1';}input.value=control.default;params[control.key]=control.default;input.oninput=()=>{params[control.key]=control.type==='number'?Number(input.value):input.value;revision++;clearTimeout(timer);timer=setTimeout(calculate,250);};label.append(input);form.append(label);}
 void calculate();return box;
}
