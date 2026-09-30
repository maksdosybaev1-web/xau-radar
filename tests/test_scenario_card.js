const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const nodes = new Map();
const element = id => {
  if (!nodes.has(id)) nodes.set(id, {hidden:false, textContent:'', innerHTML:'', className:'', value:''});
  return nodes.get(id);
};
const current = {
  id:'plan-1', model:'SBR', key:'level-1', created_at:1000, stage_at:1060,
  stage:'confirmed', stage_reason:'M5 закрылась под пробитой поддержкой',
  valid_until:1600, plan_delivery:'sent', decision:null, trade:null,
  guidance:{tone:'ready',title:'Условия модели выполнены: проверьте SELL вручную',
    steps:['Сверьте условия в MT5','После исполнения запишите факт входа']},
  plan:{side:'short', entry:[4275,4277], stop:4284, targets:[4267,4257,4248],
    target_method:'midpoint_r_1_2_3', cancel_rule:'закрытие M5 выше 4284'},
  checks:{current:true, fresh:true, in_zone:true, spread_ok:true, unknown_open_risk:0,
    ready_to_review_entry:true, quote_price:4276, spread:0.2, open_risk:0,
    account_currency:'USD', risk_budget_025_pct:25, remaining_budget:25,
    loss_per_lot_at_stop:800, max_lots_under_budget:0.03}
};
const old = {...current, id:'plan-old', key:'level-old', stage:'expired',
  stage_reason:'Срок плана истёк', decision:null, trade:null,
  checks:{...current.checks, current:false, ready_to_review_entry:false}};
let payload = {quote_fresh:true, active_id:current.id, scenarios:[current,old]};
let fetchError = false;
let lastRequest;
let browserNow = 1061000;
let freshnessTick;
let soundCount=0;
class FakeAudio {
  state='running';currentTime=0;destination={};
  async resume(){}
  createOscillator(){return {frequency:{value:0},connect(){},start(){soundCount++},stop(){}}}
  createGain(){return {gain:{setValueAtTime(){},linearRampToValueAtTime(){}},connect(){}}}
}
const context = vm.createContext({
  document:{getElementById:element, activeElement:null},
  window:{AudioContext:FakeAudio},
  Date:class extends Date {static now(){return browserNow}},
  FormData:class {get(name){return name==='entry_time'?'1970-01-01T00:17:42Z':null}},
  setInterval:fn=>{freshnessTick=fn},
  fetch:async (url,options) => {lastRequest={url,options};if(fetchError)throw Error('Нет ответа сервера');return {ok:true, json:async () => payload}},
});
vm.runInContext(fs.readFileSync(path.join(__dirname,'..','web','scenarios.js'),'utf8'),context);

