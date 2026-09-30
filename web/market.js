/* Market overview is separate from the historical nested-FVG simulation. */
const marketUI={tf:'M15',source:'history',cursor:null,data:null,request:0,lastEvents:null,showAllEvents:false};
const m$=s=>document.querySelector(s);
const mfmt=(n,d=2)=>n==null?'—':Number(n).toLocaleString('ru-RU',{minimumFractionDigits:d,maximumFractionDigits:d});
const mesc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const eventReason=e=>String(e.reason||'').startsWith(String(e.label||'')+' ')?String(e.reason).slice(String(e.label).length+1):e.reason;
const mdate=t=>new Date(t*1000).toLocaleString('ru-RU',{timeZone:'UTC',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'})+' UTC';
const trendName={buy:'↑ Восходящий',sell:'↓ Нисходящий',neutral:'Боковой / смешанный'};
const planKey=e=>e.level_id?'level:'+e.level_id:e.zone_id?'zone:'+e.zone_id:null;
function currentBeginnerPlan(s,now=Math.floor(Date.now()/1000)){
 if(marketUI.source!=='live'||s.stale||!s.quote||now-s.quote.time<0||now-s.quote.time>90)return null;
 const events=s.plan_events||[];
 const resolved=new Set(events.filter(e=>['fvg_cancelled','fvg_confirmed','sbr_sell_closed',
   'rbs_buy_closed','sbr_sell_confirmed','rbs_buy_confirmed'].includes(e.type)).map(planKey));
 for(const e of events){
  const key=planKey(e);
  if(!key)continue;
  if(!['fvg_near','sbr_sell_plan','rbs_buy_plan'].includes(e.type)||!e.analysis_plan)continue;
  if(resolved.has(key)||e.time>now||now-e.time>15*60)continue;
  return e;
 }
 return null;
}
function renderBeginnerPlan(s){
 if(window.scenarioWorkspace)return;
 const title=m$('#beginnerHeadline'),description=m$('#beginnerExplanation'),levels=m$('#beginnerLevels');
 const plan=currentBeginnerPlan(s),live=marketUI.source==='live';
 levels.hidden=!plan;
 m$('#beginnerPlanTime').textContent=plan?
  (plan.type==='fvg_near'?'FVG-':plan.type==='sbr_sell_plan'?'SBR-':'RBS-')+
  String(plan.level_id||plan.zone_id).slice(0,12)+' · '+mdate(plan.time):'';
 m$('#beginnerCancel').textContent='';
 m$('#beginnerCaution').textContent='';
 if(!live){title.textContent='Выберите MetaTrader 5 для текущих сценариев';description.textContent='Архив и ручная зона служат для изучения прошлых ситуаций.';return}
 if(s.stale||!s.quote||Date.now()/1000-s.quote.time<0||Date.now()/1000-s.quote.time>90){title.textContent='Свежей котировки нет';description.textContent='Текущие уровни скрыты до восстановления данных MT5.';return}
 if(!plan){title.textContent='Свежего предварительного плана сейчас нет';description.textContent='Радар ждёт новых условий на закрытых свечах. Оценка индикаторов выше не является планом входа.';return}
 const p=plan.analysis_plan,buy=p.side==='long',fvg=plan.type==='fvg_near';
 title.textContent=(fvg?'Цена подошла к зоне. Проверьте возможную ':'Ждём ретеста для возможной ')+(buy?'покупки':'продажи');
 description.textContent=fvg?'Причина: цена приблизилась к вложенной зоне FVG; подтверждение M5 ещё ожидается.':
  'Причина: уровень пробит; нужен возврат к полосе и подтверждение M5.';
 const price=x=>Number(x).toLocaleString('ru-RU',{maximumFractionDigits:8});
 m$('#beginnerEntry').textContent=p.entry.map(price).join('–');
 m$('#beginnerStop').textContent=price(p.stop);
 m$('#beginnerTargets').textContent=p.targets.map(price).join(' · ');
 m$('#beginnerCancel').textContent='Когда план отменяется: '+p.cancel_rule+'.';
 m$('#beginnerCaution').textContent='План предварительный. Это ориентиры для анализа, не заявка и не гарантия входа, исполнения стопа или достижения целей.';
}
window.updateMarket=(s,source)=>{marketUI.source=source==='manual'?'history':source;marketUI.cursor=s?.time||null;loadMarket();window.scenarioWorkspace?.refresh(source)};
async function loadMarket(){const sequence=++marketUI.request;try{
 const q=new URLSearchParams({source:marketUI.source,tf:marketUI.tf});if(marketUI.cursor&&marketUI.source!=='live')q.set('cursor',marketUI.cursor);
 const r=await fetch('/api/technical?'+q);const s=await r.json();if(sequence!==marketUI.request)return;
 if(!r.ok)throw Error(s.error||'Не удалось загрузить обзор');marketUI.data=s;
 if(!s.ready){m$('#marketContent').hidden=true;m$('#marketEmpty').hidden=false;m$('#marketEmpty').textContent=s.message||'Для выбранного момента ещё нет закрытых свечей.';return}
 m$('#marketContent').hidden=false;m$('#marketEmpty').hidden=true;renderMarket(s);
 }catch(e){m$('#marketEmpty').hidden=false;m$('#marketEmpty').textContent=e.message;m$('#marketContent').hidden=true}}
function renderMarket(s){const v=s.frames[marketUI.tf];renderBeginnerPlan(s);m$('#techTime').textContent='Закрытие '+marketUI.tf+': '+mdate(v.end);
 m$('#marketTrend').textContent=trendName[v.trend];m$('#marketScore').textContent=v.score==null?'—':v.score+'/100';
 m$('#marketStatus').textContent=s.stale?'ДАННЫЕ УСТАРЕЛИ':v.status==='WATCH'?'УСЛОВИЯ СОВПАЛИ · ПРОВЕРЬТЕ ВРУЧНУЮ':v.ready?'ПОРОГ НЕ ДОСТИГНУТ':'НЕДОСТАТОЧНО СВЕЧЕЙ';
 m$('#marketStatus').className='status-pill '+(s.stale||v.status==='WATCH'?'warning':'');
 m$('#quoteInfo').textContent=s.quote?`Bid ${mfmt(s.quote.bid)} · Ask ${mfmt(s.quote.ask)} · Спред ${mfmt(s.quote.spread)} · ${mdate(s.quote.time)}`:'Архив: цена закрытия '+mfmt(v.close)+' USD';
 if(s.quote)m$('#price').textContent=mfmt(s.quote.bid);
 m$('#techRsi').textContent=mfmt(v.rsi,1);m$('#rsiMeaning').textContent=v.rsi==null?'Нужно 15 свечей':v.rsi>70?'Выше 70 · крайний импульс':v.rsi<30?'Ниже 30 · крайний импульс':'Внутри диапазона 30–70';
 m$('#techEma').innerHTML=`<span>20 <b>${mfmt(v.ema20)}</b></span><span>50 <b>${mfmt(v.ema50)}</b></span><span>200 <b>${mfmt(v.ema200)}</b></span>`;
 m$('#techMacd').textContent=mfmt(v.macd,3);m$('#macdMeaning').textContent=`Сигнал ${mfmt(v.macd_signal,3)} · гист. ${mfmt(v.macd_hist,3)}`;
 m$('#techAtr').textContent='$ '+mfmt(v.atr);m$('#atrMeaning').textContent=`Wilder 14 · ${mfmt(v.atr_ratio)}× медианы ATR`;
 m$('#techVolume').textContent=mfmt(v.volume_ratio)+'×';m$('#volumeMeaning').textContent='К среднему 20 предыдущих свечей';
 m$('#techVolatility').textContent=({high:'Высокая',medium:'Обычная',low:'Низкая',unknown:'Нет базы'})[v.volatility];
 m$('#techSupport').textContent=mfmt(v.support?.price);m$('#techResistance').textContent=mfmt(v.resistance?.price);
 m$('#supportDistance').textContent=v.support?'До уровня '+mfmt(Math.abs((s.quote?.bid??v.close)-v.support.price))+' USD':'Подтверждённый уровень ниже цены не найден';
 m$('#resistanceDistance').textContent=v.resistance?'До уровня '+mfmt(Math.abs(v.resistance.price-(s.quote?.bid??v.close)))+' USD':'Подтверждённый уровень выше цены не найден';
 m$('#volumeNote').textContent=s.volume_kind;
 m$('#scoreParts').innerHTML=v.score_parts.map(p=>`<div class="score-row"><span class="${p.met?'positive':'muted'}">${p.met?'✓':'○'} ${mesc(p.label)}</span><b>${p.points}/${p.weight}</b></div>`).join('');
 m$('#tfTable').innerHTML='<table><thead><tr><th>Период</th><th>Тренд</th><th>RSI</th><th>ATR</th><th>Условия</th></tr></thead><tbody>'+Object.entries(s.frames).map(([tf,x])=>`<tr><td>${tf}</td><td>${x?trendName[x.trend]:'—'}</td><td>${mfmt(x?.rsi,1)}</td><td>${mfmt(x?.atr)}</td><td>${x?.score==null?'Разогрев':x.score+'/100'}</td></tr>`).join('')+'</tbody></table>';
 drawTechnical(s.chart,v);renderPlan();
 const delivery={sent:'Telegram: доставлено',pending:'Telegram: очередь',failed:'Telegram: ошибка',sending:'Доставка не подтверждена',local_only:'Локальная лента',expired:'Доставка устарела',suppressed:'Повтор ограничен'};
 const repeats=s.events.filter(e=>e.delivery==='suppressed').length;
 const visible=marketUI.showAllEvents?s.events:s.events.filter(e=>e.delivery!=='suppressed').slice(0,20);
 m$('#technicalEventSummary').textContent=`Показано ${visible.length} из ${s.events.length}; повторов скрыто ${marketUI.showAllEvents?0:repeats}. Полный журнал доступен в CSV.`;
 const toggle=m$('#technicalEventsToggle');toggle.hidden=s.events.length<=20&&repeats===0;
 toggle.textContent=marketUI.showAllEvents?'Свернуть список':'Показать все события';
 m$('#technicalEvents').innerHTML=visible.length?visible.map(e=>`<article class="tech-event"><time>${mdate(e.time)} · ${mesc(e.tf)}</time><strong>${mesc(e.type==='watch'?'Совпали индикаторные условия':e.label)}</strong><p>${mesc(eventReason(e))}</p><small>${mesc(delivery[e.delivery]||'Историческое событие')}${e.delivery_detail?' · '+mesc(e.delivery_detail):''}</small></article>`).join(''):'<p class="empty">Новых событий пока нет. Стартовая история MT5 не выдаётся за новые сигналы.</p>';
 if(marketUI.source==='live'&&!s.stale&&marketUI.lastEvents&&m$('#techNotify').checked&&window.Notification?.permission==='granted'){
  for(const e of s.events.filter(e=>!marketUI.lastEvents.has(e.id)&&e.delivery!=='suppressed'))new Notification('XAU/USD · '+(e.type==='watch'?'Совпали индикаторные условия':e.label),{silent:true,body:e.tf+' · '+e.reason});
 }
 marketUI.lastEvents=marketUI.source==='live'?new Set(s.events.map(e=>e.id)):null;
}
function renderPlan(){const v=marketUI.data?.frames?.[marketUI.tf],p=v?.plan;
 if(!p){m$('#planValues').innerHTML='<p>Расчёт появится после разогрева и определения направления EMA.</p>';return}
 const equity=Number(m$('#calcEquity').value),risk=Number(m$('#calcRisk').value);
 const valid=Number.isFinite(equity)&&equity>0&&Number.isFinite(risk)&&risk>0&&risk<=5;
 const budget=valid?equity*risk/100:null,qty=budget==null?null:Math.floor(budget/p.distance*100)/100;
 m$('#planValues').innerHTML=`<div class="plan-grid"><div><small>Направление сценария</small><b>${p.direction==='buy'?'Вверх':'Вниз'}</b></div><div><small>От закрытия свечи</small><b>${mfmt(p.reference)}</b></div><div><small>Расчётный SL</small><b class="negative">${mfmt(p.stop)}</b></div><div><small>Расчётный TP · 2R</small><b class="positive">${mfmt(p.target)}</b></div><div><small>Условный риск</small><b>${mfmt(budget)} USD</b></div><div><small>Условный объём</small><b>${mfmt(qty)} унций</b></div></div><p class="muted">1,5 ATR до стопа. Это справочный сценарий, не заявка. Спред, комиссии, проскальзывание, маржа и размер лота брокера в этом калькуляторе не учтены.${!valid?' Укажите капитал > 0 и риск от 0 до 5%.':''}</p>`;
}
function drawTechnical(b,v){const svg=m$('#techChart');if(!b.length){svg.innerHTML='';return}
 const values=b.flatMap(x=>[x.high,x.low,x.ema20,x.ema50,x.ema200].filter(x=>x!=null));
 const rawLow=Math.min(...values),rawHigh=Math.max(...values),pad=Math.max(.2,(rawHigh-rawLow)*.08),lo=rawLow-pad,hi=rawHigh+pad;
 const y=p=>15+(hi-p)/(hi-lo)*330,dx=900/b.length,x=i=>12+(i+.5)*dx;let out=[];
 for(let i=0;i<5;i++){const p=lo+(hi-lo)*i/4;out.push(`<line x1="12" x2="920" y1="${y(p)}" y2="${y(p)}" stroke="#2a333f"/><text x="928" y="${y(p)+4}" fill="#99a5b3" font-size="11">${mfmt(p)}</text>`)}
 b.forEach((c,i)=>{const color=c.close>=c.open?'#67c6a3':'#e98085';out.push(`<line x1="${x(i)}" x2="${x(i)}" y1="${y(c.high)}" y2="${y(c.low)}" stroke="${color}"/><rect x="${x(i)-dx*.3}" y="${y(Math.max(c.open,c.close))}" width="${Math.max(1,dx*.6)}" height="${Math.max(1,Math.abs(y(c.open)-y(c.close)))}" fill="${color}"/>`)});
 for(const [key,color] of [['ema20','#e2bc76'],['ema50','#75aaff'],['ema200','#c790ee']])out.push(`<polyline points="${b.map((c,i)=>c[key]==null?'':`${x(i)},${y(c[key])}`).filter(Boolean).join(' ')}" fill="none" stroke="${color}" stroke-width="1.5"/>`);
 for(const [level,label,color] of [[v.support,'Поддержка','#67c6a3'],[v.resistance,'Сопротивление','#e98085']])if(level&&level.price>=lo&&level.price<=hi)out.push(`<line x1="12" x2="920" y1="${y(level.price)}" y2="${y(level.price)}" stroke="${color}" stroke-dasharray="6 5"/><text x="18" y="${y(level.price)-6}" fill="${color}" font-size="11">${label} ${mfmt(level.price)}</text>`);
 for(let i=0;i<b.length;i+=Math.max(1,Math.floor(b.length/4)))out.push(`<text x="${x(i)}" y="374" fill="#99a5b3" font-size="10">${new Date(b[i].time*1000).toLocaleString('ru-RU',{timeZone:'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</text>`);
 svg.innerHTML=out.join('');svg.onmousemove=e=>{const r=svg.getBoundingClientRect(),i=Math.max(0,Math.min(b.length-1,Math.floor(((e.clientX-r.left)/r.width*1000-12)/dx)));const c=b[i];m$('#techTooltip').textContent=mdate(c.time)+` · O ${mfmt(c.open)} H ${mfmt(c.high)} L ${mfmt(c.low)} C ${mfmt(c.close)}`};svg.onmouseleave=()=>m$('#techTooltip').textContent='Наведите курсор на свечу';
}
m$('#techTf').onchange=e=>{marketUI.tf=e.target.value;loadMarket()};m$('#calcEquity').oninput=renderPlan;m$('#calcRisk').oninput=renderPlan;
m$('#technicalEventsToggle').onclick=()=>{marketUI.showAllEvents=!marketUI.showAllEvents;if(marketUI.data?.ready)renderMarket(marketUI.data)};
m$('#techNotify').onchange=async e=>{if(e.target.checked&&'Notification' in window)e.target.checked=await Notification.requestPermission()==='granted';else e.target.checked=false};
async function telegramState(){const s=await(await fetch('/api/telegram')).json();m$('#telegramEnabled').checked=s.enabled;m$('#telegramChat').value=s.chat_id||'';m$('#telegramToken').placeholder=s.configured?'Токен сохранён; оставьте пустым для сохранения':'Токен от BotFather';m$('#telegramStatus').textContent=s.message;m$('#telegramTest').disabled=!s.configured}
m$('#telegramForm').onsubmit=async e=>{e.preventDefault();const button=m$('#telegramSave');button.disabled=true;try{const r=await fetch('/api/telegram',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:m$('#telegramToken').value,chat_id:m$('#telegramChat').value,enabled:m$('#telegramEnabled').checked})});const s=await r.json();if(!r.ok)throw Error(s.error);m$('#telegramToken').value='';await telegramState()}catch(err){m$('#telegramStatus').textContent=err.message}finally{button.disabled=false}};
m$('#telegramTest').onclick=async()=>{m$('#telegramTest').disabled=true;m$('#telegramStatus').textContent='Отправляется тестовое сообщение…';try{const r=await fetch('/api/telegram-test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});const s=await r.json();if(!r.ok)throw Error(s.error||'Telegram не ответил');m$('#telegramStatus').textContent=`${s.message}. ID ${s.message_id??'неизвестен'}; проверьте чат на телефоне.`}catch(e){m$('#telegramStatus').textContent=e.message}finally{m$('#telegramTest').disabled=false}};
telegramState().catch(e=>m$('#telegramStatus').textContent=e.message);
fetch('/api/technical-validation').then(r=>r.json()).then(s=>{m$('#technicalValidation').textContent=s.summary_text||'Историческая проверка нового обзора ещё не подготовлена.'}).catch(()=>{});
