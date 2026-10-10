const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={};
for(const id of ['k23-workload-chart','k23-outcomes-chart'])nodes[id]={hidden:false,nextElementSibling:{hidden:true},setAttribute(k,v){this[k]=v;}};
let theme='light',created=0;
const ctx={console,setTimeout(){},document:{documentElement:{},addEventListener(){},getElementById:id=>nodes[id]},getComputedStyle:()=>({getPropertyValue:key=>theme==='dark'?'#a9b2c0':'#63718a'}),Chart:function(canvas,config){Object.assign(this,config);this.update=()=>{this.updated=true};created++;}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('static/js/features/views.js','utf8').split('function renderAnalytics()')[0],ctx);
const perf={agents:[{name:'Sam',handled:3},{name:'Alex',handled:7}],resolved:5,active:3,missed:2,reassigned:4};
ctx.renderLeaderboardCharts(perf,[]);
assert.equal(created,2);assert.equal(ctx.k23WorkloadChart.data.labels[0],'Alex');
assert.equal(ctx.k23OutcomesChart.type,'bar');
assert.match(nodes['k23-workload-chart']['aria-label'],/Alex 7/);
theme='dark';ctx.renderLeaderboardCharts(perf,[]);
assert.equal(created,2);assert.equal(ctx.k23WorkloadChart.options.scales.y.ticks.color,'#a9b2c0');
assert.equal(ctx.k23WorkloadChart.options.plugins.tooltip.backgroundColor,'#a9b2c0');
ctx.renderLeaderboardCharts({agents:[]},[]);
assert.equal(nodes['k23-workload-chart'].hidden,true);assert.equal(nodes['k23-outcomes-chart'].nextElementSibling.hidden,false);
ctx.renderLeaderboardCharts(perf,[]);assert.equal(nodes['k23-workload-chart'].hidden,false);
const buttons=[0,1,2].map(()=>({classList:{remove(){},add(){}},setAttribute(k,v){this[k]=v;}}));
const state={setTimeout(){},preferences:{get(){},set(){}},document:{documentElement:{setAttribute(){}},getElementById(){return null},querySelectorAll(){return buttons}},renderLeaderboard(){},console};
vm.createContext(state);vm.runInContext(fs.readFileSync('static/js/core/state.js','utf8').split('function setAnalyticsPeriod')[0],state);
state.leaderboardRankPage=4;state.setLbPeriod('week',buttons[1]);
assert.equal(state.leaderboardRankPage,0);assert.equal(buttons[1]['aria-pressed'],'true');assert.equal(buttons[0]['aria-pressed'],'false');
console.log('PASS: chart sorting, activity counts, theme updates, empty-state recovery, period selection and pagination reset');



