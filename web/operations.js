async function updateOperations(){
 const label=(id,text)=>document.getElementById(id).textContent=text;
 try{
  const response=await fetch('/api/operations');if(!response.ok)throw Error('Не удалось получить состояние');
  const s=await response.json(),c=s.journal.last_24h,u=s.journal.unresolved;
  label('opsBridge',s.bridge.label);label('opsQuote',s.quote.label);label('opsTelegram',s.telegram.label);
  label('opsCounts',`За 24 часа: событий ${Object.values(c).reduce((a,b)=>a+b,0)}, доставлено ${c.sent||0}, ошибок ${c.failed||0}, повторов ограничено ${c.suppressed||0}. В очереди ${u.pending||0}; без окончательного подтверждения ${u.sending||0}.`);
  const a=s.quote_archive;
  label('opsArchive',`Архив bid/ask: ${a.total} котировок, за 24 часа ${a.last_24h}. Интервалов свыше 90 секунд: ${a.intervals_over_90s}. Это выборки примерно раз в 10 секунд; движения между ними могут быть пропущены.`);
  const t=s.tick_archive;
  label('opsTicks',`Тики MT5: ${t.total} записей, за 24 часа ${t.last_24h}; запросов ${t.pulls}, со статусом не ok ${t.non_ok_pulls}. Последний запрос: ${t.last_pull?.status||'ещё не было'}. Полнота истории брокера не доказана.`);
  const f=s.fvg_forward;
  label('forwardStatus',f?.status==='error'?f.message:f?.status==='not_started'?'Живое FVG-наблюдение ещё не запущено.':f?`FVG с ${new Date(f.forward_since*1000).toLocaleString('ru-RU',{timeZone:'UTC'})} UTC: новых M1 ${f.new_m1}, своевременных подтверждений ${f.timely_confirmed}, закрытых условных исходов ${f.paper_closed}, исходов с разрывом данных ${f.paper_data_gap}. Поздних событий ${f.events.late_bar||0}; при устаревшей котировке ${f.events.stale_quote||0}. ${f.quote_fresh?'Котировка свежая, наблюдение продолжается.':'Котировка устарела, новых результатов пока нет.'}`:'Состояние FVG недоступно.');
  label('opsNote',s.ready_for_new_alerts?'Свежие котировки поступают, очередь доставки работает.':!s.bridge.alive?'Проверьте запуск MT5-моста через start.bat.':s.bridge.error_type==='RatesUnavailable'?`MT5 не возвращает свечи M1 (код ${s.bridge.error_code??'неизвестен'}). Проверьте историю символа и подключение терминала; радар не создаёт новые сигналы без свечей.`:!s.quote.fresh?'Мост и свежесть рыночной котировки проверяются отдельно. Устаревшая цена не создаёт уведомлений.':'Проверьте состояние очереди и настройки Telegram.');
 }catch(e){for(const id of ['opsBridge','opsQuote','opsTelegram'])label(id,'Нет ответа');label('opsArchive','Архив: состояние недоступно');label('opsTicks','Тики: состояние недоступно');label('opsNote','Статус недоступен: проверьте локальный сервер.')}
}
updateOperations();setInterval(updateOperations,10000);
