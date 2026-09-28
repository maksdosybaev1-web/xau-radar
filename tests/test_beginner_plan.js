const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const nodes = new Map();
const context = vm.createContext({
  document: {querySelector: selector => {
    if (!nodes.has(selector)) nodes.set(selector, {hidden:false,textContent:''});
    return nodes.get(selector);
  }},
  window: {},
  fetch: () => new Promise(() => {}),
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'web', 'market.js'), 'utf8'), context);
vm.runInContext("marketUI.source='live'", context);
const choose = (state, now) => {
  context.testState = state;
  context.testNow = now;
  return vm.runInContext('currentBeginnerPlan(testState,testNow)', context);
};
const plan = {type:'sbr_sell_plan',level_id:'sbr-1',time:1000,
  analysis_plan:{side:'short',entry:[4275,4277],stop:4284,targets:[4267,4257,4248]}};
const state = events => ({stale:false,quote:{time:1100},plan_events:events});
assert.equal(choose(state([plan]),1100).level_id,'sbr-1');
assert.equal(choose(state([{type:'sbr_sell_closed',level_id:'sbr-1',time:1060},plan]),1100),null);
assert.equal(choose(state([{type:'sbr_sell_confirmed',level_id:'sbr-1',time:1060},plan]),1100),null);
assert.equal(choose(state([plan,{type:'sbr_sell_confirmed',level_id:'sbr-1',time:1000}]),1100),null);
assert.equal(choose({...state([plan]),stale:true},1100),null);
assert.equal(choose({...state([plan]),quote:{time:900}},1100),null);
assert.equal(choose({...state([plan]),quote:{time:1200}},1100),null);
assert.equal(choose({...state([plan]),quote:{time:2000}},2000),null);
const currentTime=Math.floor(Date.now()/1000);
const livePlan={...plan,time:currentTime};
context.testState={stale:false,quote:{time:currentTime},plan_events:[livePlan]};
vm.runInContext('renderBeginnerPlan(testState)',context);
assert.match(nodes.get('#beginnerHeadline').textContent,/возможной продажи/);
assert.equal(nodes.get('#beginnerEntry').textContent,'4 275–4 277');
assert.equal(nodes.get('#beginnerStop').textContent,'4 284');
assert.equal(nodes.get('#beginnerLevels').hidden,false);
context.testState.plan_events.unshift({type:'sbr_sell_closed',level_id:'sbr-1',time:currentTime});
vm.runInContext('renderBeginnerPlan(testState)',context);
assert.equal(nodes.get('#beginnerLevels').hidden,true);
assert.match(nodes.get('#beginnerHeadline').textContent,/плана сейчас нет/);
console.log('Beginner plan: fresh, resolved, stale and old states passed');
