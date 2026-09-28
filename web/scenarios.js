/* One live scenario, its user's decision and manually reported broker outcome. */
(() => {
 const $ = id => document.getElementById(id);
 const fmt = x => Number(x).toLocaleString('ru-RU',{maximumFractionDigits:8});
 const money = x => Number(x).toLocaleString('ru-RU',{minimumFractionDigits:2,maximumFractionDigits:2});
 const stamp = x => new Date(x*1000).toLocaleString('ru-RU',{timeZone:'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})+' UTC';
 const localNow = () => new Date(Date.now()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
 const esc = s => String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;','\'':'&#39;'}[c]));
 let snapshot=null,selectedId=null,request=0,source='history';
 const stages={waiting:'Ждём возврата к зоне',near:'Цена у зоны · ждём M5',confirmed:'M5 подтвердила условия',ended:'Сценарий завершён',expired:'Срок плана истёк'};
 const currentRow=()=>snapshot?.scenarios.find(r=>r.id===(selectedId||snapshot.active_id))||null;
 function render(force=false){
  if(!force&&document.activeElement?.closest?.('.scenario-form'))return;
  const area=$('scenarioWorkspace');
  if(source!=='live'){
   area.hidden=true;$('beginnerHeadline').textContent='Выберите MetaTrader 5 для новых сценариев';
   $('beginnerExplanation').textContent='Архив доступен для изучения, решения здесь записываются только для живых событий.';
   $('beginnerLevels').hidden=true;$('beginnerPlanTime').textContent='';$('beginnerCancel').textContent='';$('beginnerCaution').textContent='';return;
  }
  area.hidden=false;
  if(!snapshot){$('beginnerHeadline').textContent='Загружаю сценарии…';$('beginnerLevels').hidden=true;return}
  const picker=$('scenarioSelect');
  picker.innerHTML='<option value="">Текущий сценарий</option>'+snapshot.scenarios.map(r=>
   `<option value="${esc(r.id)}">${r.model}-${esc(r.key.slice(0,12))} · ${esc(stages[r.stage])} · ${esc(stamp(r.created_at))}</option>`).join('');
  picker.value=selectedId||'';
  const row=currentRow();
  $('scenarioMessage').textContent='';
  if(!row){
   $('beginnerHeadline').textContent=snapshot.quote_fresh?'Свежего действующего плана сейчас нет':'Свежей котировки нет';
   $('beginnerExplanation').textContent=snapshot.quote_fresh?'Последние завершённые сценарии доступны в списке ниже.':'Дождитесь восстановления MT5; исторические планы нельзя использовать как текущие.';
   $('beginnerLevels').hidden=true;$('beginnerPlanTime').textContent='';$('beginnerCancel').textContent='';$('beginnerCaution').textContent='';
   $('scenarioChecks').textContent='';$('scenarioDecision').textContent='';$('scenarioTrade').textContent='';return;
  }
  const plan=row.plan,checks=row.checks,live=checks.current;
  $('beginnerPlanTime').textContent=`${row.model}-${row.key.slice(0,12)} · ${stamp(row.stage_at)}`+
   (live&&row.valid_until?` · актуален до ${stamp(row.valid_until)}, если не отменён раньше`:'');
  $('beginnerHeadline').textContent=stages[row.stage]+' · возможная '+(plan.side==='long'?'покупка':'продажа');
  $('beginnerExplanation').textContent='Почему: '+row.stage_reason;
  $('beginnerLevels').hidden=false;
  $('beginnerEntry').textContent=plan.entry.map(fmt).join('–');
  $('beginnerStop').textContent=fmt(plan.stop);
  $('beginnerTargets').textContent=plan.targets.map(fmt).join(' · ');
  $('beginnerCancel').textContent='Условие отмены: '+plan.cancel_rule+'.';
  const delivery={sent:'доставлено',pending:'в очереди',failed:'ошибка',local_only:'только в приложении',suppressed:'повтор не отправлен',expired:'не доставлено вовремя',sending:'подтверждение доставки неизвестно'};
  $('beginnerCaution').textContent=(live?'План расчётный. «Взять в работу» сохраняет ваше решение и не отправляет заявку. Стоп и цели не гарантируют исполнение.':'Историческая карточка: уровни сейчас не действуют. Её можно использовать для разбора уже принятого решения.')+
   (['SBR','RBS'].includes(row.model)&&plan.target_method==='midpoint_r_1_2_3'?
    (live?' ТП 1R/2R/3R — ориентиры; структурную цель M15 проверим после ретеста.':
     ' ТП 1R/2R/3R были расчётными ориентирами, а не структурной целью M15.'):'')+
   ' Ранний план в Telegram: '+(delivery[row.plan_delivery]||'статус неизвестен')+'.';
  const items=[
   [checks.fresh,'Котировка свежая','Котировка устарела'],
   [checks.in_zone,'Цена в зоне входа','Цена вне зоны входа'],
   [checks.spread_ok,'Спред не превышает ¼ расстояния до стопа','Спред велик либо не проверен'],
   [checks.unknown_open_risk===0,'Риск открытых позиций известен','Есть позиции без известного риска'],
   [checks.ready_to_review_entry,'Можно проверить вход вручную','Условия для проверки входа неполные']
  ];
  $('scenarioChecks').innerHTML='<h3>Проверка перед решением</h3><ul>'+items.map(([ok,yes,no])=>`<li class="${ok?'check-ok':'check-warn'}">${ok?'✓':'!'} ${ok?yes:no}</li>`).join('')+'</ul>'+
   `<p>Цена ${checks.quote_price==null?'—':fmt(checks.quote_price)} · спред ${checks.spread==null?'—':fmt(checks.spread)}. `+
   `Открытый риск ${checks.open_risk==null?'неизвестен':money(checks.open_risk)} ${esc(checks.account_currency||'')}; `+
   `ориентир 0,25% капитала ${checks.risk_budget_025_pct==null?'неизвестен':money(checks.risk_budget_025_pct)}; `+
   `остаток ${checks.remaining_budget==null?'неизвестен':money(checks.remaining_budget)}. `+
   `Оценка потери до стопа на 1 лот: ${checks.loss_per_lot_at_stop==null?'неизвестна':money(checks.loss_per_lot_at_stop)}; `+
   `максимум по этому ориентиру: ${checks.max_lots_under_budget==null?'неизвестен':fmt(checks.max_lots_under_budget)} лота. `+
   `Это расчёт по размеру контракта, без проскальзывания и комиссий.</p>`;
  if(!live)$('scenarioChecks').innerHTML='<p>Проверки текущей цены и риска относятся только к действующему сценарию.</p>';
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
   $('scenarioTrade').innerHTML=`<form id="scenarioEntryForm" class="scenario-form"><h3>Если вы открыли сделку вручную в MT5</h3><label>Фактический вход <input name="entry" type="number" step="any" min="0.01" required></label><label>Объём, лоты MT5 <input name="lots" type="number" step="any" min="0.001" required></label><label>Время входа на вашем компьютере <input name="entry_time" type="datetime-local" required value="${localNow()}"></label><button type="submit">Записать факт входа</button><p class="muted">Вносите только фактическое исполнение, подтверждённое в MT5. Расчётный план не создаёт заявку.</p></form>`;
   $('scenarioEntryForm').onsubmit=e=>formSubmit(e,'/api/scenario-entry',row.id,'entry_time');return;
  }
  const inRange=plan.entry[0]<=trade.entry&&trade.entry<=plan.entry[1];
  const comparison=`Фактический вход ${fmt(trade.entry)} · ${fmt(trade.lots)} лота · ${stamp(trade.entry_time)}. ${inRange?'Цена попала в расчётную зону.':'Цена оказалась вне расчётной зоны.'} Оценка потери до стопа: ${trade.estimated_stop_loss==null?'неизвестна':money(trade.estimated_stop_loss)+' '+(checks.account_currency||'')}.`;
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
   if(!response.ok)throw Error(data.error||'Не удалось загрузить сценарии');snapshot=data;render();
  }catch(e){$('scenarioMessage').textContent=e.message}}
 $('scenarioSelect').onchange=e=>{selectedId=e.target.value||null;render(true)};
 window.scenarioWorkspace={refresh};
})();