(async () => {
  await context.window.scenarioWorkspace.refresh('live');
  assert.match(element('beginnerHeadline').textContent,/Проверьте вход вручную/);
  assert.equal(element('beginnerHeadline').className,'scenario-ready');
  assert.match(element('beginnerNext').textContent,/Сверьте условия/);
  assert.equal(element('beginnerEntry').textContent,'4 275–4 277');
  assert.match(element('beginnerTargetMethod').textContent,/1R \/ 2R \/ 3R/);
  assert.match(element('beginnerLot').textContent,/не более 0,03 лота/);
  assert.equal(element('scenarioChecks').hidden,false);
  assert.match(element('scenarioCheckContent').innerHTML,/Оценка потери до стопа/);
  assert.match(element('assistantTitle').textContent,/SELL вручную/);

  payload = {quote_fresh:false, active_id:current.id, scenarios:[
    {...current, checks:{...current.checks, current:false, fresh:false, ready_to_review_entry:false}}
  ]};
  await context.window.scenarioWorkspace.refresh('live');
  assert.match(element('beginnerNext').textContent,/Ждём свежую котировку/);
  assert.match(element('beginnerLot').textContent,/не показывается/);
  assert.equal(element('scenarioCardTitle').textContent,'Проверка сценария приостановлена');
  assert.equal(element('beginnerHeadline').className,'');

  payload = {quote_fresh:true,data_ready:false,data_reason:'Нет свежей закрытой M1',
    active_id:current.id,scenarios:[{...current,checks:{...current.checks,current:false,
      data_ready:false,data_reason:'Нет свежей закрытой M1',ready_to_review_entry:false}}]};
  await context.window.scenarioWorkspace.refresh('live');
  assert.match(element('beginnerHeadline').textContent,/Проверка приостановлена/);
  assert.match(element('beginnerNext').textContent,/Нет свежей закрытой M1/);
  assert.doesNotMatch(element('scenarioDecision').innerHTML,/scenarioWatch/);
  assert.equal(element('beginnerHeadline').className,'');

  payload = {quote_fresh:true,active_id:current.id,scenarios:[
    {...current,decision:{action:'watch',time:1060}}
  ]};
  await context.window.scenarioWorkspace.refresh('live');
  const unsavedForm=element('scenarioTrade').innerHTML;
  assert.match(unsavedForm,/scenarioEntryForm/);
  payload.scenarios[0].mt5_position={ticket:123,position_id:120,entry:4276,lots:.02};
  payload.scenarios[0].mt5_link_reason='Найдена позиция MT5';
  await context.window.scenarioWorkspace.refresh('live');
  assert.match(element('scenarioTrade').innerHTML,/Подтянуть позицию MT5 и записать/);
  assert.match(element('scenarioTrade').innerHTML,/Тикет 123/);
  await element('scenarioImportPosition').onclick();
  assert.equal(lastRequest.url,'/api/scenario-import-position');
  assert.deepEqual(JSON.parse(lastRequest.options.body),{scenario_id:'plan-1',ticket:123,entry_time:1062});
  const linkedForm=element('scenarioTrade').innerHTML;
  context.document.activeElement={closest:()=>({})};
  fetchError=true;
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(element('beginnerHeadline').className,'');
  assert.match(element('beginnerNext').textContent,/Нет ответа сервера/);
  assert.match(element('assistantTitle').textContent,/Пауза/);
  assert.match(element('beginnerLot').textContent,/не показывается/);
  assert.equal(element('scenarioTrade').innerHTML,linkedForm);
  fetchError=false;
  context.document.activeElement=null;

  payload={...payload,as_of:1061,data_ready:true};
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(element('beginnerHeadline').className,'scenario-ready');
  context.document.activeElement={closest:()=>({})};
  const retainedForm=element('scenarioTrade').innerHTML;
  browserNow=1107000;
  freshnessTick();
  assert.equal(element('beginnerHeadline').className,'');
  assert.match(element('beginnerNext').textContent,/больше 45 секунд/);
  assert.equal(element('scenarioTrade').innerHTML,retainedForm);
  context.document.activeElement=null;

  payload = {quote_fresh:true, active_id:current.id, scenarios:[current,old]};
  await context.window.scenarioWorkspace.refresh('live');

  element('scenarioSelect').onchange({target:{value:'plan-old'}});
  assert.equal(element('beginnerHeadline').className,'');
  assert.match(element('beginnerHeadline').textContent,/Срок плана истёк/);
  assert.match(element('beginnerHeadline').textContent,/план продажи/);
  assert.equal(element('scenarioCardTitle').textContent,'Разбор завершённого сценария');
  assert.match(element('beginnerLot').textContent,/не рассчитывается/);
  assert.doesNotMatch(element('beginnerNext').textContent,/Сверьте условия/);

  browserNow=1061000;
  await element('assistantSound').onchange({target:{checked:true}});
  payload={as_of:1061,data_ready:true,quote_fresh:true,active_id:'plan-sound',scenarios:[
    {...current,id:'plan-sound'},old
  ]};
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(soundCount,1);
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(soundCount,1);
  payload={...payload,data_ready:false,data_reason:'Нет M1'};
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(soundCount,1);
  payload={...payload,data_ready:true};
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(soundCount,1);

  payload = {quote_fresh:true, active_id:null, scenarios:[]};
  element('scenarioSelect').onchange({target:{value:''}});
  await context.window.scenarioWorkspace.refresh('live');
  assert.equal(element('scenarioChecks').hidden,true);
  assert.equal(element('beginnerLot').textContent,'');
  console.log('Scenario card: current, historical and empty states passed');
})().catch(error => {console.error(error); process.exitCode=1});
