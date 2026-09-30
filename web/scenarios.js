/* One live scenario, its user's decision and manually reported broker outcome. */
(() => {
 const $ = id => document.getElementById(id);
 const fmt = x => Number(x).toLocaleString('ru-RU',{maximumFractionDigits:8});
 const money = x => Number(x).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 const stamp = x => new Date(x*1000).toLocaleString('ru-RU',{timeZone:'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})+' UTC';
 const localNow = () => new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
 const esc = s => String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
 let snapshot=null,selectedId=null,request=0,source='history';
 let audio=null,soundEnabled=false;
 const sounded=new Set();
 const stages={waiting:'Ждём возврата к зоне',near:'Цена у зоны · ждём M5',confirmed:'M5 подтвердила условия',ended:'Сценарий завершён',expired:'Срок плана истёк'};
 const currentRow=()=>snapshot?.scenarios.find(r=>r.id===(selectedId||snapshot.active_id))||null;
 const clearSummary=()=>{for(const id of ['beginnerNext','beginnerTargetMethod','beginnerLot'])$(id).textContent='';$('beginnerHeadline').className='';};
 function countdown(){
  const row=currentRow();
  $('assistantCountdown').textContent='';
  if(source!=='live'||!row?.checks.current||!row.valid_until)return;
  const remaining=Math.max(0,row.valid_until-Math.floor(Date.now()/1000));
  $('assistantCountdown').textContent=remaining?`До истечения плана: ${Math.floor(remaining/60)} мин ${remaining%60} сек. Отмена возможна раньше.`:'Срок плана истёк. Ждём обновления состояния.';
 }
 function renderAssistant(row){
  let guide=source!=='live'?{title:'Выберите MT5 для активного наблюдения',steps:['Выберите источник «MetaTrader 5 · подключение».','Здесь появятся следующие шаги по новым сценариям.']}:
   !snapshot?{title:'Проверяю данные и сценарии…',steps:[]}:
   snapshot.data_ready===false?{title:'Пауза: проверка сценариев приостановлена',steps:[snapshot.data_reason,'Проверьте подключение и открытые позиции в MT5.']}:
   row?.guidance||{title:'Готового сценария сейчас нет',steps:['Посмотрите зоны и причины последних событий.',
    'Дождитесь нового FVG, SBR или RBS-плана; ранний план потребуется подтвердить.',
    'Подготовьте MT5 и проверьте действующие стопы. Новые планы также приходят в Telegram.']};
  $('assistantTitle').textContent=guide.title;
  $('assistantTitle').className=guide.tone==='ready'&&row?.checks.ready_to_review_entry?'assistant-ready':'';
  $('assistantSteps').innerHTML=(guide.steps||[]).map(step=>`<li>${esc(step)}</li>`).join('');
  const live=row?.checks.current,c=row?.checks;
  const gates=live?[[c.data_ready,'Данные'],[row.stage==='confirmed','Подтверждение M5'],
   [c.in_zone,'Цена в зоне'],[c.spread_ok,'Спред'],[c.unknown_open_risk===0,'Известный риск'],
   [c.max_lots_under_budget>0,'Доступный объём']]:[];
  $('assistantChecklist').innerHTML=gates.map(([ok,label])=>`<span class="${ok?'check-ok':'check-warn'}">${ok?'✓':'○'} ${label}</span>`).join('');
  $('assistantDistance').textContent=live&&c.quote_price!=null?`Цена для ${row.plan.side==='long'?'покупки (Ask)':'продажи (Bid)'}: ${fmt(c.quote_price)}. `+
   (c.in_zone?'Цена находится в зоне.':`До зоны: ${fmt(Math.max(row.plan.entry[0]-c.quote_price,c.quote_price-row.plan.entry[1],0))} USD на унцию.`):'';
  $('assistantNote').textContent=guide.note||'Советы основаны на проверках сценария. Решение и исполнение остаются за вами.';
  const a=source==='live'?snapshot?.account_review:null;
  $('assistantRisk').textContent=!a?'':!a.fresh?'Риск счёта: ждём свежие данные MT5.':
   a.unknown_risk?`Открытых позиций: ${a.positions}. У ${a.unknown_risk} риск неизвестен — проверьте их стопы.`:
   `Открытых позиций: ${a.positions}. Известный оставшийся риск до стопов: ${a.known_risk==null?'неизвестен':money(a.known_risk)+' '+(a.currency||'')}. Остаток расчётного лимита 0,25%: ${a.headroom==null?'неизвестен':money(a.headroom)+' '+(a.currency||'')}.`;
  countdown();
 }
 function notifyReady(){
  const row=snapshot?.scenarios.find(r=>r.id===snapshot.active_id);
  if(source!=='live'||snapshot.data_ready===false||row?.guidance?.tone!=='ready'||!row.checks.ready_to_review_entry||row.valid_until<=Date.now()/1000)return;
  const key=row.id+':'+row.stage_at;
  if(sounded.has(key))return;
  sounded.add(key);
  if(sounded.size>100)sounded.delete(sounded.values().next().value);
  if(soundEnabled&&audio?.state==='running'){
   const oscillator=audio.createOscillator(),gain=audio.createGain(),start=audio.currentTime;
   oscillator.frequency.value=660;gain.gain.setValueAtTime(0,start);
   gain.gain.linearRampToValueAtTime(.04,start+.03);gain.gain.linearRampToValueAtTime(0,start+.35);
   oscillator.connect(gain);gain.connect(audio.destination);oscillator.start(start);oscillator.stop(start+.36);
   $('assistantSoundStatus').textContent='Сценарий готов к ручной проверке. Сверьте условия в MT5.';
  }
 }
 function pause(reason){
  snapshot={...(snapshot||{}),data_ready:false,data_reason:reason,scenarios:(snapshot?.scenarios||[]).map(row=>
   ({...row,checks:{...row.checks,current:false,data_ready:false,data_reason:reason,ready_to_review_entry:false}}))};
  render();
 }
 function nextCheck(row,checks){
  if(['waiting','near','confirmed'].includes(row.stage)&&checks.data_ready===false)return checks.data_reason||'Проверка приостановлена: ждём восстановления данных MT5.';
  if(['waiting','near','confirmed'].includes(row.stage)&&!checks.fresh)return 'Ждём свежую котировку MT5; проверка входа приостановлена.';
  if(!checks.current)return 'Исторический план: новые проверки по нему не выполняются.';
  if(row.stage==='waiting')return 'Ждём первого возврата цены к пробитому уровню и закрытия M5.';
  if(row.stage==='near')return 'Ждём подтверждения на закрытой M5.';
  if(!checks.in_zone)return 'Ждём цену в зоне входа; подтверждение M5 уже есть.';
  if(!checks.spread_ok)return 'Проверьте спред: он велик либо данные для проверки отсутствуют.';
  if(checks.unknown_open_risk!==0||checks.max_lots_under_budget==null||checks.max_lots_under_budget<=0)return 'Уточните риск открытых позиций и доступный объём.';
  return 'Сверьте условия и фактическую цену в MT5 перед своим решением.';
 }
 function render(force=false){
  renderAssistant(currentRow());
  const editing=!force&&document.activeElement?.closest?.('.scenario-form');
  const area=$('scenarioWorkspace');
  if(source!=='live'){
   $('scenarioCardTitle').textContent='Сценарий в работе';
   area.hidden=true;$('beginnerHeadline').textContent='Выберите MetaTrader 5 для новых сценариев';
   $('beginnerExplanation').textContent='Архив доступен для изучения, решения здесь записываются только для живых событий.';
   $('beginnerLevels').hidden=true;$('beginnerPlanTime').textContent='';$('beginnerCancel').textContent='';$('beginnerCaution').textContent='';clearSummary();return;
  }
  area.hidden=false;
  if(!snapshot){$('beginnerHeadline').textContent='Загружаю сценарии…';$('beginnerLevels').hidden=true;clearSummary();return}
  const picker=$('scenarioSelect');
  picker.innerHTML='<option value="">Текущий сценарий</option>'+snapshot.scenarios.map(r=>
   `<option value="${esc(r.id)}">${r.model}-${esc(r.key.slice(0,12))} · ${esc(stages[r.stage])} · ${esc(stamp(r.created_at))}</option>`).join('');
  picker.value=selectedId||'';
  const row=currentRow();
  $('scenarioMessage').textContent='';
  if(!row){
   $('scenarioCardTitle').textContent='Сценарий в работе';
   $('beginnerHeadline').textContent=snapshot.data_ready===false?'Проверка сценариев приостановлена':snapshot.quote_fresh?'Свежего действующего плана сейчас нет':'Свежей котировки нет';
   $('beginnerExplanation').textContent=snapshot.data_ready===false?snapshot.data_reason:snapshot.quote_fresh?'Последние завершённые сценарии доступны в списке ниже.':'Дождитесь восстановления MT5; исторические планы нельзя использовать как текущие.';
   $('beginnerLevels').hidden=true;$('beginnerPlanTime').textContent='';$('beginnerCancel').textContent='';$('beginnerCaution').textContent='';clearSummary();
   $('scenarioChecks').hidden=true;$('scenarioCheckContent').textContent='';$('scenarioDecision').textContent='';$('scenarioTrade').textContent='';return;
  }
  const plan=row.plan,checks=row.checks,live=checks.current;
  $('scenarioCardTitle').textContent=live?'Сценарий в работе':row.stage==='ended'||row.stage==='expired'?'Разбор завершённого сценария':'Проверка сценария приостановлена';
  $('beginnerPlanTime').textContent=`${row.model}-${row.key.slice(0,12)} · ${stamp(row.stage_at)}`+
   (live&&row.valid_until?` · актуален до ${stamp(row.valid_until)}, если не отменён раньше`:'');
  const direction=plan.side==='long'?'покупка':'продажа';
  $('beginnerHeadline').textContent=(checks.ready_to_review_entry?'Проверьте вход вручную':!live&&['waiting','near','confirmed'].includes(row.stage)?'Проверка приостановлена':stages[row.stage])+' · '+(row.stage==='ended'||row.stage==='expired'?'план '+(plan.side==='long'?'покупки':'продажи'):'возможная '+direction);
  $('beginnerHeadline').className=checks.ready_to_review_entry?'scenario-ready':'';
  $('beginnerExplanation').textContent='Почему: '+row.stage_reason;
  $('beginnerNext').textContent='Следующая проверка: '+nextCheck(row,checks);
  $('beginnerLevels').hidden=false;
  $('beginnerEntry').textContent=plan.entry.map(fmt).join('–');
  $('beginnerStop').textContent=fmt(plan.stop);
  $('beginnerTargets').textContent=plan.targets.map(fmt).join(' · ');
  $('beginnerTargetMethod').textContent=plan.target_method==='midpoint_r_1_2_3'?'Цели 1R / 2R / 3R рассчитаны от середины зоны и стопа; это ориентиры, не подтверждённые структурные уровни.':'Метод расчёта целей не указан в этом плане.';
  $('beginnerLot').textContent=!live?(['waiting','near','confirmed'].includes(row.stage)?'Объём не показывается до восстановления данных MT5.':'Объём для завершённого плана не рассчитывается.'):checks.max_lots_under_budget==null?'Ориентир объёма пока не рассчитан: проверьте параметры счёта и риск открытых позиций.':checks.max_lots_under_budget<=0?'По заданному лимиту риска доступного объёма нет.':`Ориентир объёма: не более ${fmt(checks.max_lots_under_budget)} лота по лимиту 0,25% капитала.`;
  $('beginnerCancel').textContent='Условие отмены: '+plan.cancel_rule+'.';
  const delivery={sent:'доставлено',pending:'в очереди',failed:'ошибка',local_only:'только в приложении',suppressed:'повтор не отправлен',expired:'не доставлено вовремя',sending:'подтверждение доставки неизвестно'};
  $('beginnerCaution').textContent=(live?'План расчётный. «Взять в работу» сохраняет ваше решение и не отправляет заявку. Стоп и цели не гарантируют исполнение.':['waiting','near','confirmed'].includes(row.stage)?'Данные MT5 не подтверждены: уровни и объём нельзя использовать до восстановления наблюдения.':'Историческая карточка: уровни сейчас не действуют. Её можно использовать для разбора уже принятого решения.')+
   ' Ранний план в Telegram: '+(delivery[row.plan_delivery]||'статус неизвестен')+'.';
  const items=[
   [checks.fresh,'Котировка свежая','Котировка устарела'],
   [checks.in_zone,'Цена в зоне входа','Цена вне зоны входа'],
   [checks.spread_ok,'Спред не превышает ¼ расстояния до стопа','Спред велик либо не проверен'],
   [checks.unknown_open_risk===0,'Риск открытых позиций известен','Есть позиции без известного риска'],
   [checks.ready_to_review_entry,'Можно проверить вход вручную','Условия для проверки входа неполные']
  ];
  $('scenarioChecks').hidden=false;
  $('scenarioCheckContent').innerHTML='<h3>Проверка перед решением</h3><ul>'+items.map(([ok,yes,no])=>`<li class="${ok?'check-ok':'check-warn'}">${ok?'✓':'!'} ${ok?yes:no}</li>`).join('')+'</ul>'+
   `<p>Цена ${checks.quote_price==null?'—':fmt(checks.quote_price)} · спред ${checks.spread==null?'—':fmt(checks.spread)}. `+
   `Открытый риск ${checks.open_risk==null?'неизвестен':money(checks.open_risk)} ${esc(checks.account_currency||'')}; `+
   `ориентир 0,25% капитала ${checks.risk_budget_025_pct==null?'неизвестен':money(checks.risk_budget_025_pct)}; `+
   `остаток ${checks.remaining_budget==null?'неизвестен':money(checks.remaining_budget)}. `+
   `Оценка потери до стопа на 1 лот: ${checks.loss_per_lot_at_stop==null?'неизвестна':money(checks.loss_per_lot_at_stop)}; `+
   `максимум по этому ориентиру: ${checks.max_lots_under_budget==null?'неизвестен':fmt(checks.max_lots_under_budget)} лота. `+
   `Это расчёт по размеру контракта, без проскальзывания и комиссий.</p>`;
  if(!live)$('scenarioCheckContent').innerHTML='<p>Проверки текущей цены и риска относятся только к действующему сценарию.</p>';
  if(editing)return;
  const d=row.decision;
  $('scenarioDecision').innerHTML=d?`<p>Ваше решение: <strong>${d.action==='watch'?'Взять в работу':'Пропустить'}</strong> · ${stamp(d.time)}</p>`:
   live?'<button id="scenarioWatch" type="button">Взять в работу</button> <button id="scenarioSkip" type="button">Пропустить</button><p class="muted">Кнопки записывают ваше решение. Они не открывают сделку.</p>':
   '<p>Решение во время действия сценария не записано.</p>';
  if(!d&&live){
   $('scenarioWatch').onclick=()=>submit('/api/scenario-decision',{scenario_id:row.id,action:'watch'});
   $('scenarioSkip').onclick=()=>submit('/api/scenario-decision',{scenario_id:row.id,action:'skip'});
  }
  const trade=row.trade;
  if(d?.action!=='watch'){$('scenarioTrade').innerHTML='';return}
  if(!trade){
   const position=row.mt5_position;
   $('scenarioTrade').innerHTML=`<form id="scenarioEntryForm" class="scenario-form"><h3>Если вы открыли сделку вручную в MT5</h3><label>Фактический вход <input name="entry" type="number" step="any" min="0.01" required></label><label>Объём, лоты MT5 <input name="lots" type="number" step="any" min="0.001" required></label><label>Время входа на вашем компьютере <input name="entry_time" type="datetime-local" required value="${localNow()}"></label><button type="submit">Записать факт входа</button><p class="muted">Вносите только фактическое исполнение, подтверждённое в MT5. Расчётный план не создаёт заявку.</p></form>`;
   $('scenarioTrade').innerHTML+=`<p>${esc(row.mt5_link_reason||'')}</p><p>Комментарий заявки MT5: <strong>${esc(row.id)}</strong></p>`+
    (position?`<p>Тикет ${esc(position.ticket)} · цена позиции ${fmt(position.entry)} · ${fmt(position.lots)} лота. Проверьте время входа в форме выше. После доливки цена может быть средней, объём — оставшимся.</p><button id="scenarioImportPosition" type="button">Подтянуть позицию MT5 и записать</button>`:'');
   $('scenarioEntryForm').onsubmit=e=>formSubmit(e,'/api/scenario-entry',row.id,'entry_time');
   if(position)$('scenarioImportPosition').onclick=()=>{
    const form=new FormData($('scenarioEntryForm'));
    const when=new Date(form.get('entry_time')).getTime();
    if(!Number.isFinite(when)){$('scenarioMessage').textContent='Сверьте время входа с MT5';return}
    return submit('/api/scenario-import-position',{scenario_id:row.id,ticket:position.ticket,entry_time:Math.floor(when/1000)});
   };
   return;
  }
  const inRange=plan.entry[0]<=trade.entry&&trade.entry<=plan.entry[1];
  const comparison=`${trade.mt5_ticket?'Снимок позиции MT5 · тикет '+trade.mt5_ticket+' ·':'Фактический вход'} ${fmt(trade.entry)} · ${fmt(trade.lots)} лота · ${stamp(trade.entry_time)}. ${inRange?'Цена попала в расчётную зону.':'Цена оказалась вне расчётной зоны.'} Оценка потери до стопа: ${trade.estimated_stop_loss==null?'неизвестна':money(trade.estimated_stop_loss)+' '+(checks.account_currency||'')}.`;
  if(trade.exit_time){
   $('scenarioTrade').innerHTML=`<h3>Фактический результат</h3><p>${esc(comparison)}</p><p>Выход ${fmt(trade.exit_price)} · ${stamp(trade.exit_time)}. Итог по данным брокера: <strong>${money(trade.net_pnl)} ${esc(checks.account_currency||'')}</strong>.</p>`;
  }else{
   $('scenarioTrade').innerHTML=`<p>${esc(comparison)}</p><form id="scenarioExitForm" class="scenario-form"><h3>После закрытия сделки в MT5</h3><label>Фактический выход <input name="exit_price" type="number" step="any" min="0.01" required></label><label>Итог брокера с расходами, ${esc(checks.account_currency||'валюта счёта')} <input name="net_pnl" type="number" step="any" required></label><label>Время выхода на вашем компьютере <input name="exit_time" type="datetime-local" required value="${localNow()}"></label><button type="submit">Записать итог</button></form>`;
   $('scenarioExitForm').onsubmit=e=>formSubmit(e,'/api/scenario-exit',row.id,'exit_time');
  }
 }
 async function submit(url,body){
  try{const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
   const data=await response.json();if(!response.ok)throw Error(data.error||'Не удалось сохранить');snapshot=data;render(true);$('scenarioMessage').textContent='Сохранено в журнале сценария.';
  }catch(e){$('scenarioMessage').textContent=e.message}
 }
 function formSubmit(e,url,id,timeField){e.preventDefault();const f=new FormData(e.currentTarget),body={scenario_id:id};for(const [k,v] of f)body[k]=v;
  const t=new Date(body[timeField]).getTime();if(!Number.isFinite(t)){$('scenarioMessage').textContent='Проверьте время';return}body[timeField]=Math.floor(t/1000);submit(url,body)}
 async function refresh(nextSource=source){source=nextSource;if(source!=='live'){render();return}const sequence=++request;
  try{const response=await fetch('/api/scenarios');const data=await response.json();if(sequence!==request)return;
   if(!response.ok)throw Error(data.error||'Не удалось загрузить сценарии');
   if(document.activeElement?.closest?.('.scenario-form')&&currentRow())selectedId=currentRow().id;
   snapshot=data;render();notifyReady();
  }catch(e){
   if(sequence!==request)return;
   pause('Нет ответа сервера; проверка сценария приостановлена.');$('scenarioMessage').textContent=e.message;
  }}
 setInterval(()=>{
  if(source==='live'&&snapshot?.as_of&&snapshot.data_ready!==false&&Date.now()/1000-snapshot.as_of>45)
   pause('Состояние радара не обновлялось больше 45 секунд; проверка сценария приостановлена.');
  if(source==='live'&&snapshot?.data_ready!==false&&currentRow()?.checks.current&&currentRow().valid_until<=Date.now()/1000)
   pause('Срок плана закончился; дождитесь обновления сценариев.');
  countdown();
 },1000);
 $('assistantSound').onchange=async e=>{
  soundEnabled=false;
  if(!e.target.checked){$('assistantSoundStatus').textContent='Звук выключен. BUY/SELL вы нажимаете вручную в MT5.';return}
  try{
   const Audio=window.AudioContext||window.webkitAudioContext;
   if(!Audio)throw Error('Браузер не поддерживает звук');
   audio=audio||new Audio();await audio.resume();
   soundEnabled=audio.state==='running';e.target.checked=soundEnabled;
   $('assistantSoundStatus').textContent=soundEnabled?'Звук включён для новых готовых сценариев в этой вкладке.':'Нажмите ещё раз, чтобы разрешить звук.';
  }catch(e){$('assistantSound').checked=false;$('assistantSoundStatus').textContent=e.message}
 };
 for(const [id,target] of [['assistantZones','radar'],['assistantJournal','journal'],['assistantResults','research']])
  $(id).onclick=()=>document.querySelector(`.tab[data-tab="${target}"]`).click();
 $('scenarioSelect').onchange=e=>{selectedId=e.target.value||null;render(true)};
 window.scenarioWorkspace={refresh};
})();
