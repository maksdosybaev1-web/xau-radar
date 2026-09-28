const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('assert');
(async()=>{
 const base='http://127.0.0.1:8767';
 const browser=await chromium.launch({headless:true,executablePath:'C:/Users/user/AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe'});
 const page=await browser.newPage({viewport:{width:1440,height:1050}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 await page.waitForFunction(()=>document.querySelector('#price').textContent!=='—');
 await page.locator('#sourceSelect').selectOption('history');
 await page.waitForFunction(()=>document.querySelector('#mode').textContent.includes('ИСТОРИЯ'));
 await page.locator('[data-tab="radar"]').click();
 assert((await page.locator('#mode').innerText()).includes('ИСТОРИЯ'));
 assert(!(await page.locator('#message').innerText()).includes('неполный'));
 const result=JSON.parse(fs.readFileSync(path.join(__dirname,'../results/run.json'),'utf8'));
 const near=result.events.find(e=>e.type==='near');
 await page.locator('#timeline').evaluate((el,t)=>{el.value=t;el.dispatchEvent(new Event('change'))},near.time);
 await page.waitForFunction(()=>document.querySelector('#zoneCount').textContent!=='0');
 await page.locator('.event.near').first().click();
 assert(await page.locator('#detail').isVisible());assert(await page.locator('#askAI').isDisabled());
 await page.locator('#closeDetail').click();
 const confirmed=result.events.find(e=>e.type==='confirmed');
 await page.locator('#timeline').evaluate((el,t)=>{el.value=t;el.dispatchEvent(new Event('change'))},confirmed.time);
 await page.waitForFunction(()=>document.querySelector('.event.confirmed'));
 await page.locator('.event.confirmed').first().click();
 assert(await page.locator('#askAI').isEnabled());
 await page.locator('#askAI').click();
 await page.waitForFunction(()=>document.querySelector('#aiReply').textContent.includes('M5'));
 await page.locator('#closeDetail').click();
 await page.screenshot({path:path.join(__dirname,'../results/radar-desktop.png'),fullPage:true});
 await page.locator('[data-tab="research"]').click();assert(await page.locator('#evaluation table').isVisible());
 await page.locator('[data-tab="rules"]').click();assert((await page.locator('#rulesText').innerText()).includes('Наши определения'));
 const download=await page.request.get(base+'/download/trades.csv');assert(download.ok());assert((await download.body()).length>100);
 await page.locator('[data-tab="radar"]').click();
 const zone=result.events.find(e=>e.type==='zone_created');
 await page.locator('#timeline').evaluate((el,t)=>{el.value=t;el.dispatchEvent(new Event('change'))},zone.time);
 await page.waitForFunction(t=>document.querySelector('#clock').textContent.includes(t),new Date(zone.time*1000).toLocaleDateString('ru-RU',{timeZone:'UTC'}));
 await page.locator('#manualForm [name="direction"]').selectOption(zone.direction);
 await page.locator('#manualForm [name="low"]').fill(String(zone.low));
 await page.locator('#manualForm [name="high"]').fill(String(zone.high));
 await page.locator('#manualForm button[type="submit"]').click();
 await page.waitForFunction(()=>document.querySelector('#mode').textContent.includes('РУЧНАЯ ЗОНА'));
 assert((await page.locator('#zoneCount').innerText())==='1');
 assert((await page.locator('#source').innerText()).includes('Ручная зона'));
 await page.locator('[data-tab="research"]').click();
 assert((await page.locator('#evaluation').innerText()).includes('не входит в исходную проверку'));
 await page.locator('[data-tab="radar"]').click();
 const liveResponse=await page.request.get(base+'/api/state?source=live');
 const live=await liveResponse.json();
 await page.locator('#sourceSelect').selectOption('live');
 if(live.ready){
  await page.waitForFunction(()=>document.querySelector('#mode').textContent.startsWith('MT5'));
  assert((await page.locator('#source').innerText()).includes(String(live.source.rows)));
  assert(await page.locator('#timeline').isDisabled());
  assert(await page.locator('#manualForm').isHidden());
  if(live.stale){
   assert((await page.locator('#mode').innerText()).includes('УСТАРЕЛИ'));
   assert((await page.locator('#message').innerText()).includes('Последняя котировка'));
  }
  await page.screenshot({path:path.join(__dirname,'../results/radar-mt5.png'),fullPage:true});
 }
 // Model a missing bridge only inside this browser; do not stop the real process.
 await page.route('**/api/state?source=live',route=>route.fulfill({json:{ready:false}}));
 await page.locator('#refresh').click();
 await page.waitForFunction(()=>document.querySelector('#mode').textContent==='НЕТ ПОДКЛЮЧЕНИЯ');
 assert((await page.locator('#price').innerText())==='—');
 await page.unroute('**/api/state?source=live');
 await page.locator('#sourceSelect').selectOption('history');
 await page.waitForFunction(()=>document.querySelector('#price').textContent!=='—');
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:path.join(__dirname,'../results/radar-mobile.png'),fullPage:true});
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
 assert(!overflow,'Горизонтальное переполнение мобильной страницы');assert.deepStrictEqual(errors,[]);
 const report={passed:true,checked_at:new Date().toISOString(),project_root:path.resolve(__dirname,'..'),
  checks:['Архив и события','ИИ-пояснение подтверждения','Ручная зона','Правила и экспорт','Текущий режим MT5','Устаревшая котировка','Нет подключения','Мобильная вёрстка'],
  live_ready:!!live.ready,live_rows:live.source?.rows||0,browser:'Chromium',desktop:'1440x1050',mobile:'390x844',errors};
 fs.writeFileSync(path.join(__dirname,'../results/ui-check.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
