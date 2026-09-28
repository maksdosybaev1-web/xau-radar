const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('assert');
(async()=>{
 const base='http://127.0.0.1:8767';
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/user/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.waitForFunction(()=>document.querySelector('#marketScore').textContent.includes('/100'));
  assert(await page.locator('#market').isVisible());
  await page.waitForFunction(()=>document.querySelector('#opsArchive').textContent.includes('bid/ask:'));
  const ops=await(await page.request.get(base+'/api/operations')).json();
  assert(ops.quote_archive.complete_tick_feed===false);
  assert(Number.isInteger(ops.quote_archive.total));
  const quotes=await page.request.get(base+'/download/live_quotes.csv');
  assert(quotes.status()===200);
  assert((await quotes.text()).includes('quote_time_utc,time_msc,observed_at_utc,bid,ask,spread'));
  assert(await page.locator('a[href="/download/live_quotes.csv"]').count()===1);
  for(const tf of ['M5','H1','M15']){
   await page.locator('#techTf').selectOption(tf);
   await page.waitForFunction(tf=>document.querySelector('#techTime').textContent.startsWith('Закрытие '+tf+':'),tf);
   assert((await page.locator('#techRsi').innerText())!=='—');assert(await page.locator('#techChart polyline').count()===3);
  }
  assert(await page.locator('#scoreParts .score-row').count()===7);
  await page.locator('#calcEquity').fill('20000');assert((await page.locator('#planValues').innerText()).includes('50,00 USD'));
  await page.screenshot({path:path.join(__dirname,'../results/market-desktop.png'),fullPage:true});
  await page.locator('[data-tab="settings"]').click();assert(await page.locator('#telegramToken').getAttribute('type')==='password');
  const settings=await(await page.request.get(base+'/api/telegram')).json();assert(!('token' in settings));
  const refused=await page.request.post(base+'/api/telegram',{headers:{Origin:'https://example.invalid'},data:{enabled:false}});assert(refused.status()===403);
  await page.locator('[data-tab="market"]').click();await page.locator('#sourceSelect').selectOption('live');
  await page.waitForFunction(()=>document.querySelector('#quoteInfo').textContent.includes('Bid'));
  const live=await(await page.request.get(base+'/api/technical?source=live&tf=H1')).json();
  assert(live.ready&&live.frames.H1.ready&&live.frames.H1.bars_seen>=200);
  if(live.stale){assert((await page.locator('#marketStatus').innerText()).includes('УСТАРЕЛИ'));}
  await page.screenshot({path:path.join(__dirname,'../results/market-mt5.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)));
  await page.screenshot({path:path.join(__dirname,'../results/market-mobile.png'),fullPage:true});
  await page.locator('[data-tab="settings"]').click();assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)));
  const report=await page.request.get(base+'/download/technical_validation.md');assert(report.ok());assert.deepStrictEqual(errors,[]);
  const result={passed:true,checked_at:new Date().toISOString(),checks:['M5/M15/H1','EMA20/50/200','RSI/MACD/ATR','7 частей оценки','Калькулятор риска','Приватность токена','Защита локальных настроек','MT5 bid/ask','Разогрев H1','Устаревшие котировки','Мобильная вёрстка','Отчёт'],live_bars:Object.fromEntries(Object.entries(live.frames).map(([k,v])=>[k,v.bars_seen])),errors};
  fs.writeFileSync(path.join(__dirname,'../results/market-ui-check.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
