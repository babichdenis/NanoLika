import {tr} from './i18n.js';
const labels={idle:[tr('Готова помочь'),tr('Дыхание и моргание.')],working:[tr('Работает'),tr('Печатает на ноутбуке, сосредоточена на задаче.')],waiting:[tr('Ждёт решения'),tr('Переводит внимание на вас, ждёт подтверждения.')],happy:[tr('Готово'),tr('Улыбается и радуется результату.')],error:[tr('Что-то не вышло'),tr('Спокойное смущение, без испуга.')]};
const release=new URLSearchParams(location.search).get('release');
document.getElementById('play').hidden=true;
document.getElementById('pause').hidden=true;
document.getElementById('access').onsubmit=async event=>{
 event.preventDefault();
 const key=document.getElementById('key').value;document.getElementById('key').value='';
 try{
 const response=await fetch('/v1/admin/avatar-releases',{cache:'no-store',headers:{'X-Admin-Token':key}});
 if(!response.ok)throw new Error(tr('Для предпросмотра нужен ключ администратора.'));
 const data=await response.json();if(!data.releases.some(p=>p.id===release))throw new Error(tr('Набор не найден.'));
 document.getElementById('access').hidden=true;document.getElementById('play').hidden=false;document.getElementById('pause').hidden=false;
 document.getElementById('status').textContent='';
 if(!/^[a-f0-9]{24}$/.test(release||''))document.getElementById('status').textContent=tr('Откройте предпросмотр набора из кабинета управления образами.');
else for(const [mood,[title,description]] of Object.entries(labels)){const base='/v1/brand/avatar/assets/'+release+'/'+mood;const card=document.createElement('article');card.className='card';const frame=document.createElement('div');frame.className='large';const image=document.createElement('img');image.src=base+'.webp';image.alt=title;frame.append(image);if(mood!=='error'){const video=document.createElement('video');video.src=base+'.mp4';video.poster=image.src;video.muted=true;video.loop=true;video.playsInline=true;video.preload='none';video.hidden=true;frame.append(video);}const h=document.createElement('h2');h.textContent=title;const p=document.createElement('p');p.textContent=description;const small=document.createElement('div');small.className='small';for(const size of [44,76]){const img=document.createElement('img');img.src=image.src;img.alt=title+' '+size+' px';img.width=size;img.height=size;small.append(img);}card.append(frame,h,p,small);document.getElementById('cards').append(card);}

 }catch(error){document.getElementById('status').textContent=error.message;}
};
document.getElementById('play').onclick=()=>document.querySelectorAll('video').forEach(async v=>{v.hidden=false;try{await v.play();}catch{v.hidden=true;document.getElementById('status').textContent=tr('Не удалось включить клип, показана картинка.');}});document.getElementById('pause').onclick=()=>document.querySelectorAll('video').forEach(v=>{v.pause();v.hidden=true;});document.addEventListener('visibilitychange',()=>{if(document.hidden)document.querySelectorAll('video').forEach(v=>v.pause());});
