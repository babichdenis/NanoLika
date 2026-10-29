export function amountValue(raw){
 const value=String(raw).trim();
 if(!/^\d{1,6}([.,]\d{1,2})?$/.test(value))return null;
 const [whole,fraction='']=value.replace(',','.').split('.');
 const cents=Number(whole)*100+Number(fraction.padEnd(2,'0'));
 if(!Number.isSafeInteger(cents)||cents<1||cents>10000000)return null;
 return {cents,amount:`${Number(whole)}.${fraction.padEnd(2,'0')}`};
}
export function groupWebDevices(devices){
 const web=devices.filter(d=>d.kind==='web');
 const ordinary=devices.filter(d=>d.kind!=='web');
 if(!web.length)return ordinary;
 return [...ordinary,{kind:'web',name:'web',online:web.some(d=>d.online),last_seen:Math.max(0,...web.map(d=>Number(d.last_seen)||0)),connections:web}];
}
export function bankTarget(value){
 try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&['pay.tbank-online.com','securepayments.tinkoff.ru','securepayments.tbank.ru','securepay.tinkoff.ru','securepay.tbank.ru','yookassa.ru','yoomoney.ru','auth.robokassa.ru'].includes(u.hostname)?u.href:null;}catch{return null;}
}
