async function updateOperations(){
 const label=(id,text)=>document.getElementById(id).textContent=text;
 const el=id=>document.getElementById(id);
 try{
  const response=await fetch('/api/operations');if(!response.ok)throw Error('Не удалось получить состояние');
  const s=await response.json(),c=s.journal.last_24h,u=s.journal.unresolved;
  // Overall status indicator
  const overall=s.overall||{label:'Статус сервера не обновлён',color:'red'};
  const dot=el('opsOverallDot');dot.className='status-dot status-'+overall.color;
  label('opsOverall',overall.label);
  const quoteAge=s.quote.age_seconds,m1Age=s.m1?.age_seconds;
  const detail=!s.overall?'Перезапустите радар':`Котировка: ${quoteAge!=null?Math.round(quoteAge)+' сек':'нет данных'}; закрытая M1: ${m1Age!=null?Math.round(m1Age)+' сек':'нет данных'}`;
  label('opsOverallDetail',detail);
  // Component labels with color classes
  const bridgeEl=el('opsBridge');bridgeEl.textContent=s.bridge.label;
  bridgeEl.className=s.bridge.alive&&s.bridge.state==='running'?'status-text-green':s.bridge.alive?'status-text-yellow':'status-text-red';
  const quoteEl=el('opsQuote');quoteEl.textContent=s.quote.label+(quoteAge!=null?' ('+Math.round(quoteAge)+' сек)':'');
  quoteEl.className=s.quote.fresh?'status-text-green':'status-text-red';
  const tgEl=el('opsTelegram');tgEl.textContent=s.telegram.label;
  tgEl.className=!s.telegram.enabled||!s.telegram.configured?'status-text-yellow':s.telegram.worker_alive&&s.telegram.worker_state==='running'?'status-text-green':s.telegram.worker_alive&&s.telegram.worker_state==='starting'?'status-text-yellow':'status-text-red';
  label('opsCounts',`За 24 часа: событий ${Object.values(c).reduce((a,b)=>a+b,0)}, доставлено ${c.sent||0}, ошибок ${c.failed||0}, повторов ограничено ${c.suppressed||0}. В очереди ${u.pending||0}; без окончательного подтверждения ${u.sending||0}.`);
  const all=s.journal.all_statuses||{},receipt=s.journal.last_confirmed_send;
  const utc=stamp=>new Date(stamp*1000).toLocaleString('ru-RU',{timeZone:'UTC'})+' UTC';
  label('opsDeliveryHistory',`За всё время: прежних ошибок доставки ${all.failed||0}. ${receipt?`Последнее подтверждение Telegram API: ${utc(receipt.sent_at)}, ID сообщения ${receipt.telegram_message_id??'не сохранён'}.`:'Подтверждённых отправок с сохранённым ID после обновления ещё нет.'}`);
  const recovery=s.bridge.last_recovery,start=s.bridge.outage_started_at;
  label('opsRecovery',start?`Сбой получения данных начался ${utc(start)}; длительность ${Math.max(0,s.as_of-start)} сек. Новые планы не выдаются.`:recovery?`Последний сбой: ${utc(recovery.started_at)}–${utc(recovery.ended_at)}, ${recovery.duration_seconds} сек; M1 до сбоя ${recovery.last_m1_before?utc(recovery.last_m1_before):'неизвестна'}, после восстановления ${recovery.last_m1_after?utc(recovery.last_m1_after):'неизвестна'}.`:'');
  const gaps=s.bridge.m1_gaps||[],gap=gaps[gaps.length-1];
  label('opsM1Gaps',gap?`Промежутков без M1 в истории MT5: ${gaps.length}; последний: ${utc(gap.before)}–${utc(gap.after)} (${gap.missing_minutes} мин). Причина не подтверждена.`:'');
  const a=s.quote_archive;
  label('opsArchive',`Архив bid/ask: ${a.total} котировок, за 24 часа ${a.last_24h}. Интервалов свыше 90 секунд: ${a.intervals_over_90s}. Это выборки примерно раз в 10 секунд; движения между ними могут быть пропущены.`);
  const t=s.tick_archive;
  label('opsTicks',`Тики MT5: ${t.total} записей, за 24 часа ${t.last_24h}; запросов ${t.pulls}, со статусом не ok ${t.non_ok_pulls}. Последний запрос: ${t.last_pull?.status||'ещё не было'}. Полнота истории брокера не доказана.`);
  const f=s.fvg_forward;
  label('forwardStatus',f?.status==='error'?f.message:f?.status==='not_started'?'Живое FVG-наблюдение ещё не запущено.':f?`FVG с ${new Date(f.forward_since*1000).toLocaleString('ru-RU',{timeZone:'UTC'})} UTC: новых M1 ${f.new_m1}, своевременных подтверждений ${f.timely_confirmed}, закрытых условных исходов ${f.paper_closed}, исходов с разрывом данных ${f.paper_data_gap}. Поздних событий ${f.events.late_bar||0}; при устаревшей котировке ${f.events.stale_quote||0}. ${f.quote_fresh?'Котировка свежая, наблюдение продолжается.':'Котировка устарела, новых результатов пока нет.'}`:'Состояние FVG недоступно.');
  label('opsNote',s.ready_for_new_alerts?'Свежие котировки и закрытые M1 поступают, очередь доставки работает.':!s.bridge.alive?'Проверьте запуск MT5-моста через start.bat.':s.bridge.error_type==='RatesUnavailable'?`MT5 не возвращает свечи M1 (код ${s.bridge.error_code??'неизвестен'}). Проверьте историю символа и подключение терминала; радар не создаёт новые сигналы без свечей.`:s.bridge.error_type==='ClosedM1Stale'||s.m1?.fresh===false?'Нет свежей закрытой M1. Радар ждёт обновления истории MT5 и не выдаёт новые сценарии.':!s.quote.fresh?'Мост и свежесть рыночной котировки проверяются отдельно. Устаревшая цена не создаёт уведомлений.':'Проверьте состояние очереди и настройки Telegram.');
 }catch(e){
  for(const id of ['opsBridge','opsQuote','opsTelegram'])label(id,'Нет ответа');
  label('opsOverall','Нет ответа');el('opsOverallDot').className='status-dot status-red';label('opsOverallDetail','');
  for(const id of ['opsDeliveryHistory','opsRecovery','opsM1Gaps'])label(id,'');
  label('opsArchive','Архив: состояние недоступно');label('opsTicks','Тики: состояние недоступно');label('opsNote','Статус недоступен: проверьте локальный сервер.')
 }
}
updateOperations();setInterval(updateOperations,10000);
async function updateForwardModels(){
 const target=document.getElementById('forwardModels');
 try{
  const response=await fetch('/download/v2_forward_evidence.json');if(!response.ok)throw Error('нет среза');
  const report=await response.json(),models=report.models;
  const rows=['FVG','SBR','RBS'].map(name=>{
   const item=models[name];return `${name}: событий ${item.journal_rows}, планов ${item.plans}, своевременных подтверждений ${item.timely_confirmed_plans}`;
  });
  target.textContent=`Сохранённый forward-срез: ${rows.join('; ')}. Цифры фиксированы на момент создания отчёта и не являются текущими сигналами.`;
 }catch(_){target.textContent='Сохранённый forward-срез пока недоступен.'}
}
updateForwardModels();

