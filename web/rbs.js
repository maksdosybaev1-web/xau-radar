const $=id=>document.getElementById(id);
const date=t=>new Date(t*1000).toISOString().replace('T',' ').slice(0,16)+' UTC';
function fact(box,label,value){const item=document.createElement('div'),small=document.createElement('small'),strong=document.createElement('strong');small.textContent=label;strong.textContent=String(value);item.append(small,strong);box.append(item)}
async function refresh(){try{
 const response=await fetch('/api/rbs-live'),d=await response.json();if(!d.version)throw Error(d.message||'Нет данных MT5');
 $('rbsStatus').textContent=d.bridge_stale?'Мост MT5 не обновляет данные.':d.stale?'Котировка MT5 устарела; новых сигналов нет.':d.ready?'Наблюдение готово.':'Идёт накопление закрытых H1/M15.';
 const box=$('rbsFacts');box.replaceChildren();fact(box,'Закрытых H1',d.h1_bars+'/240');fact(box,'Закрытых M15',d.m15_bars+'/120');fact(box,'Последняя M1',d.last_m1?date(d.last_m1):'—');fact(box,'Событий в журнале',d.forward_events_total);fact(box,'После запуска',d.forward_events_this_run);
 const delivery={sent:'Доставлено',pending:'В очереди',sending:'Отправляется',failed:'Ошибка',expired:'Истекло',suppressed:'Повтор подавлен',local_only:'Только журнал'};
 fact(box,'Telegram',d.telegram?.enabled&&d.telegram?.configured?'Включён':'Выключен');const latest=d.telegram_alerts?.[0];fact(box,'Последняя доставка RBS',latest?(latest.type==='rbs_buy_plan'?'План: ':'Подтверждение: ')+(delivery[latest.delivery]||latest.delivery):'Ещё нет');
 const delivered=new Map((d.telegram_alerts||[]).map(a=>[a.id,a]));
 const events=$('rbsEvents');events.replaceChildren();for(const e of d.events.slice().reverse().slice(0,15)){const alert=delivered.get(e.delivery_alert?.id);const status=alert?' · Telegram: '+(delivery[alert.delivery]||alert.delivery):'';const p=document.createElement('p');p.className='rbs-muted';p.textContent=date(e.time)+' · '+e.state+' · '+e.reason+' · '+e.observation_quality+status;events.append(p)}
 if(!d.events.length){const p=document.createElement('p');p.className='rbs-muted';p.textContent='Новых событий пока нет.';events.append(p)}
}catch(e){$('rbsStatus').textContent=e.message}}
refresh();setInterval(refresh,30000);
