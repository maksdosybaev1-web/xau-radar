const $=id=>document.getElementById(id);
const fmt=x=>x==null?'—':Number(x).toFixed(2);
const date=t=>new Date(t*1000).toISOString().replace('T',' ').slice(0,16)+' UTC';
const svgNS='http://www.w3.org/2000/svg';
function node(tag,attrs={},text){const e=document.createElement(tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);if(text!=null)e.textContent=text;return e}
function svg(tag,attrs={}){const e=document.createElementNS(svgNS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e}
function fact(box,label,value){const e=node('div');e.append(node('small',{},label),node('strong',{},value));box.append(e)}
function line(chart,y,color,label){chart.append(svg('line',{x1:64,x2:950,y1:y,y2:y,stroke:color,'stroke-width':2}));const t=svg('text',{x:954,y:y+4,fill:color,'font-size':13});t.textContent=label;chart.append(t)}
function draw(bars,level){const chart=$('sbrChart');chart.replaceChildren();if(!bars.length)return;
 const values=bars.flatMap(b=>[b.low,b.high]);for(const x of [level.low_band,level.high_band,level.stop,level.target])if(x!=null)values.push(x);
 let lo=Math.min(...values),hi=Math.max(...values);const pad=Math.max((hi-lo)*.08,.1);lo-=pad;hi+=pad;
 const y=p=>360-(p-lo)/(hi-lo)*310,x=i=>72+i*850/Math.max(1,bars.length-1);
 chart.append(svg('rect',{x:64,y:y(level.high_band),width:886,height:y(level.low_band)-y(level.high_band),fill:'#718898','fill-opacity':.2}));
 line(chart,y(level.level),'#d6a962','SBR '+fmt(level.level));
 if(level.stop!=null)line(chart,y(level.stop),'#dc7777','SL '+fmt(level.stop));
 if(level.target!=null)line(chart,y(level.target),'#65cba0','TP '+fmt(level.target));
 bars.forEach((b,i)=>{const xx=x(i),up=b.close>=b.open,color=up?'#48c99a':'#e47b78';chart.append(svg('line',{x1:xx,x2:xx,y1:y(b.high),y2:y(b.low),stroke:color,'stroke-width':1.5}));chart.append(svg('rect',{x:xx-2.5,y:Math.min(y(b.open),y(b.close)),width:5,height:Math.max(2,Math.abs(y(b.close)-y(b.open))),fill:color}));});
 const idx=bars.findIndex(b=>b.end===level.confirmation_time);if(idx>=0){const xx=x(idx);chart.append(svg('line',{x1:xx,x2:xx,y1:25,y2:375,stroke:'#40c99b','stroke-dasharray':'5 5'}));}
 for(const [yy,value] of [[32,hi],[367,lo]]){const t=svg('text',{x:7,y:yy,fill:'#b5c6d4','font-size':13});t.textContent=fmt(value);chart.append(t)}
 const first=svg('text',{x:64,y:405,fill:'#b5c6d4','font-size':13});first.textContent=date(bars[0].time);chart.append(first);
 const last=svg('text',{x:790,y:405,fill:'#b5c6d4','font-size':13});last.textContent=date(bars.at(-1).end);chart.append(last);
}
async function selectCase(id){const res=await fetch('/api/sbr?id='+encodeURIComponent(id));if(!res.ok)throw Error('Не удалось загрузить сценарий');const data=await res.json(),s=data.selected,l=s.level,t=s.trade;
 for(const b of document.querySelectorAll('.sbr-case'))b.setAttribute('aria-current',String(b.dataset.id===id));
 $('sbrTitle').textContent='SBR SELL · '+date(l.confirmation_time);$('sbrSubtitle').textContent='Поддержка стала известна '+date(l.known_at)+'. Пробой '+date(l.break_time)+'.';
 draw(s.bars,l);const box=$('sbrDetails');box.replaceChildren();fact(box,'Поддержка',fmt(l.level));fact(box,'Допуск',fmt(l.tolerance));fact(box,'Контекст H1',l.h1_context_confirmation||'—');fact(box,'Исход',t?.state==='closed'?fmt(t.r)+' R · '+t.exit_reason:l.state+' · '+s.events.at(-1).reason);
 const body=$('sbrEvents');body.replaceChildren();for(const e of s.events){const tr=node('tr');tr.append(node('td',{},date(e.time)),node('td',{},e.state),node('td',{},e.reason));body.append(tr)}
}
async function init(){try{const res=await fetch('/api/sbr');const data=await res.json();if(!data.ready)throw Error(data.message||'Нет данных');
 $('sbrStatus').textContent=data.source+' · '+data.version;const box=$('sbrSummary');fact(box,'Опорных уровней',data.summary.levels);fact(box,'Подтверждённых возвратов',data.cases.length);fact(box,'Завершённых опытов',data.summary.closed);fact(box,'Средний результат',data.summary.average_r==null?'—':fmt(data.summary.average_r)+' R');
 const list=$('sbrCases');for(const c of data.cases){const b=node('button',{class:'sbr-case','data-id':c.id,'aria-current':'false'});b.append(node('strong',{},date(c.confirmation_time)),node('small',{},'L '+fmt(c.level)+' · '+c.state+(c.r==null?'':' · '+fmt(c.r)+' R')));b.onclick=()=>selectCase(c.id).catch(e=>$('sbrStatus').textContent=e.message);list.append(b)}
 if(data.cases.length)await selectCase(data.cases[0].id);
 }catch(e){$('sbrStatus').textContent=e.message}}
async function live(){try{const res=await fetch('/api/sbr-live'),d=await res.json();if(!d.version)throw Error(d.message||'Нет данных MT5');
 $('sbrLiveStatus').textContent=d.bridge_stale?'Мост MT5 не обновляет данные.':d.stale?'Котировка MT5 устарела; новых сигналов нет.':d.ready?'Наблюдение готово.':'Идёт накопление закрытых H1/M15.';
 const box=$('sbrLiveFacts');box.replaceChildren();fact(box,'Закрытых H1',d.h1_bars+'/240');fact(box,'Закрытых M15',d.m15_bars+'/120');fact(box,'Последняя M1',d.last_m1?date(d.last_m1):'—');fact(box,'Событий в журнале',d.forward_events_total);fact(box,'После запуска',d.forward_events_this_run);
 const delivery={sent:'Доставлено',pending:'В очереди',sending:'Отправляется',failed:'Ошибка',expired:'Истекло',suppressed:'Повтор подавлен',local_only:'Только журнал'};
 fact(box,'Telegram',d.telegram?.enabled&&d.telegram?.configured?'Включён':'Выключен');const latest=d.telegram_alerts?.[0];fact(box,'Последняя доставка SBR',latest?(latest.type==='sbr_sell_plan'?'План: ':'Подтверждение: ')+(delivery[latest.delivery]||latest.delivery):'Ещё нет');
 const delivered=new Map((d.telegram_alerts||[]).map(a=>[a.id,a]));
 const events=$('sbrLiveEvents');events.replaceChildren();for(const e of d.events.slice().reverse().slice(0,15)){const alert=delivered.get(e.delivery_alert?.id);const status=alert?' · Telegram: '+(delivery[alert.delivery]||alert.delivery):'';const item=node('p',{class:'sbr-muted'},date(e.time)+' · '+e.state+' · '+e.reason+' · '+e.observation_quality+status);events.append(item)}
 }catch(e){$('sbrLiveStatus').textContent=e.message}}
init();live();setInterval(live,30000);
